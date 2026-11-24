from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import attendo_client as client


def page(estimate='07:35', counted=False):
    return json.dumps({'login': 'student', 'todayHoursEstimation': estimate,
                      'period': {'weekStart': '2026-11-09', 'weekEnd': '2026-11-15',
                                 'total': 2.5, 'daily': [{'date': '2026-11-14', 'hours': 2.5, 'counted': counted}]}})


class ClientTests(unittest.TestCase):
    def test_current_period_schema_preserves_recorded_hours(self):
        data = json.loads(page())
        data['period'].update(countedDays=1, breakdown={'source': 2.5}, daily=[
            {'id': 'day', 'date': '2026-11-14', 'hoursLogged': 3,
             'counted': True, 'sourceHours': 3, 'hours': 2.5},
            {'id': 'next-day', 'date': '2026-11-15', 'hoursLogged': 1,
             'counted': False},
        ])
        profile = client.parse_profile(json.dumps(data))
        self.assertEqual(profile.current.days, (
            client.Day('2026-11-14', 2.5, True),
            client.Day('2026-11-15', 1, False),
        ))

    def test_new_period_appears_at_campus_midnight_before_first_record(self):
        closed = {'weekStart': '2026-11-09', 'weekEnd': '2026-11-15', 'total': 28,
                  'daily': [{'date': f'2026-11-{day:02}', 'hours': 4, 'counted': True}
                            for day in range(9, 16)]}
        data = {'login': 'student', 'periods': [closed]}
        target = '<p>Attendance target 30h per counted-day period</p>'
        midnight = datetime(2026, 11, 16, tzinfo=ZoneInfo('Africa/Casablanca'))
        midnight = midnight.astimezone(timezone(timedelta(hours=-5)))
        before = midnight - timedelta(seconds=1)
        self.assertIsNone(client.parse_profile(target + json.dumps(data), before).current)

        profile = client.parse_profile(target + json.dumps(data), midnight)
        self.assertEqual(profile.current, client.Period('2026-11-16', '2026-11-16', 0, ()))
        self.assertEqual(profile.previous.total, 28)
        self.assertEqual(profile.previous.counted_days, 7)
        goal = client.attendance_goal(profile)
        self.assertEqual(goal['remaining_days'], 7)
        self.assertEqual(goal['daily_seconds'], 257 * 60)

        first_day = {'date': '2026-11-16', 'hours': 1.5, 'counted': True}
        data['periods'].insert(0, {'weekStart': '2026-11-16', 'weekEnd': '2026-11-16',
                                   'total': 1.5, 'daily': [first_day]})
        posted = client.parse_profile(target + json.dumps(data), midnight)
        self.assertEqual(posted.current.total, 1.5)
        self.assertEqual(posted.current.days, (client.Day('2026-11-16', 1.5, True),))
        self.assertEqual(posted.previous, profile.previous)

    def test_today_recorded_hours_are_separate_from_estimate(self):
        for value in (2, 0, 2.5, None):
            data = json.loads(page())
            data['todayHours'] = value
            profile = client.parse_profile(json.dumps(data))
            self.assertEqual(profile.today_hours, value)
            self.assertEqual(profile.live_estimate, '07:35')

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

    def test_daily_recommendation_comes_from_attendo(self):
        html = ('<div>Attendance target</div><p>30h per counted-day period</p>'
                '<p>Aim for about <strong>2h 46m</strong> per remaining counted day · 2 to go.</p>' + page())
        profile = client.parse_profile(html)
        self.assertEqual(profile.target_hours, 30)
        goal = client.attendance_goal(profile)
        self.assertEqual(goal['daily_seconds'], 166 * 60)
        self.assertEqual(goal['target_seconds'], 30 * 3600)
        self.assertEqual(goal['remaining_seconds'], 27.5 * 3600)
        self.assertEqual(goal['remaining_days'], 7)

    def test_missing_target_is_not_replaced_with_arbitrary_goal(self):
        self.assertIsNone(client.attendance_goal(client.parse_profile(page())))

    def test_daily_recommendation_without_period_target(self):
        for value, expected_minutes in [('12h', 720), ('46m', 46), ('0h 05m', 5), ('0m', 0)]:
            with self.subTest(value=value):
                html = f'<p>Aim for about {value} per remaining counted day</p>' + page()
                goal = client.attendance_goal(client.parse_profile(html))
                self.assertEqual(goal['daily_seconds'], expected_minutes * 60)
                self.assertIsNone(goal['target_seconds'])

    def test_missing_or_unreadable_recommendation_uses_remaining_counted_days(self):
        for value in ('', 'unavailable', '2h later'):
            with self.subTest(value=value):
                html = ('<p>Attendance target 30h per counted-day period</p>'
                        f'<p>Aim for about {value} per remaining counted day</p>' + page(counted=True))
                goal = client.attendance_goal(client.parse_profile(html))
                self.assertEqual(goal['daily_seconds'], 275 * 60)
                self.assertEqual(goal['target_seconds'], 30 * 3600)

    def test_zero_daily_recommendation_or_remaining_time(self):
        target = '<p>Attendance target 30h per counted-day period</p>'
        recommended = '<p>Aim for about 0m per remaining counted day</p>'
        profile = client.parse_profile(target + recommended + page())
        self.assertEqual(client.attendance_goal(profile)['daily_seconds'], 0)
        data = json.loads(page())
        data['period']['total'] = 31
        profile = client.parse_profile(target + json.dumps(data))
        self.assertEqual(client.attendance_goal(profile)['daily_seconds'], 0)

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

    def test_chromium_filters_cookie_host_name_and_expiry(self):
        def cookie(domain='attendo.1337.ma', name='student_token', value='valid', expired=False):
            return SimpleNamespace(domain=domain, name=name, value=value, expires=9999999999,
                                   is_expired=lambda: expired)
        loader = MagicMock(return_value=[cookie(domain='evilattendo.1337.ma'),
                                        cookie(name='other'), cookie(expired=True), cookie()])
        module = SimpleNamespace(chrome=loader, brave=loader)
        for browser in ('chrome', 'brave'):
            with self.subTest(browser=browser), \
                 patch.object(client, '_chromium_cookie_files', return_value=[Path('/test/Cookies')]), \
                 patch.dict('sys.modules', {'browser_cookie3': module}):
                self.assertEqual(client.chromium_student_token(browser), 'valid')
                loader.assert_called_with(cookie_file='/test/Cookies', domain_name='attendo.1337.ma')

    def test_unreadable_profile_does_not_hide_other_profiles(self):
        valid = SimpleNamespace(domain='.attendo.1337.ma', name='student_token', value='test-token',
                                expires=None, is_expired=lambda: False)
        loader = MagicMock(side_effect=[RuntimeError('keyring unavailable'), [valid]])
        with patch.object(client, '_chromium_cookie_files', return_value=[Path('/one'), Path('/two')]), \
             patch.dict('sys.modules', {'browser_cookie3': SimpleNamespace(brave=loader)}):
            self.assertEqual(client.chromium_student_token('brave'), 'test-token')

    def test_browser_selection_and_automatic_fallback(self):
        with patch.dict('os.environ', {'ATTENDO_BROWSER': 'auto'}), \
             patch.object(client, 'firefox_student_token', return_value=None), \
             patch.object(client, 'chromium_student_token', side_effect=[None, 'brave-token']) as read:
            self.assertEqual(client.browser_student_token(), 'brave-token')
            self.assertEqual([call.args[0] for call in read.call_args_list], ['chrome', 'brave'])
        with patch.dict('os.environ', {'ATTENDO_BROWSER': 'chrome'}), \
             patch.object(client, 'firefox_student_token') as firefox, \
             patch.object(client, 'chromium_student_token', return_value='chrome-token'):
            self.assertEqual(client.browser_student_token(), 'chrome-token')
            firefox.assert_not_called()

    def test_chromium_profiles_include_network_and_numbered_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            for browser, root in [('chrome', 'google-chrome'), ('brave', 'BraveSoftware/Brave-Browser')]:
                for relative in ('Default/Cookies', 'Profile 2/Network/Cookies'):
                    path = config / root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.touch()
                with patch.dict('os.environ', {'XDG_CONFIG_HOME': str(config)}), \
                     patch('attendo_client.Path.home', return_value=config):
                    self.assertEqual(len(client._chromium_cookie_files(browser)), 2)

    def test_reuses_browser_session_without_interactive_terminal(self):
        with patch.dict('os.environ', {}, clear=True), \
             patch.object(client, 'browser_student_token', return_value='test-token'), \
             patch.object(client, '_browser_cookie', None):
            client.ensure_browser_session()
            self.assertEqual(client._cookie_from_environment(), 'student_token=test-token')

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
