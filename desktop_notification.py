#!/usr/bin/python3
"""Native GNOME notification-center integration, without a Shell extension."""
import getpass
import json
from pathlib import Path
import signal

import gi
gi.require_version('Notify', '0.7')
from gi.repository import Gio, GLib, Notify

from daily import duration
from shared_history import DEFAULT_HISTORY, HistoryCollector

PROJECT = Path(__file__).resolve().parent


class NotificationApp(Gio.Application):
    def __init__(self):
        super().__init__(application_id='local.loggedtime.Notification')
        self.card = None
        self.pending = set()
        self.processes = set()
        self.timeouts = set()
        self.report = 'Loading Attendo…'
        self.locks = 'Loading lock history…'
        self.title = 'Logged Time'
        self.show_locks = False
        self.stopping = False

    def do_activate(self):
        if self.card:
            self.refresh()
            return
        self.hold()
        Notify.init('Logged Time')
        self.card = Notify.Notification.new(self.title, self.report, 'appointment-soon-symbolic')
        self.card.set_hint('resident', GLib.Variant('b', True))
        self.card.set_hint('transient', GLib.Variant('b', False))
        self.card.set_hint('suppress-sound', GLib.Variant('b', True))
        self.card.set_hint('desktop-entry', GLib.Variant('s', 'logged-time-notification'))
        self.card.set_timeout(Notify.EXPIRES_NEVER)
        self.card.add_action('default', 'Refresh', lambda *_: self.refresh(), None)
        self.card.add_action('refresh', 'Refresh', lambda *_: self.refresh(), None)
        self.card.add_action('locks', 'Show locks', self.toggle_locks, None)
        self.card.add_action('quit', 'Quit', lambda *_: self.quit(), None)
        self.card.connect('closed', lambda *_: self.quit() if not self.stopping else None)
        self.collector = HistoryCollector(DEFAULT_HISTORY, getpass.getuser())
        self.collector.start()
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.quit)
        self.render()
        self.refresh()

    def render(self):
        if not self.stopping:
            body = self.locks if self.show_locks else self.report
            self.card.update(self.title, GLib.markup_escape_text(body), 'appointment-soon-symbolic')
            self.card.show()

    def toggle_locks(self, *_):
        self.show_locks = not self.show_locks
        self.render()
        self.refresh()

    def request(self, kind, args, done):
        if kind in self.pending:
            return
        self.pending.add(kind)
        process = Gio.Subprocess.new(
            ['/usr/bin/python3', str(PROJECT / 'daily.py'), *args],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        self.processes.add(process)

        def expire():
            self.timeouts.discard(timeout)
            process.force_exit()
            return False

        timeout = GLib.timeout_add_seconds(200, expire)
        self.timeouts.add(timeout)

        def finished(source, result):
            self.processes.discard(process)
            self.pending.discard(kind)
            if timeout in self.timeouts:
                GLib.source_remove(timeout)
                self.timeouts.discard(timeout)
            try:
                _, output, error = source.communicate_utf8_finish(result)
                if self.stopping:
                    return
                if not source.get_successful():
                    raise ValueError(error.strip() or 'Request timed out. Click Refresh to retry.')
                done(json.loads(output))
            except (GLib.Error, ValueError, KeyError, TypeError) as error:
                if not self.stopping:
                    setattr(self, kind, str(error))
                    if kind == 'report':
                        self.title = 'Logged Time · unavailable'
            self.render()

        process.communicate_utf8_async(None, None, finished)

    def refresh(self):
        def report(data):
            self.title = f"Logged Time · {duration(data['logged_seconds'])}"
            self.report = '\n'.join([
                f"{data['date']} · {data['username']} · {data['time_kind']}",
                f"Attendo: {duration(data['attendo_seconds'])}",
                f"Lock deduction: {duration(data['lock_deduction_seconds'])}",
                f"Updated {data['attendo_fetched_at'][11:19]}",
                *data.get('warnings', []),
            ])

        def locks(data):
            lines = [f"Today’s locks · {duration(data['locked_seconds'])}"]
            for period in data['periods']:
                end = 'now' if period['ongoing'] else period['end'][11:16]
                lines.append(f"{period['start'][11:16]} – {end}    {duration(period['seconds'])}")
            if not data['periods']:
                lines.append('No observed lock periods today.')
            self.locks = '\n'.join(lines + data.get('warnings', []))

        self.request('locks', ['locks', '--json'], locks)
        self.request('report', ['--once'], report)

    def do_shutdown(self):
        self.stopping = True
        for timeout in self.timeouts:
            GLib.source_remove(timeout)
        for process in self.processes:
            process.force_exit()
        if self.card:
            self.collector.stop()
            self.card.close()
        Gio.Application.do_shutdown(self)


if __name__ == '__main__':
    raise SystemExit(NotificationApp().run())
