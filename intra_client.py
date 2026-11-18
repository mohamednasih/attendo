"""Read daily location totals using a signed-in 42 Intra browser session."""
from datetime import date, datetime
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, Request, build_opener

from attendo_client import AttendoError, _NoRedirect, _chromium_cookie_files, _firefox_profiles

HOST = 'translate.intra.42.fr'
PROFILE_ORIGIN = 'https://profile.intra.42.fr'
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36')


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


def browser_sessions():
    dependencies = Path(__file__).resolve().parent / '.browser-deps'
    if dependencies.is_dir() and str(dependencies) not in sys.path:
        sys.path.insert(0, str(dependencies))
    try:
        import browser_cookie3
    except ImportError:
        raise ValueError('Browser session support is missing. Run make dependencies.') from None

    firefox_files = [profile / 'cookies.sqlite' for profile in _firefox_profiles()]
    for browser, files in (
        ('firefox', firefox_files),
        ('chrome', _chromium_cookie_files('chrome')),
        ('brave', _chromium_cookie_files('brave')),
    ):
        for path in files:
            try:
                cookies = getattr(browser_cookie3, browser)(cookie_file=str(path), domain_name='42.fr')
            except Exception:
                continue
            jar = CookieJar()
            for cookie in cookies:
                if cookie.domain.lstrip('.') in (HOST, 'intra.42.fr', '42.fr') and not cookie.is_expired():
                    jar.set_cookie(cookie)
            if len(jar):
                yield jar


def fetch_locations(login):
    request_url = f'https://{HOST}/users/{quote(login, safe="")}/locations_stats.json'
    for jar in browser_sessions():
        request = Request(request_url, headers={
            'Accept': '*/*',
            'Origin': PROFILE_ORIGIN,
            'Referer': f'{PROFILE_ORIGIN}/',
            'User-Agent': USER_AGENT,
        })
        try:
            opener = build_opener(HTTPCookieProcessor(jar), _NoRedirect())
            with opener.open(request, timeout=10) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError('42 Intra returned an oversized location statistics response.')
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            return parse_locations(payload), datetime.now().astimezone()
        except HTTPError as error:
            if error.code not in (401, 403):
                raise ValueError(f'42 Intra returned HTTP {error.code}.') from None
        except URLError:
            raise ValueError('Could not reach 42 Intra. Check your connection and refresh.') from None
        except AttendoError:
            continue
    raise ValueError('Sign in to 42 Intra in Firefox, Chrome, or Brave, then refresh.')
