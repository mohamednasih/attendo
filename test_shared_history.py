import json
from datetime import date, datetime
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from logged_time import locked_seconds, snapshot
from shared_history import HistoryWriter, HistoryCollector, fetch_history, migrate_history, legacy_history, list_locks, format_locks


class SharedTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name) / 'history'
        self.addCleanup(self.folder.cleanup)

    def test_multiple_machines_and_overlapping_locks(self):
        a = HistoryWriter(self.directory, 'student')
        b = HistoryWriter(self.directory, 'student')
        a.observe([(10, 30)], 40)
        b.observe([(20, 40)], 50)
        rows, warnings = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(warnings, [])
        self.assertEqual(locked_seconds(rows, [(0, 100)], 100), 30)

    def test_collectors_share_one_tracked_row_per_lock(self):
        a = HistoryWriter(self.directory, 'student')
        b = HistoryWriter(self.directory, 'student')
        a.observe([(10, 20)], 30)
        b.observe([(10, 20)], 30)
        self.assertEqual([path.name for path in self.directory.iterdir()], ['locks.sqlite3'])
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 20)])
        self.assertEqual(locked_seconds(rows, [(0, 100)], 100), 10)
        a.observe([(10, 25)], 30)
        b.observe([(10, None)], 40)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 25)], []))

    def test_restart_preserves_old_history_and_bounds_open_lock(self):
        a = HistoryWriter(self.directory, 'student')
        a.observe([(10, None)], 20)
        a.observe([], 50)
        b = HistoryWriter(self.directory, 'student')
        b.observe([(60, 70)], 80)
        rows, _ = fetch_history(self.directory, 'student', 0, 200)
        self.assertEqual(sorted(rows), [(10, 20), (60, 70)])

    def test_open_lock_updates_then_closes(self):
        a = HistoryWriter(self.directory, 'student')
        a.observe([(10, None)], 20)
        a = HistoryWriter(self.directory, 'student')
        a.observe([(10, None)], 29)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 20)], []))
        a.observe([(10, None)], 30)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 30)], []))
        a.observe([(10, 29)], 31)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 29)], []))

    def test_failed_write_preserves_previous_observation(self):
        a = HistoryWriter(self.directory, 'student')
        a.observe([(10, None)], 20)
        with patch('shared_history.history_database', side_effect=sqlite3.OperationalError('database unavailable')):
            with self.assertRaises(sqlite3.OperationalError):
                a.observe([(10, None)], 30)
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 20)])
        a.observe([(10, None)], 40)
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 40)])

    def test_corrupt_file_warns_without_losing_other_machines(self):
        self.directory.mkdir()
        (self.directory / 'broken.json').write_text('{')
        a = HistoryWriter(self.directory, 'student')
        a.observe([(10, 20)], 30)
        (self.directory / 'interrupted.tmp').write_text('{')
        self.assertEqual(migrate_history(self.directory, delete_imported=True)[:2], (0, 0))
        rows, warnings = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 20)])
        self.assertEqual(len(warnings), 1)
        self.assertIn('broken.json', warnings[0])

    def test_other_users_and_invalid_times(self):
        a = HistoryWriter(self.directory, 'other')
        a.observe([(10, 20)], 30)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([], []))
        with self.assertRaises(ValueError):
            a.observe([(10, 40)], 30)
        with self.assertRaises(ValueError):
            a.observe([(float('nan'), None)], 30)

    def test_sqlite_imports_existing_history_before_normal_reads(self):
        self.directory.mkdir()
        original = self.directory / 'old.json'
        original.write_text(json.dumps({
            'version': 1, 'username': 'student', 'observed': 80,
            'intervals': [(10, 20), (50, 60)],
        }))
        duplicate = self.directory / 'duplicate.json'
        duplicate.write_bytes(original.read_bytes())
        self.assertEqual(migrate_history(self.directory), (2, 0, []))
        self.assertTrue(original.exists())
        self.assertEqual(fetch_history(self.directory, 'student', 0, 40), ([(10, 20)], []))
        self.assertFalse(original.exists())
        self.assertFalse(duplicate.exists())
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('JSON was read after import')):
            self.assertEqual(fetch_history(self.directory, 'student', 40, 80), ([(50, 60)], []))
        self.assertEqual(migrate_history(self.directory, delete_imported=True), (0, 0, []))
        self.assertEqual(fetch_history(self.directory, 'student', 0, 80), ([(10, 20), (50, 60)], []))

    def test_migration_keeps_files_if_any_import_cannot_be_verified(self):
        self.directory.mkdir()
        valid = self.directory / 'valid.json'
        valid.write_text(json.dumps({'version': 1, 'username': 'student', 'observed': 20, 'intervals': [(10, 20)]}))
        invalid = self.directory / 'invalid.json'
        invalid.write_text('{')
        imported, removed, warnings = migrate_history(self.directory, delete_imported=True)
        self.assertEqual((imported, removed), (1, 0))
        self.assertIn('invalid.json', warnings[0])
        self.assertTrue(valid.exists())
        self.assertTrue(invalid.exists())

    def test_existing_sqlite_history_is_deduplicated_on_open(self):
        self.directory.mkdir()
        path = self.directory / 'locks.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE intervals (source TEXT, position INTEGER, '
                       'username TEXT, start REAL, end REAL)')
            db.executemany('INSERT INTO intervals VALUES (?, ?, ?, ?, ?)', [
                ('a', 10, 'student', 10, 20),
                ('b', 10, 'student', 10, 20),
                ('c', 10, 'other', 10, 20),
            ])
            db.execute('PRAGMA user_version = 2')
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 20)], []))
        self.assertEqual(fetch_history(self.directory, 'other', 0, 100), ([(10, 20)], []))
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM intervals').fetchone(), (2,))
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(),
                             [('intervals',)])

    def test_schema_upgrade_removes_observation_tables_and_preserves_intervals(self):
        self.directory.mkdir()
        path = self.directory / 'locks.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE intervals (id INTEGER PRIMARY KEY, username TEXT, start REAL, end REAL);
                CREATE TABLE observations (source TEXT, interval_id INTEGER REFERENCES intervals(id));
                CREATE TABLE import_errors (warning TEXT);
                INSERT INTO intervals VALUES (1, 'student', 10, 20), (2, 'student', 10, 22);
                INSERT INTO observations VALUES ('a', 1), ('b', 1), ('c', 2);
                PRAGMA user_version = 4;
            """)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 20), (10, 22)], []))
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(),
                             [('intervals',)])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])

    def test_old_archive_is_read_without_creation(self):
        path = Path(self.folder.name) / 'old.sqlite3'
        self.assertEqual(legacy_history(path, 'student'), [])
        self.assertFalse(path.exists())
        snapshot(path, 'student', [(10, 20)], 30)
        self.assertEqual(legacy_history(path, 'student'), [(10, 20)])

    def test_collector_records_failure_and_recovers(self):
        collector = HistoryCollector(self.directory, 'student')
        with patch('shared_history.read_state', side_effect=OSError('not mounted')):
            collector.observe()
        self.assertIn('not mounted', collector.error)
        with patch('shared_history.read_state', return_value=[(10, 20)]):
            collector.observe()
        self.assertIsNone(collector.error)
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 20)])

    def test_list_merges_sources_clips_midnight_and_marks_open_lock(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 11, 15)
        start = datetime(2026, 11, 15, tzinfo=zone).timestamp()
        now = start + 3600
        writer = HistoryWriter(self.directory, 'student')
        writer.observe([(start-60, start+60), (start+30, start+90)], now)
        with patch('shared_history.read_state', return_value=[(now-30, None)]), \
             patch('shared_history.legacy_history', return_value=[(start+60, start+120)]):
            data = list_locks(self.directory, 'student', Path('unused'), Path('unused'), day, zone, now)
        self.assertEqual(data['locked_seconds'], 150)
        self.assertEqual(len(data['periods']), 2)
        self.assertEqual(data['periods'][0]['seconds'], 120)
        self.assertFalse(data['periods'][0]['ongoing'])
        self.assertTrue(data['periods'][1]['ongoing'])
        self.assertIn('Total locked: 00h:02m', format_locks(data))

    def test_list_missing_current_state_keeps_saved_periods(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 11, 15)
        start = datetime(2026, 11, 15, tzinfo=zone).timestamp()
        writer = HistoryWriter(self.directory, 'student')
        writer.observe([(start+10, start+20)], start+60)
        with patch('shared_history.read_state', side_effect=OSError('unavailable')), \
             patch('shared_history.legacy_history', return_value=[]):
            data = list_locks(self.directory, 'student', Path('unused'), Path('unused'), day, zone, start+100)
        self.assertEqual(data['locked_seconds'], 10)
        self.assertTrue(any('Current lock state unavailable' in w for w in data['warnings']))

    def test_hourly_locks_split_an_open_lock_across_repeated_campus_hour(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 2, 15)
        first_two = datetime(2026, 2, 15, 2, tzinfo=zone, fold=0).timestamp()
        second_two = datetime(2026, 2, 15, 2, tzinfo=zone, fold=1).timestamp()
        now = second_two + 300
        with patch('shared_history.legacy_history', return_value=[(first_two + 3300, second_two)]), \
             patch('shared_history.read_state', return_value=[(first_two + 3000, None)]):
            data = list_locks(self.directory, 'student', None, None, day, zone, now)
        hours = data['hourly_locks']
        self.assertEqual(len(hours), 25)
        self.assertEqual([h['locked_seconds'] for h in hours], [0, 0, 600, 300] + [None] * 21)
        self.assertEqual(hours[2]['start'], '2026-02-15T02:00:00+01:00')
        self.assertEqual(hours[3]['start'], '2026-02-15T02:00:00+00:00')
        self.assertEqual(sum(h['locked_seconds'] or 0 for h in hours), data['locked_seconds'])

    def test_period_chart_splits_observations_at_campus_midnight(self):
        zone = ZoneInfo('Africa/Casablanca')
        midnight = datetime(2026, 11, 15, tzinfo=zone).timestamp()
        now = midnight + 1800
        writer = HistoryWriter(self.directory, 'student')
        writer.observe([(midnight - 900, midnight + 600)], now)
        with patch('shared_history.legacy_history', return_value=[(midnight - 300, midnight + 900)]), \
             patch('shared_history.read_state', return_value=[(midnight + 1200, None)]):
            data = list_locks(self.directory, 'student', None, None, date(2026, 11, 14), zone, now,
                              period=(date(2026, 11, 13), date(2026, 11, 16)))
        self.assertEqual(data['locked_seconds'], 900)
        self.assertEqual(data['period_days'], [
            {'date': '2026-11-13', 'locked_seconds': 0},
            {'date': '2026-11-14', 'locked_seconds': 900},
            {'date': '2026-11-15', 'locked_seconds': 1500},
            {'date': '2026-11-16', 'locked_seconds': None},
        ])

    def test_list_empty_and_future_day(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 11, 15)
        now = datetime(2026, 11, 15, 12, tzinfo=zone).timestamp()
        with patch('shared_history.read_state', return_value=[]), \
             patch('shared_history.legacy_history', return_value=[]):
            data = list_locks(self.directory, 'student', Path('unused'), Path('unused'), day, zone, now)
            self.assertIn('No observed lock periods', format_locks(data))
            with self.assertRaises(ValueError):
                list_locks(self.directory, 'student', Path('unused'), Path('unused'), date(2026, 11, 16), zone, now)


if __name__ == '__main__':
    unittest.main()
