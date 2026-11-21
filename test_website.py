from email.message import Message
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import website


class WebsiteTests(unittest.TestCase):
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

    def test_manual_refresh_bypasses_cache(self):
        with patch.object(website, 'command_json', return_value={}) as fetch:
            website.dashboard('2026-11-15')
            website.dashboard('2026-11-15', refresh=True)
        self.assertEqual(fetch.call_count, 4)

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

    def test_responses_disable_browser_caching(self):
        with patch.object(website.Handler, 'send_header') as header, \
             patch.object(website.SimpleHTTPRequestHandler, 'end_headers'):
            website.Handler.end_headers(website.Handler.__new__(website.Handler))
        header.assert_any_call('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')


if __name__ == '__main__':
    unittest.main()
