// Game data admin modal: picking the GTNH version whose item icons and
// scanner item list the server uses, and which textures the icons are
// rendered with (server/gcm/gamedata.py).

import { AUTH_USER, closeSettingsMenu } from './auth.js';
import { delegateActions, escapeHtml, onBackdropClick } from './util.js';

let pollTimer = null;
// The last listing: texture set details, and per version what can be
// picked ({version, published_at, base_size, sizes: {textures: bytes},
// installed: Set of textures on disk, update_available: a newer build of
// the version was published since it was downloaded}).
let textureSets = [];
let versions = [];
let current = { version: null, textures: null };

function showError(message) {
  const error = document.getElementById('gamedataError');
  error.textContent = message || '';
  error.style.display = message ? '' : 'none';
}

function formatMb(bytes) {
  return `${Math.round(bytes / 1e6)} MB`;
}

function versionLabel(entry) {
  if (entry.update_available) {
    return entry.version === current.version
      ? `${entry.version} (in use, update available)`
      : `${entry.version} (update available)`;
  }
  if (entry.version === current.version) return `${entry.version} (in use)`;
  if (entry.installed.size) return `${entry.version} (downloaded)`;
  return entry.published_at
    ? `${entry.version} - ${new Date(entry.published_at).toLocaleDateString()}`
    : entry.version;
}

function texturesLabel(set, entry) {
  const inUse = entry.version === current.version && set.id === current.textures;
  // An update downloads the whole bundle again, whatever's on disk.
  if (!entry.update_available) {
    if (inUse) return `${set.name} (in use)`;
    if (entry.installed.has(set.id)) return `${set.name} (downloaded)`;
  }
  // The lookup and catalog come along only with the version's first set.
  const withBase = entry.update_available || !entry.installed.size;
  const size = entry.sizes[set.id] + (withBase ? entry.base_size || 0 : 0);
  const label = size ? `${set.name} - ${formatMb(size)}` : set.name;
  return inUse ? `${label} (in use, update available)` : label;
}

function renderCredit() {
  const set = textureSets.find((t) => t.id === document.getElementById('gamedataTextures').value);
  const credit = document.getElementById('gamedataCredit');
  credit.style.display = set && set.credit ? '' : 'none';
  credit.innerHTML = set && set.credit
    ? `Rendered with the <a href="${escapeHtml(set.url)}" target="_blank" rel="noopener">${escapeHtml(set.credit)}</a>.`
    : '';
}

function renderTextures() {
  const entry = versions.find((v) => v.version === document.getElementById('gamedataVersion').value);
  const select = document.getElementById('gamedataTextures');
  const sets = entry
    ? textureSets.filter((t) => t.id in entry.sizes || entry.installed.has(t.id))
    : [];
  const previous = select.value;
  select.innerHTML = sets.map((t) =>
    `<option value="${escapeHtml(t.id)}">${escapeHtml(texturesLabel(t, entry))}</option>`,
  ).join('');
  const keep = sets.find((t) => t.id === previous)
    || sets.find((t) => entry.version === current.version && t.id === current.textures);
  if (keep) select.value = keep.id;
  select.disabled = sets.length === 0;
  renderCredit();
}

function renderStatus(status) {
  const installing = status.state === 'installing';
  document.getElementById('gamedataProgress').style.display = installing ? '' : 'none';
  document.getElementById('gamedataInstallBtn').disabled = installing;
  if (installing) {
    const percent = status.total_bytes ? Math.min(100, (100 * status.done_bytes) / status.total_bytes) : 0;
    document.getElementById('gamedataProgressFill').style.width = `${percent}%`;
    const set = textureSets.find((t) => t.id === status.textures);
    document.getElementById('gamedataProgressLabel').textContent =
      `Downloading ${status.version}${set ? ` (${set.name})` : ''}: `
      + `${formatMb(status.done_bytes)} of ${formatMb(status.total_bytes)}`;
  }
  if (status.state === 'error') showError(status.error);
}

async function loadGameData(refresh) {
  const currentLine = document.getElementById('gamedataCurrent');
  const select = document.getElementById('gamedataVersion');
  showError('');
  currentLine.textContent = 'Loading...';
  const response = await fetch(`/api/admin/gamedata${refresh ? '?refresh=1' : ''}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    currentLine.textContent = '';
    showError(data.error || 'Could not load game data.');
    return;
  }
  textureSets = data.textures;
  current = { version: data.selected, textures: data.selected_textures };
  const inUse = textureSets.find((t) => t.id === data.selected_textures);
  const selectedRelease = data.available.find((r) => r.version === data.selected);
  currentLine.textContent = data.selected
    ? `In use: GTNH ${data.selected}, ${inUse ? inUse.name : data.selected_textures} textures`
      + (selectedRelease && selectedRelease.update_available
        ? '. A newer build has been published: pick it again to update.' : '')
    : 'In use: the icons that came with the server. Pick your GTNH version below.';

  // Bundles built locally (tools/icon-export/run.sh --install) aren't
  // published, but can still be switched to.
  const installed = new Map(data.installed.map((v) => [v.version, new Set(v.textures)]));
  versions = [
    ...data.available.map((r) => ({
      ...r, sizes: r.textures, installed: installed.get(r.version) || new Set(),
    })),
    ...data.installed
      .filter((v) => !data.available.some((r) => r.version === v.version))
      .map((v) => ({ version: v.version, sizes: {}, installed: installed.get(v.version) })),
  ];
  const previous = select.value;
  select.innerHTML = versions.map((v) =>
    `<option value="${escapeHtml(v.version)}">${escapeHtml(versionLabel(v))}</option>`,
  ).join('') || '<option value="">No game data published yet</option>';
  if (versions.some((v) => v.version === previous)) select.value = previous;
  else if (data.selected) select.value = data.selected;
  renderTextures();
  document.getElementById('gamedataInstallBtn').disabled = versions.length === 0;

  if (data.available_error) showError(data.available_error);
  renderStatus(data.status);
  if (data.status.state === 'installing') startPolling();
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollStatus, 1000);
}

async function pollStatus() {
  const response = await fetch('/api/admin/gamedata/status');
  if (!response.ok) {
    stopPolling();
    return;
  }
  const data = await response.json();
  renderStatus(data.status);
  if (data.status.state !== 'installing') {
    stopPolling();
    await loadGameData(false);
  }
}

async function installGameData() {
  const version = document.getElementById('gamedataVersion').value;
  const textures = document.getElementById('gamedataTextures').value || 'default';
  if (!version) return;
  showError('');
  const response = await fetch('/api/admin/gamedata/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version, textures }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    showError(data.error || 'Could not switch versions.');
    return;
  }
  if (data.status.state === 'installing') {
    renderStatus(data.status);
    startPolling();
  } else {
    // Already downloaded: switched straight away.
    await loadGameData(false);
  }
}

async function openGameDataModal() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  document.getElementById('gamedataModal').style.display = 'flex';
  await loadGameData(false);
}

export function closeGameDataModal() {
  document.getElementById('gamedataModal').style.display = 'none';
  stopPolling();
}

export function setupGameDataActions() {
  delegateActions(document, {
    'open-gamedata': () => { closeSettingsMenu(); openGameDataModal(); },
    'close-gamedata': () => closeGameDataModal(),
    'refresh-gamedata': () => loadGameData(true),
    'install-gamedata': () => installGameData(),
  });
  onBackdropClick('gamedataModal', closeGameDataModal);
  document.getElementById('gamedataVersion').addEventListener('change', renderTextures);
  document.getElementById('gamedataTextures').addEventListener('change', renderCredit);
}
