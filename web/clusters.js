import {apiFetch, apiUrl} from './api.js';

const byId = id => document.getElementById(id);
const page = byId('clusters-page');
const switcher = byId('cluster-switcher');
const shortcutHint = byId('cluster-shortcuts');
const map = byId('cluster-map');
const results = byId('cluster-results');
const inspector = byId('cluster-inspector');
const profileCard = byId('cluster-profile');
const profiles = new WeakMap();
let profileSeat;
let profileDelay;
let dismissDelay;
const campusDateTime = new Intl.DateTimeFormat('en-GB', {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Africa/Casablanca'});
const natural = new Intl.Collator(undefined, {numeric: true});

let groups = [];
let current;
let selected;
let request;

function element(tag, text) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  return node;
}

function groupStations(stations, floors) {
  const campuses = new Set([...stations, ...floors].map(item => item.campus_id));
  const groups = new Map();
  const hosts = new Map();

  function group(campus, name, plan = null) {
    const key = `${campus}/${name}`;
    if (!groups.has(key)) {
      const label = campuses.size > 1 ? `Campus ${campus ?? '?'} · ${name}` : name;
      const number = /^Cluster ([0-9])$/i.exec(name)?.[1];
      groups.set(key, {key, label, number, plan, stations: []});
    }
    return groups.get(key);
  }
  for (const floor of floors) {
    const room = group(floor.campus_id, floor.name, floor);
    for (const seat of floor.seats) hosts.set(`${floor.campus_id}/${seat.host}`, room);
  }
  for (const station of stations) {
    let room = hosts.get(`${station.campus_id}/${station.host}`);
    if (!room) {
      const cluster = /^c(\d+)r\d+p\d+$/i.exec(station.host);
      const name = cluster ? `Cluster ${cluster[1]}` : 'Other hosts';
      room = group(station.campus_id, groups.get(`${station.campus_id}/${name}`)?.plan ? 'Other hosts' : name);
    }
    room.stations.push(station);
  }
  const rooms = [...groups.values()];
  for (const room of rooms) {
    if (room.number && !rooms.some(other => other !== room && other.number === room.number)) {
      room.shortcut = room.number;
    }
    room.stations.sort((a, b) => natural.compare(a.host, b.host));
    if (room.plan) {
      const emptyHosts = new Set(room.plan.seats.map(seat => seat.host));
      const total = emptyHosts.size;
      for (const station of room.stations) emptyHosts.delete(station.host);
      room.availability = `${emptyHosts.size}/${total} empty`;
    }
  }
  return rooms.sort((a, b) => natural.compare(a.label, b.label));
}

function selectGroup(group, station) {
  current = group;
  selected = station;
  render();
  const target = station ? results.querySelector(`[data-host="${CSS.escape(station.host)}"]`)
                         : switcher.querySelector('[aria-pressed="true"]');
  target?.focus({preventScroll: true});
  if (station) target?.scrollIntoView({block: 'nearest', inline: 'nearest'});
}

function renderSwitcher() {
  switcher.replaceChildren();
  shortcutHint.hidden = !groups.some(group => group.shortcut);
  for (const group of groups) {
    const button = element('button', group.label);
    if (group.shortcut) {
      const key = element('kbd', group.shortcut);
      key.setAttribute('aria-hidden', 'true');
      button.append(key);
      button.setAttribute('aria-keyshortcuts', group.shortcut);
    }
    button.append(element('small', group.availability || 'Capacity unknown'));
    button.type = 'button';
    button.setAttribute('aria-pressed', String(group === current));
    button.addEventListener('click', () => selectGroup(group));
    switcher.append(button);
  }
}

function renderInspector() {
  inspector.replaceChildren();
  if (!selected) {
    if (!current.plan) inspector.textContent = 'Select a workstation to see its session.';
    return;
  }
  inspector.append(element('strong', selected.host), element('span', selected.login || 'Login unavailable'));
  const start = new Date(selected.begin_at);
  if (selected.begin_at && Number.isFinite(start.getTime())) {
    const since = element('time', `Signed in ${campusDateTime.format(start)}`);
    since.dateTime = selected.begin_at;
    inspector.append(since);
  }
}

function hideProfile() {
  clearTimeout(profileDelay);
  clearTimeout(dismissDelay);
  profileSeat?.removeAttribute('aria-describedby');
  profileSeat = null;
  profileCard.hidden = true;
}

function positionProfile() {
  if (!profileSeat || profileCard.hidden) return;
  const seat = profileSeat.getBoundingClientRect();
  profileCard.style.removeProperty('--profile-space');
  const card = profileCard.getBoundingClientRect();
  const center = seat.left + seat.width / 2;
  const space = document.documentElement.clientWidth;
  const left = Math.max(6, Math.min(center - card.width / 2, space - card.width - 6));
  const aboveMap = seat.top - Math.max(0, map.getBoundingClientRect().top);
  const below = aboveMap < card.height + 6 && innerHeight - seat.bottom > aboveMap;
  const room = (below ? innerHeight - seat.bottom : seat.top) - 12;
  profileCard.style.setProperty('--profile-space', `${Math.max(44, room - 20)}px`);
  const top = below ? seat.bottom + 6 : seat.top - profileCard.offsetHeight - 6;
  profileCard.style.left = `${left}px`;
  profileCard.style.top = `${Math.max(6, top)}px`;
  profileCard.style.setProperty('--arrow-left', `${Math.max(8, Math.min(center - left, card.width - 8))}px`);
  profileCard.classList.toggle('below-seat', below);
}

function scheduleProfileDismiss() {
  clearTimeout(dismissDelay);
  if (profileCard.hidden && !profileSeat?.matches(':hover, :focus-visible')) {
    hideProfile();
    return;
  }
  dismissDelay = setTimeout(() => {
    if (!profileSeat?.matches(':hover, :focus-visible') && !profileCard.matches(':hover')) hideProfile();
  }, 140);
}

function showProfile(seat, station) {
  clearTimeout(dismissDelay);
  if (profileSeat === seat) return;
  hideProfile();
  profileSeat = seat;
  profileDelay = setTimeout(async () => {
    const name = byId('cluster-profile-name');
    const level = byId('cluster-profile-level');
    const projects = byId('cluster-profile-projects');
    const avatar = profileCard.querySelector('.cluster-profile-avatar');
    name.textContent = station.login;
    level.hidden = true;
    projects.textContent = 'Loading projects…';
    avatar.textContent = station.login.slice(0, 2).toUpperCase();
    const portrait = seat.querySelector('img');
    if (portrait) {
      const photo = portrait.cloneNode();
      photo.addEventListener('error', () => photo.remove(), {once: true});
      avatar.append(photo);
    }
    profileCard.hidden = false;
    seat.setAttribute('aria-describedby', profileCard.id);
    positionProfile();
    let pending = profiles.get(station);
    if (!pending) {
      pending = apiFetch(`/api/cluster-profile?login=${encodeURIComponent(station.login)}`).then(response => {
        if (!response.ok) throw new Error('Profile unavailable');
        return response.json();
      });
      profiles.set(station, pending);
    }
    try {
      const profile = await pending;
      if (profileSeat !== seat) return;
      name.textContent = profile.name || station.login;
      level.hidden = !Number.isFinite(profile.level);
      if (!level.hidden) level.textContent = `42 cursus · Level ${profile.level.toFixed(2)}`;
      if (profile.projects?.length) {
        const list = element('ul');
        for (const project of profile.projects) list.append(element('li', project));
        projects.replaceChildren(element('strong', 'Current projects'), list);
      } else {
        projects.textContent = profile.projects ? 'No current projects' : 'Current projects unavailable';
      }
    } catch {
      if (profiles.get(station) === pending) profiles.delete(station);
      if (profileSeat !== seat) return;
      projects.textContent = 'Profile details unavailable';
    }
    positionProfile();
  }, 140);
}

function occupiedSeat(station) {
  const login = station.login?.trim();
  const seat = element(login ? 'a' : 'button', (login || station.host).slice(0, 2).toUpperCase());
  if (login) {
    seat.href = `https://profile.intra.42.fr/users/${encodeURIComponent(login)}`;
    seat.target = '_blank';
    seat.rel = 'noopener noreferrer';
    seat.setAttribute('aria-label', `Open ${login}’s 42 Intra profile (opens in a new tab) · ${station.host}`);
    seat.addEventListener('pointerenter', event => {
      if (event.pointerType !== 'touch') showProfile(seat, station);
    });
    seat.addEventListener('pointerleave', scheduleProfileDismiss);
    seat.addEventListener('blur', scheduleProfileDismiss);
  } else {
    seat.type = 'button';
    seat.title = `View session at ${station.host}`;
    seat.setAttribute('aria-label', seat.title);
  }
  if (station.portrait) {
    const photo = element('img');
    photo.alt = '';
    photo.loading = 'lazy';
    photo.decoding = 'async';
    photo.draggable = false;
    photo.addEventListener('error', () => photo.remove(), {once: true});
    photo.src = apiUrl(station.portrait);
    seat.append(photo);
  }
  seat.dataset.host = station.host;
  if (station === selected) seat.setAttribute('aria-current', 'true');
  function selectSeat() {
    if (selected === station) return;
    map.querySelector('[aria-current]')?.removeAttribute('aria-current');
    seat.setAttribute('aria-current', 'true');
    selected = station;
    renderInspector();
  }
  // Pointer focus precedes the click. Keep the seat still until the link activates.
  seat.addEventListener('focus', () => {
    if (seat.matches(':focus-visible')) {
      selectSeat();
      if (login) showProfile(seat, station);
    }
  });
  seat.addEventListener('click', selectSeat);
  return seat;
}

function renderMap() {
  const {seats, labels} = current.plan;
  const active = new Map(current.stations.map(station => [station.host, station]));
  // The original SVGs use negative coordinates and generous outer margins.
  // Fit their content while preserving every seat's position and aspect ratio.
  const left = Math.min(...seats.map(s => s.x), ...labels.map(l => l.x)) - 3;
  const top = Math.min(...seats.map(s => s.y), ...labels.map(l => l.y - 16)) - 3;
  const width = Math.max(...seats.map(s => s.x + s.width), ...labels.map(l => l.x + l.text.length * 9)) - left + 3;
  const height = Math.max(...seats.map(s => s.y + s.height), ...labels.map(l => l.y + 4)) - top + 3;
  const plan = element('div');
  plan.className = 'floor-plan';
  map.style.setProperty('--plan-width', width);
  map.style.setProperty('--plan-ratio', width / height);
  plan.setAttribute('role', 'group');
  plan.setAttribute('aria-label', current.label);

  function place(node, x, y) {
    node.style.left = `${100 * (x - left) / width}%`;
    node.style.top = `${100 * (y - top) / height}%`;
    plan.append(node);
  }
  for (const label of labels) {
    const node = element('span', label.text);
    node.className = 'floor-label';
    place(node, label.x, label.y);
  }
  for (const seat of seats) {
    const station = active.get(seat.host);
    const node = station ? occupiedSeat(station) : element('span');
    node.className = 'floor-seat';
    node.style.width = `${100 * seat.width / width}%`;
    node.style.height = `${100 * seat.height / height}%`;
    if (!station) {
      node.title = seat.host;
      node.setAttribute('role', 'img');
      node.setAttribute('aria-label', `${seat.host} · No session reported`);
    }
    place(node, seat.x, seat.y);
  }
  map.replaceChildren(plan);
}

function addWorkstation(group, station) {
  const button = element('button');
  button.type = 'button';
  button.dataset.host = station.host;
  button.setAttribute('aria-pressed', String(station === selected));
  button.append(element('strong', station.host), element('span', station.login || 'Login unavailable'));
  const location = element('small', group.label);
  button.append(location);
  button.addEventListener('click', () => selectGroup(group, station));
  const item = element('li');
  item.append(button);
  results.append(item);
}

function render() {
  hideProfile();
  byId('cluster-heading').textContent = current?.label || 'Workstation map';
  renderSwitcher();
  results.replaceChildren();
  map.hidden = !current?.plan;
  byId('cluster-heading').parentElement.classList.toggle('sr-only', !map.hidden);
  results.hidden = !current || !map.hidden;
  inspector.hidden = !current;
  let summary = '42 Intra reports no active workstations.';
  if (current) {
    const count = current.stations.length;
    summary = `${count} active workstation${count === 1 ? '' : 's'} in ${current.label}`;
    if (current.plan) renderMap();
    else {
      summary += ' · Floor plan unavailable';
      current.stations.forEach(station => addWorkstation(current, station));
    }
    renderInspector();
  }
  byId('cluster-summary').textContent = summary;
}

export function clearClusters() {
  request?.abort();
  request = null;
  groups = [];
  current = selected = null;
  map.replaceChildren();
  inspector.replaceChildren();
  render();
  byId('cluster-summary').textContent = '';
  byId('cluster-sign-in').hidden = true;
}

export async function loadClusters() {
  if (request) return;
  const controller = new AbortController();
  request = controller;
  byId('cluster-sign-in').hidden = true;
  if (!groups.length) byId('cluster-summary').textContent = 'Loading workstations…';
  let needsSignIn = false;
  try {
    const response = await apiFetch('/api/clusters', {cache: 'no-store', signal: controller.signal});
    if (response.status === 404) throw new Error('Restart the local server to load the cluster map.');
    const data = await response.json();
    if (controller.signal.aborted) return;
    if (!response.ok) {
      needsSignIn = response.status === 401;
      throw new Error(data.error || 'Unable to load workstations.');
    }
    groups = groupStations(data.stations, data.floors || []);
    current = groups.find(group => group.key === current?.key) || groups[0];
    selected = current?.stations.find(station => station.host === selected?.host);
    render();
  } catch (error) {
    if (controller.signal.aborted) return;
    clearClusters();
    byId('cluster-sign-in').hidden = !needsSignIn;
    byId('cluster-summary').textContent = error.message;
  } finally {
    if (request === controller) request = null;
  }
}

profileCard.addEventListener('pointerenter', () => clearTimeout(dismissDelay));
profileCard.addEventListener('pointerleave', scheduleProfileDismiss);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') hideProfile();
  if (!/^[0-9]$/.test(event.key) || page.closest('[hidden]') || document.querySelector('dialog[open]')) return;
  if (event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  if (event.target.isContentEditable || event.target.closest?.('input, textarea, select')) return;
  const group = groups.find(group => group.shortcut === event.key);
  if (group && group !== current) {
    event.preventDefault();
    selectGroup(group);
  }
});
window.addEventListener('hashchange', hideProfile);
window.addEventListener('resize', positionProfile);
window.addEventListener('scroll', positionProfile, {passive: true});
