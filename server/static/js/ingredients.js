// Craft ingredients modal: a grid of one CPU's items, laid out like
// AE2's own crafting status screen, kept live by the crafts poll.
//
// A modal rather than a list inside the card: an open list stretched
// every card in its grid row, and the 3s re-render of the cards reset
// its scroll position. This lives outside #root, and a re-render only
// swaps the grid's contents, so the scroll position survives.

import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import { delegateActions, escapeHtml, formatQty, iconClass, iconUrl, onBackdropClick } from './util.js';

let openCpu = null;       // CPU name the modal is showing, null when closed
let shownItems = new Map(); // item key -> merged item, from the last render
let hoveredKey = null;    // item key the tooltip was last shown for

function itemKey(it) {
  return [it.mod || '', it.internal || '', it.damage ?? '', it.name || ''].join('|');
}

// AE2 reports active/pending/stored as separate lists, and one item is
// often in several at once (some stored, more crafting, more scheduled)
// - the game shows that as one cell with each count, so merge here too.
function mergeItems(job) {
  const merged = new Map();
  const add = (list, field) => {
    for (const it of list || []) {
      const key = itemKey(it);
      let m = merged.get(key);
      if (!m) {
        m = {
          key, name: it.name, icon: it.icon, mod: it.mod, internal: it.internal, damage: it.damage,
          crafting: 0, scheduled: 0, stored: 0,
        };
        merged.set(key, m);
      }
      m[field] += Number(it.size) || 0;
      if (!m.icon && it.icon) m.icon = it.icon;
    }
  };
  add(job.active, 'crafting');
  add(job.pending, 'scheduled');
  add(job.stored, 'stored');
  return merged;
}

// What the card's button counts - cells, not list entries.
export function ingredientCount(job) {
  return mergeItems(job).size;
}

// Crafting first, then scheduled, then stored-only; by name within
// each, so cells don't reshuffle from one poll to the next.
function sortRank(m) {
  return m.crafting > 0 ? 0 : (m.scheduled > 0 ? 1 : 2);
}

function countLines(m) {
  return [['Crafting', m.crafting], ['Scheduled', m.scheduled], ['Stored', m.stored]]
    .filter(([, n]) => n > 0);
}

function cellHtml(m) {
  // Without an icon (no game data installed, or none for this item) a
  // cell of bare counts says nothing about what it is.
  const name = m.icon ? '' : `<div class="ingredient-cell-name">${escapeHtml(m.name || '?')}</div>`;
  const lines = countLines(m)
    .map(([label, n]) => `<div>${label}: <b>${formatQty(n)}</b></div>`)
    .join('');
  const icon = m.icon
    ? `<img class="${iconClass('ingredient-cell-icon', m.icon)}" src="${iconUrl(m.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  const linkAttrs = `data-mod="${escapeHtml(m.mod || '')}" data-internal="${escapeHtml(m.internal || '')}" `
    + `data-damage="${m.damage != null ? m.damage : ''}" data-name="${escapeHtml(m.name || '?')}" `
    + `data-icon="${escapeHtml(m.icon || '')}"`;
  return `<div class="ingredient-cell item-history-link${m.crafting > 0 ? ' crafting' : ''}" data-key="${escapeHtml(m.key)}" ${linkAttrs}>
    <div class="ingredient-cell-lines">${name}${lines}</div>${icon}
  </div>`;
}

function tooltipHtml(m) {
  const stats = countLines(m)
    .map(([label, n]) => `<div class="network-tooltip-stat">${label}: ${n.toLocaleString()}</div>`)
    .join('');
  // A bare name with no mod is how a fluid comes through here (see
  // openItemHistoryFromCraft in history.js).
  return `
    <div class="network-tooltip-name">${escapeHtml(m.name || '?')}</div>
    ${stats}
    ${m.mod ? `<div class="network-tooltip-mod">${escapeHtml(m.mod)}</div>` : `<div class="network-tooltip-fluid">Fluid</div>`}
  `;
}

function renderHead(job) {
  const icon = job.final_output_icon
    ? `<img class="${iconClass('craft-icon', job.final_output_icon)}" src="${iconUrl(job.final_output_icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  document.getElementById('ingredientsTitle').innerHTML =
    `${icon}<span>${escapeHtml(job.final_output || 'Crafting job')}</span>`;
  document.getElementById('ingredientsCpu').textContent =
    `CPU ${job.name || '?'} · storage ${job.storage ?? '?'} · coprocessors ${job.coprocessors ?? '?'}`;
  const progress = document.getElementById('ingredientsProgress');
  if (job.progress_percent != null) {
    progress.innerHTML = `
      <div class="progress-track"><div class="progress-fill"></div></div>
      <div class="progress-label">${escapeHtml(job.progress_percent)}% &middot; ${escapeHtml(job.steps_done ?? '?')}/${escapeHtml(job.steps_total ?? '?')} steps done</div>
    `;
    // Set through the DOM, not an inline style attribute, which the
    // Content-Security-Policy blocks.
    progress.querySelector('.progress-fill').style.width = `${Number(job.progress_percent)}%`;
  } else {
    progress.innerHTML = '';
  }
}

function renderGrid(job) {
  shownItems = mergeItems(job);
  const items = Array.from(shownItems.values()).sort((a, b) =>
    sortRank(a) - sortRank(b) || String(a.name || '').localeCompare(String(b.name || '')));

  const grid = document.getElementById('ingredientsGrid');
  const scrollTop = grid.scrollTop;
  grid.innerHTML = items.length
    ? items.map(cellHtml).join('')
    : '<div class="network-note">No ingredients reported.</div>';
  grid.scrollTop = scrollTop;

  // The cell under a still cursor was just replaced, and no mouseout
  // fires for a removed element - refresh the tooltip's numbers from
  // the same item, or drop it if that item is gone.
  if (tooltipVisible() && hoveredKey) {
    const m = shownItems.get(hoveredKey);
    if (m) showTooltip(tooltipHtml(m)); else hideTooltip();
  }
}

export function openIngredientsModal(cpuName, data) {
  openCpu = cpuName;
  // Cleared first: if the CPU went idle since its card was drawn, the
  // refresh below keeps what's here, which would be another CPU's grid.
  shownItems = new Map();
  for (const id of ['ingredientsTitle', 'ingredientsProgress', 'ingredientsGrid']) {
    document.getElementById(id).innerHTML = '';
  }
  document.getElementById('ingredientsCpu').textContent = `CPU ${cpuName}`;
  document.getElementById('ingredientsGrid').scrollTop = 0;
  document.getElementById('ingredientsModal').style.display = 'flex';
  refreshIngredientsModal(data);
}

export function closeIngredientsModal() {
  openCpu = null;
  hoveredKey = null;
  hideTooltip();
  document.getElementById('ingredientsModal').style.display = 'none';
}

// Called after every crafts poll. Once the job is over (the CPU went
// idle, or vanished), the last grid stays up with a note rather than
// the modal emptying or closing under you.
export function refreshIngredientsModal(data) {
  if (!openCpu || !data) return;
  const job = (data.jobs || []).find(j => j.name === openCpu);
  const live = job && job.busy;
  const note = document.getElementById('ingredientsNote');
  note.style.display = live ? 'none' : '';
  if (!live) {
    note.textContent = 'This CPU has finished - showing the last snapshot.';
    return;
  }
  renderHead(job);
  renderGrid(job);
}

export function setupIngredientsActions() {
  const modal = document.getElementById('ingredientsModal');
  delegateActions(modal, {
    'close-ingredients': () => closeIngredientsModal(),
  });
  onBackdropClick('ingredientsModal', closeIngredientsModal);
  bindCellTooltip(document.getElementById('ingredientsGrid'), '.ingredient-cell', (cell) => {
    const m = shownItems.get(cell.dataset.key);
    hoveredKey = m ? m.key : null;
    return m ? tooltipHtml(m) : null;
  });
}
