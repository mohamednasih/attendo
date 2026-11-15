#!/usr/bin/env python3
"""Self-contained Attendo profile reader and Firefox session authentication."""
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


def parse_profile(html: str, fetched_at: datetime | None = None) -> Snapshot:
    data = _next_data(html)
    if not data:
        # Useful for saved fragments and tolerant of the escaped RSC form.
        data = html.replace(r'\"', '"')

    estimates = re.findall(r'"todayHoursEstimation"\s*:\s*(?:"([0-9]+:[0-9]{2})"|null)', data)
    estimate = estimates[-1] if estimates and estimates[-1] else None

    logins = re.findall(r'"login"\s*:\s*"([^"\\]+)"', data)
    if not logins:
        raise AttendoError('Could not identify the student in the profile page.')
    login = logins[-1]

    period_re = re.compile(
        r'"weekStart"\s*:\s*"([^"\\]+)"\s*,\s*'
        r'"weekEnd"\s*:\s*"([^"\\]+)"\s*,\s*'
        r'"total"\s*:\s*([0-9.]+)\s*,\s*"daily"\s*:\s*\[(.*?)\]\s*\}',
        re.DOTALL,
    )
    day_re = re.compile(
        r'"date"\s*:\s*"([^"\\]+)"\s*,\s*'
        r'"hours"\s*:\s*([0-9.]+)\s*,\s*"counted"\s*:\s*(true|false)'
    )
    periods: list[Period] = []
    for start, end, total, daily in period_re.findall(data):
        days = tuple(
            Day(date, float(hours), counted == "true")
            for date, hours, counted in day_re.findall(daily)
        )
        periods.append(Period(start, end, float(total), days))
    if not periods:
        raise AttendoError("Could not find attendance periods in the profile page; the site may have changed.")

    # Attendo closes a period after seven counted days.
    if periods[-1].counted_days < 7:
        current = periods[-1]
        previous = periods[-2] if len(periods) > 1 else None
    else:
        current = None
        previous = periods[-1]

    return Snapshot(
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
    """Reuse Firefox's Attendo session or open the supported 42 OAuth flow."""
    global _browser_cookie
    if os.environ.get("ATTENDO_COOKIE", "").strip() or os.environ.get("ATTENDO_TOKEN", "").strip():
        return
    token = firefox_student_token()
    if token:
        _browser_cookie = token
        return
    if not sys.stdin.isatty():
        raise AttendoError("ATTENDO_TOKEN is not set and interactive browser login is unavailable.")
    firefox_command = _firefox_command()
    print("No Attendo session found. Opening the 42 sign-in in Firefox…", file=sys.stderr)
    print("Complete the login there; this terminal will continue automatically.", file=sys.stderr)
    try:
        subprocess.Popen(
            [*firefox_command, LOGIN_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise AttendoError(f"Could not open Firefox: {exc}") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        token = firefox_student_token()
        if token:
            _browser_cookie = token
            print("Attendo session acquired.", file=sys.stderr)
            return
        time.sleep(1)
    raise AttendoError("Timed out waiting for the Attendo login. Run the command again to retry.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # An expired session can redirect to login; never forward its cookie.
        raise AttendoError('Attendo redirected the profile request. Sign in again in Firefox or refresh ATTENDO_TOKEN.')


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
            raise AttendoError('Attendo rejected the session. Sign in again in Firefox or refresh ATTENDO_TOKEN.') from error
        raise AttendoError(f'Attendo returned HTTP {error.code}.') from error
    except (urllib.error.URLError, OSError) as error:
        raise AttendoError('Could not reach Attendo; check your connection.') from error
    return parse_profile(body.decode('utf-8', errors='replace'))
