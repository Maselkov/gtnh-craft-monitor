// Network tab: item grid, tooltip, pins, infinite scroll.

import { openCraftRequestModal } from './craft-actions.js';
import { openItemHistory, tryOpenItemFromUrl, updateItemHistoryPinButton } from './history.js';
import { buildSearchHighlightHtml, itemMatchesSearch, parseSearchQuery } from './search.js';
import { activeTab } from './tabs.js';
import { delegateActions, escapeHtml, formatQty, iconClass, iconUrl } from './util.js';

// ---------- Network browser ----------
export let lastNetworkData = null;
let networkSort = 'size';
let networkShownItems = [];  // the currently-rendered array, indexed by
                              // each cell's data-idx - lets the tooltip
                              // handler look up full item data without
                              // re-parsing anything out of the DOM

// Item pins (Network tab "favorite this item") - a different concept
// from the CPU-based pin system used for craft completion tracking,
// kept entirely separate client-side too (own Set, own endpoints).
// Format matches the server's own _item_key() exactly (mod|internal|
// damage|kind) - this duplication is low-risk: it's only used for a
// fast client-side Set lookup, the server's own mod/internal/damage/
// kind FIELDS remain the actual source of truth, so if this ever
// drifted out of sync the worst case is a cosmetic "badge didn't
// show", not a real data problem.
export let pinnedItemKeys = new Set();

export function networkItemKey(mod, internal, damage, kind) {
  return (mod || '') + '|' + (internal || '') + '|' + (damage != null ? damage : '') + '|' + (kind || 'item');
}

export async function fetchNetworkPins() {
  try {
    const res = await fetch('/api/network/pins');
    const data = await res.json();
    pinnedItemKeys = new Set((data.pins || []).map(p => networkItemKey(p.mod, p.internal, p.damage, p.kind)));
    // Whichever of fetchNetwork()/fetchNetworkPins() resolves second
    // is what actually needs to trigger the correctly-pinned render -
    // safe to call unconditionally, renderNetworkList() itself
    // handles the case where lastNetworkData isn't loaded yet.
    renderNetworkList();
  } catch (e) {
    // leave whatever we last had in place
  }
}

export async function toggleNetworkItemPin(it) {
  const key = networkItemKey(it.mod, it.internal, it.damage, it.kind);
  const isPinned = pinnedItemKeys.has(key);
  const path = isPinned ? '/api/network/pins/unpin' : '/api/network/pins';
  try {
    await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mod: it.mod, internal: it.internal, damage: it.damage, kind: it.kind || 'item' }),
    });
  } catch (e) {
    return;  // don't update local state if the request itself failed
  }
  if (isPinned) pinnedItemKeys.delete(key); else pinnedItemKeys.add(key);
  updateItemHistoryPinButton(it);
  renderNetworkList();  // reflect the new pin badge/sort order immediately
}

// ---------- Network browser: custom instant tooltip ----------
// Deliberately not the native `title` attribute - that has a real,
// noticeable OS-level hover delay before it appears, which is exactly
// what a "like in-game" browse experience shouldn't have. This shows
// immediately on mouseover instead, positioned at the cursor.
let networkTooltipEl = null;

function ensureNetworkTooltip() {
  if (!networkTooltipEl) {
    networkTooltipEl = document.createElement('div');
    networkTooltipEl.className = 'network-tooltip';
    networkTooltipEl.style.display = 'none';
    document.body.appendChild(networkTooltipEl);
  }
  return networkTooltipEl;
}

function hideNetworkTooltip() {
  if (networkTooltipEl) networkTooltipEl.style.display = 'none';
}

function positionNetworkTooltip(tip, x, y) {
  const offset = 16;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const rect = tip.getBoundingClientRect();
  let left = x + offset;
  let top = y + offset;
  // Clamp to the viewport so it can't run off-screen near an edge -
  // flip to the other side of the cursor instead of clipping.
  if (left + rect.width > vw - 8) left = x - rect.width - offset;
  if (top + rect.height > vh - 8) top = y - rect.height - offset;
  tip.style.left = Math.max(4, left) + 'px';
  tip.style.top = Math.max(4, top) + 'px';
}

function showNetworkTooltip(cell, x, y) {
  const idx = parseInt(cell.dataset.idx, 10);
  const it = networkShownItems[idx];
  if (!it) return;

  // Always the real number now (including 0) - craftability is its
  // own separate line, not a replacement for the actual count.
  // Fluids are stored in mB (milli-buckets), same convention AE2's
  // own UI uses - appended only for fluids since an item count has
  // no such unit.
  const statLine = 'Stored: ' + (it.size || 0).toLocaleString() + (it.kind === 'fluid' ? ' mB' : '');

  const tip = ensureNetworkTooltip();
  tip.innerHTML = `
    <div class="network-tooltip-name">${escapeHtml(it.name || '?')}</div>
    <div class="network-tooltip-stat">${statLine}</div>
    ${it.kind === 'fluid' ? `<div class="network-tooltip-fluid">Fluid</div>` : ''}
    ${it.isCraftable ? `<div class="network-tooltip-craftable">Craftable</div>` : ''}
    ${it.mod ? `<div class="network-tooltip-mod">${escapeHtml(it.mod)}</div>` : ''}
  `;
  tip.style.display = 'block';
  positionNetworkTooltip(tip, x, y);
}

export function setupNetworkTooltipEvents() {
  const listEl = document.getElementById('networkList');
  // Event delegation on the (stable) container, not per-cell listeners -
  // the grid's inner content gets replaced wholesale on every render
  // (search input, sort change, periodic refresh), so per-cell
  // listeners would just be discarded each time anyway.
  listEl.addEventListener('mouseover', (e) => {
    const cell = e.target.closest('.network-cell');
    if (!cell) return;
    showNetworkTooltip(cell, e.clientX, e.clientY);
  });
  listEl.addEventListener('mousemove', (e) => {
    const cell = e.target.closest('.network-cell');
    if (!cell || !networkTooltipEl || networkTooltipEl.style.display === 'none') return;
    positionNetworkTooltip(networkTooltipEl, e.clientX, e.clientY);
  });
  listEl.addEventListener('mouseout', (e) => {
    const cell = e.target.closest('.network-cell');
    if (!cell) return;
    // Only actually hide if the cursor left the cell itself, not just
    // moved onto a child element (the icon/qty span) within it.
    if (cell.contains(e.relatedTarget)) return;
    hideNetworkTooltip();
  });

  // Suppress the native middle-click autoscroll indicator - without
  // this, the browser shows its own scrolling cursor/UI on middle
  // mouse DOWN regardless of what our click handler does afterward.
  listEl.addEventListener('mousedown', (e) => {
    if (e.button === 1) e.preventDefault();
  });

  listEl.addEventListener('click', (e) => {
    const cell = e.target.closest('.network-cell');
    if (!cell) return;
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
    if (it.isCraftable) openCraftRequestModal(it);
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

export function tickNetworkSourceLine() {
  const el = document.getElementById('networkSourceLine');
  if (!lastNetworkData || !lastNetworkData.updated_at) {
    el.textContent = lastNetworkData && lastNetworkData.in_progress
      ? 'First scan in progress...' : 'Waiting for data from network_browser.lua...';
    el.classList.remove('stale-warning');
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
  el.textContent = baseText + progressNote;
}

export async function fetchNetwork() {
  try {
    const res = await fetch('/api/network');
    const data = await res.json();
    lastNetworkData = data;
    tickNetworkSourceLine();
    renderNetworkList();
    tryOpenItemFromUrl();
  } catch (e) {
    document.getElementById('networkSourceLine').textContent = 'Could not reach server.';
  }
}

function setNetworkSort(sort) {
  networkSort = sort;
  document.querySelectorAll('#networkTab .range-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sort === sort);
  });
  renderNetworkList();
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
    : '';
  const qty = `<span class="network-cell-qty">${formatQty(it.size)}</span>`;
  // Blank Pattern icon (appliedenergistics2:item.ItemMultiMaterial
  // damage 52, confirmed against the real NESQL export) - shown for
  // ANY craftable item regardless of current stock, matching how the
  // real AE2 terminal marks craftability.
  const patternBadge = it.isCraftable
    ? `<img class="network-cell-pattern-badge" src="${iconUrl(NETWORK_PATTERN_ICON)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  const isPinned = pinnedItemKeys.has(networkItemKey(it.mod, it.internal, it.damage, it.kind));
  const pinBadge = isPinned ? `<span class="network-cell-pin-badge">&#128204;</span>` : '';
  return `<div class="network-cell${it.isCraftable ? ' craftable' : ''}" data-idx="${idx}">${icon}${qty}${patternBadge}${pinBadge}</div>`;
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

function renderNetworkList() {
  // A re-render replaces the DOM under the cursor without a real
  // mouse-move event firing (e.g. typing in the search box) - hide
  // any currently-shown tooltip so it can't linger showing stale
  // data for whatever used to be there.
  hideNetworkTooltip();

  const listEl = document.getElementById('networkList');
  const emptyEl = document.getElementById('networkEmpty');
  const items = (lastNetworkData && lastNetworkData.items) || [];

  if (items.length === 0) {
    listEl.innerHTML = '';
    networkShownItems = [];
    networkFilteredSorted = [];
    networkRenderedCount = 0;
    emptyEl.style.display = lastNetworkData && lastNetworkData.in_progress ? 'none' : 'block';
    if (lastNetworkData && lastNetworkData.in_progress) {
      listEl.innerHTML = '<div class="network-note">First scan in progress - this takes a minute or two.</div>';
    }
    return;
  }
  emptyEl.style.display = 'none';

  const query = document.getElementById('networkSearch').value.trim().toLowerCase();
  let filtered = items;
  if (query) {
    const parsedQuery = parseSearchQuery(query);
    filtered = items.filter(it => itemMatchesSearch(it, parsedQuery));
  }

  filtered = filtered.slice().sort((a, b) => {
    // Pinned items float to the top as a GROUP regardless of sort
    // mode - but within each group (pinned vs not), the normal sort
    // criteria still applies, rather than pinned items falling back
    // to some arbitrary pin-timestamp order.
    const aPinned = pinnedItemKeys.has(networkItemKey(a.mod, a.internal, a.damage, a.kind));
    const bPinned = pinnedItemKeys.has(networkItemKey(b.mod, b.internal, b.damage, b.kind));
    if (aPinned !== bPinned) return aPinned ? -1 : 1;
    if (networkSort === 'size') return (b.size || 0) - (a.size || 0);
    return (a.name || '').localeCompare(b.name || '');
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
  });
}
