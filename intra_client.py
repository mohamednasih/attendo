"""Read location totals and campus workstation sessions from 42 Intra."""
from datetime import date, datetime
from hashlib import sha256
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener

from attendo_client import AttendoSessionError, _NoRedirect, _chromium_cookie_files, _firefox_profiles
import cluster_layout
from cluster_profile import parse_profile

HOST = 'translate.intra.42.fr'
META_HOST = 'meta.intra.42.fr'
CDN_HOST = 'cdn.intra.42.fr'
_portrait_urls = {}
_cluster_logins = frozenset()
PROFILE_ORIGIN = 'https://profile.intra.42.fr'
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36')


class IntraSessionError(ValueError):
    pass


def parse_locations(payload):
    if not isinstance(payload, dict):
        raise ValueError('42 Intra returned an unexpected location statistics format.')
    totals = {}
    for key, value in payload.items():
        match = re.fullmatch(r'(\d+):([0-5]?\d):([0-5]?\d(?:\.\d+)?)', value) if isinstance(value, str) else None
        if not match:
            raise ValueError('42 Intra returned an invalid location duration.')
        day = date.fromisoformat(key)
        hours, minutes, seconds = match.groups()
        totals[day] = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return totals


def browser_sessions(host=HOST):
    dependencies = Path(__file__).resolve().parent / '.browser-deps'
    if dependencies.is_dir() and str(dependencies) not in sys.path:
        sys.path.insert(0, str(dependencies))
    try:
        import browser_cookie3
    except ImportError:
        raise ValueError('Browser session support is missing. Run make dependencies.') from None

    firefox_files = [profile / 'cookies.sqlite' for profile in _firefox_profiles()
                     if (profile / 'cookies.sqlite').is_file()]
    unreadable = False
    for browser, files in (
        ('firefox', firefox_files),
        ('chrome', _chromium_cookie_files('chrome')),
        ('brave', _chromium_cookie_files('brave')),
    ):
        for path in files:
            try:
                cookies = getattr(browser_cookie3, browser)(cookie_file=str(path), domain_name='42.fr')
            except Exception:
                unreadable = True
                continue
            jar = CookieJar()
            for cookie in cookies:
                if cookie.domain.lstrip('.') in (host, 'intra.42.fr', '42.fr') and not cookie.is_expired():
                    jar.set_cookie(cookie)
            if len(jar):
                yield jar
    if unreadable:
        raise ValueError('Could not read the 42 Intra browser session. Unlock your desktop keyring and try again.')


def _fetch_session(host, path, headers, description, parse):
    invalid_response = False
    for jar in browser_sessions(host):
        request = Request(f'https://{host}{path}', headers={'Accept': '*/*', 'User-Agent': USER_AGENT, **headers})
        try:
            opener = build_opener(HTTPCookieProcessor(jar), _NoRedirect())
            with opener.open(request, timeout=10) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError(f'42 Intra returned an oversized {description} response.')
            try:
                return parse(body)
            except ValueError:
                if not re.search(rb'<input\b[^>]*\btype\s*=\s*["\']?password\b', body, re.I):
                    invalid_response = True
                continue
        except HTTPError as error:
            if error.code not in (401, 403):
                raise ValueError(f'42 Intra returned HTTP {error.code}.') from None
        except (URLError, OSError):
            raise ValueError('Could not reach 42 Intra. Check your connection and refresh.') from None
        except AttendoSessionError:
            continue
    if invalid_response:
        raise ValueError(f'42 Intra returned an unexpected {description} response.')
    raise IntraSessionError('Sign in to 42 Intra in Firefox, Chrome, or Brave, then refresh.')


def _fetch_json(host, path, headers, description):
    return _fetch_session(host, path, headers, description, json.loads)


def fetch_locations(login):
    payload = _fetch_json(HOST, f'/users/{quote(login, safe="")}/locations_stats.json',
                          {'Origin': PROFILE_ORIGIN, 'Referer': f'{PROFILE_ORIGIN}/'}, 'location statistics')
    return parse_locations(payload), datetime.now().astimezone()


def _cluster_floors():
    campus, index = _fetch_session(META_HOST, '/clusters', {'Accept': 'text/html'}, 'floor plan', cluster_layout.floor_index)
    floors = []
    for name, url in index:
        try:
            plan = cluster_layout.fetch_geometry(url, USER_AGENT)
        except (ValueError, OSError, AttendoSessionError):
            continue
        floors.append({'name': name, 'campus_id': campus, **plan})
    return floors


def fetch_cluster_portrait(key):
    url = _portrait_urls.get(key)
    if not url:
        return None
    for jar in browser_sessions(CDN_HOST):
        req = Request(url, headers={'User-Agent': USER_AGENT, 'Referer': f'https://{META_HOST}/'})
        try:
            with build_opener(HTTPCookieProcessor(jar), _NoRedirect()).open(req, timeout=6) as response:
                mime = response.headers.get_content_type()
                if mime in ('image/jpeg', 'image/png', 'image/webp'):
                    body = response.read(512 * 1024 + 1)
                    if 0 < len(body) <= 512 * 1024:
                        return mime, body
        except HTTPError as error:
            if error.code not in (401, 403):
                return None
        except AttendoSessionError:
            continue
    return None


def fetch_cluster_profile(login):
    if login not in _cluster_logins:
        return None
    return _fetch_session('profile.intra.42.fr', f'/users/{quote(login, safe="")}',
                          {'Accept': 'text/html'}, 'student profile', parse_profile)


def fetch_clusters():
    global _portrait_urls, _cluster_logins
    payload = _fetch_json(META_HOST, '/clusters.json',
                          {'Referer': f'https://{META_HOST}/clusters', 'X-Requested-With': 'XMLHttpRequest'},
                          'cluster')
    if not isinstance(payload, list):
        raise ValueError('42 Intra returned an unexpected cluster format.')
    stations = {}
    portraits = {}
    for record in payload:
        if not isinstance(record, dict) or not isinstance(record.get('host'), str) or not record['host'].strip():
            raise ValueError('42 Intra returned an invalid workstation.')
        if record.get('end_at'):
            continue
        host = record['host'].strip().lower()
        campus = record.get('campus_id')
        if campus is not None and type(campus) is not int:
            raise ValueError('42 Intra returned an invalid campus ID.')
        key = (campus, host)
        login = record.get('login')
        started = record.get('begin_at')
        stations[key] = {'host': host, 'campus_id': campus, 'login': login if isinstance(login, str) else None,
                         'begin_at': started if isinstance(started, str) else None}
        url = record.get('cdn_uri')
        if isinstance(url, str) and re.fullmatch(r'https://cdn\.intra\.42\.fr/users/[\w-]+/[\w.-]+\.(?i:jpe?g|png|webp)', url, re.ASCII):
            photo_key = sha256(url.encode()).hexdigest()
            portraits[photo_key] = url
            stations[key]['portrait'] = f'/api/cluster-portraits/{photo_key}'
    _portrait_urls = portraits
    _cluster_logins = frozenset(station['login'] for station in stations.values() if station['login'])
    try:
        floors = _cluster_floors()
    except ValueError:
        floors = []
    return {'stations': list(stations.values()), 'floors': floors, 'fetched_at': datetime.now().astimezone().isoformat()}
