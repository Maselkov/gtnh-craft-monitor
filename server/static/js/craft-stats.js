// Crafts tab: "Statistics", totals, the most crafted items and how
// busy each CPU was over a range, from the jobs that ended, and the
// machines running jobs were held up at (gcm/bottlenecks.py). Like
// Recent crafts it lives outside #root and only fetches while open:
// when it opens, when the range changes, and every REFRESH_MS after.

import { nameText } from './description.js';
import { delegateActions, escapeHtml, formatDuration, iconClass, iconUrl, machinePlace } from './util.js';

const REFRESH_MS = 30000;

let range = 'week';
let lastFetchAt = 0;
let generation = 0;  // replies for a range no longer picked are dropped

export function setupCraftStats() {
  const section = document.getElementById('craftStats');
  section.addEventListener('toggle', () => {
    if (section.open) refreshCraftStats(true);
  });
  delegateActions(section, {
    'stats-range': (el) => {
      range = el.dataset.range;
      section.querySelectorAll('[data-action="stats-range"]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.range === range);
      });
      refreshCraftStats(true);
    },
  });
}

// Called on the crafts tab's 3s refresh too; that only fetches once
// REFRESH_MS has passed.
export async function refreshCraftStats(now = false) {
  const section = document.getElementById('craftStats');
  if (!section.open) return;
  if (!now && Date.now() - lastFetchAt < REFRESH_MS) return;
  lastFetchAt = Date.now();
  const gen = ++generation;
  try {
    const res = await fetch(`/api/crafts/stats?range=${encodeURIComponent(range)}`);
    if (!res.ok) throw new Error(`stats: ${res.status}`);
    const data = await res.json();
    if (gen !== generation) return;
    const body = document.getElementById('craftStatsBody');
    body.innerHTML = renderStats(data);
    for (const bar of body.querySelectorAll('.stats-bar-fill')) {
      bar.style.width = `${Number(bar.dataset.fill)}%`;
    }
  } catch (e) {
    // Keep what's shown; the next refresh tries again.
  }
}

function percent(part, whole) {
  return whole ? Math.round(part / whole * 100) : 0;
}

function bar(fill) {
  return `<div class="stock-bar stats-bar"><div class="stats-bar-fill" data-fill="${Number(fill)}"></div></div>`;
}

function itemRow(item, maxCount) {
  const icon = item.icon
    ? `<img class="${iconClass('craft-icon', item.icon)}" src="${iconUrl(item.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '<span class="craft-icon"></span>';
  const name = escapeHtml(nameText(item.itemName || 'Unknown item'));
  const title = item.internal
    ? `<span class="item-history-link" data-mod="${escapeHtml(item.mod || '')}" data-internal="${escapeHtml(item.internal)}" data-damage="${item.damage != null ? escapeHtml(item.damage) : ''}" data-name="${escapeHtml(item.itemName || 'Unknown item')}" data-icon="${escapeHtml(item.icon || '')}">${name}</span>`
    : `<span>${name}</span>`;
  const details = [`${item.count}&times;`];
  if (item.medianSeconds != null) details.push(`usually ${formatDuration(item.medianSeconds)}`);
  if (item.incomplete) details.push(`${item.incomplete} incomplete`);
  return `
    <div class="history-row stats-row">
      ${icon}
      <div class="history-row-main">
        <div class="history-row-name">${title}</div>
        <div class="cpu-id">${details.join(' &middot; ')}</div>
        ${bar(percent(item.count, maxCount))}
      </div>
    </div>
  `;
}

function cpuRow(cpu) {
  const busy = Math.round(cpu.busyFraction * 100);
  return `
    <div class="history-row stats-row">
      <div class="history-row-main">
        <div class="history-row-name">CPU ${escapeHtml(cpu.cpu)}</div>
        <div class="cpu-id">busy ${busy}% &middot; ${formatDuration(cpu.busySeconds)} &middot; ${cpu.count} ${cpu.count === 1 ? 'job' : 'jobs'}</div>
        ${bar(busy)}
      </div>
    </div>
  `;
}

// A machine jobs were held up at: what the whole job waited on it first,
// as that's what it cost, then why steps there were held up. The bar is
// cost(m) of the most.
function machineRow(m, cost, maxCost) {
  const place = machinePlace(m);
  const details = [];
  if (m.critical >= 1) details.push(`jobs waited ${formatDuration(m.critical)}`);
  if (m.blocked >= 1) details.push(`couldn't send ${formatDuration(m.blocked)}`);
  if (m.busy >= 1) details.push(`busy elsewhere ${formatDuration(m.busy)}`);
  if (m.stalled >= 1) details.push(`nothing back ${formatDuration(m.stalled)}`);
  details.push(`${m.jobs} ${m.jobs === 1 ? 'job' : 'jobs'}`);
  return `
    <div class="history-row stats-row">
      <div class="history-row-main">
        <div class="history-row-name">${escapeHtml(nameText(m.name || '?'))}${place ? ` <span class="cpu-id">${escapeHtml(place)}</span>` : ''}</div>
        <div class="cpu-id">${details.join(' &middot; ')}</div>
        ${bar(percent(cost(m), maxCost))}
      </div>
    </div>
  `;
}

function machinesColumn(machines) {
  if (!machines.length) return '';
  // As the server ranks them: what whole jobs waited, or while no job has
  // waited on any, how long steps were held up there.
  const cost = machines.some(m => m.critical > 0) ? (m) => m.critical : (m) => m.blocked + m.busy + m.stalled;
  const maxCost = Math.max(...machines.map(cost), 1);
  return `
      <div class="craft-history-list">
        <div class="history-day" title="Where running jobs' steps were held up: a step its machine wouldn't take, a machine busy with another step, or one giving nothing back for a long while">Held up at</div>
        ${machines.map(m => machineRow(m, cost, maxCost)).join('')}
      </div>`;
}

export function renderStats(data) {
  const machines = data.bottlenecks || [];
  if (!data.total) {
    if (!machines.length) return '<div class="empty">No crafts ended in this range.</div>';
    return `
    <div class="stats-summary">No crafts ended in this range.</div>
    <div class="stats-columns">${machinesColumn(machines)}
    </div>
  `;
  }
  const summary = [`${data.total} ${data.total === 1 ? 'craft' : 'crafts'}`];
  summary.push(`${data.distinctItems} ${data.distinctItems === 1 ? 'item' : 'items'}`);
  if (data.incomplete) summary.push(`${data.incomplete} incomplete (${percent(data.incomplete, data.total)}%)`);
  if (data.auto) summary.push(`${data.auto} auto`);
  const maxCount = Math.max(...data.items.map(i => i.count), 1);
  return `
    <div class="stats-summary">${summary.join(' &middot; ')}</div>
    <div class="stats-columns">
      <div class="craft-history-list">
        <div class="history-day">Most crafted</div>
        ${data.items.map(i => itemRow(i, maxCount)).join('')}
      </div>
      <div class="craft-history-list">
        <div class="history-day" title="Time spent on jobs that ended in this range, from when the server saw them start">CPU busy time</div>
        ${data.cpus.map(cpuRow).join('')}
      </div>${machinesColumn(machines)}
    </div>
  `;
}
