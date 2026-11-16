#!/usr/bin/env python3
"""Live daily Attendo time minus locally observed lock time."""
import argparse
from datetime import date, datetime, time as day_time, timedelta
import json
import math
import getpass
from pathlib import Path
import re
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

import attendo_client as client

from logged_time import DEFAULT_ARCHIVE, DEFAULT_STATE, locked_seconds, read_state
from shared_history import DEFAULT_HISTORY, HistoryCollector, fetch_history, legacy_history


def day_window(day, zone, now):
    start = datetime.combine(day, day_time(), zone).timestamp()
    end = datetime.combine(day + timedelta(days=1), day_time(), zone).timestamp()
    if start > now:
        raise ValueError('Cannot calculate a future day')
    return start, min(end, now)


def daily_total(profile, day, today):
    if day == today:
        match = re.fullmatch(r'(\d+):([0-5]\d)', profile.live_estimate or '')
        if not match:
            raise ValueError('Attendo did not provide a live HH:MM total for today; saved hours are not a live substitute')
        return int(match[1]) * 3600 + int(match[2]) * 60, 'todayHoursEstimation', 60
    for period in (profile.current, profile.previous):
        if period:
            for entry in period.days:
                if entry.date == day.isoformat():
                    value = entry.hours * 3600
                    if not math.isfinite(value) or value < 0:
                        raise ValueError('Invalid saved daily total')
                    return value, 'daily.hours', None
    raise ValueError(f'Attendo did not return a daily total for {day}')


def report(profile, day, zone, intervals, now, warnings):
    today = datetime.fromtimestamp(now, zone).date()
    start, end = day_window(day, zone, now)
    fetched = profile.fetched_at.timestamp()
    if profile.fetched_at.tzinfo is None or not math.isfinite(fetched) or fetched > now:
        raise ValueError('Attendo fetch time must be timezone-aware and cannot be in the future')
    if day == today and datetime.fromtimestamp(fetched, zone).date() != today:
        raise ValueError('No fresh Attendo data for this day')
    # A cached total describes the fetch, not the later display time. Subtracting
    # newer locks would steadily reduce logged time during a network outage.
    accounting_end = min(end, fetched)
    if accounting_end < start:
        raise ValueError('Attendo data predates the requested day')
    total, source, resolution = daily_total(profile, day, today)
    locked = locked_seconds(intervals, [(start, accounting_end)], accounting_end)
    if total > accounting_end - start + (resolution or 0):
        raise ValueError('Attendo total exceeds this local day; check the reporting timezone')
    deduction = locked if day == today else 0
    difference = total - deduction
    return {
        'date': day.isoformat(), 'timezone': str(zone), 'username': profile.login,
        'attendance_goal': client.attendance_goal(profile),
        'attendo_seconds': total, 'locked_seconds': locked,
        'lock_deduction_seconds': deduction,
        'time_kind': 'estimated' if day == today else 'recorded',
        'logged_seconds': difference if difference >= 0 else None,
        'attendo_source': source, 'attendo_resolution_seconds': resolution,
        'attendo_fetched_at': profile.fetched_at.isoformat(),
        'accounted_through': datetime.fromtimestamp(accounting_end, zone).isoformat(),
        'calculated_at': datetime.fromtimestamp(now, zone).isoformat(),
        'history_complete': False,
        'warnings': warnings + (['Observed lock history may be incomplete; collectors must run on every workstation.'] if day == today else [])
        + (['Lock duration exceeds the Attendo total; wait for a fresh total or verify its scope.'] if difference < 0 else []),
    }


def duration(seconds):
    if seconds is None:
        return 'unavailable'
    value = int(seconds)
    return f'{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}'


def format_report(data, refresh):
    updated = datetime.fromisoformat(data['attendo_fetched_at']).astimezone(ZoneInfo(data['timezone']))
    lines = [
        f"DAILY LOGGED TIME · {data['date']}",
        f"{data['username']} · {data['timezone']}",
        '',
        f"  Attendo total    {duration(data['attendo_seconds']):>12}",
        f"  Lock deduction − {duration(data['lock_deduction_seconds']):>12}",
        '  ' + '─' * 29,
        f"  Logged time      {duration(data['logged_seconds']):>12} ({data['time_kind']})",
        '',
        f"Updated {updated.strftime('%H:%M:%S')} · refresh every {refresh:g}s · Ctrl+C to quit",
    ]
    if data['warnings']:
        lines += ['', *[f"Note: {warning}" for warning in data['warnings']]]
    return '\n'.join(lines)


def main():
    if sys.argv[1:2] == ['dock']:
        import os
        os.execv('/usr/bin/python3', ['/usr/bin/python3', str(Path(__file__).resolve().with_name('dock_dashboard.py')), *sys.argv[2:]])
    if sys.argv[1:2] == ['notifications']:
        import os
        os.execv('/usr/bin/python3', ['/usr/bin/python3', str(Path(__file__).resolve().with_name('desktop_notification.py')), *sys.argv[2:]])
    if sys.argv[1:2] == ['widget']:
        import os
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
  (no command)   Show the live daily report; Ctrl-C to exit
  locks          Display lock periods and total locked time
  notifications  GNOME notification card with Refresh and Show locks actions
  widget         Compact floating time indicator; click for details
  dock           GNOME dashboard with totals and lock history

Examples:
  logged-time
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
    parser.add_argument('--once', action='store_true', help='Fetch and print one report')
    parser.add_argument('--date', type=date.fromisoformat, help='Default: today, rolling at midnight')
    parser.add_argument('--timezone', default='Africa/Casablanca')
    parser.add_argument('--refresh', type=float, default=60, help='Fetch and display interval in seconds (minimum 15)')
    parser.add_argument('--json', action='store_true', help='Emit a JSON report at each refresh')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument('--history-dir', type=Path, default=DEFAULT_HISTORY, help='History directory on the shared home mount')
    args = parser.parse_args()
    collector = None
    try:
        if not math.isfinite(args.refresh) or args.refresh < 15:
            raise ValueError('--refresh must be at least 15 seconds')
        zone = ZoneInfo(args.timezone)
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
            start, end = day_window(args.date or today, zone, now)
            shared, file_warnings = fetch_history(args.history_dir, profile.login, start, end)
            intervals.extend(shared)
            warnings.extend(file_warnings)
            try:
                intervals.extend(legacy_history(args.archive, profile.login))
            except (OSError, ValueError, sqlite3.Error) as error:
                warnings.append(f'Legacy lock archive unavailable: {error}')
            if collector.error:
                warnings.append(collector.error)
            data = report(profile, args.date or today, zone, intervals, now, warnings)
            from dataclasses import asdict
            data['attendance_periods'] = [dict(asdict(period), label=label)
                                          for label, period in (('Current period', getattr(profile, 'current', None)),
                                                                ('Previous period', getattr(profile, 'previous', None)))
                                          if period is not None]
            data['history_source'] = 'attendo_recorded' if data['time_kind'] == 'recorded' else 'shared_home'
            if args.json or args.once:
                print(json.dumps(data), flush=True)
            else:
                text = format_report(data, args.refresh)
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
