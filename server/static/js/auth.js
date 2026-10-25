// Sign-in state, admin user/token management modals, settings menu.

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
import { openGameDataModal } from './gamedata.js';
import { updateGameDataNote } from './network.js';
import { delegateActions, escapeHtml, formatQty, onBackdropClick } from './util.js';

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
  document.getElementById('settingsWrap').style.display = AUTH_USER ? '' : 'none';
  document.getElementById('settingsUser').textContent = AUTH_USER ? `Signed in as ${AUTH_USER.display_name}` : '';
  if (!AUTH_USER) closeSettingsMenu();
  const adminVisible = AUTH_USER && AUTH_USER.role === 'admin';
  adminButton.style.display = adminVisible ? '' : 'none';
  document.body.classList.toggle('is-admin', Boolean(adminVisible));
  document.getElementById('gamedataBtn').style.display = adminVisible ? '' : 'none';
  document.getElementById('webhooksBtn').style.display = adminVisible ? '' : 'none';
  document.getElementById('backupBtn').style.display = adminVisible ? '' : 'none';
  document.getElementById('agentsBtn').style.display = adminVisible ? '' : 'none';
  if (AUTH_USER) syncPushSubscription();
  updateGameDataNote();
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
  button.textContent = 'Restore';
  button.disabled = !document.getElementById('restoreFile').files.length;
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

async function openAdminUsersModal() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  document.getElementById('adminUsersModal').style.display = 'flex';
  document.getElementById('adminUsersError').style.display = 'none';
  document.getElementById('adminTokenResult').style.display = 'none';
  document.getElementById('privateToggle').checked = SERVER_PRIVATE;
  await refreshAdminUsers();
  document.getElementById('adminUserName').focus();
}

export function closeAdminUsersModal() {
  document.getElementById('adminUsersModal').style.display = 'none';
}

async function refreshAdminUsers() {
  const list = document.getElementById('adminUsersList');
  list.textContent = 'Loading users...';
  const response = await fetch('/api/admin/users');
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    list.textContent = data.error || 'Could not load users.';
    return;
  }
  list.innerHTML = data.users.map((user) => {
    const tokens = (user.tokens || []).map((token) => `
      <div class="admin-token-row">
        <span class="admin-token-id">${escapeHtml(token.id)}</span>
        <button class="admin-revoke-btn" data-action="revoke-token" data-token-id="${escapeHtml(token.id)}">Revoke</button>
      </div>`).join('') || '<div class="admin-token-row">No active tokens</div>';
    return `<div class="admin-user-row">
      <div>
        <div class="admin-user-name">${escapeHtml(user.display_name)}</div>
        <div class="admin-token-list">${tokens}</div>
      </div>
      <div class="admin-user-actions">
        <span class="admin-user-role">${escapeHtml(user.role)}${user.disabled_at ? ' (disabled)' : ''}</span>
        <button class="admin-history-btn" data-action="user-history" data-user-id="${escapeHtml(user.id)}">History</button>
        <button class="admin-history-btn" data-action="regenerate-token" data-user-id="${escapeHtml(user.id)}" data-display-name="${escapeHtml(user.display_name)}">New token</button>
        <button class="admin-revoke-btn" data-action="delete-user" data-user-id="${escapeHtml(user.id)}" data-display-name="${escapeHtml(user.display_name)}">Delete</button>
      </div>
    </div>`;
  }).join('') || '<div class="admin-user-row">No users yet.</div>';
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
    'revoke-token': (el) => revokeAdminToken(el.dataset.tokenId),
    'user-history': (el) => openUserHistory(el.dataset.userId),
    'regenerate-token': (el) => regenerateAdminToken(el.dataset.userId, el.dataset.displayName),
    'delete-user': (el) => deleteAdminUser(el.dataset.userId, el.dataset.displayName),
  });
}

function closeUserHistoryModal() {
  document.getElementById('userHistoryModal').style.display = 'none';
}

function formatHistoryTime(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString() : 'Pending';
}

async function openUserHistory(userId) {
  const list = document.getElementById('userHistoryList');
  document.getElementById('userHistoryModal').style.display = 'flex';
  document.getElementById('userHistoryTitle').textContent = 'User history';
  list.textContent = 'Loading history...';
  const response = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/history`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    list.textContent = data.error || 'Could not load history.';
    return;
  }
  document.getElementById('userHistoryTitle').textContent = `${data.user.display_name} history`;
  list.innerHTML = data.events.map((event) => `
    <div class="user-history-row">
      <div class="user-history-target">${event.type === 'cancel' ? 'Cancel' : (event.source === 'auto' ? 'Auto request' : 'Request')}: ${escapeHtml(event.target || '?')}</div>
      <div class="user-history-meta">${escapeHtml(event.status)} · ${formatHistoryTime(event.created_at)}${event.reason ? ` · ${escapeHtml(event.reason)}` : ''}</div>
    </div>`).join('') || '<div class="user-history-row">No craft actions recorded.</div>';
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
    document.getElementById('adminTokenValue').value = data.token;
    document.getElementById('adminTokenResult').style.display = 'block';
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

async function revokeAdminToken(tokenId) {
  if (!window.confirm(`Revoke ${tokenId}? Any sessions created with it will end immediately.`)) return;
  const response = await fetch(`/api/admin/tokens/${encodeURIComponent(tokenId)}/revoke`, {
    method: 'POST',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showToast(data.error || 'Could not revoke token.', true);
    return;
  }
  showToast('Access token revoked.');
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
  document.getElementById('adminTokenValue').value = data.token;
  document.getElementById('adminTokenResult').style.display = 'block';
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
