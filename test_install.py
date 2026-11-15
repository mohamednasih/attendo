import os
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
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                       BIN_DIR=str(binary), PATH=f'{binary}:/usr/bin:/bin')
            subprocess.run(['bash', str(PROJECT / 'install.sh')], env=env, cwd=home,
                           check=True, capture_output=True)
            for args in (['--help'], ['locks', '--help']):
                result = subprocess.run(['logged-time', *args], env=env, cwd=home,
                                        check=True, capture_output=True, text=True)
                self.assertIn('usage:', result.stdout)
            self.assertTrue((home / '.config/autostart/logged-time.desktop').is_file())
            subprocess.run(['bash', str(PROJECT / 'install.sh'), '--uninstall'],
                           env=env, cwd=home, check=True, capture_output=True)
            self.assertFalse((binary / 'logged-time').exists())
            self.assertFalse((home / '.config/autostart/logged-time.desktop').exists())


if __name__ == '__main__':
    unittest.main()
