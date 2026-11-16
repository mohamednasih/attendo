import os
import json
import socket
import urllib.request
from pathlib import Path
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parent


class InstallTests(unittest.TestCase):
    def test_command_runs_from_another_directory_and_uninstalls(self):
        with tempfile.TemporaryDirectory(prefix='logged time ') as directory:
            home = Path(directory)
            binary = home / '.local/bin'
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            env = dict(os.environ, PORT=str(port), XDG_STATE_HOME=str(home / '.local/state'), HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
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
            self.assertTrue((home / '.config/autostart/logged-time.desktop').is_file())
            subprocess.run(['bash', str(PROJECT / 'install.sh'), '--uninstall'],
                           env=env, cwd=home, check=True, capture_output=True)
            self.assertFalse((runtime / 'server.json').exists())
            self.assertFalse((runtime / 'server.log').exists())
            self.assertFalse(Path(f"/proc/{record['pid']}/cmdline").read_bytes() if Path(f"/proc/{record['pid']}/cmdline").exists() else b'')
            self.assertFalse((binary / 'logged-time').exists())
            self.assertFalse((home / '.config/autostart/logged-time.desktop').exists())


if __name__ == '__main__':
    unittest.main()
