import os
import json
import socket
import urllib.request
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import server_control
from desktop_startup import set_launch_at_login


PROJECT = Path(__file__).resolve().parent


class InstallTests(unittest.TestCase):
    def test_login_starts_server_with_browser_enabled_or_disabled(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(server_control, 'manage', return_value=47832) as manage, \
             patch.object(server_control, 'wait_for_server') as wait_for_server, \
             patch.object(server_control.webbrowser, 'open') as open_browser, \
             patch.object(server_control.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 1)), \
             patch.object(server_control.sys, 'argv', ['server_control.py', 'login']):
            entry = Path(directory) / 'logged-time.desktop'
            with patch.object(server_control, 'autostart_path', return_value=entry):
                for enabled in (False, True):
                    with self.subTest(enabled=enabled):
                        set_launch_at_login(entry, enabled, PROJECT, '/usr/bin/python3', 47831)
                        manage.reset_mock()
                        open_browser.reset_mock()
                        wait_for_server.reset_mock()
                        server_control.main()
                        manage.assert_called_once_with('start', 47831)
                        wait_for_server.assert_called_once_with()
                        if enabled:
                            open_browser.assert_called_once_with('http://localhost:47832/')
                        else:
                            open_browser.assert_not_called()

    def test_command_runs_from_another_directory_and_uninstalls(self):
        with tempfile.TemporaryDirectory(prefix='logged time ') as directory:
            home = Path(directory)
            binary = home / '.local/bin'
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            env = dict(os.environ, BROWSER='/bin/true', PORT=str(port), XDG_STATE_HOME=str(home / '.local/state'), HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                       BIN_DIR=str(binary), PATH=f'{binary}:/usr/bin:/bin')
            subprocess.run(['bash', str(PROJECT / 'install.sh')], env=env, cwd=home,
                           check=True, capture_output=True)
            runtime = home / '.local/state/logged-time-web'
            record = json.loads((runtime / 'server.json').read_text())
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health') as response:
                self.assertEqual(json.load(response)['pid'], record['pid'])
            subprocess.run(['bash', str(PROJECT / 'install.sh')], env=env, cwd=home,
                           check=True, capture_output=True)
            self.assertEqual(json.loads((runtime / 'server.json').read_text())['pid'], record['pid'])
            for args in (['--help'], ['locks', '--help']):
                result = subprocess.run(['logged-time', *args], env=env, cwd=home,
                                        check=True, capture_output=True, text=True)
                self.assertIn('usage:', result.stdout)
            self.assertIn('login --port', (home / '.config/autostart/logged-time.desktop').read_text())
            self.assertIn('server_control.py', (binary / 'logged-time').read_text())
            subprocess.run(['/usr/bin/python3', str(PROJECT / 'server_control.py'), 'stop'],
                           env=env, check=True, capture_output=True)
            entry = home / '.config/autostart/logged-time.desktop'
            set_launch_at_login(entry, False, PROJECT, '/usr/bin/python3', port)
            git = binary / 'git'
            git.write_text('#!/bin/sh\nexit 1\n')
            git.chmod(0o755)
            with subprocess.Popen(
                ['/usr/bin/python3', str(PROJECT / 'server_control.py'), 'login', '--port', str(port)],
                env=env, cwd=home, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ) as login:
                try:
                    for _ in range(50):
                        if (runtime / 'server.json').exists():
                            break
                        time.sleep(.1)
                    with self.assertRaises(subprocess.TimeoutExpired):
                        login.wait(timeout=1)
                    record = json.loads((runtime / 'server.json').read_text())
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health') as response:
                        self.assertEqual(json.load(response)['pid'], record['pid'])
                    subprocess.run(['bash', str(PROJECT / 'install.sh'), '--uninstall'],
                                   env=env, cwd=home, check=True, capture_output=True)
                    self.assertEqual(login.wait(timeout=5), 0)
                finally:
                    if login.poll() is None:
                        login.terminate()
            self.assertFalse((runtime / 'server.json').exists())
            self.assertFalse((runtime / 'server.log').exists())
            self.assertFalse(Path(f"/proc/{record['pid']}/cmdline").read_bytes() if Path(f"/proc/{record['pid']}/cmdline").exists() else b'')
            self.assertFalse((binary / 'logged-time').exists())
            self.assertFalse((home / '.config/autostart/logged-time.desktop').exists())


if __name__ == '__main__':
    unittest.main()
