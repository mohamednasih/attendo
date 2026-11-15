from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

import attendo_client as client


def page(estimate='07:35', counted=False):
    return json.dumps({'login': 'student', 'todayHoursEstimation': estimate,
                      'period': {'weekStart': '2026-11-09', 'weekEnd': '2026-11-15',
                                 'total': 2.5, 'daily': [{'date': '2026-11-14', 'hours': 2.5, 'counted': counted}]}})


class ClientTests(unittest.TestCase):
    def test_plain_and_streamed_profile_preserve_minutes(self):
        plain = page()
        split = len(plain) // 2
        streamed = ''.join('<script>self.__next_f.push(' + json.dumps([1, chunk]) + ')</script>'
                           for chunk in (plain[:split], plain[split:]))
        now = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        for html in (plain, streamed):
            profile = client.parse_profile(html, now)
            self.assertEqual(profile.login, 'student')
            self.assertEqual(profile.live_estimate, '07:35')
            self.assertEqual(profile.current.days[0].hours, 2.5)
            self.assertEqual(profile.fetched_at, now)

    def test_missing_live_estimate_stays_missing(self):
        self.assertIsNone(client.parse_profile(page(None)).live_estimate)

    def test_login_page_or_missing_identity_is_rejected(self):
        for html in ('<html>Sign in</html>', page().replace('"login": "student",', '')):
            with self.assertRaises(client.AttendoError):
                client.parse_profile(html)

    def test_environment_session_requires_no_browser(self):
        with patch.dict('os.environ', {'ATTENDO_TOKEN': 'test-secret', 'ATTENDO_LOGIN': 'student'}, clear=True), \
             patch.object(client, '_browser_cookie', None), \
             patch.object(client, 'firefox_student_token') as browser:
            client.ensure_browser_session()
            browser.assert_not_called()
            self.assertEqual(client._cookie_from_environment(), 'student_token=test-secret; student_login=student')

    def test_reads_only_unexpired_attendo_firefox_cookie(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / 'test.default'
            profile.mkdir()
            with sqlite3.connect(profile / 'cookies.sqlite') as db:
                db.execute('CREATE TABLE moz_cookies (name TEXT,host TEXT,value TEXT,expiry INTEGER)')
                db.executemany('INSERT INTO moz_cookies VALUES (?,?,?,?)', [
                    ('student_token', 'other.example', 'wrong-host', 9999999999),
                    ('student_token', 'attendo.1337.ma', 'expired', 1),
                    ('student_token', 'attendo.1337.ma', 'valid-test-token', 9999999998)])
            self.assertEqual(client.firefox_student_token((root,)), 'valid-test-token')

    def test_fetch_uses_local_client_and_handles_auth_errors(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = page().encode()
        with patch.object(client, '_cookie_from_environment', return_value='student_token=test'), \
             patch('attendo_client.urllib.request.build_opener', return_value=opener):
            self.assertEqual(client.fetch_profile().login, 'student')
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, client.DEFAULT_URL)
            self.assertEqual(request.get_header('User-agent'), 'logged-time/1.0')
            opener.open.side_effect = urllib.error.HTTPError(client.DEFAULT_URL, 401, 'Unauthorized', None, None)
            with self.assertRaises(client.AttendoError):
                client.fetch_profile()

    def test_redirect_never_forwards_cookie(self):
        with self.assertRaises(client.AttendoError):
            client._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example')


if __name__ == '__main__':
    unittest.main()
