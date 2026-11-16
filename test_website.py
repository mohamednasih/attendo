import json
import unittest
from unittest.mock import patch

import website


class WebsiteTests(unittest.TestCase):
    def setUp(self):
        website.CACHE.clear()

    def test_attendance_failure_keeps_offline_locks(self):
        locks = {'periods': [], 'locked_seconds': 0}
        with patch.object(website, 'command_json', side_effect=[ValueError('Sign in required'), locks]):
            result = website.dashboard('2026-11-15')
        self.assertIsNone(result['report'])
        self.assertEqual(result['locks'], locks)
        self.assertEqual(result['errors'], ['Sign in required'])
        json.dumps(result, allow_nan=False)

    def test_cached_fetch_avoids_repeated_upstream_requests(self):
        with patch.object(website, 'command_json', return_value={}) as fetch:
            first = website.dashboard('2026-11-15')
            second = website.dashboard('2026-11-15')
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(first, second)

    def test_manual_refresh_bypasses_cache(self):
        with patch.object(website, 'command_json', return_value={}) as fetch:
            website.dashboard('2026-11-15')
            website.dashboard('2026-11-15', refresh=True)
        self.assertEqual(fetch.call_count, 4)

    def test_cache_is_bounded(self):
        with patch.object(website, 'command_json', return_value={}):
            for day in range(40):
                website.dashboard(str(day))
        self.assertEqual(len(website.CACHE), 32)


if __name__ == '__main__':
    unittest.main()
