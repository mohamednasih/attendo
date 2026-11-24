#!/usr/bin/env python3
"""Live daily Attendo time minus locally observed lock time."""
import argparse
from datetime import date, datetime, time as day_time, timedelta
import json
import math
import os
import getpass
from pathlib import Path
import platform
import re
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

import attendo_client as client
import intra_client

from logged_time import DEFAULT_ARCHIVE, DEFAULT_STATE, duration, locked_seconds, read_state
from shared_history import DEFAULT_HISTORY, HistoryCollector, fetch_history, legacy_history


def day_window(day, zone, now):
    start = datetime.combine(day, day_time(), zone).timestamp()
    end = datetime.combine(day + timedelta(days=1), day_time(), zone).timestamp()
    if start > now:
        raise ValueError('Cannot calculate a future day')
    return start, min(end, now)


def recorded_total(profile, day):
    fetched = getattr(profile, 'fetched_at', None)
    today_hours = getattr(profile, 'today_hours', None)
    if fetched is not None and today_hours is not None and day == fetched.astimezone(ZoneInfo('Africa/Casablanca')).date():
        value = today_hours * 3600
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid saved daily total')
        return value
    for period in (getattr(profile, 'current', None), getattr(profile, 'previous', None)):
        if period:
            for entry in period.days:
                if entry.date == day.isoformat():
                    value = entry.hours * 3600
                    if not math.isfinite(value) or value < 0:
                        raise ValueError('Invalid saved daily total')
                    return value
    return None


def daily_total(profile, day, today):
    if day == today:
        match = re.fullmatch(r'(\d+):([0-5]\d)', profile.live_estimate or '')
        if not match:
            return None, None, None
        return int(match[1]) * 3600 + int(match[2]) * 60, 'todayHoursEstimation', 60
    saved = recorded_total(profile, day)
    if saved is not None:
        return saved, 'daily.hours', None
    return None, 'daily.hours', None


def period_log_totals(period, locations):
    first, last = (date.fromisoformat(bound[:10]) for bound in (period.start, period.end))
    totals = {}
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        totals[day.isoformat()] = locations.get(day)
    return totals


def report(profile, day, zone, intervals, now, warnings, *, intra_estimate=None):
    today = datetime.fromtimestamp(now, zone).date()
    start, end = day_window(day, zone, now)
    estimate_time = intra_estimate[1] if intra_estimate is not None else profile.fetched_at
    fetched = estimate_time.timestamp()
    if estimate_time.tzinfo is None or not math.isfinite(fetched) or fetched > now:
        raise ValueError('Attendo fetch time must be timezone-aware and cannot be in the future')
    if day == today and datetime.fromtimestamp(fetched, zone).date() != today:
        raise ValueError('No fresh Attendo data for this day')
    # A cached total describes the fetch, not the later display time. Subtracting
    # newer locks would steadily reduce logged time during a network outage.
    accounting_end = min(end, fetched)
    if accounting_end < start:
        raise ValueError('Attendo data predates the requested day')
    if intra_estimate is None:
        total, source, resolution = daily_total(profile, day, today)
    else:
        total, source, resolution = intra_estimate[0], '42_intra_locations', None
    locked = locked_seconds(intervals, [(start, accounting_end)], accounting_end)
    if intra_estimate is None and total is not None and total > accounting_end - start + (resolution or 0):
        raise ValueError('Attendo total exceeds this local day; check the reporting timezone')
    estimated = intra_estimate is not None or day == today
    deduction = (locked if estimated else 0) if total is not None else None
    difference = total - deduction if total is not None else None
    if total is None and intra_estimate is None:
        missing_total = ('Attendo has not provided a live total for today.' if day == today
                         else f'Attendo did not return a daily total for {day}.')
        warnings = [*warnings, missing_total]
    return {
        'date': day.isoformat(), 'timezone': str(zone), 'username': profile.login,
        'current_workstation': platform.node().partition('.')[0] or None,
        'attendance_goal': client.attendance_goal(profile),
        'attendo_seconds': total, 'locked_seconds': locked,
        'attendo_recorded_seconds': recorded_total(profile, day),
        'lock_deduction_seconds': deduction,
        'time_kind': 'estimated' if estimated else 'recorded',
        'logged_seconds': difference if difference is not None and difference >= 0 else None,
        'attendo_source': source, 'attendo_resolution_seconds': resolution,
        'attendo_fetched_at': estimate_time.isoformat(),
        'accounted_through': datetime.fromtimestamp(accounting_end, zone).isoformat(),
        'calculated_at': datetime.fromtimestamp(now, zone).isoformat(),
        'history_complete': False,
        'warnings': warnings + (['Observed lock history may be incomplete; collectors must run on every workstation.'] if estimated else [])
        + (['Lock duration exceeds the attendance total; wait for a fresh total or verify its scope.'] if difference is not None and difference < 0 else []),
    }


def format_report(data, refresh=None):
    updated = datetime.fromisoformat(data['attendo_fetched_at']).astimezone(ZoneInfo(data['timezone']))
    lines = [
        f"DAILY LOGGED TIME · {data['date']}",
        f"{data['username']} · {data['timezone']}",
        '',
        f"  Attendo total    {duration(data['attendo_seconds']):>12}",
        f"  Attendo recorded {duration(data['attendo_recorded_seconds']):>12}",
        f"  Locked time      {duration(data['locked_seconds']):>12}",
        '  ' + '─' * 29,
        f"  Logged time      {duration(data['logged_seconds']):>12} ({data['time_kind']})",
        '',
        f"Updated {updated.strftime('%H:%M:%S')}" + (f" · refresh every {refresh:g}s · Ctrl+C to quit" if refresh is not None else ""),
    ]
    if data['time_kind'] == 'recorded':
        lines += ['', 'Recorded attendance already excludes locks; locked time is shown for reference.']
    if data['warnings']:
        lines += ['', *[f"Note: {warning}" for warning in data['warnings']]]
    return '\n'.join(lines)


def main():
    if sys.argv[1:2] == ['dock']:
        os.execv('/usr/bin/python3', ['/usr/bin/python3', str(Path(__file__).resolve().with_name('dock_dashboard.py')), *sys.argv[2:]])
    if sys.argv[1:2] == ['notifications']:
        os.execv('/usr/bin/python3', ['/usr/bin/python3', str(Path(__file__).resolve().with_name('desktop_notification.py')), *sys.argv[2:]])
    if sys.argv[1:2] == ['widget']:
        os.execv('/usr/bin/python3', ['/usr/bin/python3', str(Path(__file__).resolve().with_name('desktop_widget.py')), *sys.argv[2:]])
    if sys.argv[1:2] == ['locks']:
        from shared_history import main as history_main
        return history_main(['list', *sys.argv[2:]])
    parser = argparse.ArgumentParser(
        prog='logged-time',
        usage='%(prog)s [COMMAND] [OPTIONS]',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Commands:
  (no command)   Open the website (installed launcher)
  stats          Print a one-time terminal report
  locks          Display lock periods and total locked time
  notifications  GNOME notification card with Refresh and Show locks actions
  widget         Compact floating time indicator; click for details
  dock           GNOME dashboard with totals and lock history

Examples:
  logged-time
  logged-time stats
  logged-time stats --date 2026-11-14
  logged-time --once
  logged-time --date 2026-11-14 --once
  logged-time locks
  logged-time locks --date 2026-11-14 --json
  logged-time notifications
  logged-time widget
  logged-time dock

Report options are listed above. For lock options, use logged-time locks --help.
Desktop reports load initially and refresh on interaction.
Attendo reports require a Firefox, Chrome, or Brave login or ATTENDO_TOKEN; locks work offline.''',
    )
    parser.add_argument('--intra', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--no-collect', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--once', action='store_true', help='Fetch and print one report')
    parser.add_argument('--date', type=date.fromisoformat, help='Default: today, rolling at midnight')
    parser.add_argument('--timezone', default='Africa/Casablanca')
    parser.add_argument('--refresh', type=float, default=60, help='Fetch and display interval in seconds (minimum 15)')
    parser.add_argument('--json', action='store_true', help='Emit a JSON report at each refresh')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument('--history-dir', type=Path, default=DEFAULT_HISTORY, help='History directory on the shared home mount')
    terminal_stats = sys.argv[1:2] == ['stats']
    args = parser.parse_args(sys.argv[2:] if terminal_stats else None)
    if terminal_stats:
        args.once = True
    collector = None
    try:
        if not math.isfinite(args.refresh) or args.refresh < 15:
            raise ValueError('--refresh must be at least 15 seconds')
        zone = ZoneInfo(args.timezone)
        if not args.no_collect:
            collector = HistoryCollector(args.history_dir, getpass.getuser(), args.state)
            collector.start()
        client.ensure_browser_session()
        profile = None
        next_fetch = 0
        next_display = 0
        rendered_lines = 0
        display_day = None
        fetch_day = None
        while True:
            now = time.time()
            today = datetime.fromtimestamp(now, zone).date()
            warnings = []
            if time.monotonic() >= next_fetch or fetch_day != today:
                try:
                    profile = client.fetch_profile()
                    fetch_day = today
                    next_fetch = time.monotonic() + args.refresh
                except client.AttendoError as error:
                    if args.once or profile is None:
                        raise ValueError(str(error)) from error
                    print(f'Attendo refresh failed: {error}', file=sys.stderr)
                    next_fetch = time.monotonic() + args.refresh
            if profile is None:
                raise ValueError('No Attendo profile available')
            now = time.time()
            today = datetime.fromtimestamp(now, zone).date()
            intra_estimate = None
            locations = {}
            day = args.date or today
            if args.intra and day in (today, today - timedelta(days=1)):
                login = os.environ.get('INTRA_LOGIN') or profile.login
                try:
                    locations, fetched_at = intra_client.fetch_locations(login)
                    total = locations.get(day)
                    if total is None:
                        warnings.append(f'42 Intra returned no location total for {day}.')
                    intra_estimate = total, fetched_at
                except ValueError as error:
                    warnings.append(str(error))
                    intra_estimate = None, profile.fetched_at
            now = time.time()
            age = now - profile.fetched_at.timestamp()
            if age > args.refresh + 5:
                warnings.append('Attendo data is stale; total and lock deductions stop at the last fetch.')
            try:
                current = read_state(args.state, profile.login)
            except (OSError, ValueError, KeyError, TypeError) as error:
                current = []
                warnings.append(f'Current lock state unavailable: {error}')
            intervals = [(a, now if b is None else b) for a, b in current]
            if not args.once and time.monotonic() < next_display and display_day == today:
                time.sleep(1)
                continue
            if fetch_day != today and (args.date is None or args.date == today):
                raise ValueError('No fresh Attendo data for the new day; retry after the connection recovers')
            start, end = day_window(day, zone, now)
            shared, file_warnings = fetch_history(args.history_dir, profile.login, start, end)
            intervals.extend(shared)
            warnings.extend(file_warnings)
            try:
                intervals.extend(legacy_history(args.archive, profile.login))
            except (OSError, ValueError, sqlite3.Error) as error:
                warnings.append(f'Legacy lock archive unavailable: {error}')
            if collector is not None and collector.error:
                warnings.append(collector.error)
            data = report(profile, day, zone, intervals, now, warnings,
                          intra_estimate=intra_estimate)
            if args.intra and profile.current:
                data['period_log_seconds'] = period_log_totals(profile.current, locations)
            from dataclasses import asdict
            data['attendance_periods'] = [dict(asdict(period), label=label)
                                          for label, period in (('Current period', getattr(profile, 'current', None)),
                                                                ('Previous period', getattr(profile, 'previous', None)))
                                          if period is not None]
            data['history_source'] = 'attendo_recorded' if data['time_kind'] == 'recorded' else 'shared_home'
            if args.json or (args.once and not terminal_stats):
                print(json.dumps(data), flush=True)
            else:
                text = format_report(data, None if terminal_stats else args.refresh)
                if sys.stdout.isatty() and rendered_lines:
                    print(f'\033[{rendered_lines}F\033[J', end='')
                print(text, flush=True)
                rendered_lines = len(text.splitlines())
            next_display = time.monotonic() + args.refresh
            display_day = today
            if args.once:
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print()
    except Exception as error:
        parser.exit(2, f'Error: {error}\n')
    finally:
        if collector is not None:
            collector.stop()


if __name__ == '__main__':
    main()
