// Network tab: item grid, tooltip, pins, infinite scroll.

import { AUTH_USER, isOperator } from './auth.js';
import { openCraftRequestModal } from './craft-actions.js';
import { descriptionHtml, descriptionPlainText, formulaSearchText, nameHtml, nameText } from './description.js';
import { openItemHistory, tryOpenItemFromUrl, updateItemHistoryPinButton } from './history.js';
import { buildSearchHighlightHtml, itemMatchesSearch, parseSearchQuery, searchNeedsItemInfo } from './search.js';
import { stockBadgeFor } from './stock.js';
import { activeTab } from './tabs.js';
import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import {
  closeNote, delegateActions, escapeHtml, essentiaBadgeHtml, formatDuration, formatQty, formatRate,
  formatRelativeTime, iconClass, iconUrl, kindTooltipHtml, noteClosed, PIN_ICON, TERMINAL_DOCS_URL,
} from './util.js';

// ---------- Network browser ----------
export let lastNetworkData = null;
let networkSort = 'size';
// The Change sort's range (day or week) and the server's answer for it:
// {item key: {from, per_second, seconds_to_empty}}, only items that
// changed.
let networkChangeRange = 'day';
let networkChanges = null;
// Grid (icons only, like the game's terminal) or list (with names, for
// a touch screen where there's no hover tooltip), per viewer.
const NETWORK_VIEW_KEY = 'gtnhCraftMonitor.networkView';
const TERMINAL_NOTE_DISMISSED_KEY = 'gtnhCraftMonitor.terminalNoteDismissed';
let networkView = 'grid';
let networkShownItems = [];  // the currently-rendered array, indexed by
                              // each cell's data-idx - lets the tooltip
                              // handler look up full item data without
                              // re-parsing anything out of the DOM

// Item pins (Network tab "favorite this item") - a different concept
// from the CPU-based pin system used for craft completion tracking,
// kept entirely separate client-side too (own Set, own endpoints).
// Format matches the server's own item_key() exactly (mod|internal|
// damage|kind, then |variant for an NBT variant) - this duplication is low-risk: it's only used for a
// fast client-side Set lookup, the server's own mod/internal/damage/
// kind FIELDS remain the actual source of truth, so if this ever
// drifted out of sync the worst case is a cosmetic "badge didn't
// show", not a real data problem.
export let pinnedItemKeys = new Set();

// What the game data says about the network's items (gcm/item_info.py):
// {items: {item key: {description, shift_description, rarity, formula,
// ore}},
// mods: {mod id: name}}. Fetched apart from the item list (megabytes on
// a big network), once a tooltip shows or a search needs it (#, $, =, @),
// and again with every new item list after that. Null until then.
let networkItemInfo = null;
let itemInfoWanted = false;
const searchInfo = new Map();  // item key -> itemMatchesSearch()'s info
// The cell the tooltip was last built for, to rebuild it when the info
// arrives or Shift is pressed.
let tooltipCell = null;
// Shift held: tooltips show their Shift text, as in the game.
let shiftHeld = false;

function wantItemInfo() {
  if (itemInfoWanted) return;
  itemInfoWanted = true;
  fetchNetworkItemInfo();
}

async function fetchNetworkItemInfo() {
  try {
    const res = await fetch('/api/network/info');
    if (!res.ok) throw new Error(`info: ${res.status}`);
    const data = await res.json();
    networkItemInfo = { items: data.items || {}, mods: data.mods || {} };
    searchInfo.clear();
  } catch (e) {
    return;  // keep what we had; the next item list asks again
  }
  const query = document.getElementById('networkSearch').value;
  if (searchNeedsItemInfo(parseSearchQuery(query))) renderNetworkList();
  refreshTooltip();
}

function refreshTooltip() {
  if (tooltipVisible() && tooltipCell && tooltipCell.isConnected) {
    const html = networkTooltipHtml(tooltipCell);
    if (html) showTooltip(html);
  }
}

function infoOf(it) {
  return (networkItemInfo && networkItemInfo.items[itemKeyOf(it)]) || {};
}

function modNameOf(it) {
  if (!it.mod || !networkItemInfo) return null;
  const mods = networkItemInfo.mods;
  if (mods[it.mod]) return mods[it.mod];
  const lower = it.mod.toLowerCase();
  const id = Object.keys(mods).find(m => m.toLowerCase() === lower);
  return id ? mods[id] : null;
}

function searchInfoOf(it) {
  const key = itemKeyOf(it);
  if (!searchInfo.has(key)) {
    const info = infoOf(it);
    searchInfo.set(key, {
      description: info.description ? descriptionPlainText(info.description) : '',
      ore: info.ore || [],
      modName: modNameOf(it) || '',
      formula: info.formula ? formulaSearchText(info.formula) : '',
    });
  }
  return searchInfo.get(key);
}

export function networkItemKey(mod, internal, damage, kind, variant) {
  const key = (mod || '') + '|' + (internal || '') + '|' + (damage != null ? damage : '') + '|' + (kind || 'item');
  return variant ? key + '|' + variant : key;
}

export async function fetchNetworkPins() {
  try {
    const res = await fetch('/api/network/pins');
    const data = await res.json();
    pinnedItemKeys = new Set((data.pins || []).map(p => networkItemKey(p.mod, p.internal, p.damage, p.kind, p.variant)));
    // Whichever of fetchNetwork()/fetchNetworkPins() resolves second
    // is what actually needs to trigger the correctly-pinned render -
    // safe to call unconditionally, renderNetworkList() itself
    // handles the case where lastNetworkData isn't loaded yet.
    renderNetworkList();
  } catch (e) {
    // leave whatever we last had in place
  }
}

// Pin requests go out one at a time, so the server sees them in the
// order they were made.
let pinRequests = Promise.resolve();

function setItemPinned(it, key, pinned) {
  if (pinned) pinnedItemKeys.add(key); else pinnedItemKeys.delete(key);
  updateItemHistoryPinButton(it);
  renderNetworkList();  // reflect the new pin badge/sort order immediately
}

export function toggleNetworkItemPin(it) {
  const key = networkItemKey(it.mod, it.internal, it.damage, it.kind, it.variant);
  const pinning = !pinnedItemKeys.has(key);
  // Flipped before the request, so pressing P again straight away
  // undoes this one rather than pinning a second time.
  setItemPinned(it, key, pinning);
  const body = JSON.stringify({
    mod: it.mod, internal: it.internal, damage: it.damage, kind: it.kind || 'item', variant: it.variant || null,
  });
  pinRequests = pinRequests.then(async () => {
    try {
      const res = await fetch(pinning ? '/api/network/pins' : '/api/network/pins/unpin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });
      if (!res.ok) throw new Error(`pin: ${res.status}`);
    } catch (e) {
      // Put it back, unless a later toggle already has.
      if (pinnedItemKeys.has(key) === pinning) setItemPinned(it, key, !pinning);
    }
  });
  return pinRequests;
}

// ---------- Network browser: tooltip and clicks ----------
function networkTooltipHtml(cell) {
  const idx = parseInt(cell.dataset.idx, 10);
  const it = networkShownItems[idx];
  if (!it) return null;
  tooltipCell = cell;
  wantItemInfo();

  // Always the real number now (including 0) - craftability is its
  // own separate line, not a replacement for the actual count.
  // Fluids are stored in mB (milli-buckets), same convention AE2's
  // own UI uses - appended only for fluids since an item count has
  // no such unit.
  const statLine = 'Stored: ' + (it.size || 0).toLocaleString() + (it.kind === 'fluid' ? ' mB' : '');

  const change = changeOf(it);
  const changeLine = change ? changeTooltipHtml(change, it.kind) : '';
  const info = infoOf(it);
  const description = (shiftHeld && info.shift_description) || info.description;
  const modName = modNameOf(it) || it.mod;
  // The name in its rarity's colour, as the game shows it.
  const nameClass = 'network-tooltip-name' + (info.rarity ? ' mc-' + info.rarity : '');

  return `
    <div class="${nameClass}">${nameHtml(it.name || '?')}</div>
    ${it.variant_name ? `<div class="network-tooltip-variant">${escapeHtml(nameText(it.variant_name))}</div>` : ''}
    ${info.shift_description && !shiftHeld ? '<div class="network-tooltip-hint">[shift]</div>' : ''}
    <div class="network-tooltip-stat">${statLine}</div>
    ${changeLine}
    ${kindTooltipHtml(it.kind)}
    ${it.isCraftable ? `<div class="network-tooltip-craftable">Craftable</div>` : ''}
    ${description ? `<div class="game-text network-tooltip-description">${descriptionHtml(description)}</div>` : ''}
    ${modName ? `<div class="network-tooltip-mod">${escapeHtml(modName)}</div>` : ''}
  `;
}

// ---------- Keyboard ----------
// One cell at a time is in the tab order (a roving tabindex): Tab lands
// on the grid, the arrow keys move within it.

function itemKeyOf(it) {
  return networkItemKey(it.mod, it.internal, it.damage, it.kind, it.variant);
}

function cellItemKey(cell) {
  const it = cell && cell.classList && cell.classList.contains('network-cell')
    ? networkShownItems[parseInt(cell.dataset.idx, 10)] : null;
  return it ? itemKeyOf(it) : null;
}

function cellAt(idx) {
  return document.querySelector(`#networkList .network-cell[data-idx="${idx}"]`);
}

// Makes cell idx the grid's one tab stop, and focuses it if asked.
function setRovingCell(idx, focus) {
  const listEl = document.getElementById('networkList');
  for (const cell of listEl.querySelectorAll('.network-cell[tabindex="0"]')) cell.tabIndex = -1;
  const cell = cellAt(idx);
  if (!cell) return;
  cell.tabIndex = 0;
  if (focus) cell.focus();
}

// Cells per row, from where they wrap - right at any window width.
function gridColumns() {
  const cells = document.querySelectorAll('#networkList .network-cell');
  if (cells.length === 0) return 1;
  const top = cells[0].offsetTop;
  let n = 0;
  while (n < cells.length && cells[n].offsetTop === top) n++;
  return Math.max(1, n);
}

function moveFocus(idx) {
  const last = networkFilteredSorted.length - 1;
  idx = Math.max(0, Math.min(idx, last));
  while (idx >= networkRenderedCount && networkRenderedCount < networkFilteredSorted.length) growNetworkList();
  setRovingCell(idx, true);
  cellAt(idx)?.scrollIntoView({ block: 'nearest' });
}

// Whether the last item opened came from the keyboard, so closing its
// dialog with Escape can hand focus back to the grid.
let openedFromKeyboard = false;

function onNetworkGridKey(e) {
  const cell = e.target.closest('.network-cell');
  if (!cell || e.ctrlKey || e.metaKey || e.altKey) return;
  const idx = parseInt(cell.dataset.idx, 10);
  const it = networkShownItems[idx];
  const columns = gridColumns();
  const moves = {
    ArrowLeft: idx - 1,
    ArrowRight: idx + 1,
    ArrowUp: idx - columns,
    ArrowDown: idx + columns,
    Home: 0,
    End: networkFilteredSorted.length - 1,
    PageUp: idx - columns * 5,
    PageDown: idx + columns * 5,
  };
  if (e.key in moves) {
    if (e.key === 'ArrowUp' && idx < columns) {
      document.getElementById('networkSearch').focus();
    } else {
      moveFocus(moves[e.key]);
    }
  } else if (e.key === 'Enter' || e.key === ' ') {
    openedFromKeyboard = true;
    handleNetworkCellClick(cell, 'left');
  } else if (e.key === 'c' || e.key === 'C') {
    if (!it || !it.isCraftable || !isOperator()) return;
    openedFromKeyboard = true;
    hideTooltip();
    openCraftRequestModal(it);
  } else if (e.key === 'p' || e.key === 'P') {
    if (it) toggleNetworkItemPin(it);
  } else if (e.key === 'Escape') {
    document.getElementById('networkSearch').focus();
  } else {
    return;
  }
  e.preventDefault();
}

function showCellTooltip(cell) {
  const html = networkTooltipHtml(cell);
  if (!html) return;
  const r = cell.getBoundingClientRect();
  showTooltip(html, r.right - 8, r.bottom - 8);
}

export function setupNetworkTooltipEvents() {
  const listEl = document.getElementById('networkList');
  bindCellTooltip(listEl, '.network-cell', networkTooltipHtml);
  // Holding Shift shows a tooltip's Shift text (a multiblock's structure).
  const setShift = (held) => {
    if (held === shiftHeld) return;
    shiftHeld = held;
    refreshTooltip();
  };
  window.addEventListener('keydown', (e) => { if (e.key === 'Shift') setShift(true); });
  window.addEventListener('keyup', (e) => { if (e.key === 'Shift') setShift(false); });
  window.addEventListener('blur', () => setShift(false));

  listEl.addEventListener('keydown', onNetworkGridKey);
  // The hover tooltip, for the cell the keyboard is on - only when the
  // keyboard put it there, not on a click.
  listEl.addEventListener('focusin', (e) => {
    const cell = e.target.closest('.network-cell');
    if (!cell) return;
    setRovingCell(parseInt(cell.dataset.idx, 10), false);
    if (cell.matches(':focus-visible')) showCellTooltip(cell);
  });
  listEl.addEventListener('focusout', (e) => {
    if (!listEl.contains(e.relatedTarget)) hideTooltip();
  });
  document.getElementById('networkSearch').addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowDown' || !networkShownItems.length) return;
    e.preventDefault();
    const tabStop = listEl.querySelector('.network-cell[tabindex="0"]');
    moveFocus(tabStop ? parseInt(tabStop.dataset.idx, 10) : 0);
  });
  // Escape closing a dialog opened from the grid puts focus back on the
  // item. The dialogs close in craft-actions.js's own Escape handler;
  // capturing sees one still open before it runs.
  const dialogOpen = () => [...document.querySelectorAll('.modal-overlay')].some(m => m.style.display !== 'none');
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !openedFromKeyboard || !dialogOpen()) return;
    setTimeout(() => {
      if (dialogOpen()) return;  // only the top one closed
      openedFromKeyboard = false;
      const tabStop = listEl.querySelector('.network-cell[tabindex="0"]');
      if (tabStop && activeTab === 'network') tabStop.focus();
    }, 0);
  }, true);

  // Suppress the native middle-click autoscroll indicator - without
  // this, the browser shows its own scrolling cursor/UI on middle
  // mouse DOWN regardless of what our click handler does afterward.
  listEl.addEventListener('mousedown', (e) => {
    if (e.button === 1) e.preventDefault();
  });

  listEl.addEventListener('click', (e) => {
    const cell = e.target.closest('.network-cell');
    if (!cell) return;
    openedFromKeyboard = false;
    handleNetworkCellClick(cell, 'left');
  });

  // auxclick fires for non-primary-button clicks (middle, and
  // usually right, though right typically opens a context menu
  // first) - button 1 specifically means middle here.
  listEl.addEventListener('auxclick', (e) => {
    if (e.button !== 1) return;
    const cell = e.target.closest('.network-cell');
    if (!cell) return;
    e.preventDefault();
    handleNetworkCellClick(cell, 'middle');
  });
}

function handleNetworkCellClick(cell, button) {
  const idx = parseInt(cell.dataset.idx, 10);
  const it = networkShownItems[idx];
  if (!it) return;

  if (button === 'middle') {
    // Desktop-only shortcut straight to the craft request modal,
    // matching AE2's own autocraft gesture - skips the history view
    // entirely. Only meaningful for a craftable item.
    if (it.isCraftable && isOperator()) openCraftRequestModal(it);
    return;
  }

  // Left-click (and a tap, which arrives as a left-click event on
  // touch devices - there's no separate event type to distinguish)
  // always opens the history view now, for ANY item, craftable or
  // not. This replaced an earlier stock-dependent rule that only
  // ever did anything for craftable items and had no answer for
  // touch devices at all (no middle-click gesture exists there) -
  // the history popup has its own "Request Craft" button for when
  // that's what you actually wanted, so the click itself no longer
  // needs to guess your intent or special-case device type.
  openItemHistory(it);
}

// A scan normally takes 1-2 minutes - well past that with in_progress
// still true is worth flagging directly rather than quietly saying
// "(new scan in progress)" forever, which is exactly what happened
// during a real stuck scan: nothing in the UI distinguished a normal
// few-minutes-long scan from one that would never finish, so old
// data just sat there looking completely normal.
const NETWORK_SCAN_STALE_SECONDS = 600;

// network_browser.lua caught an error that killed its scanning and is
// starting over (the server keeps the report until a scan finishes).
function crashNote(crash) {
  if (!crash) return '';
  const memory = crash.free_memory != null ? `, ${Math.round(crash.free_memory / 1024)}k memory free` : '';
  return ` — network_browser.lua crashed ${formatRelativeTime(crash.at)}${memory}: ${crash.error}`
    + ' (it restarts on its own; details in network_browser_debug.log)';
}

export function tickNetworkSourceLine() {
  const el = document.getElementById('networkSourceLine');
  const crash = lastNetworkData && lastNetworkData.crash;
  if (!lastNetworkData || !lastNetworkData.updated_at) {
    el.textContent = (lastNetworkData && lastNetworkData.in_progress
      ? 'First scan in progress...' : 'Waiting for data from network_browser.lua...') + crashNote(crash);
    el.classList.toggle('stale-warning', Boolean(crash));
    return;
  }
  const age = Math.max(0, Math.round(Date.now() / 1000 - lastNetworkData.updated_at));
  let progressNote = '';
  let stale = false;
  if (lastNetworkData.in_progress) {
    const scanAge = lastNetworkData.scan_started_at
      ? Math.round(Date.now() / 1000 - lastNetworkData.scan_started_at) : 0;
    if (scanAge > NETWORK_SCAN_STALE_SECONDS) {
      stale = true;
      progressNote = ` — scan may be stuck (running ${Math.round(scanAge / 60)}m, normally 1-2m) - check network_browser.lua`;
    } else {
      progressNote = ' (new scan in progress)';
    }
  }
  if (crash) stale = true;
  el.classList.toggle('stale-warning', stale);
  // Reconstructed (loaded from the DB after a server restart, not a
  // real scan yet) gets its own wording rather than claiming to be
  // a fresh "last scan" - the age is still meaningful (it's exactly
  // when the last REAL scan before the restart actually completed),
  // just framed honestly as old data rather than implying it's
  // current. The in-progress/stale-warning logic above is
  // unaffected either way - a scan starting right after boot still
  // correctly shows "(new scan in progress)" layered on top.
  const baseText = lastNetworkData.is_reconstructed
    ? `${lastNetworkData.item_count} items - showing data from before the last restart (${age}s ago)`
    : `${lastNetworkData.item_count} items - last scan ${age}s ago`;
  el.textContent = baseText + progressNote + crashNote(crash);
}

// Until an admin picks a GTNH version the Network tab stays empty, and
// nothing else on the page says why: admins get a button to pick one.
export function updateGameDataNote() {
  const note = document.getElementById('networkGameDataNote');
  note.hidden = !lastNetworkData || lastNetworkData.game_data !== null;
  const admin = Boolean(AUTH_USER && AUTH_USER.role === 'admin');
  document.getElementById('networkGameDataBtn').hidden = !admin;
  document.getElementById('networkGameDataAsk').hidden = admin;
}

// No patterns, and network_browser.lua says it has no ME Interface
// Terminal to read them with: admins, who set up the game side, are
// told what one adds, until they close the note.
export function updateTerminalNote() {
  const admin = Boolean(AUTH_USER && AUTH_USER.role === 'admin');
  document.getElementById('networkTerminalNote').hidden =
    !admin || noteClosed(TERMINAL_NOTE_DISMISSED_KEY) || !lastNetworkData || !lastNetworkData.needs_terminal;
}

function dismissTerminalNote() {
  closeNote(TERMINAL_NOTE_DISMISSED_KEY);
  updateTerminalNote();
}

export async function fetchNetwork() {
  try {
    const res = await fetch('/api/network');
    const data = await res.json();
    lastNetworkData = data;
    updateGameDataNote();
    updateTerminalNote();
    // Its ETag makes this an empty 304 unless the items or game data changed.
    if (itemInfoWanted) fetchNetworkItemInfo();
    if (networkSort === 'change') await fetchNetworkChanges();
    tickNetworkSourceLine();
    renderNetworkList();
    tryOpenItemFromUrl();
  } catch (e) {
    document.getElementById('networkSourceLine').textContent = 'Could not reach server.';
  }
}

async function fetchNetworkChanges() {
  const range = networkChangeRange;
  try {
    const res = await fetch(`/api/network/changes?range=${encodeURIComponent(range)}`);
    if (!res.ok) throw new Error(`changes: ${res.status}`);
    const data = await res.json();
    if (range === networkChangeRange) networkChanges = data.changes || {};
  } catch (e) {
    // Keep the last answer; the next network refresh asks again.
  }
}

function changeOf(it) {
  if (!networkChanges) return null;
  return networkChanges[networkItemKey(it.mod, it.internal, it.damage, it.kind, it.variant)] || null;
}

// How far an item's quantity moved over the range, as a share of the
// larger of its quantities then and now - -1 for one used up entirely,
// so items and fluids (in mB) compare by how much of each went rather
// than by raw amounts. Null for one that didn't change.
export function changeFraction(size, change) {
  if (!change) return null;
  const now = size || 0;
  return (now - change.from) / Math.max(change.from, now, 1);
}

// "−80%", "+12%", "−0.4%" - one decimal under 1%, so a small move
// doesn't read as none.
export function formatChangePercent(fraction) {
  const percent = Math.abs(fraction * 100);
  const text = percent < 1 ? percent.toFixed(1) : String(Math.round(percent));
  return `${fraction < 0 ? '−' : '+'}${text}%`;
}

// The Change sort: most used up first, then most gained last among the
// ones that changed, then everything unchanged by quantity.
export function compareByChange(a, b) {
  if (a.fraction != null && b.fraction != null) return a.fraction - b.fraction;
  if (a.fraction != null) return -1;
  if (b.fraction != null) return 1;
  return (b.size || 0) - (a.size || 0);
}

function changeTooltipHtml(change, kind) {
  const unit = kind === 'fluid' ? ' mB' : '';
  const range = networkChangeRange === 'week' ? 'a week' : 'a day';
  // No rate until the item's history spans long enough to mean something.
  const rate = change.per_second != null ? formatRate(change.per_second, unit) : null;
  const empty = change.seconds_to_empty != null ? ` &middot; empty in ${escapeHtml(formatDuration(change.seconds_to_empty))}` : '';
  return `<div class="network-tooltip-change">${escapeHtml(formatQty(change.from) + unit)} ${range} ago${
    rate ? ` &middot; ${escapeHtml(rate)}` : ''}${empty}</div>`;
}

async function setNetworkSort(sort) {
  networkSort = sort;
  document.querySelectorAll('#networkTab [data-action="network-sort"]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sort === sort);
  });
  document.getElementById('networkChangeRange').hidden = sort !== 'change';
  if (sort === 'change') await fetchNetworkChanges();
  renderNetworkList();
}

async function setNetworkChangeRange(range) {
  networkChangeRange = range;
  document.querySelectorAll('#networkTab [data-action="network-change-range"]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === range);
  });
  networkChanges = null;
  await fetchNetworkChanges();
  lastNetworkSort = null;  // a new order: back to the top
  renderNetworkList();
}

function showNetworkView() {
  document.getElementById('networkList').classList.toggle('list', networkView === 'list');
  document.querySelectorAll('#networkTab [data-action="network-view"]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === networkView);
  });
}

function setNetworkView(view) {
  networkView = view;
  try { localStorage.setItem(NETWORK_VIEW_KEY, view); } catch (e) { /* per-viewer nicety only */ }
  showNetworkView();
  // Rows are taller than cells: a list that filled the window as a grid
  // may not any more, or the other way round.
  fillNetworkViewportIfNeeded();
}

// Infinite scroll: all items are already loaded client-side (the
// whole network snapshot lives in lastNetworkData.items) - this was
// never about fetching more data, only about how much to render as
// DOM at once. Renders an initial batch, then appends more as the
// page nears the bottom, rather than hard-capping with a "refine
// your search" wall.
const NETWORK_INITIAL_RENDER = 150;
const NETWORK_RENDER_BATCH = 150;
const NETWORK_PATTERN_ICON = 'item/appliedenergistics2/item.ItemMultiMaterial~52.png';  // Blank Pattern

let networkFilteredSorted = [];  // the full filtered+sorted list for
                                  // the CURRENT search/sort - grown
                                  // into incrementally, not recomputed
                                  // per scroll
let networkRenderedCount = 0;
let lastNetworkQuery = null;
let lastNetworkSort = null;

function buildNetworkCellHtml(it, idx) {
  const icon = it.icon
    ? `<img class="${iconClass('network-cell-icon', it.icon)}" src="${iconUrl(it.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : essentiaBadgeHtml(it, 'network-cell-icon');
  const qty = `<span class="network-cell-qty">${formatQty(it.size)}</span>`;
  // In the Change sort, how much it moved: in place of the quantity in
  // the grid, beside it in the list. The rate is in the tooltip.
  const fraction = networkSort === 'change' ? changeFraction(it.size, changeOf(it)) : null;
  const changeLabel = fraction != null
    ? `<span class="network-cell-change ${fraction < 0 ? 'falling' : 'rising'}">${escapeHtml(formatChangePercent(fraction))}</span>`
    : '';
  // Hidden in the grid view by CSS - both views share this one cell.
  const name = `<span class="network-cell-name">${escapeHtml(nameText(it.name || '?'))}${
    it.variant_name ? ` <span class="network-cell-variant">${escapeHtml(nameText(it.variant_name))}</span>` : ''}</span>`;
  // Blank Pattern icon (appliedenergistics2:item.ItemMultiMaterial
  // damage 52, confirmed against the real NESQL export) - shown for
  // ANY craftable item regardless of current stock, matching how the
  // real AE2 terminal marks craftability.
  const patternBadge = it.isCraftable
    ? `<img class="network-cell-pattern-badge" src="${iconUrl(NETWORK_PATTERN_ICON)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  const isPinned = pinnedItemKeys.has(networkItemKey(it.mod, it.internal, it.damage, it.kind, it.variant));
  const pinBadge = isPinned ? `<span class="network-cell-pin-badge">${PIN_ICON}</span>` : '';
  const stock = stockBadgeFor(it);
  const stockBadge = stock ? `<span class="network-cell-stock-badge${stock.low ? ' low' : ''}">${stock.icon}</span>` : '';
  const classes = 'network-cell' + (it.isCraftable ? ' craftable' : '') + (stock && stock.low ? ' stock-low' : '')
    + (changeLabel ? ' has-change' : '');
  const label = `${nameText(it.name || '?')}${it.variant_name ? ' ' + nameText(it.variant_name) : ''}, ${formatQty(it.size)}`;
  return `<div class="${classes}" data-idx="${idx}" tabindex="-1" aria-label="${escapeHtml(label)}">${icon}${name}${changeLabel}${qty}${patternBadge}${pinBadge}${stockBadge}</div>`;
}

export function updateNetworkSearchHighlight() {
  const input = document.getElementById('networkSearch');
  const overlay = document.getElementById('networkSearchHighlight');
  overlay.innerHTML = buildSearchHighlightHtml(input.value);
  overlay.scrollLeft = input.scrollLeft;
}

function onNetworkSearchInput() {
  updateNetworkSearchHighlight();
  renderNetworkList();
}

function buildNetworkNoteHtml(query) {
  if (networkFilteredSorted.length === 0) {
    return `<div class="network-note">No items match "${escapeHtml(query)}".</div>`;
  }
  if (networkRenderedCount < networkFilteredSorted.length) {
    return `<div class="network-note">Showing ${networkRenderedCount} of ${networkFilteredSorted.length} - scroll for more.</div>`;
  }
  return `<div class="network-note">End of list - ${networkFilteredSorted.length} items.</div>`;
}

// For a change that only affects how cells look (stock rules).
export function rerenderNetworkList() {
  if (lastNetworkData) renderNetworkList();
}

function changeSortKey(it) {
  return { size: it.size, fraction: changeFraction(it.size, changeOf(it)) };
}

function renderNetworkList() {
  // A re-render replaces the DOM under the cursor without a real
  // mouse-move event firing (e.g. typing in the search box) - hide
  // any currently-shown tooltip so it can't linger showing stale
  // data for whatever used to be there.
  hideTooltip();

  const listEl = document.getElementById('networkList');
  const emptyEl = document.getElementById('networkEmpty');
  const items = (lastNetworkData && lastNetworkData.items) || [];
  // A refresh rebuilds every cell: keep keyboard focus on the same item
  // (looked up before networkShownItems changes under the cell's index).
  const focusedKey = listEl.contains(document.activeElement) ? cellItemKey(document.activeElement) : null;

  if (items.length === 0) {
    listEl.innerHTML = '';
    networkShownItems = [];
    networkFilteredSorted = [];
    networkRenderedCount = 0;
    emptyEl.style.display = lastNetworkData && lastNetworkData.in_progress ? 'none' : 'block';
    if (lastNetworkData && lastNetworkData.in_progress) {
      listEl.innerHTML = '<div class="network-note">First scan in progress - on a big network this can take 10 minutes or more.</div>';
    }
    return;
  }
  emptyEl.style.display = 'none';

  const query = document.getElementById('networkSearch').value.trim().toLowerCase();
  let filtered = items;
  if (query) {
    const parsedQuery = parseSearchQuery(query);
    const needsInfo = searchNeedsItemInfo(parsedQuery);
    if (needsInfo) wantItemInfo();
    filtered = items.filter(it => itemMatchesSearch(it, parsedQuery, needsInfo ? searchInfoOf(it) : {}));
  }

  filtered = filtered.slice().sort((a, b) => {
    // Pinned items float to the top as a GROUP regardless of sort
    // mode - but within each group (pinned vs not), the normal sort
    // criteria still applies, rather than pinned items falling back
    // to some arbitrary pin-timestamp order.
    const aPinned = pinnedItemKeys.has(networkItemKey(a.mod, a.internal, a.damage, a.kind, a.variant));
    const bPinned = pinnedItemKeys.has(networkItemKey(b.mod, b.internal, b.damage, b.kind, b.variant));
    if (aPinned !== bPinned) return aPinned ? -1 : 1;
    if (networkSort === 'size') return (b.size || 0) - (a.size || 0);
    if (networkSort === 'change') return compareByChange(changeSortKey(a), changeSortKey(b));
    return nameText(a.name || '').localeCompare(nameText(b.name || ''));
  });
  networkFilteredSorted = filtered;

  // A periodic background refresh (new scan data, same search/sort
  // still active) shouldn't yank someone back to the top of a list
  // they've scrolled deep into - only an ACTUAL change in what
  // they're searching/sorting by resets how much is rendered.
  const filterChanged = (query !== lastNetworkQuery) || (networkSort !== lastNetworkSort);
  lastNetworkQuery = query;
  lastNetworkSort = networkSort;
  networkRenderedCount = filterChanged
    ? Math.min(NETWORK_INITIAL_RENDER, filtered.length)
    : Math.min(networkRenderedCount || NETWORK_INITIAL_RENDER, filtered.length);

  const shown = filtered.slice(0, networkRenderedCount);
  networkShownItems = shown;  // indexed by data-idx below, read by the tooltip/click handlers

  const cells = shown.map((it, i) => buildNetworkCellHtml(it, i)).join('');
  listEl.innerHTML = cells + buildNetworkNoteHtml(query);
  fillNetworkViewportIfNeeded();
  const keep = focusedKey != null ? networkShownItems.findIndex(it => itemKeyOf(it) === focusedKey) : -1;
  setRovingCell(keep >= 0 ? keep : 0, keep >= 0);
}

function growNetworkList() {
  if (networkRenderedCount >= networkFilteredSorted.length) return;  // nothing more to add
  const startIdx = networkRenderedCount;
  const nextCount = Math.min(networkRenderedCount + NETWORK_RENDER_BATCH, networkFilteredSorted.length);
  const newItems = networkFilteredSorted.slice(startIdx, nextCount);
  networkRenderedCount = nextCount;
  networkShownItems = networkFilteredSorted.slice(0, networkRenderedCount);

  const listEl = document.getElementById('networkList');
  const existingNote = listEl.querySelector('.network-note');
  if (existingNote) existingNote.remove();

  // insertAdjacentHTML appends without touching existing DOM nodes -
  // unlike innerHTML=, this doesn't discard/recreate what's already
  // there, and the delegated mouseover/click listeners on #networkList
  // (set up once, not per-cell) keep working on the new cells too
  // without needing anything re-attached.
  const newCellsHtml = newItems.map((it, i) => buildNetworkCellHtml(it, startIdx + i)).join('');
  listEl.insertAdjacentHTML('beforeend', newCellsHtml);
  const query = document.getElementById('networkSearch').value.trim().toLowerCase();
  listEl.insertAdjacentHTML('beforeend', buildNetworkNoteHtml(query));
}

function fillNetworkViewportIfNeeded() {
  if (activeTab !== 'network') return;
  // Keep growing the rendered set as long as the page doesn't
  // actually need to scroll to show what's already there AND
  // there's more to render - otherwise, moving the window to a
  // bigger monitor (or just starting on one) can leave the grid
  // permanently stuck at its initial/previous batch size, since
  // infinite scroll is entirely driven by an actual scroll event -
  // which never fires if there's nothing to scroll in the first
  // place. Confirmed as a real reported bug: no scrollbar existed
  // on the larger monitor, so there was no way to trigger loading
  // more without first shrinking the window back down to create one.
  let guard = 0;
  while (
    document.documentElement.scrollHeight <= window.innerHeight
    && networkRenderedCount < networkFilteredSorted.length
    && guard < 100  // sanity cap - growNetworkList() itself already
                     // stops once everything's rendered, this just
                     // guards against looping forever on something
                     // unexpected rather than being load-bearing
  ) {
    growNetworkList();
    guard++;
  }
}

let networkScrollTicking = false;
function onNetworkScroll() {
  if (activeTab !== 'network' || networkScrollTicking) return;
  networkScrollTicking = true;
  requestAnimationFrame(() => {
    networkScrollTicking = false;
    const nearBottom = (window.scrollY + window.innerHeight) > (document.documentElement.scrollHeight - 600);
    if (nearBottom) growNetworkList();
  });
}

let networkResizeTicking = false;

export function setupNetworkActions() {
  try { networkView = localStorage.getItem(NETWORK_VIEW_KEY) === 'list' ? 'list' : 'grid'; } catch (e) { /* grid */ }
  document.getElementById('networkTerminalHow').href = TERMINAL_DOCS_URL;
  showNetworkView();
  window.addEventListener('scroll', onNetworkScroll);

  window.addEventListener('resize', () => {
    if (networkResizeTicking) return;
    networkResizeTicking = true;
    requestAnimationFrame(() => {
      networkResizeTicking = false;
      fillNetworkViewportIfNeeded();
    });
  });

  document.getElementById('networkSearch').addEventListener('input', onNetworkSearchInput);
  delegateActions(document, {
    'network-sort': (el) => setNetworkSort(el.dataset.sort),
    'network-change-range': (el) => setNetworkChangeRange(el.dataset.range),
    'network-view': (el) => setNetworkView(el.dataset.view),
    'dismiss-terminal-note': dismissTerminalNote,
  });
}
