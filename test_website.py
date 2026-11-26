from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import website


class WebsiteTests(unittest.TestCase):
    def test_clusters_endpoint_handles_feed_and_session_failures(self):
        handler = website.Handler.__new__(website.Handler)
        handler.path = '/api/clusters'
        handler.headers = {}
        handler.send_json = Mock()
        data = {'stations': [], 'fetched_at': '2026-11-24T09:00:00Z'}
        with patch.object(website.intra_client, 'fetch_clusters', return_value=data):
            handler.do_GET()
            handler.send_json.assert_called_with(data)
        for error, status in ((website.intra_client.IntraSessionError('Sign in'), 401),
                              (ValueError('Unable to reach Intra'), 502)):
            with self.subTest(status=status), patch.object(website.intra_client, 'fetch_clusters', side_effect=error):
                handler.do_GET()
                handler.send_json.assert_called_with({'error': str(error)}, status)

    def test_session_endpoint_requires_both_logins(self):
        handler = website.Handler.__new__(website.Handler)
        handler.path = '/api/session'
        handler.headers = {}
        handler.send_json = Mock()
        for attendo in (True, False):
            for intra in (True, False):
                profile = SimpleNamespace(login='student') if attendo else None
                rejection = None if intra else website.intra_client.IntraSessionError()
                with self.subTest(attendo=attendo, intra=intra), \
                     patch.dict('os.environ', {'INTRA_LOGIN': ''}), \
                     patch.object(website.getpass, 'getuser', return_value='workstation-user'), \
                     patch.object(website.attendo_client, 'session_profile', return_value=profile), \
                     patch.object(website.intra_client, 'fetch_locations', side_effect=rejection) as locations:
                    handler.do_GET()
                    locations.assert_called_once_with('student' if attendo else 'workstation-user')
                    handler.send_json.assert_called_with(
                        {'attendo': attendo, 'intra': intra, 'active': attendo and intra, 'errors': []}, 200)

    def test_session_check_distinguishes_unavailable_services_from_missing_logins(self):
        with patch.dict('os.environ', {'INTRA_LOGIN': 'configured-user'}), \
             patch.object(website.attendo_client, 'session_profile',
                          side_effect=website.attendo_client.AttendoError('Could not reach Attendo.')), \
             patch.object(website.intra_client, 'fetch_locations', return_value=({}, None)) as locations:
            sessions = website.session_status()
            locations.assert_called_once_with('configured-user')
        self.assertEqual(sessions, {'attendo': None, 'intra': True, 'active': None,
                                    'errors': ['Could not reach Attendo.']})

        with patch.object(website.attendo_client, 'session_profile', return_value=None), \
             patch.object(website.intra_client, 'fetch_locations', side_effect=ValueError('42 Intra is offline.')):
            sessions = website.session_status()
        self.assertEqual(sessions, {'attendo': False, 'intra': None, 'active': False,
                                    'errors': ['42 Intra is offline.']})

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
        fetch.assert_any_call('daily.py', ['--intra', '--once', '--no-collect', '--date', '2026-11-15'])
        self.assertEqual(first, second)

    def test_dashboard_uses_both_attendo_periods_even_when_viewing_another_day(self):
        report = {'attendance_periods': [
            {'label': 'Previous period', 'start': '2026-11-03', 'end': '2026-11-11'},
            {'label': 'Current period', 'start': '2026-11-12', 'end': '2026-11-21'},
        ]}
        locks = {'period_days': [{'date': '2026-11-12', 'locked_seconds': 42}]}
        with patch.object(website, 'command_json', side_effect=[report, locks]) as fetch:
            result = website.dashboard('2026-11-11')
        fetch.assert_called_with('shared_history.py', ['list', '--json', '--date', '2026-11-11',
                                                       '--previous-period', '2026-11-03', '2026-11-11',
                                                       '--period', '2026-11-12', '2026-11-21'])
        self.assertEqual(result['locks'], locks)

    def test_manual_refresh_bypasses_cache(self):
        with patch.object(website, 'command_json', return_value={}) as fetch:
            website.dashboard('2026-11-15')
            website.dashboard('2026-11-15', refresh=True)
        self.assertEqual(fetch.call_count, 4)

    def test_dashboard_reuses_previous_lock_totals_from_report(self):
        previous_days = [{'date': '2026-11-11', 'locked_seconds': 120}]
        report = {'previous_period_days': previous_days, 'attendance_periods': [
            {'label': 'Previous period', 'start': '2026-11-03', 'end': '2026-11-11'},
            {'label': 'Current period', 'start': '2026-11-12', 'end': '2026-11-21'},
        ]}
        with patch.object(website, 'command_json', side_effect=[report, {}]) as fetch:
            result = website.dashboard('2026-11-15')
        fetch.assert_called_with('shared_history.py', ['list', '--json', '--date', '2026-11-15',
                                                       '--period', '2026-11-12', '2026-11-21'])
        self.assertEqual(result['locks']['previous_period_days'], previous_days)

    def test_different_days_can_load_concurrently(self):
        reports_started = Barrier(2, timeout=5)

        def fetch(script, args):
            if script == 'daily.py':
                reports_started.wait()
            return {'date': args[-1]}

        days = ['2026-11-14', '2026-11-15']
        with patch.object(website, 'command_json', side_effect=fetch), ThreadPoolExecutor(2) as pool:
            results = list(pool.map(website.dashboard, days))
        for day, result in zip(days, results):
            self.assertEqual(result, {'date': day, 'report': {'date': day}, 'locks': {'date': day}, 'errors': []})

    def test_startup_setting_toggles_persistent_desktop_entry(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'autostart' / 'logged-time.desktop'
            handler = website.Handler.__new__(website.Handler)
            handler.path = '/api/settings'
            handler.server = SimpleNamespace(server_port=47831)
            handler.send_json = Mock()
            handler.headers = Message()
            with patch.object(website, 'autostart_path', return_value=path):
                handler.do_GET()
                handler.send_json.assert_called_with({'launch_at_login': False})
                entry_command = None
                for enabled in (True, False):
                    body = json.dumps({'launch_at_login': enabled}).encode()
                    handler.headers = Message()
                    handler.headers['Origin'] = 'http://localhost:47831'
                    handler.headers['Content-Type'] = 'application/json'
                    handler.headers['Content-Length'] = str(len(body))
                    handler.rfile = BytesIO(body)
                    handler.do_POST()
                    handler.send_json.assert_called_with({'launch_at_login': enabled})
                    self.assertEqual(website.launch_at_login(path), enabled)
                    command = next(line for line in path.read_text().splitlines() if line.startswith('Exec='))
                    if entry_command is None:
                        entry_command = command.replace('--port 47831', '--port 47832')
                        path.write_text(path.read_text().replace(command, entry_command))
                    else:
                        self.assertEqual(command, entry_command)
                self.assertIn('Hidden=false', path.read_text())
                self.assertIn('X-GNOME-Autostart-enabled=true', path.read_text())

    def test_startup_setting_rejects_cross_origin_updates(self):
        handler = website.Handler.__new__(website.Handler)
        handler.path = '/api/settings'
        handler.server = SimpleNamespace(server_port=47831)
        handler.headers = {'Origin': 'https://example.org'}
        handler.send_json = Mock()
        with patch.object(website, 'set_launch_at_login') as update:
            handler.do_POST()
        update.assert_not_called()
        self.assertEqual(handler.send_json.call_args.args[1], 403)

    def test_responses_disable_browser_caching_by_default(self):
        with patch.object(website.Handler, 'send_header') as header, \
             patch.object(website.SimpleHTTPRequestHandler, 'end_headers'):
            website.Handler.end_headers(website.Handler.__new__(website.Handler))
        header.assert_any_call('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')

    def test_only_successful_portraits_allow_browser_caching(self):
        for portrait in (('image/png', b'photo'), None):
            with self.subTest(available=bool(portrait)), \
                 patch.object(website.intra_client, 'fetch_cluster_portrait', return_value=portrait), \
                 patch.object(website.Handler, 'log_message'):
                connection = Mock()
                connection.makefile.return_value = BytesIO(b'GET /api/cluster-portraits/example HTTP/1.0\r\n\r\n')
                response = BytesIO()
                connection.sendall.side_effect = response.write
                website.Handler(connection, ('127.0.0.1', 1234), None)
                headers, body = response.getvalue().split(b'\r\n\r\n', 1)
                self.assertEqual(headers.count(b'Cache-Control:'), 1)
                if portrait:
                    self.assertIn(b'Cache-Control: private, max-age=86400', headers)
                    self.assertNotIn(b'no-cache', headers)
                    self.assertEqual(body, b'photo')
                else:
                    self.assertIn(b'404', headers)
                    self.assertIn(b'Cache-Control: no-store', headers)


if __name__ == '__main__':
    unittest.main()
