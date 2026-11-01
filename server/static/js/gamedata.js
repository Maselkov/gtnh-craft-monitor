// Game data admin modal: picking the GTNH version whose item icons and
// scanner item list the server uses, and which textures the icons are
// rendered with (server/gcm/gamedata.py); starting a pattern check
// against a version's recipes (recipe-check.js); and the page-wide notice
// that the game data looks older than the game (gcm/gamedata_check.py).

import { AUTH_USER, closeSettingsMenu } from './auth.js';
import { fetchNetwork } from './network.js';
import { openRecipeCheck, updateRecipeNotice, versionsToCheck } from './recipe-check.js';
import {
  TERMINAL_DOCS_URL, closeNote, delegateActions, escapeHtml, noteClosed, onBackdropClick,
} from './util.js';

const NOTICE_MS = 5 * 60 * 1000;
const NOTICE_PENDING_MS = 3000;
const NOTICE = 'gamedata';  // its name in gcm/notices.py
// Unknown item types listed in the dialog's own notice.
const NOTICE_NAMES = 3;

let pollTimer = null;
// The last /api/admin/gamedata-notice answer.
let notice = null;
// The last listing: texture set details, and per version what can be
// picked ({version, published_at, base_size, sizes: {textures: bytes},
// installed: Set of textures on disk, update_available: a newer build of
// the version was published since it was downloaded}).
let textureSets = [];
let versions = [];
let current = { version: null, textures: null };
// The version selected before the current one, while it's on disk.
let previousVersion = null;
// Why there's no pattern scan to check yet ({why, reason}), or null.
let patternsMissing = null;

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
  renderRecipeCheck();
}

function recipeCheckVersions() {
  return versionsToCheck(document.getElementById('gamedataVersion').value, current.version, previousVersion);
}

function renderRecipeCheck() {
  const pair = recipeCheckVersions();
  let text;
  if (patternsMissing) text = patternsMissing.reason;
  else if (pair) text = `Compares GTNH ${pair.from}'s recipes with ${pair.to}'s.`;
  else text = 'Pick a version other than the one in use, to compare the two.';
  document.getElementById('recipeCheckFor').textContent = text;
  document.getElementById('recipeCheckForHow').hidden = !patternsMissing || patternsMissing.why !== 'no_terminal';
  // Nothing to check without a pattern scan: said here, not after a click.
  document.getElementById('recipeCheckBtn').disabled = !pair || Boolean(patternsMissing);
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

async function loadGameData(refresh, pick) {
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
  previousVersion = data.previous || null;
  patternsMissing = data.patterns_missing ? { why: data.patterns_missing, reason: data.patterns_missing_reason } : null;
  const inUse = textureSets.find((t) => t.id === data.selected_textures);
  const selectedRelease = data.available.find((r) => r.version === data.selected);
  currentLine.textContent = data.selected
    ? `In use: GTNH ${data.selected}, ${inUse ? inUse.name : data.selected_textures} textures`
      + (selectedRelease && selectedRelease.update_available
        ? '. A newer build has been published: pick it again to update.' : '')
    : 'In use: no game data, so there are no item icons and the network scanner has no item list. Pick your GTNH version below.';

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
  if (pick && versions.some((v) => v.version === pick)) select.value = pick;
  else if (versions.some((v) => v.version === previous)) select.value = previous;
  else if (data.selected) select.value = data.selected;
  renderTextures();
  renderUnknown();
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

// options.version: the version to have picked, as the notice suggests.
export async function openGameDataModal(options = {}) {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  document.getElementById('gamedataModal').style.display = 'flex';
  await loadGameData(false, options.version);
}

export function closeGameDataModal() {
  document.getElementById('gamedataModal').style.display = 'none';
  stopPolling();
  fetchNetwork();  // the Network tab's "no GTNH version" note
  updateRecipeNotice();  // a switch may have changed recipes patterns use
  updateGameDataNotice();
}

// ---------- the notice that the game data looks older than the game ----------

export function gamedataNoticeText(n) {
  if (n.kind === 'prerelease') {
    return `You're using game data for GTNH ${n.selected}, and ${n.suggest} is out. If your server has updated, switch to it.`;
  }
  const one = n.count === 1;
  const names = n.ids.slice(0, NOTICE_NAMES).map((i) => i.label).join(', ')
    + (n.count > NOTICE_NAMES ? ', \u2026' : '');
  let where;
  if (n.suggest_count < n.count) where = `${n.suggest} has ${n.suggest_count} of them.`;
  else where = `${one ? 'It\'s' : 'They\'re'} in ${n.suggest}.`;
  return `Your network has ${one ? 'an item type' : `${n.count} item types`} the GTNH ${n.selected} game data`
    + ` doesn't know (${names}), so the network scanner skips ${one ? 'it' : 'them'}. ${where}`;
}

function noticeKey(n) {
  return `${n.selected}>${n.suggest}`;
}

function renderNotice() {
  const el = document.getElementById('gamedataNotice');
  const admin = Boolean(AUTH_USER && AUTH_USER.role === 'admin');
  renderUnknown();
  if (!admin || !notice || !notice.kind || noteClosed(NOTICE, noticeKey(notice))) {
    el.hidden = true;
    return;
  }
  document.getElementById('gamedataNoticeText').textContent = gamedataNoticeText(notice);
  document.getElementById('gamedataNoticeSwitch').textContent = `Switch to ${notice.suggest}\u2026`;
  el.hidden = false;
}

// In the dialog: what the notice is based on, and the item types no
// published version has.
function renderUnknown() {
  const box = document.getElementById('gamedataUnknown');
  const n = notice && notice.selected && notice.selected === current.version ? notice : null;
  const ids = n && n.kind === 'behind' ? n.ids : [];
  const count = ids.length ? n.count : 0;
  const unpublished = n ? n.unpublished_count || 0 : 0;
  box.hidden = !count && !unpublished;
  if (box.hidden) return;
  const total = count + unpublished;
  document.getElementById('gamedataUnknownSummary').textContent =
    `${total} item type${total === 1 ? '' : 's'} your network has ${total === 1 ? 'isn\'t' : 'aren\'t'} in this game data`;
  document.getElementById('gamedataUnknownList').innerHTML = ids.map((i) =>
    `<li>${escapeHtml(i.label)} <span class="gamedata-id">${escapeHtml(i.id)}</span>, since ${escapeHtml(i.version)}</li>`,
  ).join('') + (count > ids.length ? `<li>and ${count - ids.length} more</li>` : '');
  const note = document.getElementById('gamedataUnpublished');
  note.hidden = !unpublished;
  if (unpublished) {
    const names = n.unpublished.slice(0, NOTICE_NAMES).map((i) => i.label).join(', ')
      + (unpublished > NOTICE_NAMES ? ', \u2026' : '');
    document.getElementById('gamedataUnpublishedText').textContent =
      `Not in any published version: ${names}. ${unpublished === 1 ? 'It' : 'They'} may be hidden in NEI,`
      + ' or if your server runs a nightly or a modified pack, build game data for it yourself.';
  }
}

export async function updateGameDataNotice() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') {
    notice = null;
    renderNotice();
    return;
  }
  try {
    const res = await fetch('/api/admin/gamedata-notice');
    if (res.ok) notice = await res.json();
  } catch (e) { /* keep what was shown */ }
  renderNotice();
  // Worked out in the background, downloading newer versions' item lists.
  if (notice && notice.pending) setTimeout(updateGameDataNotice, NOTICE_PENDING_MS);
}

export function setupGameDataActions() {
  document.getElementById('recipeCheckForHow').href = TERMINAL_DOCS_URL;
  setInterval(updateGameDataNotice, NOTICE_MS);
  delegateActions(document, {
    'gamedata-notice-switch': () => openGameDataModal({ version: notice.suggest }),
    'dismiss-gamedata-notice': () => {
      closeNote(NOTICE, noticeKey(notice));
      renderNotice();
    },
    'open-gamedata': () => { closeSettingsMenu(); openGameDataModal(); },
    'close-gamedata': () => closeGameDataModal(),
    'refresh-gamedata': () => loadGameData(true),
    'install-gamedata': () => installGameData(),
    'check-recipes': () => {
      const pair = recipeCheckVersions();
      if (pair) openRecipeCheck(pair.from, pair.to);
    },
  });
  onBackdropClick('gamedataModal', closeGameDataModal);
  document.getElementById('gamedataVersion').addEventListener('change', renderTextures);
  document.getElementById('gamedataTextures').addEventListener('change', renderCredit);
}
