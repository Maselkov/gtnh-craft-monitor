// Status admin dialog: how fresh each in-game feed is and how the server
// is doing (server/gcm/health.py), then every in-game computer running
// gcm_agent.lua, what it runs and how it's doing, with buttons to update
// or reboot it (server/gcm/agents.py), the checkboxes for restarting
// stopped scripts and updating with the server, and the commands that
// connect a new computer. Refreshes while open. Also the notice a tab
// shows until its script first reports, which opens the dialog there.

import { AUTH_USER, closeSettingsMenu } from './auth.js';
import { showToast } from './craft-actions.js';
import {
  closeNote, delegateActions, escapeHtml, formatDuration, formatQty, formatRelativeTime, noteClosed, onBackdropClick,
} from './util.js';

const REFRESH_MS = 5000;

let refreshTimer = null;
let latestRelease = null;

const COMMAND_LABELS = {
  'update:release': () => `Update to ${latestRelease || 'the latest release'}`,
  'update:main': () => 'Update to main',
  'reboot:': () => 'Reboot',
};

function commandLabel(cmd) {
  const label = COMMAND_LABELS[`${cmd.action}:${cmd.ref || ''}`];
  const text = label ? label() : cmd.action;
  return cmd.auto ? `Auto: ${text.charAt(0).toLowerCase()}${text.slice(1)}` : text;
}

function serviceLabel(s) {
  if (s.gave_up) return ' (stopped, gave up restarting)';
  const restarted = s.restarts ? ` (restarted ${s.restarts}× this hour)` : '';
  return s.running ? restarted : ` (stopped)${restarted}`;
}

function commandLine(cmd) {
  if (!cmd) return '';
  let text;
  if (cmd.status === 'queued') text = 'waiting for the computer to pick it up…';
  else if (cmd.status === 'sent') text = 'running…';
  else if (cmd.awaiting_boot) text = 'rebooting, waiting for it to come back…';
  else if (cmd.back_at) {
    // "Updated v1 -> v2, rebooting." becomes "Updated v1 -> v2, back up 2m ago".
    const before = (cmd.message || '').replace(/,? *rebooting\.$/i, '');
    text = `${before ? `${before}, b` : 'B'}ack up ${formatRelativeTime(cmd.back_at)}`;
  } else text = cmd.message || (cmd.ok ? 'done' : 'failed');
  const failed = cmd.status === 'done' && !cmd.ok;
  return `<div class="agent-line agent-command${failed ? ' failed' : ''}">${escapeHtml(commandLabel(cmd))}: ${escapeHtml(text)}</div>`;
}

function memoryLine(c) {
  if (c.free_memory == null || !c.total_memory) return '';
  const used = 100 - Math.round(c.free_memory / c.total_memory * 100);
  return `<div class="agent-line">Memory: ${formatQty(c.free_memory)}B free of ${formatQty(c.total_memory)}B</div>
    <div class="stock-bar"><div class="stats-bar-fill" data-fill="${used}"></div></div>`;
}

// scriptsVersion: the oldest release with this server's scripts
// (config.SCRIPTS_VERSION). A computer older than that is missing script
// changes the server needs; one only behind the latest release isn't.
export function renderComputer(c, latest, scriptsVersion) {
  const version = c.version ? escapeHtml(c.version) : 'unknown version';
  let behind = '';
  if (c.scripts_behind) behind = ` &middot; <span class="agent-command failed">scripts changed in ${escapeHtml(scriptsVersion)}: update recommended</span>`;
  else if (c.behind) behind = ` &middot; update available: ${escapeHtml(latest)}`;
  const services = c.services.length
    ? c.services.map(s => `<span${s.gave_up ? ' class="agent-command failed"' : ''}><span class="agent-dot${s.running ? ' running' : ''}"></span>${escapeHtml(s.name)}${serviceLabel(s)}</span>`).join('')
    : '<span class="agent-line">No scripts installed</span>';
  const busy = c.command && (c.command.status !== 'done' || c.command.awaiting_boot);
  const address = escapeHtml(c.address);
  const button = (cmd, label) => `<button class="range-btn" data-action="agent-command" data-address="${address}"
    data-cmd="${cmd}"${busy || c.offline ? ' disabled' : ''}>${escapeHtml(label)}</button>`;
  return `
    <div class="agent-card${c.offline ? ' offline' : ''}">
      <div><span class="agent-name">Computer ${escapeHtml(c.address.slice(0, 8))}</span>${c.offline ? '<span class="status-badge incomplete">Offline</span>' : ''}</div>
      <div class="agent-line">${version}${behind}</div>
      <div class="agent-services">${services}</div>
      ${memoryLine(c)}
      <div class="agent-line">Last seen ${formatRelativeTime(c.last_seen)}${c.uptime != null ? ` &middot; up ${formatDuration(c.uptime)}` : ''}</div>
      ${commandLine(c.command)}
      <div class="agent-buttons">
        ${button('update:release', COMMAND_LABELS['update:release']())}
        ${button('update:main', 'Update to main')}
        ${button('reboot:', 'Reboot')}
      </div>
    </div>
  `;
}

function ago(seconds) {
  return seconds == null ? 'never' : `${formatDuration(seconds)} ago`;
}

function bytes(n) {
  return n == null ? '?' : `${formatQty(n)}B`;
}

// The one-line detail after each feed's age.
const FEED_DETAILS = {
  crafts: (f) => [`${f.cpus} CPU${f.cpus === 1 ? '' : 's'}`],
  network: (f) => [
    f.items ? `${f.items.toLocaleString()} entries` : null,
    f.scan_seconds != null ? `scan took ${formatDuration(f.scan_seconds)}` : null,
    f.scanning_for != null ? `scanning for ${formatDuration(f.scanning_for)}` : null,
    f.levels_age_seconds != null ? `stock rules checked ${ago(f.levels_age_seconds)}` : null,
    f.reconstructed ? 'restored from before the restart' : null,
  ],
  patterns: (f) => [`${f.patterns.toLocaleString()} patterns`],
};

function feedProblem(f) {
  if (f.crash) return `crashed during ${f.crash.phase}: ${f.crash.error}`;
  if (f.rejected) return `last scan turned away: ${f.rejected.reason}`;
  return null;
}

export function renderHealth(h) {
  const feeds = h.feeds.map((f) => {
    const details = (FEED_DETAILS[f.key] || (() => []))(f).filter(Boolean);
    const problem = feedProblem(f);
    return `<div class="health-feed">
      <span class="agent-dot ${f.ok ? 'running' : 'warn'}"></span><span class="health-label">${escapeHtml(f.label)}</span>
      <span class="agent-line">${escapeHtml(f.age_seconds == null ? `nothing from ${f.script} yet` : `updated ${ago(f.age_seconds)}`)}${
        details.length ? ' &middot; ' + details.map(escapeHtml).join(' &middot; ') : ''}</span>
      ${problem ? `<div class="agent-line agent-command failed">${escapeHtml(problem)}</div>` : ''}
    </div>`;
  }).join('');

  const files = h.storage.files.map(f => `${escapeHtml(f.name)} ${bytes(f.bytes)}`).join(' &middot; ');
  const free = h.storage.free_bytes != null ? ` &middot; ${bytes(h.storage.free_bytes)} free` : '';

  const r = h.retention;
  const thinning = !r.enabled ? 'History thinning is off.'
    : r.last_run
      ? `History thinned ${ago(r.last_run.age_seconds)}: ${
        Object.values(r.last_run.deleted).reduce((a, b) => a + b, 0).toLocaleString()} rows removed.`
      : 'History thinning hasn\'t run since the server started (it runs 5 minutes in, then daily).';

  const w = h.webhooks;
  let hooks = 'no webhooks';
  if (w.configured) {
    hooks = `${w.configured} webhook${w.configured === 1 ? '' : 's'}`;
    if (w.last) {
      hooks += `, last post ${ago(w.last.age_seconds)}`;
      if (w.last.failed.length) hooks += ` - failed: ${w.last.failed.map(x => `${x.host} (${x.error})`).join(', ')}`;
    } else {
      hooks += ', nothing posted since the server started';
    }
  }
  const webhookFailed = w.last && w.last.failed.length;

  return `${feeds}
    <div class="agent-line">Databases: ${files}${free}</div>
    <div class="agent-line">${escapeHtml(thinning)}</div>
    <div class="agent-line${webhookFailed ? ' agent-command failed' : ''}">Notifications: ${
      h.push_subscriptions} browser${h.push_subscriptions === 1 ? '' : 's'} with push &middot; ${escapeHtml(hooks)}</div>
    <div class="agent-line">Up ${formatDuration(h.uptime_seconds)} &middot; ${
      h.version ? `version ${escapeHtml(h.version)}` : 'run from source'} &middot; game data ${escapeHtml(h.gamedata || 'none')}</div>`;
}

async function loadHealth() {
  const el = document.getElementById('serverHealth');
  try {
    const res = await fetch('/api/admin/health');
    if (!res.ok) throw new Error(`health: ${res.status}`);
    el.innerHTML = renderHealth(await res.json());
  } catch (e) {
    el.textContent = 'Could not reach the server.';
  }
}

async function loadAgents() {
  try {
    const res = await fetch('/api/admin/agents');
    if (!res.ok) throw new Error(`agents: ${res.status}`);
    const data = await res.json();
    latestRelease = data.latest_release;
    document.getElementById('agentsLatest').textContent = data.latest_release
      ? `Latest release: ${data.latest_release}. Updating downloads the scripts on the computer, then reboots it.`
      : `Latest release unknown${data.latest_error ? ` (${data.latest_error})` : ''}.`;
    updateScriptsNotice();
    const list = document.getElementById('agentsList');
    list.innerHTML = data.computers.length
      ? data.computers.map(c => renderComputer(c, data.latest_release, data.scripts_version)).join('')
      : '<div class="empty">No computers yet. Connect one with the commands below. A computer installed before gcm_agent existed shows up after one <code>gcm update</code>.</div>';
    for (const bar of list.querySelectorAll('.stats-bar-fill')) {
      bar.style.width = `${Number(bar.dataset.fill)}%`;
    }
  } catch (e) {
    document.getElementById('agentsLatest').textContent = 'Could not reach the server.';
  }
}

// The Status dialog's checkboxes (server/gcm/agents.py SETTINGS_DEFAULTS).
// Auto-update needs the server to know its own release.
const SETTING_TOGGLES = { auto_restart: 'autoRestartToggle', auto_update: 'autoUpdateToggle' };
const RELEASE = /^v\d+\.\d+\.\d+$/;

function showSettings(data) {
  const released = RELEASE.test(data.server_version || '');
  for (const [key, id] of Object.entries(SETTING_TOGGLES)) {
    const box = document.getElementById(id);
    box.checked = data[key];
    box.disabled = key === 'auto_update' && !released;
  }
  document.getElementById('autoUpdateNote').textContent = released
    ? `Brings a computer on an older release to this server's, ${data.server_version}, and reboots it. Only once that's also the latest release.`
    : 'Needs a server that knows its release, as the published Docker image does.';
}

async function loadSettings() {
  try {
    const res = await fetch('/api/admin/agent-settings');
    if (!res.ok) throw new Error(`agent-settings: ${res.status}`);
    showSettings(await res.json());
  } catch (e) {
    // The rest of the dialog says the server can't be reached.
  }
}

async function saveSetting(key, box) {
  let error = 'Could not reach the server.';
  try {
    const res = await fetch('/api/admin/agent-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: box.checked }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showSettings(data);
      return;
    }
    error = data.error || 'Could not save the setting.';
  } catch (e) {
    // error stays "Could not reach the server."
  }
  box.checked = !box.checked;
  showToast(error, true);
}

// "Connect a computer": the command that installs the scripts on a new
// computer with this server's address and API key. The key is masked
// on screen, for streams and screenshots, but always copied in full.
const GCM_URL = 'https://raw.githubusercontent.com/Maselkov/gtnh-craft-monitor/main/oc/gcm.lua';
const LOCAL_HOST = /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/;

let connectInfo = null;
let connectKeyShown = false;

// Addresses OpenComputers' default internet blacklist refuses.
export function isLocalAddress(url) {
  try {
    return LOCAL_HOST.test(new URL(url).hostname);
  } catch (e) {
    return false;
  }
}

// One word for the OpenOS shell: as it is, or quoted if it has anything
// the shell might treat specially.
function shellWord(s) {
  return /^[\w.:/@%+=~-]+$/.test(s) ? s : `"${s}"`;
}

export function connectCommand(serverUrl, apiKey) {
  const url = serverUrl.trim().replace(/\/+$/, '');
  return `wget -f ${GCM_URL} /tmp/gcm.lua && `
    + `/tmp/gcm.lua install --server=${shellWord(url)} --api-key=${shellWord(apiKey)}`;
}

function renderConnect() {
  if (!connectInfo) return;
  const url = document.getElementById('connectServerUrl').value;
  const command = connectCommand(url, connectInfo.api_key);
  document.getElementById('connectCommand').textContent = connectKeyShown
    ? command : command.replace(connectInfo.api_key, '•'.repeat(12));
  document.getElementById('connectLocalNote').style.display = isLocalAddress(url) ? '' : 'none';
  document.getElementById('connectKeyBtn').textContent = connectKeyShown ? 'Hide API key' : 'Show API key';
}

async function loadConnect() {
  try {
    const res = await fetch('/api/admin/connect');
    if (!res.ok) throw new Error(`connect: ${res.status}`);
    connectInfo = await res.json();
  } catch (e) {
    document.getElementById('connectCommand').textContent = 'Could not reach the server.';
    return;
  }
  const input = document.getElementById('connectServerUrl');
  if (!input.value) input.value = connectInfo.public_url || location.origin;
  renderConnect();
}

// navigator.clipboard only exists on HTTPS or localhost, and this page is
// often opened over plain HTTP on a LAN; execCommand still works there.
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const field = document.createElement('textarea');
    field.value = text;
    field.style.position = 'fixed';
    field.style.opacity = '0';
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand('copy');
    field.remove();
    return copied;
  }
}

async function copyConnect() {
  if (!connectInfo) return;
  const url = document.getElementById('connectServerUrl').value;
  if (await copyText(connectCommand(url, connectInfo.api_key))) {
    showToast('Copied. Paste it into the computer with the middle mouse button or Insert.');
  } else {
    showToast('Could not copy: show the API key and copy the command by hand.', true);
  }
}

async function sendCommand(el) {
  const [action, ref] = el.dataset.cmd.split(':');
  const label = COMMAND_LABELS[el.dataset.cmd]();
  if (!confirm(`${label}: computer ${el.dataset.address.slice(0, 8)}? Its scripts stop while it reboots.`)) return;
  try {
    const res = await fetch(`/api/admin/agents/${encodeURIComponent(el.dataset.address)}/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ref: ref || null }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) showToast(data.error || 'Could not send the command.', true);
  } catch (e) {
    showToast('Could not reach the server.', true);
  }
  loadAgents();
}

// A tab's empty state until its script first reports: admins get a
// button to Connect a computer, everyone else is told to ask one
// (app.css hides one of the two by body.is-admin). index.html has the
// same markup for the Power and Network tabs.
export function connectNoteHtml(text) {
  return `<div class="connect-note">
    <span>${escapeHtml(text)}</span>
    <button class="range-btn admin-only" data-action="connect-computer">Connect a computer</button>
    <span class="non-admin-only">An admin can connect one under Settings &rarr; Status.</span>
  </div>`;
}

// toConnect scrolls to Connect a computer once the sections above it
// have loaded, so it doesn't move off screen as they fill in; toComputers
// to the computers.
async function openAgentsModal({ toConnect = false, toComputers = false } = {}) {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  document.getElementById('agentsModal').style.display = 'flex';
  loadSettings();
  loadConnect();
  clearInterval(refreshTimer);
  refreshTimer = setInterval(() => { loadHealth(); loadAgents(); }, REFRESH_MS);
  await Promise.all([loadHealth(), loadAgents()]);
  if (toConnect) document.querySelector('#agentsModal .connect-heading').scrollIntoView({ block: 'start' });
  if (toComputers) document.querySelector('#agentsModal .computers-heading').scrollIntoView({ block: 'start' });
}

function editConnectAddress() {
  document.getElementById('connectAddress').hidden = false;
  document.getElementById('connectAddressBtn').hidden = true;
  const input = document.getElementById('connectServerUrl');
  input.focus();
  input.select();
}

export function closeAgentsModal() {
  document.getElementById('agentsModal').style.display = 'none';
  connectKeyShown = false;
  document.getElementById('connectAddress').hidden = true;
  document.getElementById('connectAddressBtn').hidden = false;
  clearInterval(refreshTimer);
  refreshTimer = null;
}

// The page-wide notice that computers run scripts older than this
// server's (server/gcm/agents.py scripts_behind()), so admins hear of it
// without opening Status. Closing it holds for that scripts version: the
// next release that changes the scripts brings it back.
const SCRIPTS_NOTICE_MS = 5 * 60 * 1000;
const SCRIPTS_NOTICE_DISMISSED_KEY = 'gtnhCraftMonitor.scriptsNoticeDismissed';
let scriptsNotice = null;

function renderScriptsNotice() {
  const el = document.getElementById('scriptsNotice');
  const n = scriptsNotice;
  const admin = Boolean(AUTH_USER && AUTH_USER.role === 'admin');
  if (!admin || !n || !n.version || noteClosed(SCRIPTS_NOTICE_DISMISSED_KEY, n.version)) {
    el.hidden = true;
    return;
  }
  const count = n.computers.length;
  document.getElementById('scriptsNoticeText').textContent =
    `${count === 1 ? 'A computer runs' : `${count} computers run`} in-game scripts older than ${n.version}, which this server needs for everything to work.`;
  document.getElementById('scriptsNoticeChanges').href =
    `https://github.com/Maselkov/gtnh-craft-monitor/releases/tag/${encodeURIComponent(n.version)}`;
  el.hidden = false;
}

export async function updateScriptsNotice() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') {
    scriptsNotice = null;
    renderScriptsNotice();
    return;
  }
  try {
    const res = await fetch('/api/admin/scripts-notice');
    if (res.ok) scriptsNotice = await res.json();
  } catch (e) { /* keep what was shown */ }
  renderScriptsNotice();
}

function dismissScriptsNotice() {
  closeNote(SCRIPTS_NOTICE_DISMISSED_KEY, scriptsNotice.version);
  renderScriptsNotice();
}

export function setupAgentsActions() {
  setInterval(updateScriptsNotice, SCRIPTS_NOTICE_MS);
  delegateActions(document, {
    'scripts-notice-update': () => openAgentsModal({ toComputers: true }),
    'dismiss-scripts-notice': () => dismissScriptsNotice(),
    'open-agents': () => { closeSettingsMenu(); openAgentsModal(); },
    'connect-computer': () => openAgentsModal({ toConnect: true }),
    'close-agents': () => closeAgentsModal(),
    'agent-command': (el) => sendCommand(el),
    'copy-connect': () => copyConnect(),
    'edit-connect-address': () => editConnectAddress(),
    'toggle-connect-key': () => { connectKeyShown = !connectKeyShown; renderConnect(); },
  });
  document.getElementById('connectServerUrl').addEventListener('input', renderConnect);
  for (const [key, id] of Object.entries(SETTING_TOGGLES)) {
    const box = document.getElementById(id);
    box.addEventListener('change', () => saveSetting(key, box));
  }
  onBackdropClick('agentsModal', closeAgentsModal);
}
