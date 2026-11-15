import tempfile
from pathlib import Path
import unittest
from logged_time import locked_seconds, snapshot, timestamp


class AccountingTests(unittest.TestCase):
    def test_clipping_merging_and_open_lock(self):
        self.assertEqual(locked_seconds([(0, 20), (15, 30), (40, None)], [(10, 25), (45, 60)], 50), 20)

    def test_archive_deduplicates_and_finishes_open_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'locks.db'
            snapshot(path, 'user', [(10, None)], 20)
            snapshot(path, 'user', [(10, None)], 25)
            self.assertEqual(snapshot(path, 'user', [(10, 27)], 30), [(10, 27)])
            self.assertEqual(snapshot(path, 'other', [], 30), [])
            self.assertEqual(snapshot(path, 'user', [], 40), [(10, 27)])

    def test_timezone(self):
        self.assertEqual(timestamp('2026-11-14T01:00:00+01:00'), timestamp('2026-11-14T00:00:00Z'))
        with self.assertRaises(ValueError):
            timestamp('2026-11-14T00:00:00')


if __name__ == '__main__':
    unittest.main()
