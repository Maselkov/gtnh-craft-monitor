// Scripts admin dialog: every in-game computer running gcm_agent.lua,
// what it runs and how it's doing, with buttons to update or reboot it
// (server/gcm/agents.py). Refreshes while open.

import { AUTH_USER, closeSettingsMenu } from './auth.js';
import { showToast } from './craft-actions.js';
import {
  delegateActions, escapeHtml, formatDuration, formatQty, formatRelativeTime, onBackdropClick,
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
  return label ? label() : cmd.action;
}

function commandLine(cmd) {
  if (!cmd) return '';
  let text;
  if (cmd.status === 'queued') text = 'waiting for the computer to pick it up…';
  else if (cmd.status === 'sent') text = 'running…';
  else text = cmd.message || (cmd.ok ? 'done' : 'failed');
  const failed = cmd.status === 'done' && !cmd.ok;
  return `<div class="agent-line agent-command${failed ? ' failed' : ''}">${escapeHtml(commandLabel(cmd))}: ${escapeHtml(text)}</div>`;
}

function memoryLine(c) {
  if (c.free_memory == null || !c.total_memory) return '';
  const used = 100 - Math.round(c.free_memory / c.total_memory * 100);
  return `<div class="agent-line">Memory: ${formatQty(c.free_memory)}B free of ${formatQty(c.total_memory)}B</div>
    <div class="stock-bar"><div class="stats-bar-fill" data-fill="${used}"></div></div>`;
}

export function renderComputer(c, latest) {
  const version = c.version ? escapeHtml(c.version) : 'unknown version';
  const behind = c.behind ? ` &middot; update available: ${escapeHtml(latest)}` : '';
  const services = c.services.length
    ? c.services.map(s => `<span><span class="agent-dot${s.running ? ' running' : ''}"></span>${escapeHtml(s.name)}${s.running ? '' : ' (stopped)'}</span>`).join('')
    : '<span class="agent-line">No scripts installed</span>';
  const busy = c.command && c.command.status !== 'done';
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

async function loadAgents() {
  try {
    const res = await fetch('/api/admin/agents');
    if (!res.ok) throw new Error(`agents: ${res.status}`);
    const data = await res.json();
    latestRelease = data.latest_release;
    document.getElementById('agentsLatest').textContent = data.latest_release
      ? `Latest release: ${data.latest_release}. Updating downloads the scripts on the computer, then reboots it.`
      : `Latest release unknown${data.latest_error ? ` (${data.latest_error})` : ''}.`;
    const list = document.getElementById('agentsList');
    list.innerHTML = data.computers.length
      ? data.computers.map(c => renderComputer(c, data.latest_release)).join('')
      : '<div class="empty">No computers yet. In game, run <code>gcm update</code> once on each computer: it installs gcm_agent, which shows the computer here.</div>';
    for (const bar of list.querySelectorAll('.stats-bar-fill')) {
      bar.style.width = `${Number(bar.dataset.fill)}%`;
    }
  } catch (e) {
    document.getElementById('agentsLatest').textContent = 'Could not reach the server.';
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

function openAgentsModal() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  document.getElementById('agentsModal').style.display = 'flex';
  loadAgents();
  clearInterval(refreshTimer);
  refreshTimer = setInterval(loadAgents, REFRESH_MS);
}

export function closeAgentsModal() {
  document.getElementById('agentsModal').style.display = 'none';
  clearInterval(refreshTimer);
  refreshTimer = null;
}

export function setupAgentsActions() {
  delegateActions(document, {
    'open-agents': () => { closeSettingsMenu(); openAgentsModal(); },
    'close-agents': () => closeAgentsModal(),
    'agent-command': (el) => sendCommand(el),
  });
  onBackdropClick('agentsModal', closeAgentsModal);
}
