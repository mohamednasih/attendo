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
  $('remaining').textContent = goal == null ? 'Attendo recommendation unavailable' : seconds == null ? 'Waiting for your attendance' : seconds >= goal*3600 ? 'Daily goal reached. Well done!' : `${duration(goal*3600-seconds)} to your goal`;
}
function updateDayView() {
  const historical = $('date').value !== today;
  $('logged-card').hidden = historical;
  $('daily-cards').classList.toggle('historical', historical);
  $('attendo-label').textContent = historical ? 'ATTENDO RECORDED TIME' : 'ATTENDO LIVE ESTIMATE';
  $('attendo-description').textContent = historical ? 'Recorded attendance for this day' : 'Today’s campus attendance estimate';
  $('deduction').textContent = historical ? 'Observed locked time · no subtraction' : 'Deducted from today’s live estimate';
}
async function load(force = false) {
  if (loading) return;
  updateDayView();
  loading = true; $('refresh').disabled = true; $('date').disabled = true;
  $('status').className = ''; $('status').textContent = 'Syncing your attendance…';
  try {
    const response = await fetch(`/api/dashboard?date=${encodeURIComponent($('date').value)}${force ? '&refresh=1' : ''}`);
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
    if (!periods.length) { const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;cell.className='empty';cell.textContent=locks?'No observed locks for this day. A little more room to focus.':'Lock history is unavailable.';row.append(cell);$('periods').append(row); }
    $('warnings').replaceChildren();
    for (const message of new Set([...(r?.warnings || []), ...(locks?.warnings || []), ...(data.errors || [])])) { const li=document.createElement('li');li.textContent=message;$('warnings').append(li); }
    $('status').className = data.errors.length ? 'error' : '';
    $('status').textContent = data.errors.length ? 'Attendance unavailable. Sign in to Attendo in Firefox, Chrome, or Brave, then refresh. See “About these numbers” for details.' : `Connected as ${r.username} · ${r.time_kind === 'recorded' ? 'Recorded daily attendance' : 'Estimated attendance, adjusted for observed locks'}`;
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
updateGoal();load();setInterval(()=>{
  const nextToday = campusToday();
  if (today !== nextToday) { const wasToday = $('date').value === today; today = nextToday; $('date').max = today; if (wasToday) {selectDay(today); return;} }
  load();
},60000);
