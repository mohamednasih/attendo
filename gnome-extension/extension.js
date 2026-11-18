/* GNOME Shell 42 uses the legacy GJS module format. */
const {Gio, GLib, St} = imports.gi;
const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const Me = imports.misc.extensionUtils.getCurrentExtension();

function duration(seconds) {
    if (seconds === null || seconds === undefined)
        return 'unavailable';
    const value = Math.max(0, Math.floor(seconds));
    return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60]
        .map(n => String(n).padStart(2, '0')).join(':');
}

class LoggedTime {
    enable() {
        this._active = true;
        this._processes = new Set();
        this._pending = new Set();
        this._button = new PanelMenu.Button(0.0, 'xattendo');
        this._label = new St.Label({text: 'Logged: …', y_align: imports.gi.Clutter.ActorAlign.CENTER});
        this._button.add_child(this._label);
        Main.panel.addToStatusArea(Me.metadata.uuid, this._button);
        this._summary = new PopupMenu.PopupMenuSection();
        this._locks = new PopupMenu.PopupMenuSection();
        this._button.menu.addMenuItem(this._summary);
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const submenu = new PopupMenu.PopupSubMenuMenuItem('Today’s lock periods');
        submenu.menu.addMenuItem(this._locks);
        this._button.menu.addMenuItem(submenu);
        this._button.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._button.menu.addAction('Refresh', () => this._refresh());
        this._button.menu.connect('open-state-changed', (_menu, open) => {
            if (open)
                this._refreshLocks();
        });
        this._row(this._summary, 'Loading Attendo…');
        try {
            this._project = JSON.parse(imports.byteArray.toString(
                GLib.file_get_contents(`${Me.path}/config.json`)[1])).project;
            // Keep observing between report requests. Autostart collection can also run.
            const collector = Gio.Subprocess.new(
                ['/usr/bin/python3', `${this._project}/shared_history.py`, 'collect'],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
            this._processes.add(collector);
            this._refresh();
            this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 60, () => {
                this._refresh();
                return GLib.SOURCE_CONTINUE;
            });
        } catch (error) {
            this._label.text = 'Logged: error';
            this._summary.removeAll();
            this._row(this._summary, String(error));
        }
    }

    _row(section, text) {
        const item = new PopupMenu.PopupMenuItem(text, {reactive: false});
        item.label.clutter_text.set_line_wrap(true);
        section.addMenuItem(item);
    }

    _request(kind, args, onSuccess, onError) {
        if (!this._active || !this._project || this._pending.has(kind))
            return;
        this._pending.add(kind);
        let process;
        try {
            process = Gio.Subprocess.new(
                ['/usr/bin/python3', `${this._project}/daily.py`, ...args],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE);
        } catch (error) {
            this._pending.delete(kind);
            onError(String(error));
            return;
        }
        this._processes.add(process);
        const timeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 200, () => {
            process.force_exit();
            return GLib.SOURCE_REMOVE;
        });
        process.communicate_utf8_async(null, null, (source, result) => {
            // Removing an expired source causes a GLib warning, so check first.
            if (GLib.MainContext.default().find_source_by_id(timeout))
                GLib.source_remove(timeout);
            this._processes.delete(process);
            this._pending.delete(kind);
            try {
                const [, stdout, stderr] = source.communicate_utf8_finish(result);
                if (!this._active)
                    return;
                if (!source.get_successful())
                    throw new Error(stderr.trim() || 'Backend stopped or timed out. Try Refresh.');
                onSuccess(JSON.parse(stdout));
            } catch (error) {
                if (this._active)
                    onError(String(error));
            }
        });
    }

    _refresh() {
        this._refreshLocks();
        this._request('report', ['--once'], data => {
            this._label.text = `Logged: ${duration(data.logged_seconds)}`;
            this._summary.removeAll();
            this._row(this._summary, `${data.date} · ${data.username}`);
            this._row(this._summary, `Logged time (${data.time_kind}): ${duration(data.logged_seconds)}`);
            this._row(this._summary, `Attendo total: ${duration(data.attendo_seconds)}`);
            this._row(this._summary, `Lock deduction: ${duration(data.lock_deduction_seconds)}`);
            this._row(this._summary, `Updated: ${data.attendo_fetched_at.slice(11, 19)}`);
            for (const warning of data.warnings || [])
                this._row(this._summary, warning);
        }, error => {
            this._label.text = 'Logged: unavailable';
            this._summary.removeAll();
            this._row(this._summary, error);
            this._row(this._summary, 'Complete Attendo login in Firefox, then Refresh.');
        });
    }

    _refreshLocks() {
        this._request('locks', ['locks', '--json'], data => {
            this._locks.removeAll();
            this._row(this._locks, `${data.date} · ${data.timezone}`);
            for (const period of data.periods) {
                const start = period.start.slice(11, 19);
                const end = period.ongoing ? 'ongoing' : period.end.slice(11, 19);
                this._row(this._locks, `${start} → ${end}   ${duration(period.seconds)}`);
            }
            if (!data.periods.length)
                this._row(this._locks, 'No observed lock periods today.');
            this._row(this._locks, `Total locked: ${duration(data.locked_seconds)}`);
            for (const warning of data.warnings || [])
                this._row(this._locks, warning);
        }, error => {
            this._locks.removeAll();
            this._row(this._locks, error);
        });
    }

    disable() {
        this._active = false;
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        for (const process of this._processes || [])
            process.force_exit();
        this._button.destroy();
        this._button = null;
    }
}

function init() {
    let instance;
    return {
        enable() {
            instance = new LoggedTime();
            instance.enable();
        },
        disable() {
            instance.disable();
            instance = null;
        },
    };
}
