#!/usr/bin/env bash
# Install the PATH command and desktop-login collector; no root access required.
set -euo pipefail

case "${1:-}" in
  ''|--uninstall) ;;
  -h|--help)
    printf 'Usage: %s [--uninstall]\n' "$0"
    exit 0
    ;;
  *) printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
esac
if (( $# > 1 )); then
  printf 'Expected at most one argument.\n' >&2
  exit 2
fi

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
python_bin=/usr/bin/python3
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi

"$python_bin" - "$project_dir" "$config_dir" "${1:-}" "$python_bin" <<'PY'
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

project, config, action, python = sys.argv[1:]
target = Path(config) / 'autostart/logged-time.desktop'
bin_dir = Path(os.environ.get('BIN_DIR') or Path.home() / '.local/bin').expanduser().absolute()
command = bin_dir / 'logged-time'
browser = os.environ.get('ATTENDO_BROWSER', 'auto').strip().lower()
if browser not in ('auto', 'firefox', 'chrome', 'brave'):
    sys.exit('ATTENDO_BROWSER must be auto, firefox, chrome, or brave.')
if action == '--uninstall':
    subprocess.run([python, str(Path(project) / 'server_control.py'), 'stop'], check=True)
    target.unlink(missing_ok=True)
    command.unlink(missing_ok=True)
    print(f'Removed autostart: {target}')
    print(f'Removed command: {command}')
    print('Uninstalled. Existing lock history is preserved.')
    sys.exit(0)

collector = Path(project) / 'shared_history.py'
if not collector.is_file():
    sys.exit(f'Collector not found: {collector}')

def desktop_arg(value):
    if any(c in value for c in '\n\r\x00'):
        raise ValueError('Installation paths cannot contain newlines or NUL')
    # Escape Exec arguments, then escape backslashes for the desktop entry.
    escaped = ''.join('\\' + c if c in '\\"`$' else c for c in value)
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'

entry = '\n'.join([
    '[Desktop Entry]',
    'Type=Application',
    'Name=Logged Time',
    'Comment=Start the local attendance dashboard and lock collector',
    f'Exec={desktop_arg("/usr/bin/env")} {desktop_arg("ATTENDO_BROWSER=" + browser)} {desktop_arg(python)} {desktop_arg(str(Path(project) / "server_control.py"))} login --port {int(os.environ.get("PORT", "47831"))}',
    'Terminal=false',
    'X-GNOME-Autostart-enabled=true',
    '',
])
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

launcher = f'#!/bin/sh\nif [ "$#" -eq 0 ]; then\n  exec {shlex.quote(python)} {shlex.quote(str(Path(project) / "server_control.py"))} open --port {int(os.environ.get("PORT", "47831"))}\nfi\nexec {shlex.quote(python)} {shlex.quote(str(Path(project) / "daily.py"))} "$@"\n'
atomic_write(command, launcher, 0o755)
atomic_write(target, entry, 0o644)
print(f'Installed command: {command}')
if str(bin_dir) not in os.environ.get('PATH', '').split(os.pathsep):
    print(f'Add this directory to your shell PATH: {bin_dir}')
print(f'Installed autostart: {target}')
subprocess.run([python, str(Path(project) / 'server_control.py'), 'open', '--port', os.environ.get('PORT', '47831')], check=True)
print(f'Keep the project at {project}. Run logged-time to view your totals.')
PY
