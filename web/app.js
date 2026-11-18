const navigation = [...document.querySelectorAll('.sidebar .nav')];
function activateSection(hash) {
  const selected = navigation.find(link => link.hash === hash) || navigation[0];
  for (const link of navigation) {
    const active = link === selected;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
}
for (const link of navigation) {
  link.addEventListener('click', () => activateSection(link.hash));
}
window.addEventListener('hashchange', () => activateSection(window.location.hash));
activateSection(window.location.hash);

const $ = id => document.getElementById(id);
const zone = 'Africa/Casablanca';
const campusToday = () => new Intl.DateTimeFormat('en-CA', {timeZone: zone, year:'numeric', month:'2-digit', day:'2-digit'}).format(new Date());
let today = campusToday();
let goal = null;
let lastReport = null, loading = false;
$('date').value = today; $('date').max = today;
const duration = value => value == null ? '—' : `${Math.floor(value / 3600)}h ${String(Math.floor(value % 3600 / 60)).padStart(2,'0')}m`;
const clock = value => new Intl.DateTimeFormat('en-GB', {timeZone:zone,hour:'2-digit',minute:'2-digit'}).format(new Date(value));
function updateGoal() {
  $('goal-label').textContent = goal == null ? 'Unavailable' : duration(goal * 3600);
  const seconds = goal == null ? null : lastReport?.logged_seconds;
  const percent = seconds == null ? 0 : Math.min(100, (goal === 0 ? 100 : seconds / (goal * 3600) * 100));
  $('progress').style.width = `${percent}%`;
  $('percent').textContent = seconds == null ? '—' : `${Math.floor(percent)}%`;
  $('remaining').textContent = goal == null ? 'Attendo recommendation unavailable' : seconds == null ? 'Waiting for your attendance' : seconds >= goal*3600 ? 'Daily target reached' : `${duration(goal*3600-seconds)} to your goal`;
}
function updateDayView() {
  const selectedDay = $('date').value;
  const historical = selectedDay !== today && selectedDay !== previousDay();
  $('goal-panel').hidden = selectedDay !== today;
  $('logged-card').hidden = historical;
  $('recorded-card').hidden = historical;
  $('intra-card').hidden = historical;
  $('daily-cards').classList.toggle('historical', historical);
  $('historical-card').hidden = !historical;
}
async function fetchDashboard(day, force = false) {
  const response = await fetch(`/api/dashboard?date=${encodeURIComponent(day)}${force ? '&refresh=1' : ''}`, {cache: 'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Unable to load attendance.');
  return data;
}

async function load(force = false) {
  if (loading) return;
  updateDayView();
  loading = true; $('refresh').disabled = true; $('date').disabled = true;
  $('status').className = ''; $('status').textContent = 'Syncing your attendance…';
  try {
    const data = await fetchDashboard($('date').value, force);
    lastReport = data.report;
    goal = lastReport?.attendance_goal?.daily_seconds == null ? null : lastReport.attendance_goal.daily_seconds / 3600;
    renderAttendance(data);
    const r = data.report, locks = data.locks;
    $('logged').replaceChildren();
    if (r?.logged_seconds != null) {
      const seconds = r.logged_seconds;
      $('logged').append(document.createTextNode(Math.floor(seconds/3600)));
      const h=document.createElement('span');h.textContent='h';$('logged').append(h,document.createTextNode(String(Math.floor(seconds%3600/60)).padStart(2,'0')));
      const m=document.createElement('span');m.textContent='m';$('logged').append(m);
    } else $('logged').textContent = '—';
    $('historical-total').textContent = r?.time_kind === 'recorded' ? duration(r.attendo_seconds) : '—';
    const recorded = r?.attendo_recorded_seconds;
    const expectedHours = recorded == null && $('date').value === previousDay() && r?.logged_seconds != null
      ? Math.floor((r.logged_seconds + 20 * 60) / 3600) : null;
    $('recorded').textContent = recorded == null ? '—' : `${Math.floor(recorded / 3600)}h`;
    $('recorded-expected').hidden = expectedHours == null;
    $('recorded-expected').textContent = expectedHours == null ? '' : `(expected: ${expectedHours}h)`;
    const latestRecorded = (r?.attendance_periods || []).flatMap(period => period.days)
      .filter(day => day.date < $('date').value).sort((a, b) => b.date.localeCompare(a.date))[0];
    $('recorded-description').hidden = expectedHours != null;
    $('recorded-description').textContent = recorded != null ? 'Recorded campus attendance for this day'
      : !r ? 'Connect to Attendo and refresh'
      : latestRecorded ? `Not yet recorded · latest ${duration(latestRecorded.hours * 3600)} (${latestRecorded.date})`
      : 'Attendo has not returned a saved total for this day';
    $('intra-total').textContent = duration(r?.attendo_source === '42_intra_locations' ? r.attendo_seconds : null);
    $('locked').textContent = duration(locks?.locked_seconds ?? r?.locked_seconds);
    $('kind').textContent = r?.time_kind === 'recorded' ? 'Recorded' : 'Estimated';
    const periods = locks?.periods || [];
    $('count').textContent = `${periods.length} period${periods.length===1?'':'s'}`;
    renderLockRows($('periods'), locks);
    $('warnings').replaceChildren();
    for (const message of new Set([...(r?.warnings || []), ...(locks?.warnings || []), ...(data.errors || [])])) { const li=document.createElement('li');li.textContent=message;$('warnings').append(li); }
    $('status').className = data.errors.length ? 'error' : '';
    if (r) {
      const attendanceStatus = r.time_kind === 'recorded' ? (r.attendo_seconds == null ? 'Recorded total unavailable for this day' : 'Recorded daily attendance')
        : r.attendo_seconds == null ? '42 Intra location data unavailable for this day'
        : 'Estimated attendance, adjusted for observed locks';
      const details = data.errors.length || r.attendo_seconds == null ? ' · See “About these numbers” for details.' : '';
      $('status').textContent = `Connected as ${r.username} · ${attendanceStatus}${details}`;
    } else {
      $('status').textContent = data.errors[0]?.replace(/^Error: /, '') || 'Attendance is unavailable.';
    }
    $('updated').textContent = r ? `Updated ${clock(r.attendo_fetched_at)} · Casablanca` : 'Africa/Casablanca';
    updateGoal();
  } catch(error) { $('status').className='error';$('status').textContent=`${error.message} Previous values may be stale.`; }
  finally { loading=false;$('refresh').disabled=false;$('date').disabled=false; }
}
function selectDay(day) { if(loading)return; $('date').value=day; $('today').classList.toggle('selected',day===today);$('yesterday').classList.toggle('selected',day===previousDay());load(); }
function previousDay(){const d=new Date(`${today}T12:00:00Z`);d.setUTCDate(d.getUTCDate()-1);return d.toISOString().slice(0,10);}
$('refresh').addEventListener('click',()=>load(true));
$('date').addEventListener('change',()=>selectDay($('date').value || today));
$('today').addEventListener('click',()=>selectDay(today));
$('yesterday').addEventListener('click',()=>selectDay(previousDay()));
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

function renderAttendance(data) {
  const periods = data.report?.attendance_periods || [];
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
    const target = lastReport?.attendance_goal?.target_seconds;
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
    if (day.date === $('date').value) summary.setAttribute('aria-current', 'date');
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

function renderInlineDay(container, {report, locks, errors}) {
  const totals = document.createElement('dl');
  totals.className = 'inline-day-totals';
  for (const [label, seconds] of [
    ['Net attendance', report?.logged_seconds],
    ['Attendo recorded', report?.attendo_recorded_seconds],
    ['Locked time', locks?.locked_seconds ?? report?.locked_seconds]
  ]) {
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
  history.innerHTML = '<table><caption>Lock history</caption><thead><tr><th>Locked at</th><th>Unlocked at</th><th>Duration</th><th>Status</th></tr></thead><tbody></tbody></table>';
  renderLockRows(history.querySelector('tbody'), locks);
  container.replaceChildren(totals, history);
  if (errors.length) {
    const message = document.createElement('p');
    message.textContent = errors.join(' ');
    container.append(message);
  }
}

updateGoal();load(true);
