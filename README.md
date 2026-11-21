# Xattendo

A local dashboard for 1337 students to check their Attendo attendance, estimated
logged time, and screen-lock history. See your daily target and review previous
days in one place.

## Get started

On Linux, with Python 3.10+ and Make installed:

```bash
git clone https://github.com/mohamednasih/attendo.git
cd attendo
make install
```

The website opens automatically and checks your sessions for
[Attendo](https://attendo.1337.ma/students/login) and [42 Intra](https://profile.intra.42.fr/).
The **Account sign-in** panel shows a status for each service. Follow any **Sign in**
link in Firefox, Chrome, or Brave, then return to Xattendo. It checks automatically,
or you can click **Refresh status**. Once both accounts are signed in, the dashboard
opens.

Chrome can take longer to fetch your session. We recommend signing in with Firefox.
You can also start the server with `ATTENDO_TOKEN` set for Attendo.

The app starts automatically when you log in and checks for updates. Keep the
project folder where you installed it.

## Use the app

Run this from any terminal to open the dashboard:

```bash
logged-time
```

To display your stats in the terminal instead:

```bash
logged-time stats
```

For a previous day, use `logged-time stats --date 2026-11-14`.

- **Settings:** choose a theme and startup day for this browser, or toggle **Open browser at login** for this workstation. The server always starts in the background when you sign in.
- **Overview:** check logged time, 42 Intra’s location total, saved recorded time, and locked time. Recorded time already excludes locks; a missing saved total is shown as unavailable.
- **Lock history:** see when your screen was locked and for how long.
- **Attendance:** expand a period and a day to see daily attendance and lock history.

Refresh using **Refresh data**, your browser's reload button, or `logged-time`.
The page does not refresh on a timer.

Logged time for today and yesterday is an estimate that subtracts observed locks from the 42 Intra
location total. Lock history starts when the app is running; earlier locks may be
missing.

**Missing lock history?** Log in again at each workstation you previously used
and let the app run there to collect any lock records still available on that
station. Records that are no longer available cannot be recovered.

## Lock-history storage

Workstations share the SQLite database at
`~/.local/state/logged-time/history/locks.sqlite3`. Stop older JSON-writing
collectors before deploying the update. The `make` update and desktop-login
updates both import existing JSON history, verify the database, and remove the
original JSON files after the import commits. Updated collectors write directly
to SQLite.

If an import fails verification, the originals stay in place and the app
reports the error. To rerun the migration and cleanup immediately, use
`make migrate-history`. To import without removing files during that command,
run `python3 shared_history.py migrate` instead.
The older archive at `~/.local/state/logged-time/locks.sqlite3` is read separately.

## Update or uninstall

From the project folder, update and reopen the app:

```bash
make
```

To stop the app and remove its command and login startup:

```bash
make uninstall
```

Your saved lock history is kept.
