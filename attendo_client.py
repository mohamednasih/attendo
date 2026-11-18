#!/usr/bin/env python3
"""Self-contained Attendo profile reader and Firefox, Chrome, and Brave session authentication."""
from __future__ import annotations

import configparser
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://attendo.1337.ma/students/profile"
LOGIN_URL = "https://attendo.1337.ma/api/students/auth/login"
_browser_cookie: str | None = None


class AttendoError(RuntimeError):
    pass


@dataclass(frozen=True)
class Day:
    date: str
    hours: float
    counted: bool


@dataclass(frozen=True)
class Period:
    start: str
    end: str
    total: float
    days: tuple[Day, ...]

    @property
    def counted_days(self):
        return sum(day.counted for day in self.days)


@dataclass(frozen=True)
class Snapshot:
    login: str
    current: Period | None
    previous: Period | None
    live_estimate: str | None
    fetched_at: datetime
    target_hours: float | None = None
    today_hours: float | None = None


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_script = False
        self.buffer: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            self.in_script = True
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.in_script:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_script:
            self.scripts.append("".join(self.buffer))
            self.in_script = False


def _next_data(html: str) -> str:
    parser = _ScriptCollector()
    parser.feed(html)
    chunks: list[str] = []
    for script in parser.scripts:
        match = re.fullmatch(r"\s*self\.__next_f\.push\((.*)\)\s*", script, re.DOTALL)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], str):
            chunks.append(payload[1])
    return "".join(chunks)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden and text.strip():
            self.parts.append(text.strip())


def attendance_goal(profile):
    target = getattr(profile, 'target_hours', None)
    if target is None:
        return None
    current = getattr(profile, 'current', None)
    remaining_days = max(0, 7 - current.counted_days) if current else 7
    remaining_hours = max(0, target - current.total) if current else target
    return {'target_seconds': target * 3600,
            'remaining_seconds': remaining_hours * 3600,
            'remaining_days': remaining_days,
            'daily_seconds': remaining_hours * 3600 / remaining_days if remaining_days else None}


def parse_profile(html: str, fetched_at: datetime | None = None) -> Snapshot:
    data = _next_data(html)
    if not data:
        # Useful for saved fragments and tolerant of the escaped RSC form.
        data = html.replace(r'\"', '"')

    estimates = re.findall(r'"todayHoursEstimation"\s*:\s*(?:"([0-9]+:[0-9]{2})"|null)', data)
    estimate = estimates[-1] if estimates and estimates[-1] else None
    recorded = re.findall(r'"todayHours"\s*:\s*([0-9]+(?:\.[0-9]+)?|null)', data)
    today_hours = float(recorded[-1]) if recorded and recorded[-1] != 'null' else None

    logins = re.findall(r'"login"\s*:\s*"([^"\\]+)"', data)
    if not logins:
        raise AttendoError('Could not identify the student in the profile page.')
    login = logins[-1]

    decoder = json.JSONDecoder()
    periods: list[Period] = []
    for match in re.finditer(r'\{\s*"weekStart"\s*:', data):
        try:
            period, _ = decoder.raw_decode(data, match.start())
        except json.JSONDecodeError:
            continue
        start, end = period.get('weekStart'), period.get('weekEnd')
        total, daily = period.get('total'), period.get('daily')
        if not (isinstance(start, str) and isinstance(end, str)
                and type(total) in (int, float) and isinstance(daily, list)):
            continue
        days = []
        for day in daily:
            if not isinstance(day, dict):
                continue
            date, counted = day.get('date'), day.get('counted')
            hours = day.get('hours', day.get('hoursLogged'))
            if isinstance(date, str) and isinstance(counted, bool) and type(hours) in (int, float):
                days.append(Day(date, float(hours), counted))
        periods.append(Period(start, end, float(total), tuple(days)))
    if not periods:
        raise AttendoError("Could not find attendance periods in the profile page; the site may have changed.")

    # Attendo closes a period after seven counted days.
    if periods[-1].counted_days < 7:
        current = periods[-1]
        previous = periods[-2] if len(periods) > 1 else None
    else:
        current = None
        previous = periods[-1]

    visible = _VisibleText()
    visible.feed(html)
    target_match = re.search(r'Attendance target\s+(\d+(?:\.\d+)?)\s*h\s+per counted-day period',
                             ' '.join(visible.parts), re.IGNORECASE)
    target = float(target_match[1]) if target_match else None
    if target is not None and not 0 < target < 10000:
        target = None
    return Snapshot(
        today_hours=today_hours,
        target_hours=target,
        login=login,
        current=current,
        previous=previous,
        live_estimate=estimate,
        fetched_at=fetched_at or datetime.now().astimezone(),
    )


def _cookie_from_environment() -> str:
    if _browser_cookie:
        return f"student_token={_browser_cookie}"
    cookie = os.environ.get("ATTENDO_COOKIE", "").strip()
    if cookie:
        return cookie
    token = os.environ.get("ATTENDO_TOKEN", "").strip()
    if not token:
        raise AttendoError(
            "Set ATTENDO_TOKEN to your student_token cookie value "
            "(or set ATTENDO_COOKIE to the complete cookie header)."
        )
    login = os.environ.get("ATTENDO_LOGIN", "").strip()
    cookie = f"student_token={token}"
    if login:
        cookie += f"; student_login={login}"
    return cookie


def _firefox_roots() -> tuple[Path, ...]:
    home = Path.home()
    return (
        Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "mozilla/firefox",
        home / ".mozilla/firefox",
        home / "snap/firefox/common/.mozilla/firefox",
        home / ".var/app/org.mozilla.firefox/config/mozilla/firefox",
    )


def _firefox_profiles(roots: tuple[Path, ...] | None = None) -> list[Path]:
    profiles: list[Path] = []
    for root in roots or _firefox_roots():
        config = configparser.ConfigParser()
        ini = root / "profiles.ini"
        if ini.is_file():
            try:
                config.read(ini)
                sections = sorted(
                    (section for section in config.sections() if section.startswith("Profile")),
                    key=lambda section: config.getboolean(section, "Default", fallback=False),
                    reverse=True,
                )
                for section in sections:
                    value = config.get(section, "Path", fallback="")
                    if not value:
                        continue
                    path = Path(value).expanduser()
                    if config.getboolean(section, "IsRelative", fallback=True):
                        path = root / path
                    if path not in profiles:
                        profiles.append(path)
            except (configparser.Error, OSError):
                pass
        if root.is_dir():
            for path in root.glob("*.default*"):
                if path.is_dir() and path not in profiles:
                    profiles.append(path)
    return profiles


def firefox_student_token(roots: tuple[Path, ...] | None = None) -> str | None:
    """Read only Attendo's unexpired student token from Firefox profiles."""
    now = int(time.time())
    for profile in _firefox_profiles(roots):
        database = profile / "cookies.sqlite"
        if not database.is_file():
            continue
        def read_cookie(source: Path) -> tuple[str, int] | None:
            connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=1)
            try:
                return connection.execute(
                    "SELECT value, expiry FROM moz_cookies "
                    "WHERE name = ? AND host IN (?, ?) "
                    "ORDER BY expiry DESC LIMIT 1",
                    ("student_token", "attendo.1337.ma", ".attendo.1337.ma"),
                ).fetchone()
            finally:
                connection.close()

        try:
            row = read_cookie(database)
        except (OSError, sqlite3.Error):
            # Firefox can hold an exclusive lock on its live cookie database.
            # A snapshot preserves its WAL while avoiding that process lock.
            try:
                with tempfile.TemporaryDirectory() as directory:
                    snapshot = Path(directory) / "cookies.sqlite"
                    shutil.copy2(database, snapshot)
                    wal = database.with_name(f"{database.name}-wal")
                    if wal.is_file():
                        shutil.copy2(wal, snapshot.with_name(f"{snapshot.name}-wal"))
                    row = read_cookie(snapshot)
            except (OSError, sqlite3.Error):
                continue
        if row and row[0] and int(row[1]) > now:
            return str(row[0])
    return None


def _browser_order():
    selected = os.environ.get('ATTENDO_BROWSER', 'auto').strip().lower()
    if selected not in ('auto', 'firefox', 'chrome', 'brave'):
        raise AttendoError('ATTENDO_BROWSER must be auto, firefox, chrome, or brave.')
    return ('firefox', 'chrome', 'brave') if selected == 'auto' else (selected,)


def _chromium_cookie_files(browser):
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    roots = ([config / 'google-chrome', config / 'google-chrome-beta',
              home / '.var/app/com.google.Chrome/config/google-chrome'] if browser == 'chrome'
             else [config / 'BraveSoftware/Brave-Browser',
                   home / '.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser'])
    files = []
    for root in roots:
        for profile in [root / 'Default', *sorted(root.glob('Profile *'))]:
            for database in (profile / 'Network/Cookies', profile / 'Cookies'):
                if database.is_file():
                    files.append(database)
    return files


def chromium_student_token(browser):
    """Read Attendo cookies using the browser's normal desktop keyring."""
    files = _chromium_cookie_files(browser)
    if not files:
        return None
    dependencies = Path(__file__).resolve().parent / '.browser-deps'
    if dependencies.is_dir() and str(dependencies) not in sys.path:
        sys.path.insert(0, str(dependencies))
    try:
        import browser_cookie3
    except ImportError:
        raise AttendoError('Chrome/Brave cookie support is missing. Run make dependencies.') from None
    loader = getattr(browser_cookie3, browser)
    for database in files:
        try:
            cookies = loader(cookie_file=str(database), domain_name='attendo.1337.ma')
            candidates = [cookie for cookie in cookies
                          if cookie.name == 'student_token'
                          and cookie.domain in ('attendo.1337.ma', '.attendo.1337.ma')
                          and cookie.value and not cookie.is_expired()]
            if candidates:
                return max(candidates, key=lambda cookie: cookie.expires or float('inf')).value
        except Exception:
            # Keyring and database errors vary by desktop. Never include their
            # contents in logs, where decrypted cookies could be exposed.
            continue
    return None


def browser_student_token():
    for browser in _browser_order():
        token = firefox_student_token() if browser == 'firefox' else chromium_student_token(browser)
        if token:
            return token
    return None


def _browser_command():
    for browser in _browser_order():
        if browser == 'firefox':
            try:
                return _firefox_command()
            except AttendoError:
                continue
        commands = ('google-chrome', 'google-chrome-stable') if browser == 'chrome' else ('brave-browser', 'brave-browser-stable', 'brave')
        for name in commands:
            executable = shutil.which(name)
            if executable:
                return [executable]
        flatpak = shutil.which('flatpak')
        app = 'com.google.Chrome' if browser == 'chrome' else 'com.brave.Browser'
        if flatpak:
            try:
                check = subprocess.run([flatpak, 'info', app], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=5)
                if check.returncode == 0:
                    return [flatpak, 'run', app]
            except (OSError, subprocess.TimeoutExpired):
                pass
    raise AttendoError('Install Firefox, Chrome, or Brave, or set ATTENDO_TOKEN.')


def _firefox_command() -> list[str]:
    """Return a working Firefox command, including sandboxed installations."""
    errors: list[str] = []
    firefox = shutil.which("firefox")
    if firefox:
        try:
            check = subprocess.run(
                [firefox, "--version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
        else:
            if check.returncode == 0:
                return [firefox]
            detail = check.stderr.strip()
            if detail:
                errors.append(detail)

    flatpak = shutil.which("flatpak")
    if flatpak:
        try:
            check = subprocess.run(
                [flatpak, "info", "org.mozilla.firefox"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
        else:
            if check.returncode == 0:
                return [flatpak, "run", "org.mozilla.firefox"]

    message = "ATTENDO_TOKEN is not set. Automatic login requires a working Firefox installation."
    if errors:
        message += f" Firefox reported: {errors[0]}"
    raise AttendoError(message)


def ensure_browser_session(timeout: int = 180) -> None:
    """Reuse an available Attendo browser session or open the supported 42 OAuth flow."""
    global _browser_cookie
    if os.environ.get("ATTENDO_COOKIE", "").strip() or os.environ.get("ATTENDO_TOKEN", "").strip():
        return
    token = browser_student_token()
    if token:
        _browser_cookie = token
        return
    if not sys.stdin.isatty():
        raise AttendoError("No Attendo session found in Firefox, Chrome, or Brave. Sign in and unlock your desktop keyring, then refresh; or set ATTENDO_TOKEN.")
    browser_command = _browser_command()
    print("No Attendo session found. Opening the 42 sign-in in your browser…", file=sys.stderr)
    print("Complete the login there; this terminal will continue automatically.", file=sys.stderr)
    try:
        subprocess.Popen(
            [*browser_command, LOGIN_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise AttendoError(f"Could not open browser: {exc}") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        token = browser_student_token()
        if token:
            _browser_cookie = token
            print("Attendo session acquired.", file=sys.stderr)
            return
        time.sleep(1)
    raise AttendoError("Timed out waiting for the Attendo login. Run the command again to retry.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # An expired session can redirect to login; never forward its cookie.
        raise AttendoError('Attendo redirected the profile request. Sign in again in Firefox, Chrome, or Brave, or refresh ATTENDO_TOKEN.')


def fetch_profile(timeout: float = 15.0) -> Snapshot:
    request = urllib.request.Request(DEFAULT_URL, headers={
        'Accept': 'text/html,application/xhtml+xml',
        'Cookie': _cookie_from_environment(),
        'User-Agent': 'logged-time/1.0',
    })
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise AttendoError('Attendo profile response is too large.')
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AttendoError('Attendo rejected the session. Sign in again in Firefox, Chrome, or Brave, or refresh ATTENDO_TOKEN.') from error
        raise AttendoError(f'Attendo returned HTTP {error.code}.') from error
    except (urllib.error.URLError, OSError) as error:
        raise AttendoError('Could not reach Attendo; check your connection.') from error
    return parse_profile(body.decode('utf-8', errors='replace'))
