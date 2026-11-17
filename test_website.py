import json
import unittest
from unittest.mock import patch

import website


class WebsiteTests(unittest.TestCase):
    def test_attendance_failure_keeps_offline_locks(self):
        locks = {'periods': [], 'locked_seconds': 0}
        with patch.object(website, 'command_json', side_effect=[ValueError('Sign in required'), locks]):
            result = website.dashboard('2026-11-15')
        self.assertIsNone(result['report'])
        self.assertEqual(result['locks'], locks)
        self.assertEqual(result['errors'], ['Sign in required'])
        json.dumps(result, allow_nan=False)

    def test_each_request_fetches_fresh_data(self):
        with patch.object(website, 'command_json', return_value={}) as fetch:
            first = website.dashboard('2026-11-15')
            second = website.dashboard('2026-11-15')
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(first, second)

    def test_manual_refresh_bypasses_cache(self):
        with patch.object(website, 'command_json', return_value={}) as fetch:
            website.dashboard('2026-11-15')
            website.dashboard('2026-11-15', refresh=True)
        self.assertEqual(fetch.call_count, 4)

    def test_responses_disable_browser_caching(self):
        with patch.object(website.Handler, 'send_header') as header, \
             patch.object(website.SimpleHTTPRequestHandler, 'end_headers'):
            website.Handler.end_headers(website.Handler.__new__(website.Handler))
        header.assert_any_call('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')


if __name__ == '__main__':
    unittest.main()
