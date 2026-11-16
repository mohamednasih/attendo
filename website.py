#!/usr/bin/env python3
"""Loopback-only web dashboard for the existing logged-time calculator."""
import argparse
from datetime import date, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CACHE = {}
LOCK = threading.Lock()


def command_json(script, args):
    result = subprocess.run([sys.executable, str(ROOT / script), *args],
                            stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=30, cwd=ROOT)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Unable to load attendance data.')
    return json.loads(result.stdout)


def dashboard(day, refresh=False):
    with LOCK:
        cached = CACHE.get(day)
        if not refresh and cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        data = {'date': day, 'report': None, 'locks': None, 'errors': []}
        for key, script, args in (
            ('report', 'daily.py', ['--once', '--date', day]),
            ('locks', 'shared_history.py', ['list', '--json', '--date', day]),
        ):
            try:
                data[key] = command_json(script, args)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                data['errors'].append(str(error))
        if len(CACHE) >= 32:
            CACHE.pop(next(iter(CACHE)))
        CACHE[day] = (time.monotonic(), data)
        return data


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/api/health':
            body = json.dumps({'pid': os.getpid()}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == '/api/dashboard':
            try:
                today = datetime.now(ZoneInfo('Africa/Casablanca')).date()
                day = date.fromisoformat(parse_qs(url.query).get('date', [today.isoformat()])[0])
                if day > today:
                    raise ValueError('Choose today or a past day.')
                payload, status = dashboard(day.isoformat(), refresh=parse_qs(url.query).get('refresh') == ['1']), 200
            except ValueError as error:
                payload, status = {'error': str(error)}, 400
            body = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path in ('/', '/index.html', '/style.css', '/app.js'):
            super().do_GET()
        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(ROOT / 'web')))
    from shared_history import HistoryCollector, DEFAULT_HISTORY
    import getpass
    collector = HistoryCollector(DEFAULT_HISTORY, getpass.getuser())
    collector.start()
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f'Logged Time is running at http://localhost:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        collector.stop()


if __name__ == '__main__':
    main()
