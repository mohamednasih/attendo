#!/usr/bin/env python3
"""Read-only 42 lock accounting; no third-party dependencies."""
import argparse
from datetime import datetime
import getpass
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

DEFAULT_STATE = Path('/run/42-lock/seat0.json')
DEFAULT_ARCHIVE = Path.home() / '.local/state/logged-time/locks.sqlite3'


def timestamp(value):
    try:
        result = float(value)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Dates must include a timezone, e.g. 2026-11-14T00:00:00+01:00')
        result = parsed.timestamp()
    if not math.isfinite(result):
        raise ValueError('Timestamp must be finite')
    return result


def read_state(path, username):
    data = json.loads(path.read_text())
    if data['username'] != username:
        raise ValueError(f"State belongs to {data['username']}, not {username}")
    intervals = []
    for start, end in data['intervals']:
        if (not isinstance(start, (int, float)) or not math.isfinite(start)
                or (end is not None and (not isinstance(end, (int, float))
                    or not math.isfinite(end) or end < start))):
            raise ValueError('Invalid lock interval')
        intervals.append((float(start), None if end is None else float(end)))
    return intervals


def snapshot(archive, username, intervals, now):
    archive.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(archive) as db:
        db.execute('CREATE TABLE IF NOT EXISTS locks (username TEXT, start REAL, end REAL, PRIMARY KEY(username, start))')
        # Open intervals are only confirmed up to the observation time. A later
        # closed record replaces that lower bound with the actual end.
        db.executemany('INSERT INTO locks VALUES (?, ?, ?) ON CONFLICT(username,start) DO UPDATE SET end=MAX(locks.end,excluded.end)',
                       [(username, start, now if end is None else end) for start, end in intervals])
        return db.execute('SELECT start,end FROM locks WHERE username=? ORDER BY start', (username,)).fetchall()


def locked_seconds(intervals, windows, now):
    clipped = []
    for start, end in intervals:
        end = now if end is None else min(end, now)
        for lo, hi in windows:
            a, b = max(start, lo), min(end, hi)
            if b > a:
                clipped.append((a, b))
    merged = []
    for a, b in sorted(clipped):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return sum(b - a for a, b in merged)


def main():
    parser = argparse.ArgumentParser(description='Logged time = supplied total seconds minus observed locked seconds within matching session windows.')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument('--user', default=getpass.getuser())
    parser.add_argument('--watch', type=float, metavar='SECONDS', help='Continuously archive available lock intervals')
    parser.add_argument('--window', nargs=2, action='append', metavar=('START', 'END'), help='Repeat for each session; Unix seconds or ISO dates with timezone')
    parser.add_argument('--total-seconds', type=float, help='Actual locker total for exactly these windows')
    args = parser.parse_args()
    try:
        if args.watch is not None:
            if not math.isfinite(args.watch) or args.watch <= 0:
                raise ValueError('Watch interval must be positive and finite')
            print(f'Archiving available locks to {args.archive}', flush=True)
            while True:
                try:
                    intervals = read_state(args.state, args.user)
                    snapshot(args.archive, args.user, intervals, time.time())
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
                    print(f'Observation unavailable: {error}', file=sys.stderr, flush=True)
                time.sleep(args.watch)
        if not args.window:
            raise ValueError('Supply --window START END (repeat for multiple sessions), or --watch SECONDS')
        now = time.time()
        windows = [(timestamp(a), timestamp(b)) for a, b in args.window]
        if any(b <= a or b > now for a, b in windows):
            raise ValueError('Windows must have START < END and cannot end in the future')
        windows.sort()
        if any(a < previous[1] for previous, (a, b) in zip(windows, windows[1:])):
            raise ValueError('Session windows must not overlap')
        warnings = ['Available lock history may be incomplete; missing intervals cannot be reconstructed.']
        try:
            intervals = read_state(args.state, args.user)
        except (OSError, ValueError, KeyError, TypeError) as error:
            intervals = []
            warnings.append(f'Current state unavailable: {error}')
        intervals = snapshot(args.archive, args.user, intervals, now)
        locked = locked_seconds(intervals, windows, now)
        result = {'username': args.user, 'locked_seconds': locked, 'history_complete': False, 'warnings': warnings}
        if args.total_seconds is not None:
            total = args.total_seconds
            if not math.isfinite(total) or total < 0 or total > sum(b-a for a,b in windows) + 0.001:
                raise ValueError('Total must be finite, nonnegative, and no larger than the session windows')
            if locked > total:
                raise ValueError('Observed locked time exceeds supplied total; verify its scope and whether locks were already deducted')
            result.update(total_seconds=total, logged_seconds=total-locked)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(2, f'Error: {error}\n')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
