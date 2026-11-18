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
  $('daily-cards').classList.toggle('historical', historical);
  $('attendance-source').textContent = historical ? 'From your Attendo account' : 'From 42 Intra location stats';
  $('attendo-label').textContent = historical ? 'Recorded attendance' : '42 Intra recorded total';
  $('attendo-description').textContent = historical ? 'Recorded attendance for this day' : 'Campus location total for this day';
}
async function load(force = false) {
  if (loading) return;
  updateDayView();
  loading = true; $('refresh').disabled = true; $('date').disabled = true;
  $('status').className = ''; $('status').textContent = 'Syncing your attendance…';
  try {
    const response = await fetch(`/api/dashboard?date=${encodeURIComponent($('date').value)}${force ? '&refresh=1' : ''}`, {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Unable to load attendance.');
    lastReport = data.report;
    goal = lastReport?.attendance_goal?.daily_seconds == null ? null : lastReport.attendance_goal.daily_seconds / 3600;
    renderAttendance(data.report?.attendance_periods || []);
    const r = data.report, locks = data.locks;
    $('logged').replaceChildren();
    if (r?.logged_seconds != null) {
      const seconds = r.logged_seconds;
      $('logged').append(document.createTextNode(Math.floor(seconds/3600)));
      const h=document.createElement('span');h.textContent='h';$('logged').append(h,document.createTextNode(String(Math.floor(seconds%3600/60)).padStart(2,'0')));
      const m=document.createElement('span');m.textContent='m';$('logged').append(m);
    } else $('logged').textContent = '—';
    $('total').textContent = duration(r?.attendo_seconds);
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
    $('locked').textContent = duration(locks?.locked_seconds ?? r?.locked_seconds);
    $('kind').textContent = r?.time_kind === 'recorded' ? 'Recorded' : 'Estimated';
    const periods = locks?.periods || [];
    $('count').textContent = `${periods.length} period${periods.length===1?'':'s'}`;
    $('periods').replaceChildren();
    for (const period of [...periods].reverse()) {
      const row = document.createElement('tr');
      for (const value of [clock(period.start), period.ongoing ? '—' : clock(period.end), duration(period.seconds)]) {
        const cell=document.createElement('td');cell.textContent=value;row.append(cell);
      }
      const cell=document.createElement('td'), tag=document.createElement('span');tag.className='tag';tag.textContent=period.ongoing?'Ongoing':'Completed';cell.append(tag);row.append(cell);$('periods').append(row);
    }
    if (!periods.length) { const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;cell.className='empty';cell.textContent=locks?'No lock periods recorded for this day.':'Lock history is unavailable.';row.append(cell);$('periods').append(row); }
    $('warnings').replaceChildren();
    for (const message of new Set([...(r?.warnings || []), ...(locks?.warnings || []), ...(data.errors || [])])) { const li=document.createElement('li');li.textContent=message;$('warnings').append(li); }
    $('status').className = data.errors.length ? 'error' : '';
    if (r) {
      const attendanceStatus = r.time_kind === 'recorded' ? (r.attendo_seconds == null ? 'Recorded total unavailable for this day' : 'Recorded daily attendance')
        : r.attendo_seconds == null ? '42 Intra recorded total unavailable for this day'
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
let attendancePeriods = [], activePeriod = 0;
function renderAttendance(periods) {
  attendancePeriods = periods;
  activePeriod = Math.min(activePeriod, Math.max(0, periods.length - 1));
  $('period-tabs').replaceChildren();
  periods.forEach((period, index) => {
    const tab = document.createElement('button');
    tab.textContent = period.label; tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', String(index === activePeriod));
    tab.className = index === activePeriod ? 'selected' : '';
    tab.onclick = () => {activePeriod = index; renderAttendance(attendancePeriods);};
    $('period-tabs').append(tab);
  });
  $('attendance-days').replaceChildren();
  const period = periods[activePeriod];
  $('export').disabled = !period;
  $('period-summary').textContent = period ? `${period.start} – ${period.end} · ${duration(period.total * 3600)} recorded · ${period.days.filter(day => day.counted).length} / 7 counted days` : 'Attendance periods are unavailable. Connect to Attendo and refresh.';
  for (const day of period?.days || []) {
    const button = document.createElement('button'); button.className = 'attendance-day';
    button.classList.toggle('viewing', day.date === $('date').value);
    button.disabled = day.date > today;
    button.setAttribute('aria-label', `${day.date}, ${duration(day.hours * 3600)}, ${day.counted ? 'counted' : 'not counted'}`);
    const name = document.createElement('span'); name.textContent = new Intl.DateTimeFormat('en-GB', {weekday:'short', day:'numeric', month:'short', timeZone:zone}).format(new Date(`${day.date}T12:00:00Z`));
    const bar = document.createElement('div'); bar.className = 'day-track';
    const fill = document.createElement('div'); fill.style.height = `${Math.min(100, day.hours / (lastReport?.attendance_goal?.target_seconds / 3600 / 7 || 24) * 100)}%`; bar.append(fill);
    const amount = document.createElement('strong'); amount.textContent = duration(day.hours * 3600);
    const status = document.createElement('small'); status.textContent = day.counted ? '● Counted' : 'Not counted';
    button.append(name, bar, amount, status); button.onclick = () => selectDay(day.date); $('attendance-days').append(button);
  }
}
$('export').onclick = () => {
  const period = attendancePeriods[activePeriod]; if (!period) return;
  const csv = ['Date,Recorded hours,Counted', ...period.days.map(day => `${day.date},${day.hours},${day.counted}`)].join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = `attendance-${period.start}.csv`; link.click(); setTimeout(()=>URL.revokeObjectURL(url), 1000);
};
updateGoal();load(true);
