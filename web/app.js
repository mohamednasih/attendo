const $ = id => document.getElementById(id);
const pageLinks = [...document.querySelectorAll('.page-nav a')];
function initStartupPreference(id) {
  const select = $(id);
  const key = `logged-${id}`;
  try {
    const saved = localStorage.getItem(key);
    if ([...select.options].some(option => option.value === saved)) select.value = saved;
  } catch { /* Use the default option when storage is unavailable. */ }
  select.addEventListener('change', () => {
    try { localStorage.setItem(key, select.value); } catch { /* Storage is unavailable. */ }
  });
  return select.value;
}
const startupPage = initStartupPreference('default-page');
const startupDay = initStartupPreference('default-day');

function placeIn(id, containerId) {
  const element = $(id), container = $(containerId);
  if (element.parentElement !== container) container.append(element);
}
function showPage() {
  const hash = window.location.hash || `#${startupPage}`;
  const pageHash = ({'#history': '#details', '#attendance': '#week'})[hash] ?? hash;
  const active = pageLinks.find(link => link.hash === pageHash) ?? pageLinks[0];
  const details = active.hash === '#details';
  placeIn('goal-panel', details ? 'detail-net-summary' : 'logged-card');
  placeIn('current-period', details ? 'details-metrics' : 'overview-period');
  placeIn('hourly-visual', details ? 'history-graph' : 'overview-hours');
  if (hash === '#history') $('lock-periods').open = true;
  $('daily-session').hidden = active.hash === '#week';
  $('day-tabs').hidden = active.hash === '#week';
  $('last-refresh').hidden = active.hash === '#week';
  $('page-title').textContent = active.textContent;
  for (const link of pageLinks) {
    $(link.hash.slice(1) + '-page').hidden = link !== active;
    if (link === active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
}
window.addEventListener('hashchange', showPage);
showPage();

const zone = 'Africa/Casablanca';
const campusDateFormat = new Intl.DateTimeFormat('en-CA', {timeZone: zone, year:'numeric', month:'2-digit', day:'2-digit'});
function campusToday() {
  const parts = Object.fromEntries(campusDateFormat.formatToParts(new Date()).map(({type, value}) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
const startupSetting = $('launch-at-login');
let savedStartupSetting = null;
let checkingStartupSetting = false;

async function syncStartupSetting(enabled) {
  if (checkingStartupSetting) return;
  checkingStartupSetting = true;
  startupSetting.disabled = true;
  const errorMessage = $('startup-error');
  errorMessage.hidden = true;
  try {
    const options = enabled === undefined ? {} : {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({launch_at_login: enabled})
    };
    const response = await fetch('/api/settings', options);
    if (response.status === 404) throw new Error('Restart the local server to manage launch at login.');
    const data = await response.json();
    if (!response.ok || typeof data.launch_at_login !== 'boolean') {
      throw new Error(data.error || 'Unable to read the desktop startup setting.');
    }
    savedStartupSetting = data.launch_at_login;
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
  } finally {
    startupSetting.checked = savedStartupSetting === true;
    startupSetting.disabled = savedStartupSetting === null;
    checkingStartupSetting = false;
  }
}

$('open-settings').addEventListener('click', () => {
  $('settings').showModal();
  syncStartupSetting();
});
startupSetting.addEventListener('change', () => syncStartupSetting(startupSetting.checked));

let today = campusToday();
let selectedDay = startupDay === 'yesterday' ? previousDay() : today;
let loading = false;
let dashboardRequest = 0;
const dayReports = new Map();
// At 35 minutes past a full hour, attendance rounds up to the next hour.
const attendanceRoundingOffsetSeconds = (60 - 35) * 60;
const duration = value => value == null ? '—' : `${Math.floor(value / 3600)}h ${String(Math.floor(value % 3600 / 60)).padStart(2,'0')}m`;
function renderMetricDuration(element, seconds) {
  element.innerHTML = duration(seconds).replace(/h (\d+)m$/, '<small>h:</small>$1<small>m</small>');
}
const campusClock = new Intl.DateTimeFormat('en-GB', {timeZone: zone, hour: '2-digit', minute: '2-digit'});
const clock = value => campusClock.format(new Date(value));
const refreshClock = new Intl.DateTimeFormat('en-GB', {timeZone: zone, timeStyle: 'medium'});
function showRefreshTime(date) {
  const label = $('last-refresh');
  if (date) label.dateTime = date.toISOString();
  else label.removeAttribute('datetime');
  label.textContent = date ? `Last refreshed ${refreshClock.format(date)}` : '';
}
function updateGoal(report) {
  const target = report?.attendance_goal?.daily_seconds;
  const logged = report?.logged_seconds;
  $('goal-label').textContent = target == null ? 'Unavailable' : `${target / 3600}h`;
  let message = target == null ? 'Attendo recommendation unavailable' : 'Waiting for your attendance';
  let percent = null;
  if (target != null && logged != null) {
    const requiredSeconds = Math.max(0, target - attendanceRoundingOffsetSeconds);
    const minutesLeft = Math.ceil(Math.max(0, requiredSeconds - logged) / 60);
    percent = minutesLeft === 0 ? 100 : Math.floor(100 * logged / requiredSeconds);
    message = minutesLeft === 0 ? 'Daily target reached' : `${duration(minutesLeft * 60)} to your goal`;
  }
  $('progress').style.width = `${percent ?? 0}%`;
  $('percent').textContent = percent == null ? '—' : `${percent}%`;
  $('remaining').textContent = message;
}
function updateDayView() {
  $('today').setAttribute('aria-pressed', String(selectedDay === today));
  $('yesterday').setAttribute('aria-pressed', String(selectedDay === previousDay()));
  const historical = selectedDay !== today && selectedDay !== previousDay();
  $('goal-panel').hidden = selectedDay !== today;
  $('logged-card').hidden = historical;
  $('recorded-card').hidden = historical;
  $('intra-card').hidden = historical;
  $('historical-card').hidden = !historical;
}
async function fetchDashboard(day, force = false) {
  const response = await fetch(`/api/dashboard?date=${encodeURIComponent(day)}${force ? '&refresh=1' : ''}`, {cache: 'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Unable to load attendance.');
  return data;
}

function showDashboard() {
  const returnFocus = $('session-screen').contains(document.activeElement);
  $('session-screen').hidden = true;
  $('dashboard').hidden = false;
  if (returnFocus) document.querySelector('.page-nav [aria-current]').focus();
}

async function fetchSessions() {
  const response = await fetch('/api/session', {cache: 'no-store'});
  if (response.status === 404) throw new Error('Restart the local server to check both sessions.');
  const sessions = await response.json();
  if (!['attendo', 'intra'].every(key => [true, false, null].includes(sessions[key]))) {
    throw new Error('Unable to check both sessions. Restart the local server and try again.');
  }
  return sessions;
}

function showSessionScreen() {
  dayReports.clear();
  showRefreshTime(null);
  $('day-report').hidden = true;
  $('session-details').hidden = true;
  const opening = $('session-screen').hidden;
  if ($('settings').open) $('settings').close();
  $('dashboard').hidden = true;
  $('session-screen').hidden = false;
  if (opening) $('session-title').focus();
}

function renderSessions(sessions) {
  const missing = [];
  for (const service of ['intra', 'attendo']) {
    const active = sessions[service];
    const name = $(`${service}-name`).textContent;
    if (active === false) missing.push(name);
    const state = active === true ? 'signed-in' : active === false ? 'signed-out' : 'unknown';
    const status = $(`${service}-state`);
    if (status.dataset.state === state) continue;
    status.dataset.state = state;
    status.textContent = active === true ? 'Signed in' : active === false ? 'Sign-in required' : 'Unable to verify';
    const link = $(`${service}-login`);
    link.firstElementChild.textContent = active === false ? 'Sign in' : 'Open site';
    link.setAttribute('aria-label', `${active === false ? 'Sign in to' : 'Open'} ${name} in a new tab`);
  }
  const guidance = $('session-guidance');
  guidance.hidden = !missing.length;
  if (missing.length) {
    const account = missing.length === 1 ? missing[0] : 'both services';
    guidance.textContent = `Sign in to ${account} in Firefox, Chrome, or Brave, then return here.`;
  }
  const message = (sessions.errors || []).join(' ');
  if ($('session-status').textContent !== message) $('session-status').textContent = message;
}

function totalLogTime(report) {
  return report?.attendo_source === '42_intra_locations' ? report.attendo_seconds : null;
}

function renderDashboard(data) {
  renderAttendance(data);
  const r = data.report, locks = data.locks;
  $('session-username').textContent = r?.username || '—';
  $('session-workstation').textContent = r?.current_workstation || '—';
  $('session-details').hidden = !r?.username && !r?.current_workstation;
  renderMetricDuration($('logged'), r?.logged_seconds);
  renderMetricDuration($('daily-net'), r?.logged_seconds);
  const estimated = r?.time_kind === 'estimated' && r.logged_seconds != null;
  $('net-kind').hidden = !estimated;
  $('detail-estimated').hidden = !estimated;
  $('detail-date').dateTime = selectedDay;
  $('detail-date').textContent = chartDayFormat.format(new Date(selectedDay));
  const netSource = {
    '42_intra_locations': '42 Intra log time − observed locks',
    'todayHoursEstimation': 'Attendo live estimate − observed locks',
    'daily.hours': '1337 Attendo daily record'
  }[r?.attendo_source];
  $('net-source').textContent = netSource ?? '';
  $('net-source').hidden = !netSource;
  $('historical-total').textContent = r?.time_kind === 'recorded' ? duration(r.attendo_seconds) : '—';
  const recorded = r?.attendo_recorded_seconds;
  const netSeconds = r?.logged_seconds;
  const expected = $('recorded-expected');
  expected.hidden = netSeconds == null;
  expected.textContent = netSeconds == null ? '' : `Should be ${Math.floor((netSeconds + attendanceRoundingOffsetSeconds) / 3600)}h`;
  $('recorded').textContent = recorded != null ? `${Math.floor(recorded / 3600)}h` : r ? 'Not yet recorded' : '—';
  $('recorded').classList.toggle('unposted', recorded == null);
  const latestRecorded = (r?.attendance_periods || []).flatMap(period => period.days)
    .filter(day => day.date < selectedDay).sort((a, b) => b.date.localeCompare(a.date))[0];
  const recordedNote = $('recorded-description');
  recordedNote.hidden = recorded != null || (r != null && !latestRecorded);
  recordedNote.textContent = !r ? 'Connect to Attendo and refresh'
    : latestRecorded ? `Last recorded · ${periodDateFormat.format(new Date(latestRecorded.date))} · ${latestRecorded.hours}h` : '';
  renderMetricDuration($('intra-total'), totalLogTime(r));
  const lockedSeconds = locks?.locked_seconds ?? r?.locked_seconds;
  renderMetricDuration($('overview-locked'), lockedSeconds);
  renderHourlyLocks(locks?.hourly_locks);
  renderMetricDuration($('locked'), lockedSeconds);
  const periods = locks?.periods || [];
  $('count').textContent = `${periods.length} period${periods.length===1?'':'s'}`;
  renderLockRows($('periods'), locks);
  $('warnings').replaceChildren();
  for (const message of new Set([...(r?.warnings || []), ...(locks?.warnings || []), ...(data.errors || [])])) { const li=document.createElement('li');li.textContent=message;$('warnings').append(li); }
  $('status').className = data.errors.length ? 'error' : '';
  $('status').textContent = data.errors[0]?.replace(/^Error: /, '')
    || (r?.attendo_seconds == null ? 'Daily attendance unavailable. See Details for more information.' : '');
  updateGoal(r);
}

async function load(force = false, sessions = null) {
  const request = ++dashboardRequest;
  syncCampusDay();
  updateDayView();
  const day = selectedDay;
  if (!dayReports.has(day)) dayReports.set(day, {});
  const entry = dayReports.get(day);
  showRefreshTime(entry.refreshedAt);
  $('day-report').hidden = !entry.data;
  const checkSignIn = force || $('dashboard').hidden;
  loading = true;
  $('refresh').disabled = true;
  $('refresh').textContent = 'Refreshing…';
  $('retry-session').disabled = true;
  $('retry-session').textContent = 'Checking…';
  try {
    if (checkSignIn) {
      sessions = sessions ?? await fetchSessions();
      if (request !== dashboardRequest) return;
      renderSessions(sessions);
      if (!sessions.active) {
        showSessionScreen();
        return;
      }
      showDashboard();
    }

    if (entry.data) renderDashboard(entry.data);
    $('status').className = '';
    $('status').textContent = day === today ? 'Loading today’s attendance…' : 'Loading yesterday’s attendance…';
    // Reuse a date's pending request when switching away and back.
    if (!entry.pending) {
      entry.pending = fetchDashboard(day, force).then(data => {
        entry.data = data;
        if (data.report || data.locks) entry.refreshedAt = new Date();
        return data;
      }).finally(() => { entry.pending = null; });
    }
    const data = await entry.pending;
    if (request !== dashboardRequest) return;
    renderDashboard(data);
    showRefreshTime(entry.refreshedAt);
    $('day-report').hidden = false;
  } catch (error) {
    if (request !== dashboardRequest) return;
    if (checkSignIn && !sessions?.active) {
      renderSessions({errors: [error.message]});
      showSessionScreen();
    } else {
      $('status').className = 'error';
      $('status').textContent = $('day-report').hidden ? error.message : `${error.message} Previous values may be stale.`;
    }
  } finally {
    if (request === dashboardRequest) {
      loading = false;
      $('refresh').disabled = false;
      $('refresh').textContent = 'Refresh data';
      $('retry-session').disabled = false;
      $('retry-session').textContent = 'Refresh status';
    }
  }
}
function previousDay() {
  const date = new Date(`${today}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

function syncCampusDay() {
  const currentDay = campusToday();
  if (currentDay === today) return false;
  const viewingToday = selectedDay === today;
  dayReports.clear();
  today = currentDay;
  selectedDay = viewingToday ? today : previousDay();
  return true;
}

function selectDay(viewToday) {
  const rolledOver = syncCampusDay();
  const day = viewToday ? today : previousDay();
  if (!rolledOver && day === selectedDay) return;
  selectedDay = day;
  load();
}
$('retry-session').addEventListener('click', () => load(true));
$('refresh').addEventListener('click', () => load(true));
$('today').addEventListener('click', () => selectDay(true));
$('yesterday').addEventListener('click', () => selectDay(false));
function renderLockRows(body, locks) {
  const periods = locks?.periods || [];
  body.replaceChildren();
  for (const period of [...periods].reverse()) {
    const row = document.createElement('tr');
    for (const value of [clock(period.start), period.ongoing ? '—' : clock(period.end), duration(period.seconds)]) {
      const cell=document.createElement('td');cell.textContent=value;row.append(cell);
    }
    const cell=document.createElement('td'), tag=document.createElement('span');tag.className='tag';tag.textContent=period.ongoing?'Ongoing':'Completed';cell.append(tag);row.append(cell);body.append(row);
  }
  if (!periods.length) { const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;cell.className='empty';cell.textContent=locks?'No lock periods recorded for this day.':'Lock history is unavailable.';row.append(cell);body.append(row); }
}

const periodDateFormat = new Intl.DateTimeFormat('en-US', {month:'short', day:'numeric', timeZone:'UTC'});
const chartDayFormat = new Intl.DateTimeFormat('en-GB', {weekday:'short', month:'short', day:'numeric', timeZone:'UTC'});

function renderCurrentPeriod(period, target, dateRange) {
  $('period-gauge').hidden = !period;
  $('period-progress-status').hidden = !period;
  $('period-progress-count').hidden = !period;
  $('period-progress-dates').textContent = dateRange || 'Current period unavailable.';
  if (!period) return;

  const seconds = period.total * 3600;
  const hasTarget = target > 0;
  const reached = hasTarget && seconds >= target;
  const progress = hasTarget ? Math.min(1, Math.max(0, seconds / target)) : 0;
  $('period-gauge').style.setProperty('--period-sweep', `${progress * 270}deg`);
  const compactDuration = value => duration(value).replace(/ 00m$/, '');
  $('period-progress-total').textContent = compactDuration(seconds);
  $('period-progress-target').textContent = hasTarget ? `of ${compactDuration(target)}` : 'Recorded';
  $('period-progress-count').textContent = `${period.days.filter(day => day.counted).length} / 7 days counted`;
  $('period-progress-status').textContent = reached ? 'Target reached' : hasTarget ? 'In progress' : 'Target unavailable';
  $('period-progress-status').classList.toggle('target-reached', reached);
}

function chartDuration(seconds) {
  if (seconds > 0 && seconds < 60) return '<1m';
  return duration(seconds).replace(/^0h 0?/, '');
}

function renderHourlyLocks(hours) {
  const bars = $('hour-bars');
  const previous = bars.querySelector('input:checked')?.value;
  bars.replaceChildren();
  $('hourly-date').textContent = chartDayFormat.format(new Date(selectedDay));
  $('hourly-chart').hidden = !hours?.length;
  $('hourly-empty').hidden = !!hours?.length;
  if (!hours?.length) return;

  const peak = Math.max(0, ...hours.map(hour => hour.locked_seconds ?? 0));
  const scaleMinutes = [10, 20, 30, 60].find(minutes => minutes * 60 >= peak) ?? 60;
  $('hour-max').textContent = `${scaleMinutes}m`;
  $('hour-mid').textContent = `${scaleMinutes / 2}m`;
  $('hour-upcoming').hidden = hours.every(hour => hour.locked_seconds != null);
  const selected = hours.find(hour => hour.start === previous && hour.locked_seconds != null)
    ?? hours.find(hour => hour.locked_seconds === peak);
  for (const {start, locked_seconds: seconds} of hours) {
    const slot = document.createElement('label');
    const input = document.createElement('input');
    const time = clock(start);
    // Include the offset on a clock-change day to distinguish repeated hours.
    const hourLabel = hours.length === 24 ? `${time}–${clock(Date.parse(start) + 3600000)}`
      : `Hour starting ${time} UTC${start.slice(-6)}`;
    const description = seconds == null ? `${hourLabel} · Upcoming` : `${hourLabel} · ${chartDuration(seconds)} locked`;
    slot.className = 'hour-slot';
    slot.title = description;
    if (Number(time.slice(0, 2)) % 4 === 0) slot.dataset.tick = time.slice(0, 2);
    input.type = 'radio';
    input.name = 'lock-hour';
    input.value = start;
    input.disabled = seconds == null;
    input.checked = start === selected?.start;
    input.setAttribute('aria-label', description);
    if (input.checked) $('hour-value').textContent = peak === 0 ? 'No locks observed' : description;
    input.addEventListener('change', () => { $('hour-value').textContent = description; });
    if (seconds > 0) slot.style.setProperty('--bar-height', `max(2px, ${100 * seconds / (scaleMinutes * 60)}%)`);
    slot.append(input);
    bars.append(slot);
  }
}

function renderPeriodChart(logTotals, lockDays, dateRange) {
  const list = $('time-chart-days');
  list.replaceChildren();
  const locks = new Map((lockDays || []).map(day => [day.date, day.locked_seconds]));
  const dates = logTotals ? Object.keys(logTotals).sort() : [...locks.keys()].sort();
  $('time-chart-legend').hidden = !dateRange || !dates.length;
  if (!dateRange || !dates.length) {
    $('time-chart-period').textContent = dateRange ? 'Daily totals unavailable for this period.' : 'Current period unavailable.';
    return;
  }
  $('time-chart-period').textContent = `Current Attendo period · ${dateRange}`;
  const maximum = Math.max(1, ...dates.filter(date => date <= today).map(date => logTotals?.[date] ?? 0));
  for (const date of dates) {
    const row = document.createElement('li');
    const label = document.createElement('time');
    label.dateTime = date;
    label.textContent = chartDayFormat.format(new Date(date));
    const track = document.createElement('span');
    track.className = 'time-track';
    track.setAttribute('aria-hidden', 'true');
    const value = document.createElement('span');
    value.className = 'time-values';
    if (date > today) {
      row.className = 'upcoming';
      value.textContent = 'Upcoming';
    } else {
      const total = logTotals?.[date];
      const locked = locks.get(date);
      const totalLabel = total == null ? 'Total unavailable' : `${chartDuration(total)} total`;
      const lockLabel = locked == null ? 'Locks unavailable' : `${chartDuration(locked)} locked`;
      value.textContent = `${totalLabel} · ${lockLabel}`;
      if (total != null) {
        const bar = document.createElement('span');
        bar.style.width = `${100 * total / maximum}%`;
        if (locked != null && locked <= total) {
          bar.className = 'split-time';
          bar.style.setProperty('--unlocked-share', `${total > 0 ? 100 * (total - locked) / total : 0}%`);
        } else if (locked > total) {
          value.textContent += ' (exceeds total)';
        }
        track.append(bar);
      }
    }
    row.append(label, value, track);
    list.append(row);
  }
}

function renderAttendance(data) {
  const periods = data.report?.attendance_periods || [];
  const target = data.report?.attendance_goal?.target_seconds;
  const current = periods.find(period => period.label === 'Current period');
  const dateRange = current ? periodDateFormat.formatRange(new Date(current.start), new Date(current.end)) : '';
  renderCurrentPeriod(current, target, dateRange);
  renderPeriodChart(data.report?.period_log_seconds, data.locks?.period_days, dateRange);
  const list = $('attendance-periods');
  const expanded = new Set([...list.querySelectorAll('.attendance-period[open]')].map(item => item.dataset.period));
  const expandedDays = new Set([...list.querySelectorAll('.attendance-day[open]')].map(item => item.dataset.day));
  list.replaceChildren();
  if (!periods.length) {
    list.textContent = 'Attendance periods are unavailable. Connect to Attendo and refresh.';
    return;
  }
  const dateFormat = new Intl.DateTimeFormat('en-US', {month:'short', day:'numeric', year:'numeric', timeZone:'UTC'});
  for (const period of periods) {
    const details = document.createElement('details');
    details.className = 'attendance-period';
    details.dataset.period = period.start;
    details.open = expanded.has(period.start);

    const summary = document.createElement('summary');
    const description = document.createElement('span');
    const dates = document.createElement('strong');
    dates.textContent = dateFormat.formatRange(new Date(period.start), new Date(period.end));
    const counted = document.createElement('small');
    counted.textContent = `${period.label} · ${period.days.filter(day => day.counted).length}/7 days counted`;
    description.append(dates, counted);

    const totals = document.createElement('span');
    totals.className = 'period-totals';
    const total = document.createElement('strong');
    total.textContent = duration(period.total * 3600);
    totals.append(total);
    if (target != null) {
      const status = document.createElement('small');
      status.textContent = period.total * 3600 >= target ? 'Target reached' : `of ${duration(target)} target`;
      totals.append(status);
    }
    summary.append(description, totals);

    details.append(summary, renderAttendanceDays(period, expandedDays, data));
    list.append(details);
  }
}

function renderAttendanceDays(period, expandedDays, data) {
  const container = document.createElement('div');
  container.className = 'attendance-days';
  const dateFormat = new Intl.DateTimeFormat('en-US', {day:'numeric', month:'short', timeZone:'UTC'});
  const days = [...period.days].sort((a, b) => b.date.localeCompare(a.date));
  for (const day of days) {
    const details = document.createElement('details');
    details.className = 'attendance-day';
    details.dataset.day = `${period.start}/${day.date}`;
    details.open = expandedDays.has(details.dataset.day);

    const summary = document.createElement('summary');
    if (day.date === selectedDay) summary.setAttribute('aria-current', 'date');
    const name = document.createElement('span');
    name.textContent = dateFormat.format(new Date(day.date));
    const amount = document.createElement('strong');
    amount.textContent = duration(day.hours * 3600).replace(/ 00m$/, '');
    const status = document.createElement('span');
    status.className = 'day-count-status';
    status.classList.toggle('counted', day.counted);
    status.textContent = day.counted ? 'Counted' : 'Not counted';
    summary.append(name, amount, status);

    const content = document.createElement('div');
    content.className = 'attendance-day-content';
    const report = document.createElement('div');
    report.className = 'inline-day-report';
    report.setAttribute('aria-live', 'polite');
    let requested = false;
    const loadDetails = async () => {
      if (!details.open || requested) return;
      if (day.date > today) {
        report.textContent = 'Daily details will be available on this date.';
        return;
      }
      requested = true;
      report.textContent = 'Loading daily details…';
      try {
        renderInlineDay(report, day.date === data.date ? data : await fetchDashboard(day.date));
      } catch (error) {
        report.textContent = error.message;
        requested = false;
      }
    };
    details.addEventListener('toggle', loadDetails);
    if (details.open) loadDetails();
    content.append(report);
    details.append(summary, content);
    container.append(details);
  }
  return container;
}

function renderInlineDay(container, {date, report, locks, errors}) {
  const totals = document.createElement('dl');
  totals.className = 'inline-day-totals';
  const metrics = [
    ['Net attendance', report?.logged_seconds],
    ['Attendo recorded', report?.attendo_recorded_seconds],
    ['Total locked time', locks?.locked_seconds ?? report?.locked_seconds]
  ];
  if (date === today || date === previousDay()) metrics.unshift(['Total log time', totalLogTime(report)]);
  for (const [label, seconds] of metrics) {
    const metric = document.createElement('div');
    const name = document.createElement('dt');
    name.textContent = label;
    const value = document.createElement('dd');
    value.textContent = duration(seconds);
    metric.append(name, value);
    totals.append(metric);
  }
  const history = document.createElement('div');
  history.className = 'table-wrap';
  history.tabIndex = 0;
  history.setAttribute('role', 'region');
  history.setAttribute('aria-label', 'Daily lock periods');
  history.innerHTML = '<table><caption>Lock history</caption><thead><tr><th scope="col">Locked at</th><th scope="col">Unlocked at</th><th scope="col">Duration</th><th scope="col">Status</th></tr></thead><tbody></tbody></table>';
  renderLockRows(history.querySelector('tbody'), locks);
  container.replaceChildren(totals, history);
  if (errors.length) {
    const message = document.createElement('p');
    message.textContent = errors.join(' ');
    container.append(message);
  }
}

let checkingSession = false;
async function checkSession() {
  if (checkingSession || loading || document.hidden) return;
  if (syncCampusDay() && !$('dashboard').hidden) {
    await load(true);
    return;
  }
  checkingSession = true;
  const request = dashboardRequest;
  try {
    const sessions = await fetchSessions();
    if (request !== dashboardRequest) return;
    renderSessions(sessions);
    if (sessions.active === false || (sessions.active === null && $('dashboard').hidden)) {
      showSessionScreen();
    } else if (sessions.active && $('dashboard').hidden) {
      await load(true, sessions);
    }
  } catch (error) {
    if (request === dashboardRequest && !$('session-screen').hidden) renderSessions({errors: [error.message]});
  } finally {
    checkingSession = false;
  }
}

window.addEventListener('focus', checkSession);
window.addEventListener('pageshow', checkSession);
document.addEventListener('visibilitychange', checkSession);
setInterval(checkSession, 3000);
updateGoal();load(true);
