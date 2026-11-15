# Logged time calculator

## GNOME top-bar extension

Another independent option is `logged-time dock`, also available as
**Logged Time Dashboard** in the application menu. It shows totals, today's
lock periods, and history notes in a normal GNOME window. Right-click its dock
icon and choose **Add to Favorites** to keep the launcher there. The launcher's
right-click actions also open the notification card or print locks in a
terminal. This mode loads once and refreshes on request; it does not replace
or stop the notification card.

For native integration without enabling an extension, run
`logged-time notifications` or open **Logged Time Notifications** from the
application menu. It keeps a resident card in GNOME's notification center
(click the top-bar clock or press Super+V), with **Refresh**, **Show locks**,
and **Quit** actions. Reports load once and refresh only on interaction.
It observes locks while running. Dismissing the notification or choosing Quit
stops this mode. This requires system Python's `Notify` bindings. The card is
in the notification center; it does not add a time label to the top bar.

On workstations that lock the list of enabled GNOME extensions, use
`logged-time widget` instead. This opens a compact, borderless indicator that
follows the system GTK theme and stays above other windows. Drag the clock icon
to move it. Click the time for totals, today's lock periods, history notes,
Refresh, and Quit. Reports refresh when you open the popover or click Refresh,
after an initial load. Lock collection continues while the widget is open.
It requires the system Python's GTK 3 bindings (`python3-gi` and
`gir1.2-gtk-3.0`), which are already installed on this workstation. Close the
window to stop it. The application menu also includes a Logged Time launcher.

The included extension supports GNOME Shell 42 (including this machine's 42.9).
It shows estimated logged time in the panel. Click it for Attendo totals, lock
deductions, and a submenu listing today's lock periods and total locked time.
Reports refresh every 60 seconds; opening the menu refreshes lock periods.

```bash
python3 install-gnome-extension.py
gnome-extensions enable logged-time@local
```

If GNOME does not recognize the newly installed extension, log out and back in,
then enable it. Keep the project at its current location. The extension uses the
same Firefox login and shared history as the command-line app, and may open
Firefox when Attendo login is needed. Backend work runs asynchronously outside
GNOME Shell. Errors and incomplete-history warnings appear in the menu.

The extension collects locks while enabled in the unlocked desktop session.
Use `make install` to keep the independent collector running through screen
locks and preserve observations even when the extension is disabled.

Disable with `gnome-extensions disable logged-time@local`. This stops the
extension's backend processes; the separately installed autostart collector and
saved history are preserved.

## Quick start

Requires Python 3.10 or newer, Bash for installation, and Make for the shortcuts
below. No pip packages are required. Automatic session reuse/login requires
Firefox; alternatively supply your Attendo token through `ATTENDO_TOKEN`.

From this project directory:

```bash
make help       # list available commands (also the default for make)
make install    # install logged-time on PATH and enable login collection
make run        # view today's estimated logged time
```

Complete the official login in Firefox if prompted. Installing autostart needs
no sudo and only enables the collector; open the report whenever you want.
Your home directory must be mounted at the same path on each workstation.

With the `logged-time` command installed on your PATH, display lock periods from
any directory:

```bash
logged-time locks                    # today's lock periods and total
logged-time locks --date 2026-11-14  # a previous day
logged-time locks --json             # machine-readable list
```

| Command | Purpose |
| --- | --- |
| `make run` | Live daily report; Ctrl-C exits |
| `make once` | One daily JSON report |
| `make json` | JSON reports at each refresh |
| `make locks` | Today's lock start/end times, durations, and total |
| `make collect` | Continuous foreground collection without Attendo login |
| `make snapshot` | Save available lock intervals once |
| `make install` | Install the PATH command and enable desktop-login collection |
| `make uninstall` | Remove the command and autostart while preserving history |
| `make test` | Run tests using synthetic data and mocked network requests |

Pass extra script options through `ARGS`, or choose Python with `PYTHON`:

```bash
make run ARGS='--refresh 30'
make once ARGS='--date 2026-11-14'
make collect ARGS='--history-dir /path/on/shared/home/history'
make test PYTHON=/usr/bin/python3
make locks                          # today's lock periods
make locks ARGS='--json'             # machine-readable list
make locks ARGS='--date 2026-11-14'  # a previous day
```

`make locks` reads saved periods across machines, the legacy archive, and the
current supervisor state, without Attendo login or network requests. Duplicate
and overlapping periods are merged and clipped to the selected day in
`Africa/Casablanca` (`--timezone` overrides it). An open lock on the current
machine is marked `ongoing`; its displayed end is the observation time. Saved
periods from other machines end at their last confirmed observation. This
command only lists history; keep the collector running to preserve future locks.

Use the same history directory for collection and reports. Default storage is
`~/.local/state/logged-time/history/`. The collector observes locks every second;
Attendo reports refresh every 60 seconds. Missed locks before collection began
cannot be recovered from these files. Detailed behavior and direct Python
commands follow.

## Daily calculation across machines

```bash
cd /home/mnasih/projects/logged-time
python3 daily.py
```

Your home directory follows you between workstations. The app therefore saves
lock observations in `~/.local/state/logged-time/history/` on that shared home
mount. It automatically reads the files written on other machines. No shared
server, token, upload, or additional API is needed.

`daily.py` starts a background collector before fetching Attendo, so slow
requests do not pause lock observations. Keep it running during each session.
It stops collecting when you exit. No background service or autostart entry is
installed automatically. A one-shot report observes only the current state; it cannot collect
future locks.

To collect automatically at desktop login on every machine mounting this home:

```bash
./install.sh
```

The installer creates an executable `~/.local/bin/logged-time` launcher so you
can run `logged-time`, `logged-time locks`, `logged-time widget`,
`logged-time notifications`, and `logged-time dock` from any directory.
`~/.local/bin` is already on this workstation's PATH. For another writable PATH
directory, use `BIN_DIR=/your/bin make install` (use the same override for
uninstall). If the selected directory is absent from PATH, the installer tells
you to add it to your shell configuration.

The installer also adds `~/.config/autostart/logged-time.desktop` (or uses
`XDG_CONFIG_HOME` when set). It requires no root access. Collection starts at
the next desktop login; keep this project at its installed location. Running
the installer again updates the entry. To disable autostart while preserving
history, run `./install.sh --uninstall`. A running collector stops at logout.

```bash
python3 daily.py --once                  # one JSON report for today
python3 daily.py --json                  # JSON report at each refresh
python3 daily.py --date 2026-11-14 --once # historical day returned by Attendo
python3 daily.py --history-dir /path/on/shared/home/history
```

Every machine must use the same history directory. Each collector instance
writes its own JSON file, identified by a random writer ID, with a hashed
machine/boot ID and username inside. Multiple processes never write the same
file. Updates use a temporary file in the same directory followed by atomic
replacement, so readers see either the previous complete version or the new
one. This avoids concurrent writes to a shared SQLite database. Duplicate or
overlapping lock intervals from any machine are merged before subtraction.
Temporary files from interrupted writes are ignored. Corrupt or unreadable
history files are reported as warnings while other readable files remain usable.

The collector reads `/run/42-lock/seat0.json` on the current machine every
second and archives its available intervals into your home. An open lock is
confirmed only through the last successful observation. It is never extended
just because the collector disappeared, the machine rebooted, or the home
mount moved. Closed intervals remain available after switching machines.

If you prefer a separate collector, run this throughout each login session:

```bash
python3 shared_history.py collect
python3 shared_history.py collect --once # snapshot only
```

Optional collector arguments are `--history-dir PATH`, `--state PATH`, and
`--user USERNAME`. Running it alongside `daily.py` is safe; overlapping
observations are merged. File access uses your normal home-directory
permissions. Writers create private files. Workstation clocks must be correct.
If the home mount becomes unavailable, collection reports an error and retries;
readable supervisor history can be recovered when it returns, but intervals
pruned during the gap may be lost. Files accumulate history and can be backed
up with the rest of your home.

### Formula and precision

- **Today:** Attendo's live total minus observed locked duration through the
  Attendo fetch time. Labelled **estimated**.
- **Past days:** Attendo's recorded daily total directly. Labelled **recorded**.
  As confirmed for this setup, recorded totals already exclude locked time.
  Local locks are shown separately in JSON but are not deducted again.

Days use `Africa/Casablanca`, including clock changes and locks crossing
midnight. `--timezone` overrides it and must match Attendo's reporting day.
Today's `todayHoursEstimation` is interpreted as HH:MM, retaining minute
precision. Saved `todayHours` is not added again. Missing live data is an error
rather than a substitute from recorded hours. Historical precision comes from
Attendo's saved values and may be rounded.

Total and deductions stop at the same fetch time, so stale reports cannot
keep losing logged time during a lock or network outage. JSON includes
`accounted_through`, `lock_deduction_seconds`, `time_kind`, `history_source`,
and `warnings`. Fetch time approximates the remote total's observation time;
upstream delay is unknown. Failed refreshes retain the previous total and mark
it stale. A new day requires fresh data. If locked duration exceeds the total,
`logged_seconds` is null rather than silently clamped.

The display updates every 60 seconds by default (`--refresh 30` changes it).
Ctrl-C exits. Lock collection runs every second independently of display and
network refreshes. The former local `locks.sqlite3` archive is read in
read-only mode for compatibility; daily collection no longer writes it.
`--archive PATH` selects that legacy archive. `history_complete` remains false:
locks before collection began, on machines without a running collector, or
pruned while the home was unavailable cannot be reconstructed.

### Attendo client

The bundled `attendo_client.py` fetches
`https://attendo.1337.ma/students/profile` directly. This project is self-contained.
It reuses your unexpired Attendo `student_token` cookie from Firefox. When no
session exists and the terminal is interactive, it opens the official 42 login
flow in Firefox and waits for you to complete sign-in.

Alternatively provide `ATTENDO_TOKEN` through the environment; `ATTENDO_COOKIE`
can supply a full cookie header and `ATTENDO_LOGIN` can accompany a token.
Credentials are not printed or saved by this app. Profile redirects are rejected
so cookies are not forwarded to another destination. Expired sessions produce
an error asking you to sign in again. The shared-history collector requires no
Attendo authentication and makes no network requests. Python 3, standard library
only; Firefox is needed only for automatic browser-session authentication.

## Manual raw-total calculation

```bash
python3 logged_time.py --window 2026-11-14T09:00:00+01:00 2026-11-14T18:00:00+01:00 --total-seconds 32400
```

This lower-level command subtracts observed locks from a **raw total that
includes locks**. Do not supply Attendo's recorded historical total. Repeat
`--window START END` for each corresponding session. Dates require explicit
timezone offsets; Unix timestamps also work. Overlapping locks are merged and
clipped to the windows; overlapping session windows and inconsistent totals
are rejected. It uses its original local SQLite archive; use `daily.py` for
the automatic shared-home calculation.

## Inspected sources

- `/usr/libexec/42/lock-supervisor.py` publishes username, session and lock
  intervals in `/run/42-lock/seat0.json`. An open interval ends in `null`.
  Timestamps reflect supervisor observations, approximately every half second.
  History may be pruned, replaced by another user, or lost on reboot.
- `/usr/share/42/scripts/input-idle.py` measures recent physical inactivity,
  without historical attendance intervals. Idle time before locking is not
  deducted.
- `/usr/share/42/scripts/lib/home_watcher.py` handles displaced home mounts;
  it provides no attendance ledger.
- `/home/mnasih/eye-rest/eye_rest.py` schedules eye breaks without publishing
  attendance intervals. Its process uptime is not deducted.
- Ubuntu's `software-properties-dbus` is unrelated to attendance accounting.
- `/usr/share/42/ftpkg-srv.py` and `/var/timelogger/database.db` are inaccessible
  to this account. No permissions were changed.
- Static inspection of `/usr/local/bin/timelogger-cli` found session lock
  accounting and separate lock-duration upload fields. See
  [TIMELOGGER_INSPECTION.md](TIMELOGGER_INSPECTION.md). The official TimeLogger
  API was not called.

Run checks with `python3 -m unittest -v`.
