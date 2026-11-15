#!/usr/bin/env bash
# Install the collector for desktop login; no root access required.
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
import sys
import tempfile

project, config, action, python = sys.argv[1:]
target = Path(config) / 'autostart/logged-time.desktop'
if action == '--uninstall':
    target.unlink(missing_ok=True)
    print(f'Removed autostart: {target}')
    print('Existing history is preserved. Any running collector stops at logout.')
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
    'Name=Logged Time Collector',
    'Comment=Save workstation lock history to your shared home',
    f'Exec={desktop_arg(python)} {desktop_arg(str(collector))} collect',
    'Terminal=false',
    'X-GNOME-Autostart-enabled=true',
    '',
])
target.parent.mkdir(parents=True, exist_ok=True)
temporary = None
try:
    with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(entry)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, target)
finally:
    if temporary is not None:
        temporary.unlink(missing_ok=True)
print(f'Installed autostart: {target}')
print('Collection starts at your next desktop login, on machines mounting this home.')
print(f'Keep the project at {project}. Run daily.py to view your totals.')
PY
