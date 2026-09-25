// Item history popup and its chart.

import { openCraftRequestModal } from './craft-actions.js';
import {
  lastNetworkData,
  networkItemKey,
  pinnedItemKeys,
  toggleNetworkItemPin,
} from './network.js';
import { activeTab, switchTab } from './tabs.js';
import { delegateActions, formatQty, iconUrl, onBackdropClick } from './util.js';

// ---------- Item history popup ----------
let itemHistoryTarget = null;
let itemHistoryRange = 'day';
let itemHistoryChart = null;
let pendingItemFromUrl = null;  // set when the page loads (or a back/
                                 // forward nav lands) on /network/item/...
                                 // before the network snapshot has
                                 // actually arrived to look it up in -
                                 // holds the PARSED {mod,internal,
                                 // damage,kind} object, not a raw string

export function setPendingItemFromUrl(item) {
  pendingItemFromUrl = item;
}

// Clean path-based item URLs (/network/item/mod:internal:damage, or
// /network/item/internal for a fluid) instead of a JSON blob crammed
// into a query string - reuses the mod:internal:damage shape AE2
// already exposes items in natively, not new notation. kind is
// INFERRED from whether the identifier contains a colon, not its own
// segment: a fluid's internal name never contains one (the same
// heuristic already trusted elsewhere in this codebase - see the
// Cryotheum fix), while an item's mod:internal always does. damage
// is omitted from the URL entirely when it's 0 (the common case for
// non-variant items), defaulting back to 0 on parse when absent.
export const NETWORK_ITEM_PATH_PREFIX = '/network/item/';

function itemUrlPath(it) {
  if (it.kind === 'fluid') {
    return NETWORK_ITEM_PATH_PREFIX + encodeURIComponent(it.internal);
  }
  const parts = [it.mod || '', it.internal];
  if (it.damage) parts.push(it.damage);  // omit when 0/falsy
  return NETWORK_ITEM_PATH_PREFIX + parts.map(p => encodeURIComponent(String(p))).join(':');
}

// Returns null if pathname isn't an item URL at all - distinct from
// returning a parsed object, so callers can tell "not an item page"
// from "an item page with fields to use."
export function parseItemUrlPath(pathname) {
  if (!pathname.startsWith(NETWORK_ITEM_PATH_PREFIX)) return null;
  const raw = pathname.slice(NETWORK_ITEM_PATH_PREFIX.length);
  if (!raw) return null;
  const parts = raw.split(':').map(p => decodeURIComponent(p));
  if (parts.length === 1) {
    return { mod: null, internal: parts[0], damage: null, kind: 'fluid' };
  }
  const mod = parts[0] || null;
  const internal = parts[1];
  const damage = parts.length >= 3 ? parseInt(parts[2], 10) : 0;
  return { mod, internal, damage, kind: 'item' };
}

function itemHistoryQueryParams(it) {
  const params = new URLSearchParams();
  if (it.mod) params.set('mod', it.mod);
  params.set('internal', it.internal);
  if (it.damage != null) params.set('damage', it.damage);
  params.set('kind', it.kind || 'item');
  return params;
}

function findNetworkItem(mod, internal, damage, kind) {
  const items = (lastNetworkData && lastNetworkData.items) || [];
  return items.find(it =>
    (it.mod || null) === (mod || null) &&
    it.internal === internal &&
    (it.damage != null ? it.damage : null) === (damage != null ? damage : null) &&
    (it.kind || 'item') === (kind || 'item'));
}

// Crafting job items (ingredients, final_output) carry name/mod/
// internal/damage/icon but not isCraftable or kind - those are
// network-browser-specific fields. If the same item also shows up in
// the last network scan, borrow its richer data (isCraftable in
// particular, so the "Request Craft" button in the history popup
// works correctly) rather than always showing a plain history view
// with no craft option just because the crafting payload itself
// doesn't carry that field.
//
// kind is INFERRED, not assumed to always be "item": AE2's crafting-
// CPU item tracking is genuinely item-shaped, but GT's Fluid
// Discretizer can expose a real fluid AS a pseudo-item within that
// same item-shaped data - the exact same "bare, no-colon name means
// this is actually a fluid" pattern already established for icon
// resolution (see resolve_icon()/split_mod_name() elsewhere in this
// codebase). A bare name means no mod prefix survives the split, so
// mod ends up null here specifically for that case - confirmed by a
// real mismatch (Cryotheum): network_browser.lua correctly recorded
// its history as kind=fluid via the real getFluidsInNetwork() call,
// while this function was unconditionally tagging the same
// substance kind=item when clicked from a crafting ingredient,
// splitting one substance's history across two disagreeing
// identities that never actually shared data.
function openItemHistoryFromCraft(mod, internal, damage, name, icon) {
  if (!internal) return;
  const kind = mod ? 'item' : 'fluid';
  const networkMatch = findNetworkItem(mod, internal, damage, kind);
  openItemHistory({
    name: name,
    mod: mod,
    internal: internal,
    damage: damage,
    icon: (networkMatch && networkMatch.icon) || icon || null,
    size: networkMatch ? networkMatch.size : null,
    isCraftable: networkMatch ? !!networkMatch.isCraftable : false,
    kind: kind,
  });
}

function readDamageFromDataset(raw) {
  if (raw === '' || raw === undefined) return null;
  const n = parseInt(raw, 10);
  return isNaN(n) ? null : n;
}

export function setupCraftHistoryLinks() {
  // Event delegation, same reasoning as the network grid's tooltip -
  // the crafts container is rebuilt wholesale via innerHTML on every
  // poll, so per-element listeners would just be discarded each time.
  document.getElementById('root').addEventListener('click', (e) => {
    const link = e.target.closest('.item-history-link');
    if (!link) return;
    openItemHistoryFromCraft(
      link.dataset.mod || null,
      link.dataset.internal || null,
      readDamageFromDataset(link.dataset.damage),
      link.dataset.name || '?',
      link.dataset.icon || null);
  });
}

// Called once network data is actually available - either right
// after the initial fetch on page load, or after a back/forward nav
// lands on an item URL. Only ever tries once per pending key: if the
// item genuinely isn't in this network, retrying forever on every
// subsequent poll wouldn't find it either.
export function tryOpenItemFromUrl() {
  if (!pendingItemFromUrl) return;
  if (!lastNetworkData || !lastNetworkData.items || lastNetworkData.items.length === 0) return;
  const parsed = pendingItemFromUrl;
  pendingItemFromUrl = null;
  const match = findNetworkItem(parsed.mod, parsed.internal, parsed.damage, parsed.kind);
  if (match) openItemHistory(match, false);  // false: don't push a NEW url, we're already at this one
}

export function openItemHistory(it, pushUrl) {
  if (pushUrl === undefined) pushUrl = true;

  // Item history is a "subpart" of the Network tab's URL space even
  // when opened from elsewhere (e.g. a crafting card's ingredient
  // link) - switching the visible tab to match keeps a click and a
  // fresh page load of the same /network/item/... URL showing the
  // same thing, rather than the URL silently disagreeing with what's
  // actually on screen.
  if (activeTab !== 'network') {
    switchTab('network', false);
  }

  itemHistoryTarget = it;
  itemHistoryRange = 'day';
  document.querySelectorAll('#itemHistoryRangeButtons .range-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === 'day');
  });

  document.getElementById('itemHistoryName').textContent = it.name || '?';
  const iconEl = document.getElementById('itemHistoryIcon');
  if (it.icon) {
    iconEl.src = iconUrl(it.icon);
    iconEl.style.display = '';
  } else {
    iconEl.style.display = 'none';
  }

  // Fast initial paint from whatever quantity we already have (from
  // the network snapshot, or a cross-referenced match if opened from
  // a crafting ingredient) - refined below once fetchItemHistory's
  // own more authoritative "latest" value comes back.
  updateItemHistoryCurrent(it.size, it.kind);
  updateItemHistoryPinButton(it);

  document.getElementById('itemHistoryCraftBtn').style.display = it.isCraftable ? '' : 'none';
  document.getElementById('itemHistoryModal').style.display = 'flex';

  if (pushUrl) {
    history.pushState({ view: 'item' }, '', itemUrlPath(it));
  }

  fetchItemHistory();
}

export function closeItemHistory(pushUrl) {
  if (pushUrl === undefined) pushUrl = true;
  document.getElementById('itemHistoryModal').style.display = 'none';
  itemHistoryTarget = null;
  if (itemHistoryChart) {
    itemHistoryChart.destroy();
    itemHistoryChart = null;
  }
  if (pushUrl && activeTab === 'network') {
    history.pushState({ view: 'network' }, '', '/network');
  }
}

function setItemHistoryRange(range) {
  itemHistoryRange = range;
  document.querySelectorAll('#itemHistoryRangeButtons .range-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === range);
  });
  fetchItemHistory();
}

async function fetchItemHistory() {
  if (!itemHistoryTarget) return;
  try {
    const params = itemHistoryQueryParams(itemHistoryTarget);
    params.set('range', itemHistoryRange);
    const res = await fetch('/api/network/history?' + params.toString());
    const data = await res.json();
    renderItemHistoryChart(data);
  } catch (e) {
    const emptyEl = document.getElementById('itemHistoryEmpty');
    emptyEl.style.display = 'block';
    emptyEl.textContent = 'Could not reach server.';
    document.getElementById('itemHistoryChart').style.display = 'none';
  }
}

function updateItemHistoryCurrent(size, kind) {
  const el = document.getElementById('itemHistoryCurrent');
  if (size == null) {
    el.textContent = '';
    return;
  }
  const unit = kind === 'fluid' ? ' mB' : '';
  el.textContent = 'Currently stored: ' + Math.round(size).toLocaleString() + unit;
}

export function updateItemHistoryPinButton(it) {
  const btn = document.getElementById('itemHistoryPinBtn');
  const isPinned = pinnedItemKeys.has(networkItemKey(it.mod, it.internal, it.damage, it.kind));
  btn.classList.toggle('pinned', isPinned);
  btn.title = isPinned ? 'Unpin this item' : 'Pin this item';
}

// Matches the server's own ITEM_HISTORY_RANGE_SECONDS - duplicated
// here only to compute the chart's fixed axis window client-side;
// the actual query range enforcement still happens server-side.
// Shared by both charts (item history AND power) - same range keys,
// same second values on both the client and server side.
export const CHART_RANGE_SECONDS_JS = { hour: 3600, day: 86400, week: 7 * 86400, month: 30 * 86400, lifetime: null };

// Forces 24-hour, no-seconds formatting throughout the time scale -
// HH is 24-hour in date-fns's token format (hh would be 12-hour with
// AM/PM, easy to mix up). Two SEPARATE Chart.js options need this,
// not one: displayFormats controls the AXIS TICK labels (already
// fixed once before), while tooltipFormat controls the HOVER
// TOOLTIP's title specifically - a completely different formatting
// path that fixing displayFormats alone doesn't touch, which is
// exactly why the tooltip kept showing 12h (and seconds) after the
// axis itself was already fixed. Without an explicit tooltipFormat,
// Chart.js falls back to the date-fns adapter's own locale-default
// formatting, same underlying "don't trust locale defaults for
// this" issue as before, just surfacing through a different option.
const CHART_TIME_DISPLAY_FORMATS = {
  minute: 'HH:mm',
  hour: 'HH:mm',
  day: 'MMM d',
  week: 'MMM d',
  month: 'MMM yyyy',
};
export const CHART_TIME_CONFIG = {
  displayFormats: CHART_TIME_DISPLAY_FORMATS,
  tooltipFormat: 'MMM d, HH:mm',  // minute precision - seconds add
                                  // nothing useful when hovering a
                                  // chart point, only visual noise
};

function renderItemHistoryChart(data) {
  const points = data.points || [];
  const emptyEl = document.getElementById('itemHistoryEmpty');
  const canvas = document.getElementById('itemHistoryChart');

  // data.latest reflects the actual last-recorded value (including
  // the carried-forward leading point for a bounded range - see the
  // server-side comment on why that exists), which is more
  // authoritative than whatever openItemHistory had on hand when the
  // popup first opened - update the display once this real answer
  // arrives, even if a range with no history at all leaves it blank.
  if (data.latest) {
    updateItemHistoryCurrent(data.latest.size, itemHistoryTarget && itemHistoryTarget.kind);
  }

  if (points.length === 0) {
    emptyEl.style.display = 'block';
    emptyEl.textContent = 'No history yet for this item.';
    canvas.style.display = 'none';
    if (itemHistoryChart) { itemHistoryChart.destroy(); itemHistoryChart = null; }
    return;
  }
  emptyEl.style.display = 'none';
  canvas.style.display = '';

  const nowMs = Date.now();
  // {x, y} objects, not a separate labels array - the natural shape
  // for a genuine time scale, where each point carries its own real
  // position rather than relying on index-alignment with a parallel
  // label list.
  const chartPoints = points.map(p => ({ x: p.t * 1000, y: p.size }));
  // A step line only ever holds forward to its NEXT point - without
  // this, an item that hasn't changed recently would visually stop
  // short of the chart's right edge, leaving dead space, even though
  // the value is still genuinely holding right up to now. Appending
  // a synthetic point at the current moment (same value as the last
  // real one) extends the step correctly through to "now" - mirrors
  // the server's own leading-point logic, just for the trailing edge.
  const last = chartPoints[chartPoints.length - 1];
  if (last && last.x < nowMs) {
    chartPoints.push({ x: nowMs, y: last.y });
  }

  const rangeSeconds = CHART_RANGE_SECONDS_JS[data.range];
  // Bounded ranges get a FIXED axis window (now - range to now), so
  // "day" always genuinely shows a full day, evenly spaced by real
  // time, regardless of how few or many actual changes happened
  // inside it - rather than auto-fitting to just the actual data's
  // own span, which is what caused uneven, seemingly-random tick
  // spacing for a sparse, change-only-recorded series. Lifetime has
  // no fixed lower bound by definition, so it's left auto-fitted.
  const axisMin = rangeSeconds ? nowMs - rangeSeconds * 1000 : undefined;
  const axisMax = nowMs;

  if (itemHistoryChart) {
    itemHistoryChart.data.datasets[0].data = chartPoints;
    itemHistoryChart.options.scales.x.min = axisMin;
    itemHistoryChart.options.scales.x.max = axisMax;
    itemHistoryChart.update('none');
    return;
  }

  const ctx = canvas.getContext('2d');
  itemHistoryChart = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [{
        label: 'Quantity',
        data: chartPoints,
        // Item quantity is a step function (constant, then jumps),
        // not a continuously-sampled signal like power draw - a
        // straight diagonal line between two real recorded values
        // would visually imply a gradual change that never actually
        // happened. 'after' holds each value until the NEXT recorded
        // change, matching how the data was actually captured
        // (change-only rows, see the server-side comment). This
        // reads correctly now that the x-axis is a genuine time
        // scale - on the old category axis, evenly-spaced-by-index
        // points made every gap look the same length regardless of
        // how much real time it actually spanned.
        stepped: 'after',
        borderColor: '#5fb3ff',
        backgroundColor: 'rgba(95,179,255,0.15)',
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', axis: 'x', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item) => 'Quantity: ' + Math.round(item.parsed.y).toLocaleString(),
          },
        },
      },
      scales: {
        x: {
          type: 'time',
          min: axisMin,
          max: axisMax,
          time: CHART_TIME_CONFIG,
          ticks: { color: '#8a8f98', maxRotation: 0, autoSkip: true },
          grid: { color: 'rgba(128,128,128,0.1)' },
        },
        y: { beginAtZero: true, ticks: { color: '#8a8f98', callback: (v) => formatQty(v) }, grid: { color: 'rgba(128,128,128,0.1)' } },
      },
    },
  });
}

function requestCraftFromHistory() {
  if (!itemHistoryTarget) return;
  const it = itemHistoryTarget;
  closeItemHistory();
  openCraftRequestModal(it);
}

export function setupHistoryActions() {
  delegateActions(document, {
    'history-range': (el) => setItemHistoryRange(el.dataset.range),
    'history-pin': () => toggleNetworkItemPin(itemHistoryTarget),
    'close-history': () => closeItemHistory(),
    'craft-from-history': () => requestCraftFromHistory(),
  });
  onBackdropClick('itemHistoryModal', () => closeItemHistory());
}
