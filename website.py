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
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import attendo_client

ROOT = Path(__file__).resolve().parent
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
        data = {'date': day, 'report': None, 'locks': None, 'errors': []}
        for key, script, args in (
            ('report', 'daily.py', ['--intra', '--once', '--date', day]),
            ('locks', 'shared_history.py', ['list', '--json', '--date', day]),
        ):
            try:
                data[key] = command_json(script, args)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                data['errors'].append(str(error))
        return data


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_GET(self):
        # Always send current assets even if a browser has an older cached copy.
        for header in ('If-Modified-Since', 'If-None-Match'):
            if header in self.headers:
                del self.headers[header]
        url = urlsplit(self.path)
        if url.path == '/api/health':
            body = json.dumps({'pid': os.getpid()}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == '/api/session':
            try:
                payload, status = {'active': attendo_client.session_active()}, 200
            except (attendo_client.AttendoError, OSError):
                payload, status = {'error': 'Unable to check the Attendo session.'}, 503
            body = json.dumps(payload).encode()
            self.send_response(status)
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
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path in ('/', '/index.html', '/style.css', '/app.js', '/theme.js'):
            super().do_GET()
        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=47831)
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
