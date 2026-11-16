# Logged Time

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

The website opens automatically. Sign in to
[Attendo](https://attendo.1337.ma/students/login) in Firefox, Chrome, or Brave,
then click **Refresh data**.

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

- **Overview:** check logged time, attendance, and locked time.
- **Lock history:** see when your screen was locked and for how long.
- **Attendance:** select a day or export an attendance period as CSV.

Refresh using **Refresh data**, your browser's reload button, or `logged-time`.
The page does not refresh on a timer.

Today's logged time is an estimate that subtracts observed locks from Attendo
attendance. Lock history starts when the app is running; earlier locks may be
missing.

**Missing lock history?** Log in again at each workstation you previously used
and let the app run there to collect any lock records still available on that
station. Records that are no longer available cannot be recovered.

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
