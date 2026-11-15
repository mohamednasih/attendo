#!/usr/bin/env python3
"""Keep workstation lock observations in files on the user's mounted home."""
import argparse
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import uuid

from logged_time import DEFAULT_STATE, read_state

DEFAULT_HISTORY = Path.home() / '.local/state/logged-time/history'


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def source_id():
    machine = Path('/etc/machine-id').read_text().strip()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return hashlib.sha256((machine + ':' + boot).encode()).hexdigest()


class HistoryWriter:
    """One file per collector instance: writers never share a file or database."""
    def __init__(self, directory, username, source=None):
        self.directory = Path(directory)
        self.username = username
        self.source = source or source_id()
        self.path = self.directory / f'{uuid.uuid4().hex}.json'
        self.intervals = {}

    def observe(self, rows, now):
        if not number(now) or now < 0:
            raise ValueError('Invalid observation time')
        updated = self.intervals.copy()
        for start, end in rows:
            if not number(start) or start < 0 or start > now:
                raise ValueError('Invalid lock start; check workstation clock')
            if end is not None and (not number(end) or not start <= end <= now):
                raise ValueError('Invalid lock end; check workstation clock')
            # A missing/pruned interval stays confirmed only through its last
            # observation. Never infer more lock time from process uptime.
            updated[start] = now if end is None else end
        if updated == self.intervals and self.path.exists():
            return
        data = {'version': 1, 'username': self.username, 'source': self.source,
                'observed': now, 'intervals': sorted(updated.items())}
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self.intervals = updated
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def fetch_history(directory, username, start, end):
    intervals, warnings = [], []
    try:
        paths = sorted(Path(directory).glob('*.json'))
    except OSError as error:
        return [], [f'Shared home history unavailable: {error}']
    for path in paths:
        try:
            data = json.loads(path.read_text())
            if data['version'] != 1:
                raise ValueError('Unknown history version')
            if data['username'] != username:
                continue
            observed = data['observed']
            if not number(observed) or observed < 0:
                raise ValueError('Invalid observation time')
            for a, b in data['intervals']:
                if not all(number(v) for v in (a, b)) or not 0 <= a <= b <= observed:
                    raise ValueError('Invalid interval')
            intervals.extend((a, b) for a, b in data['intervals'] if b > start and a < end)
        except (OSError, ValueError, KeyError, TypeError) as error:
            warnings.append(f'History file {path.name} unavailable: {error}')
    return intervals, warnings


def legacy_history(path, username):
    """Read the previous archive without creating or writing a shared SQLite DB."""
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        return db.execute('SELECT start,end FROM locks WHERE username=?', (username,)).fetchall()


class HistoryCollector:
    def __init__(self, directory, username, state=DEFAULT_STATE):
        self.writer = HistoryWriter(directory, username)
        self.state = state
        self.error = None
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def observe(self):
        try:
            rows = read_state(self.state, self.writer.username)
            self.writer.observe(rows, time.time())
            self.error = None
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error = f'Lock collection unavailable: {error}'

    def run(self):
        while not self.stop_event.is_set():
            self.observe()
            self.stop_event.wait(1)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('collect',))
    parser.add_argument('--history-dir', type=Path, default=DEFAULT_HISTORY)
    parser.add_argument('--user', default=getpass.getuser())
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        collector = HistoryCollector(args.history_dir, args.user, args.state)
        while True:
            collector.observe()
            if collector.error:
                print(collector.error, file=sys.stderr)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as error:
        parser.exit(2, f'Error: {error}\n')


if __name__ == '__main__':
    sys.exit(main())
