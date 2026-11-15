import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from logged_time import locked_seconds, snapshot
from shared_history import HistoryWriter, HistoryCollector, fetch_history, legacy_history


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

    def test_collectors_on_same_machine_have_independent_files(self):
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        b = HistoryWriter(self.directory, 'student', 'machine-a')
        a.observe([(10, 20)], 30)
        b.observe([(10, 20)], 30)
        self.assertNotEqual(a.path, b.path)
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(len(rows), 2)
        self.assertEqual(locked_seconds(rows, [(0, 100)], 100), 10)

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

    def test_failed_atomic_replace_preserves_previous_file(self):
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        a.observe([(10, None)], 20)
        with patch('shared_history.os.replace', side_effect=OSError('mount unavailable')):
            with self.assertRaises(OSError):
                a.observe([(10, None)], 30)
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 20)])
        self.assertEqual(list(self.directory.glob('*.tmp')), [])
        a.observe([(10, None)], 40)
        rows, _ = fetch_history(self.directory, 'student', 0, 100)
        self.assertEqual(rows, [(10, 40)])

    def test_corrupt_file_warns_without_losing_other_machines(self):
        a = HistoryWriter(self.directory, 'student', 'machine-a')
        a.observe([(10, 20)], 30)
        (self.directory / 'broken.json').write_text('{')
        (self.directory / 'interrupted.tmp').write_text('{')
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


if __name__ == '__main__':
    unittest.main()
