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

project, config, action, python = sys.argv[1:]
sys.path.insert(0, project)
from desktop_startup import atomic_write, autostart_path, launch_at_login, set_launch_at_login

target = autostart_path(config)
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

enabled = launch_at_login(target) if target.exists() else True
launcher = f'#!/bin/sh\nif [ "$#" -eq 0 ]; then\n  exec {shlex.quote(python)} {shlex.quote(str(Path(project) / "server_control.py"))} open --port {int(os.environ.get("PORT", "47831"))}\nfi\nexec {shlex.quote(python)} {shlex.quote(str(Path(project) / "daily.py"))} "$@"\n'
atomic_write(command, launcher, 0o755)
set_launch_at_login(target, enabled, Path(project), python, int(os.environ.get('PORT', '47831')))
print(f'Installed command: {command}')
if str(bin_dir) not in os.environ.get('PATH', '').split(os.pathsep):
    print(f'Add this directory to your shell PATH: {bin_dir}')
print(f'Installed autostart: {target}')
server_action = 'start' if os.environ.get('LOGGED_TIME_INSTALL_OPEN_BROWSER') == '0' else 'open'
subprocess.run([python, str(Path(project) / 'server_control.py'), server_action, '--port', os.environ.get('PORT', '47831')], check=True)
print(f'Keep the project at {project}. Run logged-time to view your totals.')
PY
