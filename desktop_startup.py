"""Manage xattendo's user-level desktop login entry."""
import configparser
import os
from pathlib import Path
import tempfile


def autostart_path(config_dir=None):
    config = config_dir or os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config'
    return Path(config) / 'autostart' / 'logged-time.desktop'


def read_autostart_entry(path):
    entry = configparser.ConfigParser(interpolation=None)
    try:
        with path.open() as stream:
            entry.read_file(stream)
    except FileNotFoundError:
        pass
    return entry


def launch_at_login(path):
    entry = read_autostart_entry(path)
    if entry.has_option('Desktop Entry', 'X-Logged-Time-Open-Browser'):
        return entry.getboolean('Desktop Entry', 'X-Logged-Time-Open-Browser')
    # Older entries used the desktop autostart flags for the browser preference.
    return (
        entry.has_section('Desktop Entry')
        and not entry.getboolean('Desktop Entry', 'Hidden', fallback=False)
        and entry.getboolean('Desktop Entry', 'X-GNOME-Autostart-enabled', fallback=True)
    )


def desktop_arg(value):
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('Installation paths cannot contain newlines or NUL')
    # Escape Exec arguments, then escape backslashes for the desktop entry.
    escaped = ''.join('\\' + c if c in '\\"\x60$' else c for c in value)
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'


def atomic_write(path, text, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def set_launch_at_login(path, enabled, project, python, port, *, preserve_command=False):
    command = read_autostart_entry(path).get('Desktop Entry', 'Exec', fallback=None) if preserve_command else None
    if not command:
        browser = os.environ.get('ATTENDO_BROWSER', 'auto').strip().lower()
        if browser not in ('auto', 'firefox', 'chrome', 'brave'):
            raise ValueError('ATTENDO_BROWSER must be auto, firefox, chrome, or brave.')
        arguments = ' '.join(map(desktop_arg, (
            '/usr/bin/env', f'ATTENDO_BROWSER={browser}', python, str(project / 'server_control.py')
        )))
        command = f'{arguments} login --port {port}'
    entry = '\n'.join([
        '[Desktop Entry]',
        'Type=Application',
        'Name=xattendo',
        'Comment=Start the local attendance dashboard and lock collector',
        f'Exec={command}',
        'Terminal=false',
        'Hidden=false',
        'X-GNOME-Autostart-enabled=true',
        f'X-Logged-Time-Open-Browser={str(enabled).lower()}',
        '',
    ])
    atomic_write(path, entry, 0o644)
