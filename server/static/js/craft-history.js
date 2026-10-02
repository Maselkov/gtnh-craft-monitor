// Crafts tab: "Recent crafts", every job that ended on any CPU, newest
// first. It lives outside #root, so the 3s re-render leaves it alone;
// it only fetches while open, and after the first page only asks for
// rows newer than the newest it has. Older pages load as the page
// nears the bottom, like the network grid.

import { activeTab } from './tabs.js';
import { escapeHtml, formatDuration, formatRelativeTime, iconClass, iconUrl } from './util.js';

const PAGE_SIZE = 25;

let events = [];      // newest first
let more = false;     // whether older rows exist past the last one shown
let loaded = false;
let loading = false;

export function setupCraftHistory() {
  const section = document.getElementById('craftHistory');
  section.addEventListener('toggle', () => {
    if (section.open) refreshCraftHistory();
  });
  window.addEventListener('scroll', onScroll);
  window.addEventListener('resize', onScroll);
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
  const query = new URLSearchParams({ limit: PAGE_SIZE, ...params });
  const res = await fetch(`/api/crafts/history?${query}`);
  if (!res.ok) throw new Error(`history: ${res.status}`);
  return res.json();
}

// Called on the crafts tab's 3s refresh, and when the section opens.
export async function refreshCraftHistory() {
  const section = document.getElementById('craftHistory');
  if (!section.open || loading) return;
  loading = true;
  try {
    if (!loaded || events.length === 0) {
      const page = await fetchPage({});
      events = page.events;
      more = page.more;
      loaded = true;
    } else {
      const page = await fetchPage({ after: events[0].id });
      if (page.more) {
        // Too many new ones to splice in: start over from the top.
        const first = await fetchPage({});
        events = first.events;
        more = first.more;
      } else {
        events = page.events.concat(events);
      }
    }
  } catch (e) {
    // Keep what's shown; the next refresh tries again.
  } finally {
    loading = false;
  }
  render();
}

async function loadOlder() {
  const section = document.getElementById('craftHistory');
  if (activeTab !== 'crafts' || !section.open || !more || loading || events.length === 0) return;
  loading = true;
  document.getElementById('craftHistoryLoading').hidden = false;
  try {
    const page = await fetchPage({ before: events[events.length - 1].id });
    events = events.concat(page.events);
    more = page.more;
  } catch (e) { /* ignore - the next scroll tries again */ }
  finally {
    loading = false;
  }
  render();
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
  list.innerHTML = html || '<div class="empty">No crafts have ended yet.</div>';
  document.getElementById('craftHistoryLoading').hidden = true;
  // A page short enough not to scroll never fires a scroll event, so
  // check now too. A load renders again, which checks again, until the
  // page reaches past the window or nothing older is left.
  onScroll();
}
