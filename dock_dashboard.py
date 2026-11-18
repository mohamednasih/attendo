#!/usr/bin/python3
"""Native dashboard and dock launcher; independent of notification mode."""
import getpass

from desktop_widget import Widget
from gi.repository import Gtk
from shared_history import DEFAULT_HISTORY, HistoryCollector


class Dashboard(Widget):
    def __init__(self):
        super().__init__()
        self.set_application_id('local.loggedtime.Dashboard')

    def do_activate(self):
        if self.window:
            self.window.present()
            self.refresh()
            return
        self.window = Gtk.ApplicationWindow(application=self, title='xattendo')
        self.window.set_default_size(460, 560)
        self.window.set_icon_name('appointment-soon')
        header = Gtk.HeaderBar(title='xattendo', subtitle='Today’s work and lock history')
        header.set_show_close_button(True)
        refresh = Gtk.Button.new_from_icon_name('view-refresh-symbolic', Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text('Refresh totals and locks')
        refresh.connect('clicked', lambda *_: self.refresh())
        header.pack_end(refresh)
        self.window.set_titlebar(header)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.window.add(scroll)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin=24)
        scroll.add(content)
        caption = Gtk.Label(label='LOGGED TODAY', xalign=0)
        caption.get_style_context().add_class('dim-label')
        content.pack_start(caption, False, False, 0)
        self.clock = Gtk.Label(label='Loading…', xalign=0)
        css = Gtk.CssProvider()
        css.load_from_data(b'.logged-total { font-size: 36px; font-weight: bold; font-feature-settings: "tnum"; }')
        self.clock.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.clock.get_style_context().add_class('logged-total')
        content.pack_start(self.clock, False, False, 0)
        self.clock.connect('notify::label', self.update_title)
        self.report = Gtk.Label(label='Loading Attendo…', xalign=0, wrap=True, selectable=True)
        self.report.set_max_width_chars(50)
        content.pack_start(self.report, False, False, 0)
        content.pack_start(Gtk.Separator(), False, False, 0)
        self.locks = Gtk.Label(label='Loading lock history…', xalign=0, wrap=True, selectable=True)
        self.locks.set_max_width_chars(50)
        content.pack_start(self.locks, False, False, 0)
        notes = Gtk.Expander(label='History notes')
        self.notes = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.notes.set_max_width_chars(50)
        notes.add(self.notes)
        content.pack_start(notes, False, False, 0)
        footer = Gtk.Label(label='Refreshes when opened or when you click Refresh.', xalign=0, wrap=True)
        footer.get_style_context().add_class('dim-label')
        content.pack_start(footer, False, False, 0)
        self.window.connect('destroy', self.stop)
        self.window.show_all()
        self.collector = HistoryCollector(DEFAULT_HISTORY, getpass.getuser())
        self.collector.start()
        self.refresh()

    def update_title(self, *_):
        if self.window:
            self.window.set_title(f'{self.clock.get_text()} · xattendo')

    def stop(self, *_):
        self.window = None
        from gi.repository import GLib
        for timeout in self.timeouts:
            GLib.source_remove(timeout)
        self.timeouts.clear()
        for process in self.processes:
            process.force_exit()
        self.collector.stop()


if __name__ == '__main__':
    raise SystemExit(Dashboard().run())
