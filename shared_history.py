#!/usr/bin/env python3
"""Store workstation lock observations in SQLite on the shared home."""
import argparse
from contextlib import closing, contextmanager
from datetime import date, datetime, time as day_time, timedelta
import getpass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from logged_time import DEFAULT_ARCHIVE, DEFAULT_STATE, read_state

DEFAULT_HISTORY = Path.home() / '.local/state/logged-time/history'


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def source_id():
    machine = Path('/etc/machine-id').read_text().strip()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return hashlib.sha256((machine + ':' + boot).encode()).hexdigest()


def json_history(path):
    raw = path.read_bytes()
    data = json.loads(raw)
    if data['version'] != 1:
        raise ValueError('Unknown history version')
    username, observed = data['username'], data['observed']
    if not isinstance(username, str) or not username or not number(observed) or observed < 0:
        raise ValueError('Invalid history metadata')
    rows = []
    for start, end in data['intervals']:
        if not number(start) or not number(end) or not 0 <= start <= end <= observed:
            raise ValueError('Invalid lock interval')
        rows.append((float(start), float(end)))
    return username, rows, hashlib.sha256(raw).hexdigest()


def store_observation(db, source, position, username, start, end):
    db.execute(
        'INSERT INTO intervals(username, start, end) VALUES (?, ?, ?) '
        'ON CONFLICT(username, end, start) DO NOTHING',
        (username, start, end),
    )
    db.execute(
        'INSERT INTO observations(source, position, interval_id) '
        'SELECT ?, ?, id FROM intervals WHERE username = ? AND start = ? AND end = ? '
        'ON CONFLICT(source, position) DO UPDATE SET interval_id = excluded.interval_id',
        (source, position, username, start, end),
    )


def upgrade_history_schema(db):
    with db:
        db.execute('BEGIN IMMEDIATE')
        version, = db.execute('PRAGMA user_version').fetchone()
        if version >= 3:
            return
        if version:
            db.execute('ALTER TABLE intervals RENAME TO old_intervals')
        db.execute(
            'CREATE TABLE intervals (id INTEGER PRIMARY KEY, username TEXT NOT NULL, '
            'start REAL NOT NULL, end REAL NOT NULL, UNIQUE (username, end, start))'
        )
        db.execute(
            'CREATE TABLE observations (source TEXT NOT NULL, position INTEGER NOT NULL, '
            'interval_id INTEGER NOT NULL REFERENCES intervals(id), '
            'PRIMARY KEY (source, position)) WITHOUT ROWID'
        )
        db.execute('CREATE INDEX observations_by_interval ON observations(interval_id)')
        db.execute('CREATE TABLE IF NOT EXISTS import_errors (warning TEXT NOT NULL)')
        if version:
            db.execute('INSERT INTO intervals(username, start, end) '
                       'SELECT DISTINCT username, start, end FROM old_intervals')
            db.execute(
                'INSERT INTO observations '
                'SELECT old.source, old.position, intervals.id FROM old_intervals AS old '
                'JOIN intervals USING (username, start, end)'
            )
            db.execute('DROP TABLE old_intervals')
        for event, condition in (
            ('DELETE', ''),
            ('UPDATE OF interval_id', 'WHEN OLD.interval_id <> NEW.interval_id'),
        ):
            db.execute(
                f'CREATE TRIGGER prune_interval_after_{event.split()[0].lower()} '
                f'AFTER {event} ON observations {condition} BEGIN '
                'DELETE FROM intervals WHERE id = OLD.interval_id AND NOT EXISTS '
                '(SELECT 1 FROM observations WHERE interval_id = OLD.interval_id); END'
            )
        # Version 3 still needs JSON cleanup. Version 4 has already completed it.
        db.execute('PRAGMA user_version = 4' if version == 2 else 'PRAGMA user_version = 3')


def import_json_history(db, directory):
    imported, warnings = {}, []
    db.execute('DELETE FROM import_errors')
    for path in sorted(directory.glob('*.json')):
        try:
            username, rows, digest = json_history(path)
        except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
            warnings.append(f'History file {path.name} was not imported: {error}')
            continue

        source = f'json:{path.name}'
        db.execute('DELETE FROM observations WHERE source = ?', (source,))
        for index, (start, end) in enumerate(rows):
            store_observation(db, source, index, username, start, end)
        verified = db.execute(
            'SELECT start, end FROM observations JOIN intervals ON intervals.id = interval_id '
            'WHERE source = ? AND username = ? ORDER BY position',
            (source, username),
        ).fetchall()
        if verified != rows:
            raise sqlite3.DatabaseError(f'Could not verify imported history from {path.name}')
        imported[path] = digest

    db.executemany('INSERT INTO import_errors VALUES (?)', ((warning,) for warning in warnings))
    return imported, warnings


@contextmanager
def history_database(directory, *, auto_migrate=True):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Keep rollback journaling for workstations using the same home mount.
    with closing(sqlite3.connect(directory / 'locks.sqlite3', timeout=5)) as db:
        db.execute('PRAGMA foreign_keys = ON')
        if db.execute('PRAGMA user_version').fetchone()[0] < 3:
            upgrade_history_schema(db)
        if auto_migrate and db.execute('PRAGMA user_version').fetchone()[0] < 4:
            import_and_cleanup(db, directory, delete_imported=True, automatic=True)
        yield db


def import_and_cleanup(db, directory, *, delete_imported, automatic=False):
    with db:
        db.execute('BEGIN IMMEDIATE')
        if automatic and db.execute('PRAGMA user_version').fetchone()[0] >= 4:
            return 0, 0, []
        imported, warnings = import_json_history(db, directory)
        if delete_imported and not warnings:
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise sqlite3.DatabaseError('History database failed its integrity check')

    # The import must commit successfully before any original can be removed.
    removed = 0
    if delete_imported and not warnings:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for path, digest in imported.items():
                try:
                    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        warnings.append(f'{path.name} changed during migration and was kept')
                        continue
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    continue  # Another migration already cleaned up this file.
                except OSError as error:
                    warnings.append(f'Could not remove {path.name}: {error}')
            if warnings:
                db.executemany('INSERT INTO import_errors VALUES (?)', ((warning,) for warning in warnings))
            else:
                db.execute('PRAGMA user_version = 4')
    return len(imported), removed, warnings


def migrate_history(directory, *, delete_imported=False):
    directory = Path(directory)
    with history_database(directory, auto_migrate=False) as db:
        return import_and_cleanup(db, directory, delete_imported=delete_imported)


class HistoryWriter:
    """Record observations from one collector in the shared database."""
    def __init__(self, directory, username, source=None):
        self.directory = Path(directory)
        self.username = username
        self.source = f'{source or source_id()}:{uuid.uuid4().hex}'
        self.intervals = {}
        self.initialized = False

    def observe(self, rows, now):
        if not number(now) or now < 0:
            raise ValueError('Invalid observation time')
        changed = {}
        for start, end in rows:
            if not number(start) or start < 0 or start > now:
                raise ValueError('Invalid lock start; check workstation clock')
            if end is not None and (not number(end) or not start <= end <= now):
                raise ValueError('Invalid lock end; check workstation clock')
            changed[start] = now if end is None else end
        changed = {start: end for start, end in changed.items() if self.intervals.get(start) != end}
        if not changed and self.initialized:
            return

        with history_database(self.directory) as db, db:
            for start, end in changed.items():
                store_observation(db, self.source, start, self.username, float(start), float(end))
        self.initialized = True
        self.intervals.update(changed)


def fetch_history(directory, username, start, end):
    try:
        with history_database(directory) as db:
            rows = db.execute(
                'SELECT start, end FROM intervals WHERE username = ? AND end > ? AND start < ? '
                'ORDER BY start, end',
                (username, start, end),
            ).fetchall()
            warnings = [warning for warning, in db.execute('SELECT warning FROM import_errors')]
        return rows, warnings
    except (OSError, sqlite3.Error) as error:
        return [], [f'Shared history database unavailable: {error}']


def legacy_history(path, username):
    """Read the previous archive without creating or writing a shared SQLite DB."""
    path = Path(path)
    if not path.exists():
        return []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        return db.execute('SELECT start,end FROM locks WHERE username=?', (username,)).fetchall()


def lock_periods(intervals, start, end):
    """Clip to the reporting day and merge duplicate/overlapping observations."""
    merged = []
    for a, b in sorted((max(a, start), min(b, end)) for a, b in intervals):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def list_locks(directory, username, state, archive, day, zone, now):
    start = datetime.combine(day, day_time(), zone).timestamp()
    end = min(now, datetime.combine(day + timedelta(days=1), day_time(), zone).timestamp())
    if start > now:
        raise ValueError('Cannot list a future day')
    rows, warnings = fetch_history(directory, username, start, end)
    try:
        rows.extend(legacy_history(archive, username))
    except (OSError, ValueError, sqlite3.Error) as error:
        warnings.append(f'Legacy history unavailable: {error}')
    open_start = None
    try:
        current = read_state(state, username)
        rows.extend((a, now if b is None else b) for a, b in current)
        open_start = next((a for a, b in reversed(current) if b is None and a <= now), None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        warnings.append(f'Current lock state unavailable: {error}')
    periods = lock_periods(rows, start, end)
    return {
        'date': day.isoformat(), 'timezone': str(zone), 'username': username,
        'periods': [{'start': datetime.fromtimestamp(a, zone).isoformat(),
                     'end': datetime.fromtimestamp(b, zone).isoformat(),
                     'seconds': b-a,
                     'ongoing': open_start is not None and a <= max(open_start, start) < b and b == now}
                    for a, b in periods],
        'locked_seconds': sum(b-a for a, b in periods),
        'history_complete': False,
        'warnings': warnings + ['Only observed lock history is available; unobserved periods may be missing.'],
    }


def format_locks(data):
    def duration(seconds):
        value = int(seconds)
        return f'{value // 3600:02d}:{value // 60 % 60:02d}:{value % 60:02d}'

    lines = [f"LOCK PERIODS · {data['date']} · {data['username']} · {data['timezone']}", '',
             'Start      End        Duration']
    for period in data['periods']:
        a, b = (datetime.fromisoformat(period[key]).strftime('%H:%M:%S') for key in ('start', 'end'))
        lines.append(f"{a}   {b}   {duration(period['seconds'])}" + ('  (ongoing)' if period['ongoing'] else ''))
    if not data['periods']:
        lines.append('No observed lock periods for this day.')
    lines.extend(['', f"Total locked: {duration(data['locked_seconds'])}"])
    lines.extend(f'Note: {warning}' for warning in data['warnings'])
    return '\n'.join(lines)


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
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('collect', 'list', 'migrate'))
    parser.add_argument('--history-dir', type=Path, default=DEFAULT_HISTORY)
    parser.add_argument('--user', default=getpass.getuser())
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--date', type=date.fromisoformat, help='Day to list (default: today)')
    parser.add_argument('--timezone', default='Africa/Casablanca')
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument('--delete-imported', action='store_true', help='After migration, remove verified JSON files. Stop old JSON-writing collectors first.')
    parser.add_argument('--json', action='store_true', help='List periods as JSON')
    args = parser.parse_args(argv)
    if args.delete_imported and args.mode != 'migrate':
        parser.error('--delete-imported requires migrate')
    try:
        if args.mode == 'migrate':
            imported, removed, warnings = migrate_history(args.history_dir, delete_imported=args.delete_imported)
            print(f'Imported {imported} JSON files into {args.history_dir / "locks.sqlite3"}. Removed {removed}.')
            for warning in warnings:
                print(warning, file=sys.stderr)
            return int(bool(warnings))
        if args.mode == 'list':
            zone = ZoneInfo(args.timezone)
            now = time.time()
            day = args.date or datetime.fromtimestamp(now, zone).date()
            data = list_locks(args.history_dir, args.user, args.state, args.archive, day, zone, now)
            print(json.dumps(data) if args.json else format_locks(data))
            return 0
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
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(2, f'Error: {error}\n')


if __name__ == '__main__':
    sys.exit(main())
