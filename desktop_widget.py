#!/usr/bin/python3
"""Standalone GTK desktop indicator for desktops that restrict extensions."""
import json
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib, Gtk, Gdk

from daily import duration

PROJECT = Path(__file__).resolve().parent


class Widget(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='local.loggedtime.Widget')
        self.processes = set()
        self.pending = set()
        self.timeouts = set()
        self.window = None
        self.report_notes = []
        self.lock_notes = []

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        self.window = Gtk.ApplicationWindow(application=self, title='Logged Time')
        self.window.set_resizable(False)
        self.window.set_keep_above(True)
        self.window.set_decorated(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.window.get_style_context().add_class('logged-window')
        css = Gtk.CssProvider()
        css.load_from_data(b'''
            .logged-window { background-color: transparent; }
            .logged-indicator {
                background-color: @theme_bg_color; color: @theme_fg_color;
                border-radius: 18px; border: 1px solid alpha(@theme_fg_color, 0.16);
                padding: 2px 8px;
            }
            .logged-indicator button { border-radius: 16px; padding: 4px 8px; }
            .logged-time { font-weight: 600; font-feature-settings: "tnum"; }
        ''')
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        box.get_style_context().add_class('logged-indicator')
        self.window.add(box)
        handle = Gtk.EventBox()
        handle.add(Gtk.Image.new_from_icon_name('alarm-symbolic', Gtk.IconSize.MENU))
        handle.set_tooltip_text('Drag to move Logged Time')
        handle.connect('button-press-event', self.move)
        box.pack_start(handle, False, False, 4)
        self.button = Gtk.ToggleButton()
        self.button.set_relief(Gtk.ReliefStyle.NONE)
        self.clock = Gtk.Label(label='…')
        self.clock.get_style_context().add_class('logged-time')
        self.button.add(self.clock)
        self.button.set_tooltip_text('Logged time · click for details')
        box.pack_start(self.button, True, True, 0)
        popover = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        popover.set_title('Logged Time Details')
        popover.set_decorated(False)
        popover.set_resizable(False)
        popover.set_default_size(380, 340)
        popover.set_skip_taskbar_hint(True)
        popover.set_skip_pager_hint(True)
        popover.set_transient_for(self.window)
        popover.set_keep_above(True)
        popover.get_style_context().add_class('background')
        popover.connect('key-press-event', self.details_key)
        popover.connect('delete-event', lambda *_: self.button.set_active(False) or True)
        self.popover = popover
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        popover.add(content)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(180)
        scroll.set_max_content_height(480)
        content.pack_start(scroll, True, True, 0)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        scroll.add(details)
        self.report = Gtk.Label(label='Loading Attendo…', xalign=0, selectable=True, wrap=True)
        self.report.set_max_width_chars(65)
        self.locks = Gtk.Label(label='Loading lock history…', xalign=0, selectable=True, wrap=True)
        self.locks.set_max_width_chars(45)
        warnings = Gtk.Expander(label='History notes')
        self.notes = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.notes.set_max_width_chars(45)
        warnings.add(self.notes)
        details.pack_start(self.report, False, False, 0)
        details.pack_start(self.locks, False, False, 0)
        details.pack_start(warnings, False, False, 0)
        actions = Gtk.Box(spacing=8)
        refresh = Gtk.Button(label='Refresh')
        refresh.connect('clicked', lambda *_: self.refresh())
        actions.pack_start(refresh, True, True, 0)
        close = Gtk.Button(label='Quit')
        close.connect('clicked', lambda *_: self.window.destroy())
        actions.pack_start(close, False, False, 0)
        content.pack_start(actions, False, False, 0)
        content.show_all()
        self.button.connect('toggled', self.open_details)
        self.window.connect('destroy', self.stop)
        self.window.show_all()
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        area = monitor.get_workarea()
        self.window.move(area.x + area.width - 175, area.y + 8)
        GLib.idle_add(lambda: self.window.move(area.x + area.width - 175, area.y + 8))
        collector = Gio.Subprocess.new(
            ['/usr/bin/python3', str(PROJECT / 'shared_history.py'), 'collect'],
            Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
        self.processes.add(collector)
        self.refresh()

    def open_details(self, button):
        if button.get_active():
            self.popover.show_all()
            x, y = self.window.get_position()
            width, height = self.popover.get_size()
            area = self.window.get_display().get_monitor_at_window(self.window.get_window()).get_workarea()
            x = max(area.x, min(x, area.x + area.width - width))
            y = min(y + self.window.get_size()[1] + 6, area.y + area.height - height)
            self.popover.move(x, max(area.y, y))
            self.popover.present()
            self.refresh()
        else:
            self.popover.hide()

    def details_key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.button.set_active(False)
            return True
        return False

    def move(self, _handle, event):
        if event.button == 1:
            self.window.begin_move_drag(1, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def request(self, kind, args, callback, error_label):
        if kind in self.pending:
            return
        self.pending.add(kind)
        try:
            process = Gio.Subprocess.new(
                ['/usr/bin/python3', str(PROJECT / 'daily.py'), *args],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as error:
            self.pending.discard(kind)
            error_label.set_text(str(error))
            return
        self.processes.add(process)

        def expire():
            self.timeouts.discard(timeout)
            process.force_exit()
            return False

        timeout = GLib.timeout_add_seconds(200, expire)
        self.timeouts.add(timeout)

        def finished(source, result):
            if timeout in self.timeouts:
                GLib.source_remove(timeout)
                self.timeouts.discard(timeout)
            self.processes.discard(process)
            self.pending.discard(kind)
            try:
                _, output, error = source.communicate_utf8_finish(result)
                if not self.window:
                    return
                if not source.get_successful():
                    raise ValueError(error.strip() or 'Request timed out. Try Refresh.')
                callback(json.loads(output))
            except (GLib.Error, ValueError, KeyError, TypeError) as error:
                if self.window:
                    error_label.set_text(str(error))
                    if kind == 'report':
                        self.clock.set_text('Unavailable')

        process.communicate_utf8_async(None, None, finished)

    def refresh_locks(self):
        def show(data):
            lines = [f"Today’s locks · {duration(data['locked_seconds'])}", '']
            for period in data['periods']:
                start = period['start'][11:16]
                end = 'now' if period['ongoing'] else period['end'][11:16]
                lines.append(f"{start} – {end}     {duration(period['seconds'])}")
            if not data['periods']:
                lines.append('No locks recorded today')
            self.locks.set_text('\n'.join(lines))
            self.lock_notes = data.get('warnings', [])
            self.notes.set_text('\n'.join(dict.fromkeys(self.report_notes + self.lock_notes)))
        self.request('locks', ['locks', '--json'],
                     show, self.locks)

    def refresh(self):
        self.refresh_locks()

        def show(data):
            self.clock.set_text(duration(data['logged_seconds']))
            self.report_notes = data.get('warnings', [])
            self.notes.set_text('\n'.join(dict.fromkeys(self.report_notes + self.lock_notes)))
            self.report.set_text('\n'.join([
                f"{data['date']} · {data['username']}",
                f"Logged ({data['time_kind']}): {duration(data['logged_seconds'])}",
                f"Attendo: {duration(data['attendo_seconds'])}",
                f"Lock deduction: {duration(data['lock_deduction_seconds'])}",
                f"Updated {data['attendo_fetched_at'][11:19]}",
            ]))

        self.request('report', ['--once'], show, self.report)
        return True

    def stop(self, *_):
        self.window = None
        self.popover.destroy()
        for timeout in self.timeouts:
            GLib.source_remove(timeout)
        self.timeouts.clear()
        for process in self.processes:
            process.force_exit()


if __name__ == '__main__':
    raise SystemExit(Widget().run())
