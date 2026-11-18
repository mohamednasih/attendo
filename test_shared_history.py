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
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        b = HistoryWriter(self.directory, 'student', 'machine-b')
        a.observe([(10, 30)], 40)
        b.observe([(20, 40)], 50)
        rows, warnings = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(warnings, [])
        self.assertEqual(locked_seconds(rows, [(0, 100)], 100), 30)

    def test_collectors_share_exact_intervals_without_losing_independent_observations(self):
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        b = HistoryWriter(self.directory, 'student', 'machine-a')
        a.observe([(10, 20)], 30)
        b.observe([(10, 20)], 30)
        self.assertNotEqual(a.source, b.source)
        self.assertEqual([path.name for path in self.directory.iterdir()], ['locks.sqlite3'])
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 20)])
        self.assertEqual(locked_seconds(rows, [(0, 100)], 100), 10)
        a.observe([(10, 25)], 30)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 20), (10, 25)], []))
        b.observe([(10, 25)], 30)
        self.assertEqual(fetch_history(self.directory, 'student', 0, 100), ([(10, 25)], []))

    def test_restart_preserves_old_history_and_bounds_open_lock(self):
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        a.observe([(10, None)], 20)
        a.observe([], 50)
        b = HistoryWriter(self.directory, 'student', 'machine-b')
        b.observe([(60, 70)], 80)
        rows, _ = fetch_history(self.directory, 'student', 0, 200)
        self.assertEqual(sorted(rows), [(10, 20), (60, 70)])

    def test_open_lock_updates_then_closes(self):
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        a.observe([(10, None)], 20)
        a.observe([(10, None)], 25)
        a.observe([(10, 27)], 30)
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 27)])

    def test_failed_write_preserves_previous_observation(self):
        a = HistoryWriter(self.directory, 'student', 'machine-a')
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
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        a.observe([(10, 20)], 30)
        (self.directory / 'broken.json').write_text('{')
        (self.directory / 'interrupted.tmp').write_text('{')
        self.assertEqual(migrate_history(self.directory, delete_imported=True)[:2], (0, 0))
        rows, warnings = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 20)])
        self.assertEqual(len(warnings), 1)
        self.assertIn('broken.json', warnings[0])

    def test_other_users_and_invalid_times(self):
        a = HistoryWriter(self.directory, 'other', 'machine-a')
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
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone(), (3,))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_old_archive_is_read_without_creation(self):
        path = Path(self.folder.name) / 'old.sqlite3'
        self.assertEqual(legacy_history(path, 'student'), [])
        self.assertFalse(path.exists())
        snapshot(path, 'student', [(10, 20)], 30)
        self.assertEqual(legacy_history(path, 'student'), [(10, 20)])

    def test_collector_records_failure_and_recovers(self):
        with patch('shared_history.source_id', return_value='machine-a'):
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
        writer = HistoryWriter(self.directory, 'student', 'machine-a')
        writer.observe([(start-60, start+60), (start+30, start+90)], now)
        with patch('shared_history.read_state', return_value=[(now-30, None)]), \
             patch('shared_history.legacy_history', return_value=[(start+60, start+120)]):
            data = list_locks(self.directory, 'student', Path('unused'), Path('unused'), day, zone, now)
        self.assertEqual(data['locked_seconds'], 150)
        self.assertEqual(len(data['periods']), 2)
        self.assertEqual(data['periods'][0]['seconds'], 120)
        self.assertFalse(data['periods'][0]['ongoing'])
        self.assertTrue(data['periods'][1]['ongoing'])
        self.assertIn('00:02:30', format_locks(data))

    def test_list_missing_current_state_keeps_saved_periods(self):
        zone = ZoneInfo('Africa/Casablanca')
        day = date(2026, 11, 15)
        start = datetime(2026, 11, 15, tzinfo=zone).timestamp()
        writer = HistoryWriter(self.directory, 'student', 'machine-a')
        writer.observe([(start+10, start+20)], start+60)
        with patch('shared_history.read_state', side_effect=OSError('unavailable')), \
             patch('shared_history.legacy_history', return_value=[]):
            data = list_locks(self.directory, 'student', Path('unused'), Path('unused'), day, zone, start+100)
        self.assertEqual(data['locked_seconds'], 10)
        self.assertTrue(any('Current lock state unavailable' in w for w in data['warnings']))

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
