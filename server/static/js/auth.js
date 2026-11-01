// Sign-in state, admin user/token management modals, settings menu.

import { updateScriptsNotice } from './agents.js';
import { updateRecipeNotice } from './recipe-check.js';
import { showToast } from './craft-actions.js';
import {
  clearPins,
  dropPushSubscription,
  lastData,
  refreshPinsAndCompletions,
  render,
  syncPushSubscription,
  updateNotifToggle,
} from './crafts.js';
import { openGameDataModal, updateGameDataNotice } from './gamedata.js';
import { updateGameDataNote, updateTerminalNote } from './network.js';
import { nameText } from './description.js';
import {
  dayLabel, delegateActions, escapeHtml, formatQty, formatRelativeTime, iconClass, iconUrl, loadDismissedNotes,
  onBackdropClick,
} from './util.js';

export let AUTH_USER = null;
// Only signed-in users may see the crafts, network and power
// (server/gcm/auth.py is_private).
let SERVER_PRIVATE = false;
let dataVisibleListeners = [];

function isLocked() {
  return SERVER_PRIVATE && !AUTH_USER;
}

// Runs fn once the crafts, network and power can be fetched: at once, or
// on a private server once signed in.
export function onceDataVisible(fn) {
  if (isLocked()) dataVisibleListeners.push(fn);
  else fn();
}

// Mirrors auth.is_operator on the server: who may request or cancel crafts.
export function isOperator() {
  return Boolean(AUTH_USER && (AUTH_USER.role === 'operator' || AUTH_USER.role === 'admin'));
}

function updateAuthenticationControl() {
  const input = document.getElementById('accessTokenInput');
  const button = document.getElementById('authBtn');
  const adminButton = document.getElementById('adminBtn');
  input.style.display = AUTH_USER ? 'none' : '';
  input.value = '';
  button.style.display = AUTH_USER ? 'none' : '';
  // Signed out, the settings menu holds just the Theme.
  document.getElementById('settingsUser').textContent = AUTH_USER ? `Signed in as ${AUTH_USER.display_name}` : '';
  document.getElementById('notifToggle').style.display = AUTH_USER ? '' : 'none';
  document.getElementById('signOutBtn').style.display = AUTH_USER ? '' : 'none';
  if (!AUTH_USER) closeSettingsMenu();
  const adminVisible = AUTH_USER && AUTH_USER.role === 'admin';
  adminButton.style.display = adminVisible ? '' : 'none';
  document.body.classList.toggle('is-admin', Boolean(adminVisible));
  document.getElementById('gamedataBtn').style.display = adminVisible ? '' : 'none';
  document.getElementById('patternCheckBtn').style.display = isOperator() ? '' : 'none';
  document.getElementById('webhooksBtn').style.display = adminVisible ? '' : 'none';
  document.getElementById('backupBtn').style.display = adminVisible ? '' : 'none';
  document.getElementById('agentsBtn').style.display = adminVisible ? '' : 'none';
  if (AUTH_USER) syncPushSubscription();
  updateGameDataNote();
  // The notices wait for which of them someone dismissed.
  loadDismissedNotes(isOperator()).then(() => {
    updateScriptsNotice();
    updateGameDataNotice();
    updateRecipeNotice();
    updateTerminalNote();
  });
  const locked = isLocked();
  document.body.classList.toggle('private-locked', locked);
  document.getElementById('privateNotice').style.display = locked ? '' : 'none';
  if (locked) {
    input.focus();
  } else {
    const listeners = dataVisibleListeners;
    dataVisibleListeners = [];
    for (const fn of listeners) fn();
  }
}

export async function loadAuthentication() {
  const response = await fetch('/api/auth/session');
  const data = await response.json();
  AUTH_USER = data.authenticated ? data.user : null;
  SERVER_PRIVATE = Boolean(data.private);
  updateAuthenticationControl();
  if (data.setup_required) showSetupModal();
}

// The server makes the zip first (a few seconds with a lot of history),
// so a failure can be shown here; then the browser downloads it.
let backingUp = false;
async function downloadBackup() {
  if (backingUp) return;
  backingUp = true;
  showToast('Making a backup…');
  try {
    const response = await fetch('/api/admin/backup', { method: 'POST' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.error || 'Could not make a backup.', true);
      return;
    }
    const link = document.createElement('a');
    link.href = `/api/admin/backup/${encodeURIComponent(data.id)}`;
    link.download = data.name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    showToast(`Downloading ${data.name} (${formatQty(data.bytes)}B).`);
  } catch (e) {
    showToast('Could not reach the server.', true);
  } finally {
    backingUp = false;
  }
}

function openBackupsModal() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  document.getElementById('restoreFile').value = '';
  resetRestoreButton();
  document.getElementById('backupsModal').style.display = 'flex';
}

export function closeBackupsModal() {
  if (restoring) return;
  document.getElementById('backupsModal').style.display = 'none';
}

function showRestoreError(message) {
  const el = document.getElementById('restoreError');
  el.textContent = message || '';
  el.style.display = message ? '' : 'none';
}

let restoreArmed = false;
function resetRestoreButton() {
  restoreArmed = false;
  const button = document.getElementById('restoreBtn');
  const file = document.getElementById('restoreFile').files[0];
  button.textContent = 'Restore';
  button.disabled = !file;
  document.getElementById('restoreFileName').textContent = file ? file.name : 'No file chosen';
  showRestoreError('');
}

// The first click arms it, the second sends. On success everything the
// page holds may be stale (and the session gone), so it reloads.
let restoring = false;
async function restoreBackup() {
  const file = document.getElementById('restoreFile').files[0];
  const button = document.getElementById('restoreBtn');
  if (!file || restoring) return;
  if (!restoreArmed) {
    restoreArmed = true;
    button.textContent = 'Click again to restore';
    return;
  }
  restoring = true;
  button.disabled = true;
  button.textContent = 'Restoring…';
  showRestoreError('');
  try {
    const response = await fetch('/api/admin/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      restoring = false;
      resetRestoreButton();
      showRestoreError(data.error || 'Could not restore that backup.');
      return;
    }
    showToast(data.signed_in ? 'Backup restored. Reloading…' : 'Backup restored. Sign in again.');
    setTimeout(() => location.reload(), 1000);
  } catch (e) {
    restoring = false;
    resetRestoreButton();
    showRestoreError('Could not reach the server.');
  }
}

async function submitAccessToken() {
  const input = document.getElementById('accessTokenInput');
  const token = input.value.trim();
  if (!token) return;
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    input.value = '';
    input.placeholder = data.error || 'Sign-in failed';
    return;
  }
  AUTH_USER = data.user;
  updateAuthenticationControl();
  await refreshPinsAndCompletions();
  if (lastData) render(lastData);
}

// What each role may do, under the role picker and on each user's badge
// (docs/server.md has the same table).
const ROLE_HELP = {
  viewer: 'Sees the crafts, network and power, and keeps their own pins and completion history.',
  operator: 'Also requests and cancels crafts, sets stock targets, and sees the last game update\'s pattern check.',
  admin: 'Also manages users, computers, game data, webhooks and backups.',
};

// The token just made, shown once in its user's row until the dialog
// closes or the list changes some other way.
let shownToken = null;

function updateRoleHelp() {
  const role = document.getElementById('adminUserRole').value;
  document.getElementById('adminRoleHelp').textContent = ROLE_HELP[role] || '';
}

async function openAdminUsersModal() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  shownToken = null;
  document.getElementById('adminUsersModal').style.display = 'flex';
  document.getElementById('adminUsersError').style.display = 'none';
  document.getElementById('privateToggle').checked = SERVER_PRIVATE;
  updateRoleHelp();
  await refreshAdminUsers();
  document.getElementById('adminUserName').focus();
}

export function closeAdminUsersModal() {
  shownToken = null;
  document.getElementById('adminUsersModal').style.display = 'none';
}

// "Nov 1", or "Nov 1, 2025" for another year.
function formatTokenDate(seconds) {
  const date = new Date(seconds * 1000);
  const year = date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric';
  return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year });
}

function adminTokenRowHtml(user, token) {
  const used = token.last_used_at ? `used ${formatRelativeTime(token.last_used_at)}` : 'never used';
  return `<div class="admin-token-row" title="${escapeHtml(token.id)}">
    <span class="admin-token-meta">Token made ${formatTokenDate(token.created_at)} · ${used}</span>
    <button class="admin-token-revoke" data-action="revoke-token" data-token-id="${escapeHtml(token.id)}"
      data-display-name="${escapeHtml(user.display_name)}">Revoke</button>
  </div>`;
}

function adminNewTokenHtml(token) {
  return `<div id="adminTokenResult" class="admin-token-result">
    <div class="modal-label">New access token: share it securely, it's shown only once.</div>
    <textarea id="adminTokenValue" class="modal-amount-input admin-token-value" readonly>${escapeHtml(token)}</textarea>
    <div class="modal-buttons">
      <button class="modal-btn secondary" data-action="copy-token">Copy token</button>
    </div>
  </div>`;
}

function adminUserRowHtml(user) {
  const id = escapeHtml(user.id);
  const name = escapeHtml(user.display_name);
  const isMe = AUTH_USER && user.id === AUTH_USER.id;
  const isNew = shownToken && shownToken.userId === user.id;
  const tokens = (user.tokens || []).map((token) => adminTokenRowHtml(user, token)).join('')
    || '<div class="admin-token-row">No active tokens: they can\'t sign in.</div>';
  return `<div class="admin-user-row${isNew ? ' new' : ''}" data-user-id="${id}">
    <div class="admin-user-main">
      <div class="admin-user-title">
        <span class="admin-user-name" title="${name}">${name}</span>${isMe ? '<span class="admin-user-you">you</span>' : ''}
      </div>
      <div class="admin-token-list">${tokens}</div>
    </div>
    <div class="admin-user-role-cell">
      <span class="badge role-badge ${escapeHtml(user.role)}" title="${escapeHtml(ROLE_HELP[user.role] || '')}">${escapeHtml(user.role)}</span>
      ${user.disabled_at ? '<span class="badge role-badge disabled">disabled</span>' : ''}
    </div>
    <div class="admin-user-actions">
      <button class="range-btn" data-action="user-history" data-user-id="${id}" data-role="${escapeHtml(user.role)}">History</button>
      <button class="range-btn" data-action="regenerate-token" data-user-id="${id}" data-display-name="${name}">New token</button>
      ${isMe ? '' : `<button class="range-btn admin-delete-btn" data-action="delete-user" data-user-id="${id}" data-display-name="${name}">Delete</button>`}
    </div>
    ${isNew ? adminNewTokenHtml(shownToken.token) : ''}
  </div>`;
}

async function refreshAdminUsers() {
  const list = document.getElementById('adminUsersList');
  const count = document.getElementById('adminUsersCount');
  if (!list.children.length) list.textContent = 'Loading users...';
  const response = await fetch('/api/admin/users');
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    list.textContent = data.error || 'Could not load users.';
    count.textContent = '';
    return;
  }
  count.textContent = `(${data.users.length})`;
  list.innerHTML = data.users.map(adminUserRowHtml).join('') || '<div class="admin-users-empty">No users yet.</div>';
  list.querySelector('.admin-user-row.new')?.scrollIntoView({ block: 'nearest' });
}

export function setupAuthControls() {
  // Close the settings dropdown on any click outside it.
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#settingsWrap')) closeSettingsMenu();
  });

  document.getElementById('signInForm').addEventListener('submit', (e) => {
    e.preventDefault();
    submitAccessToken();
  });
  for (const id of ['setupName', 'setupCode']) {
    document.getElementById(id).addEventListener('keydown', (e) => {
      if (e.key === 'Enter') finishSetup();
    });
  }
  delegateActions(document, {
    'toggle-settings': () => toggleSettingsMenu(),
    'open-admin': () => { closeSettingsMenu(); openAdminUsersModal(); },
    'open-backups': () => { closeSettingsMenu(); openBackupsModal(); },
    'close-backups': () => closeBackupsModal(),
    'download-backup': () => downloadBackup(),
    'restore-backup': () => restoreBackup(),
    'sign-out': () => { closeSettingsMenu(); signOut(); },
    'finish-setup': () => finishSetup(),
    'copy-setup-token': () => copySetupToken(),
    'close-setup': () => closeSetup(),
    'close-admin': () => closeAdminUsersModal(),
    'create-user': () => createAdminUser(),
    'copy-token': () => copyAdminToken(),
    'close-user-history': () => closeUserHistoryModal(),
    'user-history-filter': (el) => { historyFilter = el.dataset.filter; renderUserHistory(); },
    'user-history-auto': () => { historyShowAuto = !historyShowAuto; loadUserHistory(); },
  });
  document.getElementById('adminUserRole').addEventListener('change', updateRoleHelp);
  document.getElementById('adminUserName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') createAdminUser();
  });
  const privateToggle = document.getElementById('privateToggle');
  privateToggle.addEventListener('change', () => savePrivate(privateToggle));
  onBackdropClick('adminUsersModal', closeAdminUsersModal);
  onBackdropClick('userHistoryModal', closeUserHistoryModal);
  onBackdropClick('backupsModal', closeBackupsModal);
  document.getElementById('restoreFile').addEventListener('change', resetRestoreButton);
}

export function setupAdminActions() {
  delegateActions(document.getElementById('adminUsersList'), {
    'revoke-token': (el) => revokeAdminToken(el.dataset.tokenId, el.dataset.displayName),
    'user-history': (el) => openUserHistory(el.dataset.userId, el.dataset.role),
    'regenerate-token': (el) => regenerateAdminToken(el.dataset.userId, el.dataset.displayName),
    'delete-user': (el) => deleteAdminUser(el.dataset.userId, el.dataset.displayName),
  });
}

// The user history dialog: one user's craft requests and cancels
// (store.requests.user_activity), newest first, filtered on the page.
// Their stock targets' requests only on asking: the server made those.
let historyUser = null;
let historyEvents = [];
let historyFilter = 'all';
let historyShowAuto = false;
let historyAutoCount = 0;

function closeUserHistoryModal() {
  document.getElementById('userHistoryModal').style.display = 'none';
}

// [badge class, text] for how a request or cancel turned out.
function historyOutcome(e) {
  if (e.status === 'pending') return ['waiting', 'Waiting'];
  if (e.type === 'cancel') return e.success ? ['cancelled', 'Cancelled'] : ['failed', 'Not cancelled'];
  return e.status === 'accepted' ? ['started', 'Started'] : ['failed', 'Failed'];
}

function historyMatches(e) {
  if (historyFilter === 'problem') return historyOutcome(e)[0] === 'failed';
  return historyFilter === 'all' || e.type === historyFilter;
}

function userHistoryRowHtml(e) {
  const icon = e.icon
    ? `<img class="${iconClass('craft-icon', e.icon)}" src="${iconUrl(e.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '<span class="craft-icon"></span>';
  const [outcome, outcomeText] = historyOutcome(e);
  const amount = e.type === 'request' && e.amount != null
    ? `<span class="user-history-amount">${formatQty(e.amount)}${e.kind === 'fluid' ? ' mB' : ''}</span> `
    : '';
  const name = e.target ? escapeHtml(nameText(e.target)) : 'A craft';
  const what = e.type === 'cancel'
    ? `Cancel on CPU ${escapeHtml(e.cpu_name || '?')}`
    : `Request${e.cpu_name ? ` &middot; CPU ${escapeHtml(e.cpu_name)}` : ''}`;
  const auto = e.source === 'auto'
    ? '<span class="status-badge auto" title="Asked for by a keep-in-stock target">Auto</span>' : '';
  const when = new Date(e.created_at * 1000).toLocaleString();
  return `<div class="history-row user-history-row">
    ${icon}
    <div class="history-row-main">
      <div class="history-row-name">${amount}${name}${auto}<span class="status-badge ${outcome}">${outcomeText}</span></div>
      <div class="cpu-id">${what}</div>
      ${e.reason ? `<div class="user-history-reason">${escapeHtml(e.reason)}</div>` : ''}
    </div>
    <div class="history-row-when" title="${escapeHtml(when)}">${formatRelativeTime(e.created_at)}</div>
  </div>`;
}

function renderUserHistory() {
  for (const button of document.querySelectorAll('#userHistoryFilters [data-filter]')) {
    button.classList.toggle('active', button.dataset.filter === historyFilter);
  }
  let html = '';
  let lastDay = null;
  for (const e of historyEvents.filter(historyMatches)) {
    const day = dayLabel(e.created_at);
    if (day !== lastDay) {
      html += `<div class="history-day">${escapeHtml(day)}</div>`;
      lastDay = day;
    }
    html += userHistoryRowHtml(e);
  }
  const none = historyEvents.length ? 'Nothing matches.'
    : historyAutoCount ? 'No craft requests or cancels of their own; Stock refills shows their stock targets\' requests.'
      : 'No craft requests or cancels yet.';
  document.getElementById('userHistoryList').innerHTML = html || `<div class="empty">${none}</div>`;
}

function openUserHistory(userId, role) {
  document.getElementById('userHistoryModal').style.display = 'flex';
  document.getElementById('userHistoryTitle').textContent = 'User history';
  document.getElementById('userHistorySummary').textContent = '';
  document.getElementById('userHistoryAuto').hidden = true;
  historyUser = { id: userId, role };
  historyEvents = [];
  historyFilter = 'all';
  historyShowAuto = false;
  loadUserHistory();
}

async function loadUserHistory() {
  const user = historyUser;
  const list = document.getElementById('userHistoryList');
  if (!historyEvents.length) list.textContent = 'Loading history...';
  const query = historyShowAuto ? '?auto=1' : '';
  const response = await fetch(`/api/admin/users/${encodeURIComponent(user.id)}/history${query}`);
  const data = await response.json().catch(() => ({}));
  if (user !== historyUser) return;  // another user's was opened meanwhile
  if (!response.ok) {
    list.textContent = data.error || 'Could not load history.';
    return;
  }
  historyEvents = data.events;
  const own = historyEvents.filter((e) => e.source !== 'auto');
  const requests = own.filter((e) => e.type === 'request').length;
  const failed = own.filter((e) => historyOutcome(e)[0] === 'failed').length;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  document.getElementById('userHistoryTitle').textContent = data.user.display_name;
  document.getElementById('userHistorySummary').textContent = [
    user.role ? user.role.charAt(0).toUpperCase() + user.role.slice(1) : '',
    plural(requests, 'request'),
    plural(own.length - requests, 'cancel'),
    failed ? `${failed} failed` : '',
  ].filter(Boolean).join(' · ');
  historyAutoCount = data.auto_count;
  const auto = document.getElementById('userHistoryAuto');
  auto.hidden = !data.auto_count;
  auto.textContent = `Stock refills (${formatQty(data.auto_count)})`;
  auto.classList.toggle('active', historyShowAuto);
  renderUserHistory();
}

async function createAdminUser() {
  const name = document.getElementById('adminUserName').value.trim();
  const role = document.getElementById('adminUserRole').value;
  const error = document.getElementById('adminUsersError');
  const button = document.getElementById('adminCreateUserBtn');
  error.style.display = 'none';
  if (!name) {
    error.textContent = 'Enter a display name.';
    error.style.display = 'block';
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch('/api/admin/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_name: name, role }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      error.textContent = data.error || 'Could not create user.';
      error.style.display = 'block';
      return;
    }
    document.getElementById('adminUserName').value = '';
    shownToken = { userId: data.user.id, token: data.token };
    await refreshAdminUsers();
  } finally {
    button.disabled = false;
  }
}

async function savePrivate(box) {
  let error = 'Could not reach the server.';
  try {
    const response = await fetch('/api/admin/visibility', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ private: box.checked }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      SERVER_PRIVATE = data.private;
      showToast(SERVER_PRIVATE ? 'Only signed-in users can see this base now.' : 'Anyone with the link can see this base now.');
      return;
    }
    error = data.error || 'Could not save the setting.';
  } catch (e) {
    // error stays "Could not reach the server."
  }
  box.checked = !box.checked;
  showToast(error, true);
}

async function revokeAdminToken(tokenId, displayName) {
  if (!window.confirm(`Revoke this token of ${displayName}? Any sessions created with it will end immediately.`)) return;
  const response = await fetch(`/api/admin/tokens/${encodeURIComponent(tokenId)}/revoke`, {
    method: 'POST',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showToast(data.error || 'Could not revoke token.', true);
    return;
  }
  showToast('Access token revoked.');
  shownToken = null;
  await refreshAdminUsers();
}

async function regenerateAdminToken(userId, displayName) {
  if (!window.confirm(`Replace ${displayName}'s access token? Their current token and sessions stop working immediately.`)) return;
  const response = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/tokens`, {
    method: 'POST',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showToast(data.error || 'Could not generate a new token.', true);
    return;
  }
  shownToken = { userId, token: data.token };
  showToast(`New token generated for ${displayName}.`);
  await refreshAdminUsers();
}

async function deleteAdminUser(userId, displayName) {
  if (!window.confirm(`Delete ${displayName}? This removes their account and tokens permanently.`)) return;
  const response = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showToast(data.error || 'Could not delete user.', true);
    return;
  }
  showToast(`${displayName} deleted.`);
  if (shownToken?.userId === userId) shownToken = null;
  await refreshAdminUsers();
}

async function copyAdminToken() {
  const field = document.getElementById('adminTokenValue');
  try {
    await navigator.clipboard.writeText(field.value);
    showToast('Access token copied.');
  } catch (e) {
    field.focus();
    field.select();
  }
}

// No users yet: the server printed a setup link, /#setup=<code>, to its
// log. The code is taken out of the address bar straight away so it
// isn't left in the history.
function showSetupModal() {
  const match = /(?:^#|&)setup=([^&]+)/.exec(location.hash);
  if (match) {
    document.getElementById('setupCode').value = decodeURIComponent(match[1]);
    history.replaceState(null, '', location.pathname + location.search);
  }
  document.getElementById('setupModal').style.display = 'flex';
  document.getElementById('setupName').focus();
}

async function finishSetup() {
  const button = document.getElementById('setupBtn');
  const error = document.getElementById('setupError');
  const displayName = document.getElementById('setupName').value.trim();
  const code = document.getElementById('setupCode').value.trim();
  if (!displayName || !code) {
    error.textContent = 'Enter your name and the setup code.';
    error.style.display = 'block';
    return;
  }
  button.disabled = true;
  error.style.display = 'none';
  try {
    const response = await fetch('/api/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, display_name: displayName }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      error.textContent = data.error || 'Setup failed.';
      error.style.display = 'block';
      return;
    }
    AUTH_USER = data.user;
    document.getElementById('setupToken').value = data.token;
    document.getElementById('setupForm').style.display = 'none';
    document.getElementById('setupResult').style.display = 'block';
  } catch (e) {
    error.textContent = 'Could not reach the server.';
    error.style.display = 'block';
  } finally {
    button.disabled = false;
  }
}

// Then straight on to the one other thing a new server needs: which GTNH
// version's icons and item list to use.
async function closeSetup() {
  document.getElementById('setupModal').style.display = 'none';
  document.getElementById('setupToken').value = '';
  updateAuthenticationControl();
  openGameDataModal();
  await refreshPinsAndCompletions();
  if (lastData) render(lastData);
}

async function copySetupToken() {
  const field = document.getElementById('setupToken');
  try {
    await navigator.clipboard.writeText(field.value);
    showToast('Access token copied.');
  } catch (e) {
    field.focus();
    field.select();
  }
}

function toggleSettingsMenu() {
  const menu = document.getElementById('settingsMenu');
  if (menu.style.display === 'none') {
    // Permission may have changed in the browser's own settings since.
    updateNotifToggle();
    menu.style.display = '';
    document.getElementById('settingsBtn').setAttribute('aria-expanded', 'true');
  } else {
    closeSettingsMenu();
  }
}

export function closeSettingsMenu() {
  document.getElementById('settingsMenu').style.display = 'none';
  document.getElementById('settingsBtn').setAttribute('aria-expanded', 'false');
}

async function signOut() {
  // While still signed in - the server only lets its owner remove it.
  await dropPushSubscription();
  await fetch('/api/auth/logout', { method: 'POST' });
  if (SERVER_PRIVATE) {
    // Starts over signed out, with none of the data left in the page.
    location.reload();
    return;
  }
  AUTH_USER = null;
  clearPins();
  updateAuthenticationControl();
  if (lastData) render(lastData);
}
