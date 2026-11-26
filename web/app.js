import {loadClusters, clearClusters} from './clusters.js';

const $ = id => document.getElementById(id);
const pageLinks = [...document.querySelectorAll('.page-nav a')];
const clustersBadge = $('clusters-badge');
const clustersBadgeKey = 'logged-clusters-badge-dismissed';
try {
  clustersBadge.hidden = localStorage.getItem(clustersBadgeKey) === '1';
} catch { /* Keep the badge visible when storage is unavailable. */ }
clustersBadge.closest('a').addEventListener('click', () => {
  clustersBadge.hidden = true;
  try { localStorage.setItem(clustersBadgeKey, '1'); } catch { /* Hide it for this visit. */ }
}, {once: true});

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
function placeAttendanceCards() {
  const slot = $('details-page').hidden ? 'overview-period' : 'detail-period';
  const yesterday = selectedDay === previousDay();
  placeIn('current-period', slot);
  $('current-period').hidden = yesterday;
  placeIn('recorded-card', yesterday ? slot : 'detail-readings');
}
function showPage() {
  const hash = window.location.hash || `#${startupPage}`;
  const pageHash = ({'#history': '#details', '#attendance': '#week'})[hash] ?? hash;
  const active = pageLinks.find(link => link.hash === pageHash) ?? pageLinks[0];
  const details = active.hash === '#details';
  const clusters = active.hash === '#clusters';
  placeIn('goal-panel', details ? 'detail-net-summary' : 'logged-card');
  placeIn('hourly-visual', details ? 'history-graph' : 'overview-hours');
  if (hash === '#history') $('lock-periods').open = true;
  $('attendance-toolbar').hidden = clusters;
  $('daily-session').hidden = active.hash === '#week';
  $('status').parentElement.hidden = clusters;
  $('day-tabs').hidden = active.hash === '#week';
  $('last-refresh').hidden = active.hash === '#week';
  $('page-title').textContent = active.querySelector('.page-nav-label')?.textContent ?? active.textContent;
  for (const link of pageLinks) {
    $(link.hash.slice(1) + '-page').hidden = link !== active;
    if (link === active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  placeAttendanceCards();
  if (!$('dashboard').hidden) {
    if (clusters) loadClusters();
    else if ($('day-report').hidden && !loading) load();
  }
}
window.addEventListener('hashchange', showPage);

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
showPage();
let loading = false;
let dashboardRequest = 0;
const dayReports = new Map();
function duration(seconds) {
  if (seconds == null) return '—';
  const totalMinutes = Math.floor(seconds / 60);
  const hours = String(Math.floor(totalMinutes / 60)).padStart(2, '0');
  const minutes = String(totalMinutes % 60).padStart(2, '0');
  return `${hours}h:${minutes}m`;
}
const campusClock = new Intl.DateTimeFormat('en-GB', {timeZone: zone, hour: '2-digit', minute: '2-digit'});
const clock = value => campusClock.format(new Date(value));
function showRefreshTime(date) {
  const label = $('last-refresh');
  if (date) label.dateTime = date.toISOString();
  else label.removeAttribute('datetime');
  label.textContent = date ? `Last refreshed ${campusClock.format(date)}` : '';
}
let goalStorageKey = null;
const goalEditor = $('goal-editor');

function setGoalEditorOpen(open) {
  goalEditor.hidden = !open;
  $('edit-goal').setAttribute('aria-expanded', String(open));
  $('goal-error').hidden = true;
}

function savedGoalMinutes(key) {
  if (!key) return null;
  try {
    const value = localStorage.getItem(key);
    const minutes = Number(value);
    return value !== null && /^\d+$/.test(value) && Number.isSafeInteger(minutes) ? minutes : null;
  } catch { return null; }
}

function updateGoal(report) {
  if (selectedDay !== today) return;
  const key = report?.username ? `logged-goal:${encodeURIComponent(report.username)}:${today}` : null;
  if (key !== goalStorageKey) setGoalEditorOpen(false);
  goalStorageKey = key;
  const personal = savedGoalMinutes(key);
  const recommended = report?.attendance_goal?.daily_seconds;
  const target = personal !== null ? personal * 60 : recommended;
  const logged = report?.logged_seconds;
  $('goal-title').textContent = personal !== null ? 'Today’s personal goal' : 'Today’s goal';
  $('goal-title').title = personal !== null ? 'Your target for this date, saved in this browser.'
    : 'Uses Attendo’s recommendation when available. Otherwise, divides the remaining period time by the counted days left.';
  $('goal-label').textContent = target == null ? 'Unavailable' : duration(target);
  $('edit-goal').disabled = !key;
  $('reset-goal').hidden = personal === null;
  $('reset-goal').textContent = recommended != null ? `Use recommended goal · ${duration(recommended)}` : 'Remove personal goal';
  if (goalEditor.hidden) {
    const minutes = Math.floor((target ?? 0) / 60);
    $('goal-hours').value = Math.floor(minutes / 60);
    $('goal-minutes').value = minutes % 60;
  }
  let message = target == null ? 'Daily goal unavailable' : 'Waiting for your attendance';
  let percent = null;
  if (target != null && logged != null) {
    const loggedMinutes = Math.floor(logged / 60);
    const targetMinutes = target / 60;
    const minutesLeft = Math.max(0, targetMinutes - loggedMinutes);
    percent = minutesLeft === 0 ? 100 : Math.floor(100 * loggedMinutes / targetMinutes);
    message = minutesLeft === 0 ? 'Daily target reached' : `${duration(minutesLeft * 60)} to your goal`;
  }
  $('progress').style.width = `${percent ?? 0}%`;
  $('percent').textContent = percent == null ? '—' : `${percent}%`;
  $('remaining').textContent = message;
  $('progress').parentElement.hidden = target == null;
}

$('edit-goal').addEventListener('click', () => {
  setGoalEditorOpen(goalEditor.hidden);
  if (!goalEditor.hidden) $('goal-hours').focus();
  else updateGoal(dayReports.get(selectedDay)?.data?.report);
});

function closeGoalEditor() {
  setGoalEditorOpen(false);
  updateGoal(dayReports.get(selectedDay)?.data?.report);
  $('edit-goal').focus();
}

function savePersonalGoal(minutes) {
  if (!goalStorageKey) return;
  try {
    if (minutes === null) localStorage.removeItem(goalStorageKey);
    else localStorage.setItem(goalStorageKey, String(minutes));
  } catch {
    $('goal-error').textContent = 'Could not save your goal in this browser.';
    $('goal-error').hidden = false;
    return;
  }
  closeGoalEditor();
}

goalEditor.addEventListener('submit', event => {
  event.preventDefault();
  const minutes = $('goal-hours').valueAsNumber * 60 + $('goal-minutes').valueAsNumber;
  if (Number.isSafeInteger(minutes)) savePersonalGoal(minutes);
  else {
    $('goal-error').textContent = 'Enter a duration in whole hours and minutes.';
    $('goal-error').hidden = false;
  }
});
$('cancel-goal').addEventListener('click', closeGoalEditor);
$('reset-goal').addEventListener('click', () => savePersonalGoal(null));
window.addEventListener('storage', event => {
  if (goalStorageKey && (event.key === goalStorageKey || event.key === null)) {
    updateGoal(dayReports.get(selectedDay)?.data?.report);
  }
});
function updateDayView() {
  $('today').setAttribute('aria-pressed', String(selectedDay === today));
  $('yesterday').setAttribute('aria-pressed', String(selectedDay === previousDay()));
  const historical = selectedDay !== today && selectedDay !== previousDay();
  $('goal-panel').hidden = selectedDay !== today;
  placeAttendanceCards();
  if (selectedDay !== today) {
    goalStorageKey = null;
    setGoalEditorOpen(false);
  }
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
  const opening = $('dashboard').hidden;
  const returnFocus = $('session-screen').contains(document.activeElement);
  $('session-screen').hidden = true;
  $('dashboard').hidden = false;
  if (opening && !$('clusters-page').hidden) loadClusters();
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
  clearClusters();
  dayReports.clear();
  updateGoal();
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
  $('logged').textContent = duration(r?.logged_seconds);
  $('daily-net').textContent = duration(r?.logged_seconds);
  const estimated = r?.time_kind === 'estimated' && r.logged_seconds != null;
  $('net-kind').hidden = !estimated;
  $('detail-estimated').hidden = !estimated;
  $('detail-date').dateTime = selectedDay;
  $('detail-date').textContent = chartDayFormat.format(new Date(selectedDay));
  const netSource = {
    '42_intra_locations': 'From Intra, minus observed locks',
    'todayHoursEstimation': 'From Attendo’s live estimate, minus observed locks',
    'daily.hours': 'From Attendo’s daily record'
  }[r?.attendo_source];
  $('net-source').textContent = netSource ?? '';
  $('net-source').hidden = !netSource;
  $('historical-total').textContent = r?.time_kind === 'recorded' ? duration(r.attendo_seconds) : '—';
  const recorded = r?.attendo_recorded_seconds;
  $('recorded').textContent = recorded != null ? duration(recorded) : r ? 'Not yet recorded' : '—';
  $('recorded').classList.toggle('unposted', recorded == null);
  const latestRecorded = (r?.attendance_periods || []).flatMap(period => period.days)
    .filter(day => day.date < selectedDay).sort((a, b) => b.date.localeCompare(a.date))[0];
  const recordedNote = $('recorded-description');
  recordedNote.hidden = recorded != null || (r != null && !latestRecorded);
  recordedNote.textContent = !r ? 'Connect to Attendo and refresh'
    : latestRecorded ? `Last recorded · ${periodDateFormat.format(new Date(latestRecorded.date))} · ${duration(latestRecorded.hours * 3600)}` : '';
  $('intra-total').textContent = duration(totalLogTime(r));
  const lockedSeconds = locks?.locked_seconds ?? r?.locked_seconds;
  $('overview-locked').textContent = duration(lockedSeconds);
  renderHourlyLocks(locks?.hourly_locks);
  $('locked').textContent = duration(lockedSeconds);
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

    if (!$('clusters-page').hidden) return;
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
const weekdayFormat = new Intl.DateTimeFormat('en-GB', {weekday:'short', timeZone:'UTC'});

function formatPeriodRange(period) {
  return periodDateFormat.formatRange(new Date(period.start.slice(0, 10)), new Date(period.end.slice(0, 10)));
}

function renderCurrentPeriod(period, target) {
  $('period-gauge').hidden = !period;
  $('period-progress-status').hidden = !period;
  $('period-progress-count').hidden = !period;
  $('period-progress-dates').textContent = period ? formatPeriodRange(period) : 'Current period unavailable.';
  if (!period) return;

  const seconds = period.total * 3600;
  const hasTarget = target > 0;
  const reached = hasTarget && seconds >= target;
  const progress = hasTarget ? Math.min(1, Math.max(0, seconds / target)) : 0;
  $('period-gauge').style.setProperty('--period-sweep', `${progress * 270}deg`);
  $('period-progress-total').textContent = duration(seconds);
  const targetLabel = target % 3600 === 0 ? `${target / 3600}h` : duration(target);
  $('period-progress-target').textContent = hasTarget ? `of ${targetLabel}` : 'Time recorded';
  $('period-progress-count').textContent = `${period.days.filter(day => day.counted).length} / 7 days counted`;
  $('period-progress-status').textContent = reached ? 'Target reached' : hasTarget ? 'In progress' : 'Target unavailable';
  $('period-progress-status').classList.toggle('target-reached', reached);
}

function chartDuration(seconds) {
  if (seconds > 0 && seconds < 60) return `<${duration(60)}`;
  return duration(seconds);
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
  $('hour-max').textContent = duration(scaleMinutes * 60);
  $('hour-mid').textContent = duration(scaleMinutes * 60 / 2);
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
    slot.className = 'duration-slot hour-slot';
    slot.title = description;
    if (Number(time.slice(0, 2)) % 4 === 0) slot.dataset.tick = time;
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

const chartPeriodButtons = [...$('chart-periods').querySelectorAll('button')];
let preferredChartPeriod = 'Current period';

function selectChartPeriod(label) {
  preferredChartPeriod = label;
  const data = dayReports.get(selectedDay)?.data;
  if (data) renderWeekChart(data);
}
for (const button of chartPeriodButtons) {
  button.addEventListener('click', () => selectChartPeriod(button.value));
}
$('period-progress-title').querySelector('a').addEventListener('click', () => selectChartPeriod('Current period'));

function renderWeekChart({report, locks}) {
  const periods = report?.attendance_periods || [];
  const period = periods.find(item => item.label === preferredChartPeriod) ?? periods[0];
  for (const button of chartPeriodButtons) {
    button.disabled = !periods.some(item => item.label === button.value);
    button.setAttribute('aria-pressed', String(button.value === period?.label));
  }
  const previous = period?.label === 'Previous period';
  renderPeriodChart(previous ? report.previous_period_log_seconds : report?.period_log_seconds,
                    previous ? (report.previous_period_days ?? locks?.previous_period_days) : locks?.period_days, period);
}

function compactDuration(seconds) {
  if (seconds == null) return '—';
  if (seconds > 0 && seconds < 60) return '<1m';
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  if (!hours) return `${minutes}m`;
  return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
}

function showPeriodTotal(id, values) {
  const known = values.filter(value => value != null);
  $(id).textContent = known.length ? compactDuration(known.reduce((sum, seconds) => sum + seconds, 0)) : '—';
  $(id + '-coverage').textContent = known.length < values.length ? `${known.length} of ${values.length} days available` : '';
}

function dayBar(series, seconds, scaleSeconds) {
  const bar = document.createElement('span');
  bar.className = `day-bar bar-${series}`;
  bar.setAttribute('aria-hidden', 'true');
  if (seconds == null) bar.classList.add('missing');
  if (seconds > 0) bar.style.height = `max(2px, ${100 * seconds / scaleSeconds}%)`;
  const value = document.createElement('span');
  value.textContent = compactDuration(seconds);
  bar.append(value);
  return bar;
}

function renderPeriodChart(logTotals, lockDays, period) {
  const bars = $('time-chart-days');
  const previous = bars.querySelector('input:checked')?.value;
  bars.replaceChildren();
  const locks = new Map((lockDays || []).map(day => [day.date, day.locked_seconds]));
  const hasData = Object.keys(logTotals || {}).length > 0 || locks.size > 0;
  $('time-chart').hidden = !period || !hasData;
  if (!period) {
    $('time-chart-period').textContent = 'Attendance period unavailable.';
    return;
  }
  const range = formatPeriodRange(period);
  $('time-chart-period').textContent = hasData ? range : `${range} · Daily totals unavailable.`;
  if (!hasData) return;
  const dates = [];
  const last = Date.parse(period.end.slice(0, 10));
  for (let stamp = Date.parse(period.start.slice(0, 10)); stamp <= last; stamp += 86400000) {
    dates.push(new Date(stamp).toISOString().slice(0, 10));
  }
  const elapsed = dates.filter(date => date <= today);
  showPeriodTotal('chart-logged-total', elapsed.map(date => logTotals?.[date]));
  showPeriodTotal('chart-locked-total', elapsed.map(date => locks.get(date)));
  const peakHours = Math.max(0, ...elapsed.map(date => Math.max(logTotals?.[date] ?? 0, locks.get(date) ?? 0))) / 3600;
  const scaleHours = [1, 2, 4, 6, 8, 12, 16, 20, 24].find(hours => hours >= peakHours) ?? Math.ceil(peakHours / 2) * 2;
  $('day-max').textContent = `${scaleHours}h`;
  $('day-mid').textContent = scaleHours === 1 ? '30m' : `${scaleHours / 2}h`;
  $('day-upcoming').hidden = elapsed.length === dates.length;
  $('day-readout').hidden = !elapsed.length;
  const selected = elapsed.includes(previous) ? previous
    : elapsed.filter(date => logTotals?.[date] != null || locks.get(date) != null).at(-1) ?? elapsed.at(-1);

  for (const date of dates) {
    const slot = document.createElement('label');
    slot.className = 'duration-slot day-slot';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'period-day';
    input.value = date;
    input.disabled = date > today;
    input.checked = date === selected;

    const day = new Date(date);
    const dayLabel = chartDayFormat.format(day);
    const tick = document.createElement('time');
    tick.dateTime = date;
    tick.dataset.weekday = weekdayFormat.format(day);
    tick.textContent = String(day.getUTCDate());
    tick.setAttribute('aria-hidden', 'true');
    const total = logTotals?.[date];
    const locked = locks.get(date);
    const totalValue = total == null ? 'Unavailable' : chartDuration(total);
    let lockedValue = locked == null ? 'Unavailable' : chartDuration(locked);
    if (total != null && locked > total) lockedValue += ' (exceeds total)';
    slot.title = input.disabled ? `${dayLabel} · Upcoming`
      : `${dayLabel} · Total log time ${totalValue} · Screen locked ${lockedValue}`;
    input.setAttribute('aria-label', slot.title);
    const showDay = () => {
      $('day-date').textContent = dayLabel;
      $('day-total').textContent = totalValue;
      $('day-locked').textContent = lockedValue;
    };
    if (input.checked) showDay();
    input.addEventListener('change', showDay);
    slot.append(input, tick);
    if (!input.disabled) slot.append(dayBar('total', total, scaleHours * 3600), dayBar('locked', locked, scaleHours * 3600));
    bars.append(slot);
  }
}

function renderAttendance(data) {
  const periods = data.report?.attendance_periods || [];
  const target = data.report?.attendance_goal?.target_seconds;
  const current = periods.find(period => period.label === 'Current period');
  renderCurrentPeriod(current, target);
  renderWeekChart(data);
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
      status.textContent = period.total * 3600 >= target ? 'Target reached' : `Target · ${duration(target)}`;
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
  if (!days.length) container.textContent = 'No daily attendance records yet.';
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
    amount.textContent = duration(day.hours * 3600);
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
showPage();
updateGoal();
load(true);
