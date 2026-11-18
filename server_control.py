#!/usr/bin/env python3
"""Manage this checkout's detached, loopback-only web server."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import webbrowser
import subprocess
import sys
import time
import urllib.request

from desktop_startup import autostart_path, launch_at_login

ROOT = Path(__file__).resolve().parent


def running(record):
    try:
        pid = record['pid']
        if type(pid) is not int or pid <= 1:
            return False
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return str(ROOT / 'website.py').encode() in args
    except (OSError, KeyError, TypeError):
        return False


def state_directory():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'logged-time-web'


def wait_for_server():
    # The desktop autostart service cleans up its cgroup when this launcher exits.
    state = state_directory()
    with (state / 'control.lock').open('a') as lock:
        while True:
            fcntl.flock(lock, fcntl.LOCK_SH)
            try:
                record = json.loads((state / 'server.json').read_text())
                if not running(record):
                    return
            except (FileNotFoundError, ValueError):
                return
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            time.sleep(1)


def manage(action, port=47831):
    state = state_directory()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    pidfile = state / 'server.json'
    logfile = state / 'server.log'
    with (state / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            record = json.loads(pidfile.read_text())
        except (OSError, ValueError):
            record = {}
        if action == 'stop':
            if running(record):
                os.kill(record['pid'], signal.SIGTERM)
                for _ in range(100):
                    if not running(record):
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError('Server did not stop; runtime files retained.')
                print('Stopped background web server.')
            pidfile.unlink(missing_ok=True)
            logfile.unlink(missing_ok=True)
            print('Cleaned web server runtime files. Saved lock history is preserved.')
            return
        if running(record):
            print(f"Web server already running at http://localhost:{record['port']}")
            return record['port']
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
        with logfile.open('w') as output:
            os.chmod(logfile, 0o600)
            process = subprocess.Popen([sys.executable, str(ROOT / 'website.py'), '--port', str(port)],
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                       cwd=ROOT, start_new_session=True)
        record = {'pid': process.pid, 'port': port}
        pidfile.write_text(json.dumps(record))
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f'Web server exited. Check {logfile}')
                try:
                    with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=.2) as response:
                        if json.load(response).get('pid') == process.pid:
                            print(f'Web server running in background at http://localhost:{port}')
                            print(f'Server log: {logfile}')
                            return port
                except (OSError, ValueError):
                    pass
                time.sleep(.1)
            raise RuntimeError(f'Web server did not become ready. Check {logfile}')
        except BaseException:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            pidfile.unlink(missing_ok=True)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', 'open', 'login'])
    parser.add_argument('--port', type=int, default=47831)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        if args.action == 'login':
            # Bring up the dashboard before any potentially slow network operation.
            port = manage('start', args.port)
            if launch_at_login(autostart_path()):
                webbrowser.open(f'http://localhost:{port}/')
            before = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
            try:
                update = subprocess.run([
                    'git', '-c',
                    'url.https://github.com/mohamednasih/attendo.git.insteadOf=git@github.com:mohamednasih/attendo.git',
                    'pull', '--ff-only',
                ], cwd=ROOT, timeout=120)
            except subprocess.TimeoutExpired:
                print('Update check timed out; keeping the installed version running.', file=sys.stderr)
                update = subprocess.CompletedProcess([], 1)
            after = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
            if update.returncode == 0 and before.stdout != after.stdout:
                manage('stop')
                subprocess.run(
                    ['make', 'install', f'PORT={args.port}'], cwd=ROOT, check=True,
                    env={**os.environ, 'LOGGED_TIME_INSTALL_OPEN_BROWSER': '0'},
                )
            wait_for_server()
            return
        port = manage('stop' if args.action == 'stop' else 'start', args.port)
        if args.action == 'open':
            webbrowser.open(f'http://localhost:{port}/')
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
