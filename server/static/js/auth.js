// Sign-in state, admin user/token management modals, settings menu.

let AUTH_USER = null;
let AUTH_MUST_ROTATE_BOOTSTRAP = false;

function userHeaders(extra) {
  return Object.assign({}, extra || {});
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
  adminButton.style.display = AUTH_USER && AUTH_USER.role === 'admin' && !AUTH_MUST_ROTATE_BOOTSTRAP ? '' : 'none';
}

async function loadAuthentication() {
  const response = await fetch('/api/auth/session');
  const data = await response.json();
  AUTH_USER = data.authenticated ? data.user : null;
  AUTH_MUST_ROTATE_BOOTSTRAP = Boolean(data.must_rotate_bootstrap);
  updateAuthenticationControl();
  if (AUTH_MUST_ROTATE_BOOTSTRAP) showBootstrapRotationModal();
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
  AUTH_MUST_ROTATE_BOOTSTRAP = Boolean(data.must_rotate_bootstrap);
  updateAuthenticationControl();
  if (AUTH_MUST_ROTATE_BOOTSTRAP) showBootstrapRotationModal();
  await refreshPinsAndCompletions();
  if (lastData) render(lastData);
}

async function openAdminUsersModal() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  document.getElementById('adminUsersModal').style.display = 'flex';
  document.getElementById('adminUsersError').style.display = 'none';
  document.getElementById('adminTokenResult').style.display = 'none';
  await refreshAdminUsers();
  document.getElementById('adminUserName').focus();
}

function closeAdminUsersModal() {
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
        <button class="admin-revoke-btn" onclick="revokeAdminToken(${jsArg(token.id)})">Revoke</button>
      </div>`).join('') || '<div class="admin-token-row">No active tokens</div>';
    return `<div class="admin-user-row">
      <div>
        <div class="admin-user-name">${escapeHtml(user.display_name)}</div>
        <div class="admin-token-list">${tokens}</div>
      </div>
      <div class="admin-user-actions">
        <span class="admin-user-role">${escapeHtml(user.role)}${user.disabled_at ? ' (disabled)' : ''}</span>
        <button class="admin-history-btn" onclick="openUserHistory(${jsArg(user.id)})">History</button>
        <button class="admin-history-btn" onclick="regenerateAdminToken(${jsArg(user.id)}, ${jsArg(user.display_name)})">New token</button>
        <button class="admin-revoke-btn" onclick="deleteAdminUser(${jsArg(user.id)}, ${jsArg(user.display_name)})">Delete</button>
      </div>
    </div>`;
  }).join('') || '<div class="admin-user-row">No users yet.</div>';
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
      <div class="user-history-target">${event.type === 'request' ? 'Request' : 'Cancel'}: ${escapeHtml(event.target || '?')}</div>
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

function showBootstrapRotationModal() {
  document.getElementById('bootstrapRotationModal').style.display = 'flex';
  document.getElementById('bootstrapRotationError').style.display = 'none';
  document.getElementById('bootstrapRotationResult').style.display = 'none';
  document.getElementById('bootstrapRotationBtn').textContent = 'Create replacement token';
  document.getElementById('bootstrapRotationBtn').disabled = false;
}

async function rotateBootstrapToken() {
  const button = document.getElementById('bootstrapRotationBtn');
  const error = document.getElementById('bootstrapRotationError');
  if (document.getElementById('bootstrapRotationResult').style.display !== 'none') {
    AUTH_MUST_ROTATE_BOOTSTRAP = false;
    document.getElementById('bootstrapRotationModal').style.display = 'none';
    updateAuthenticationControl();
    showToast('Bootstrap token revoked. Remove it from .env and restart the service.');
    return;
  }
  button.disabled = true;
  error.style.display = 'none';
  try {
    const response = await fetch('/api/auth/rotate-bootstrap', { method: 'POST' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      error.textContent = data.error || 'Could not rotate the bootstrap token.';
      error.style.display = 'block';
      return;
    }
    document.getElementById('bootstrapRotationToken').value = data.token;
    document.getElementById('bootstrapRotationIntro').textContent = 'Copy and securely store the replacement token before continuing.';
    document.getElementById('bootstrapRotationResult').style.display = 'block';
    button.textContent = 'I saved the replacement token';
  } finally {
    button.disabled = false;
  }
}

function toggleSettingsMenu() {
  const menu = document.getElementById('settingsMenu');
  if (menu.style.display === 'none') {
    menu.style.display = '';
    document.getElementById('settingsBtn').setAttribute('aria-expanded', 'true');
  } else {
    closeSettingsMenu();
  }
}

function closeSettingsMenu() {
  document.getElementById('settingsMenu').style.display = 'none';
  document.getElementById('settingsBtn').setAttribute('aria-expanded', 'false');
}

// Close the settings dropdown on any click outside it.
document.addEventListener('click', (e) => {
  if (!e.target.closest('#settingsWrap')) closeSettingsMenu();
});

async function signOut() {
  await fetch('/api/auth/logout', { method: 'POST' });
  AUTH_USER = null;
  AUTH_MUST_ROTATE_BOOTSTRAP = false;
  pinnedCpus = new Set();
  completedPins = [];
  updateAuthenticationControl();
  if (lastData) render(lastData);
}
