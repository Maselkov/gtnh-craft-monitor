// Crafts tab: "Recent crafts", every job that ended on any CPU, newest
// first. It lives outside #root, so the 3s re-render leaves it alone;
// it only fetches while open, and after the first page only asks for
// rows newer than the newest it has. Older pages load as the page
// nears the bottom, like the network grid. The filter and status
// buttons are applied by the server, so they cover every page, not
// just the ones loaded.

import { activeTab } from './tabs.js';
import {
  bindFilterInput, delegateActions, escapeHtml, formatDuration, formatRelativeTime, iconClass, iconUrl,
} from './util.js';

const PAGE_SIZE = 25;
const FILTER_DELAY_MS = 250;

let events = [];      // newest first
let more = false;     // whether older rows exist past the last one shown
let loaded = false;
let loading = false;
let query = '';
let status = '';
let generation = 0;   // bumped when the filter changes, so replies to
                      // the old one are dropped
let filterTimer = null;

export function setupCraftHistory() {
  const section = document.getElementById('craftHistory');
  section.addEventListener('toggle', () => {
    if (section.open) refreshCraftHistory();
  });
  window.addEventListener('scroll', onScroll);
  window.addEventListener('resize', onScroll);
  const filter = document.getElementById('craftHistoryFilter');
  bindFilterInput(filter, () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => setFilter(filter.value.trim(), status), FILTER_DELAY_MS);
  });
  delegateActions(section, {
    'history-status': (el) => setFilter(query, el.dataset.status),
  });
}

function setFilter(newQuery, newStatus) {
  if (newQuery === query && newStatus === status) return;
  query = newQuery;
  status = newStatus;
  document.querySelectorAll('#craftHistoryStatus [data-action="history-status"]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.status === status);
  });
  generation++;
  events = [];
  more = false;
  loaded = false;
  loading = false;
  refreshCraftHistory();
}

let scrollTicking = false;
function onScroll() {
  if (scrollTicking) return;
  scrollTicking = true;
  requestAnimationFrame(() => {
    scrollTicking = false;
    const nearBottom = (window.scrollY + window.innerHeight) > (document.documentElement.scrollHeight - 600);
    if (nearBottom) loadOlder();
  });
}

async function fetchPage(params) {
  const filters = {};
  if (query) filters.q = query;
  if (status) filters.status = status;
  const search = new URLSearchParams({ limit: PAGE_SIZE, ...filters, ...params });
  const res = await fetch(`/api/crafts/history?${search}`);
  if (!res.ok) throw new Error(`history: ${res.status}`);
  return res.json();
}

// Called on the crafts tab's 3s refresh, and when the section opens.
export async function refreshCraftHistory() {
  const section = document.getElementById('craftHistory');
  if (!section.open || loading) return;
  loading = true;
  const gen = generation;
  try {
    if (!loaded || events.length === 0) {
      const page = await fetchPage({});
      if (gen !== generation) return;
      events = page.events;
      more = page.more;
      loaded = true;
    } else {
      const page = await fetchPage({ after: events[0].id });
      if (gen !== generation) return;
      if (page.more) {
        // Too many new ones to splice in: start over from the top.
        const first = await fetchPage({});
        if (gen !== generation) return;
        events = first.events;
        more = first.more;
      } else {
        events = page.events.concat(events);
      }
    }
  } catch (e) {
    // Keep what's shown; the next refresh tries again.
  } finally {
    if (gen === generation) loading = false;
  }
  if (gen === generation) render();
}

async function loadOlder() {
  const section = document.getElementById('craftHistory');
  if (activeTab !== 'crafts' || !section.open || !more || loading || events.length === 0) return;
  loading = true;
  const gen = generation;
  document.getElementById('craftHistoryLoading').hidden = false;
  try {
    const page = await fetchPage({ before: events[events.length - 1].id });
    if (gen !== generation) return;
    events = events.concat(page.events);
    more = page.more;
  } catch (e) { /* ignore - the next scroll tries again */ }
  finally {
    if (gen === generation) loading = false;
  }
  if (gen === generation) render();
}

function renderRow(e) {
  const icon = e.icon
    ? `<img class="${iconClass('craft-icon', e.icon)}" src="${iconUrl(e.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '<span class="craft-icon"></span>';
  const name = escapeHtml(e.itemName || 'Unknown item');
  // Rows saved before items were recorded with them can't open a chart.
  const title = e.internal
    ? `<span class="item-history-link" data-mod="${escapeHtml(e.mod || '')}" data-internal="${escapeHtml(e.internal)}" data-damage="${e.damage != null ? escapeHtml(e.damage) : ''}" data-name="${name}" data-icon="${escapeHtml(e.icon || '')}">${name}</span>`
    : `<span>${name}</span>`;
  const badge = (e.auto ? '<span class="status-badge auto" title="Started to keep this item in stock">Auto</span>' : '')
    + (e.status === 'incomplete'
      ? `<span class="status-badge incomplete">Incomplete${e.progress != null ? ` &middot; ${escapeHtml(e.progress)}%` : ''}</span>`
      : '');
  const took = e.startedAt != null ? ` &middot; took ${formatDuration(e.finishedAt - e.startedAt)}` : '';
  const when = new Date(e.finishedAt * 1000).toLocaleString();
  return `
    <div class="history-row">
      ${icon}
      <div class="history-row-main">
        <div class="history-row-name">${title}${badge}</div>
        <div class="cpu-id">CPU ${escapeHtml(e.cpu)}${took}</div>
      </div>
      <div class="history-row-when" title="${escapeHtml(when)}">${formatRelativeTime(e.finishedAt)}</div>
    </div>
  `;
}

// "Today", "Yesterday", or the date, for a Unix time in seconds.
function dayLabel(seconds) {
  const day = new Date(seconds * 1000).setHours(0, 0, 0, 0);
  const today = new Date().setHours(0, 0, 0, 0);
  if (day === today) return 'Today';
  if (today - day <= 86400000 * 1.5) return 'Yesterday';  // 1.5: a DST day is 23 or 25 hours
  return new Date(day).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function render() {
  const list = document.getElementById('craftHistoryList');
  let html = '';
  let lastDay = null;
  for (const e of events) {
    const day = dayLabel(e.finishedAt);
    if (day !== lastDay) {
      html += `<div class="history-day">${escapeHtml(day)}</div>`;
      lastDay = day;
    }
    html += renderRow(e);
  }
  const none = query || status ? 'No crafts match.' : 'No crafts have ended yet.';
  list.innerHTML = html || (loaded ? `<div class="empty">${none}</div>` : '');
  document.getElementById('craftHistoryLoading').hidden = true;
  // A page short enough not to scroll never fires a scroll event, so
  // check now too. A load renders again, which checks again, until the
  // page reaches past the window or nothing older is left.
  onScroll();
}
