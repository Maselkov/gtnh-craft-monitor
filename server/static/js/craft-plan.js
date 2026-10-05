// Crafting plan in the craft-request dialog: what asking AE2 for this
// amount would take, before the request is sent - worked out on the
// server from the network's patterns and stock (gcm/planner.py).
//
// Two views, like AE2's own Request Craft screen: a list of every item
// the plan touches, coloured by how much of its stock it uses, and the
// recipe tree. The game draws its tree as a canvas of icons to drag
// around; here it's an outline, readable without hovering and usable on
// a phone, keeping the game's "Hide all available".

import { bindCellTooltip } from './tooltip.js';
import { delegateActions, escapeHtml, formatQty, formatRelativeTime, iconClass, iconUrl } from './util.js';

const VIEW_KEY = 'gtnhCraftMonitor.planView';
const HIDE_KEY = 'gtnhCraftMonitor.planHideAvailable';
const FETCH_DELAY_MS = 300;

let target = null;      // the item the dialog is for
let amount = null;      // the last valid amount asked for
let choices = {};       // item key -> pattern id picked in the tree
let openState = new Map();  // tree path -> open, where the user toggled it
let lastPlan = null;    // the last /api/network/plan answer's plan
let itemsByKey = new Map();
let fetchTimer = null;
let inFlight = null;    // AbortController of the request under way

function readSetting(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
}

function writeSetting(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* per-viewer nicety only */ }
}

// Per viewer, read in setupCraftPlanActions().
let view = 'list';
let hideAvailable = false;

// ---------- formatting (pure, tested under server/tests/js/) ----------

export function qty(n, kind) {
  return formatQty(n) + (kind === 'fluid' ? ' mB' : '');
}

// The game's colours for how much of an item's stock a plan uses:
// all of it red, then orange, green and blue as it gets less.
export function usedBand(percent) {
  if (percent >= 100) return 'used-all';
  if (percent >= 50) return 'used-high';
  if (percent >= 25) return 'used-mid';
  return 'used-low';
}

export function usedPercent(item) {
  return item.available > 0 ? (item.from_stock / item.available) * 100 : 0;
}

// "need 64 · 20 in stock · craft 44 (11×)" for a step of the tree.
export function nodeQtyText(node, isRoot) {
  const parts = [];
  if (!isRoot) parts.push('need ' + qty(node.need, node.kind));
  if (node.from_stock > 0) {
    parts.push(node.status === 'stock' ? 'in stock' : qty(node.from_stock, node.kind) + ' in stock');
  }
  // Batches only say something when one makes more than one.
  if (node.status === 'craft') {
    parts.push(`craft ${qty(node.craft, node.kind)}` + (node.batches !== node.craft ? ` (${node.batches}×)` : ''));
  }
  if (node.status === 'missing') parts.push('missing ' + qty(node.missing, node.kind));
  if (node.status === 'cycle') parts.push(`missing ${qty(node.missing, node.kind)}: its recipe needs itself`);
  return parts.join(' · ');
}

// Missing first, then by how much of the stock is used, then what's
// only crafted; by name within each, so cells don't jump around as the
// amount changes.
export function sortListItems(items) {
  const rank = (i) => (i.missing > 0 ? 0 : i.from_stock > 0 ? 1 : 2);
  return [...items].sort((a, b) => rank(a) - rank(b)
    || (rank(a) === 0 ? b.missing - a.missing : 0)
    || (rank(a) === 1 ? usedPercent(b) - usedPercent(a) : 0)
    || String(a.name || '').localeCompare(String(b.name || '')));
}

function iconHtml(it, cls) {
  return it.icon
    ? `<img class="${iconClass(cls, it.icon)}" src="${iconUrl(it.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
}

// "below your alert (10k)" for a stock rule the plan would break.
export function warningText(warning, kind) {
  return `${warning.rule} (${qty(warning.threshold, kind)})`;
}

function displayName(it) {
  return (it.name || '?') + (it.variant_name ? ` (${it.variant_name})` : '');
}

// ---------- list view ----------

function cellHtml(it) {
  const lines = [];
  if (!it.icon) lines.push(`<div class="ingredient-cell-name">${escapeHtml(displayName(it))}</div>`);
  if (it.missing > 0) lines.push(`<div class="plan-missing-line">Missing: <b>${qty(it.missing, it.kind)}</b></div>`);
  if (it.from_stock > 0) {
    const pct = usedPercent(it);
    lines.push(`<div>Available: <b>${qty(it.available, it.kind)}</b></div>`);
    lines.push(`<div class="plan-used ${usedBand(pct)}">Used: ${pct >= 99.95 ? '100' : pct.toFixed(pct < 10 ? 2 : 1)}%</div>`);
  }
  if (it.craft > 0) lines.push(`<div>Crafting: <b>${qty(it.craft, it.kind)}</b></div>`);
  if (it.warnings) lines.push('<div class="plan-warning-line">Below a stock rule</div>');
  const state = it.missing > 0 ? ' missing' : (it.craft > 0 && !it.from_stock ? ' crafting' : '');
  return `<div class="ingredient-cell plan-cell${state}" data-key="${escapeHtml(it.key)}">
    <div class="ingredient-cell-lines">${lines.join('')}</div>${iconHtml(it, 'ingredient-cell-icon')}
  </div>`;
}

function cellTooltipHtml(it) {
  const stat = (label, n) => `<div class="network-tooltip-stat">${label}: ${qty(n, it.kind)}</div>`;
  return `
    <div class="network-tooltip-name">${escapeHtml(displayName(it))}</div>
    ${stat('Needed', it.need)}
    ${it.from_stock > 0 ? stat('From stock', it.from_stock) + stat('In stock', it.available) : ''}
    ${it.craft > 0 ? stat('Crafted', it.craft) : ''}
    ${it.missing > 0 ? stat('Missing', it.missing) : ''}
    ${(it.warnings || []).map(w => `<div class="network-tooltip-stat plan-warning-line">${qty(it.left, it.kind)} left: ${escapeHtml(warningText(w, it.kind))}</div>`).join('')}
    ${it.kind === 'fluid' ? '<div class="network-tooltip-fluid">Fluid</div>' : ''}
  `;
}

function renderList(plan) {
  return `<div class="ingredients-grid plan-grid">${sortListItems(plan.items).map(cellHtml).join('')}</div>`;
}

// ---------- tree view ----------

function nodeHtml(node, parentPath, depth) {
  const path = parentPath ? parentPath + ' > ' + node.key : node.key;
  const children = (node.children || []).filter(c => !(hideAvailable && c.status === 'stock'));
  const open = openState.has(path) ? openState.get(path) : depth < 2;
  const caret = children.length
    ? `<button class="plan-caret" data-action="plan-toggle" data-path="${escapeHtml(path)}" aria-expanded="${open}" title="${open ? 'Collapse' : 'Expand'}">${open ? '▾' : '▸'}</button>`
    : '<span class="plan-caret"></span>';

  const details = [];
  if (node.pattern) details.push(escapeHtml(node.pattern.provider || '?'));
  if (node.inexact) details.push('output count unknown, counted as 1');
  for (const also of node.also_makes || []) details.push(`also makes ${qty(also.amount, also.kind)} ${escapeHtml(also.name || '?')}`);
  if (node.truncated) details.push('plan cut short here');
  const item = itemsByKey.get(node.key);
  if (item && item.warnings) {
    details.push(`<span class="plan-warning-line">${item.warnings.map(w => escapeHtml(warningText(w, item.kind))).join(', ')}</span>`);
  }

  const alternatives = node.alternatives
    ? `<select class="plan-alt" data-key="${escapeHtml(node.key)}" title="Other patterns that make this">${
      node.alternatives.map(a => `<option value="${escapeHtml(a.id)}"${a.id === node.pattern.id ? ' selected' : ''}>${
        escapeHtml(a.provider || '?')}: ${escapeHtml(a.inputs.length > 70 ? a.inputs.slice(0, 69) + '…' : a.inputs)}</option>`).join('')
    }</select>`
    : '';

  return `<li class="plan-node status-${escapeHtml(node.status)}">
    <div class="plan-row">
      ${caret}${iconHtml(node, 'plan-icon')}
      <div class="plan-text">
        <div><span class="plan-name">${escapeHtml(displayName(node))}</span> <span class="plan-qty">${nodeQtyText(node, depth === 0)}</span></div>
        ${details.length ? `<div class="plan-details">${details.join(' · ')}</div>` : ''}
        ${alternatives}
      </div>
    </div>
    ${open && children.length ? `<ul>${children.map(c => nodeHtml(c, path, depth + 1)).join('')}</ul>` : ''}
  </li>`;
}

function renderTree(plan) {
  return `<ul class="plan-tree">${nodeHtml(plan.root, '', 0)}</ul>`;
}

// ---------- the whole panel ----------

function summaryHtml(data) {
  const plan = data.plan;
  const parts = [];
  if (plan.missing.length) {
    const chips = plan.missing.map(m => `<span class="plan-chip">${iconHtml(m, 'plan-chip-icon')}<b>${qty(m.missing, m.kind)}</b> ${escapeHtml(displayName(m))}</span>`);
    parts.push(`<div class="plan-missing-strip"><span class="plan-missing-label">Missing:</span> ${chips.join('')}</div>`);
  } else {
    parts.push('<div class="plan-ok">Everything is in stock or craftable.</div>');
  }
  for (const it of plan.items.filter(i => i.warnings)) {
    parts.push(`<div class="plan-warning-line">${escapeHtml(displayName(it))}: ${qty(it.left, it.kind)} left, ${
      it.warnings.map(w => escapeHtml(warningText(w, it.kind))).join(', ')}</div>`);
  }
  const meta = [`${plan.steps.toLocaleString()} steps`];
  if (data.patterns_updated_at) meta.push('patterns read ' + formatRelativeTime(data.patterns_updated_at));
  if (data.stock_updated_at) meta.push('stock scanned ' + formatRelativeTime(data.stock_updated_at));
  if (plan.truncated) meta.push('too big to show in full');
  parts.push(`<div class="plan-meta">${meta.join(' · ')}</div>`);
  return parts.join('');
}

function setModalWide(wide) {
  document.querySelector('#craftRequestModal .modal-box').classList.toggle('with-plan', wide);
}

function showNote(text) {
  const note = document.getElementById('craftPlanNote');
  note.textContent = text || '';
  note.hidden = !text;
}

function render() {
  const panel = document.getElementById('craftPlan');
  if (!lastPlan) {
    panel.hidden = true;
    setModalWide(false);
    return;
  }
  document.querySelectorAll('#craftPlan [data-action="plan-view"]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === view);
  });
  const hide = document.getElementById('craftPlanHide');
  hide.hidden = view !== 'tree';
  hide.classList.toggle('active', hideAvailable);
  hide.setAttribute('aria-pressed', String(hideAvailable));

  const body = document.getElementById('craftPlanBody');
  const scrollTop = body.scrollTop;
  body.innerHTML = view === 'tree' ? renderTree(lastPlan.plan) : renderList(lastPlan.plan);
  body.scrollTop = scrollTop;
  document.getElementById('craftPlanSummary').innerHTML = summaryHtml(lastPlan);
  panel.hidden = false;
  setModalWide(true);
}

async function fetchPlan() {
  fetchTimer = null;
  if (!target || !amount) return;
  if (inFlight) inFlight.abort();
  const controller = new AbortController();
  inFlight = controller;
  const params = new URLSearchParams({ internal: target.internal, amount: String(amount), kind: target.kind || 'item' });
  if (target.mod) params.set('mod', target.mod);
  if (target.damage != null) params.set('damage', String(target.damage));
  if (target.variant) params.set('variant', target.variant);
  if (Object.keys(choices).length) params.set('choose', JSON.stringify(choices));
  try {
    const res = await fetch('/api/network/plan?' + params, { signal: controller.signal });
    const data = await res.json().catch(() => ({}));
    if (controller !== inFlight) return;  // a newer request took over
    if (!res.ok || !data.plan) {
      lastPlan = null;
      showNote(res.ok ? data.reason : (data.error || 'Couldn\'t work out a plan.'));
    } else {
      lastPlan = data;
      itemsByKey = new Map(data.plan.items.map(i => [i.key, i]));
      showNote(null);
    }
    render();
  } catch (e) {
    if (e.name !== 'AbortError' && controller === inFlight) showNote('Couldn\'t reach the server for a plan.');
  } finally {
    if (controller === inFlight) inFlight = null;
  }
}

function scheduleFetch(delay) {
  if (fetchTimer) clearTimeout(fetchTimer);
  fetchTimer = setTimeout(fetchPlan, delay);
}

// The dialog opened for an item; amountValue is what the quantity
// box holds (it may still be an expression being typed - see
// updateCraftPlanAmount).
export function openCraftPlan(it, amountValue) {
  target = it;
  choices = {};
  openState = new Map();
  lastPlan = null;
  itemsByKey = new Map();
  amount = null;
  render();
  showNote('Working out what this takes…');
  updateCraftPlanAmount(amountValue, 0);
}

// amountValue: a whole number, or null while the quantity box doesn't
// hold one - then the last plan stays up rather than flickering away.
export function updateCraftPlanAmount(amountValue, delay = FETCH_DELAY_MS) {
  if (!target || !amountValue || amountValue < 1 || amountValue === amount) return;
  amount = amountValue;
  scheduleFetch(delay);
}

export function closeCraftPlan() {
  target = null;
  amount = null;
  lastPlan = null;
  if (fetchTimer) clearTimeout(fetchTimer);
  fetchTimer = null;
  if (inFlight) inFlight.abort();
  inFlight = null;
  showNote(null);
  render();
}

export function setupCraftPlanActions() {
  view = readSetting(VIEW_KEY, 'list') === 'tree' ? 'tree' : 'list';
  hideAvailable = readSetting(HIDE_KEY, '0') === '1';
  const panel = document.getElementById('craftPlan');
  delegateActions(panel, {
    'plan-view': (el) => {
      view = el.dataset.view === 'tree' ? 'tree' : 'list';
      writeSetting(VIEW_KEY, view);
      render();
    },
    'plan-hide-available': () => {
      hideAvailable = !hideAvailable;
      writeSetting(HIDE_KEY, hideAvailable ? '1' : '0');
      render();
    },
    'plan-toggle': (el) => {
      const path = el.dataset.path;
      openState.set(path, el.getAttribute('aria-expanded') !== 'true');
      render();
    },
  });
  panel.addEventListener('change', (e) => {
    const select = e.target.closest('select.plan-alt');
    if (!select) return;
    choices[select.dataset.key] = select.value;
    scheduleFetch(0);
  });
  bindCellTooltip(document.getElementById('craftPlanBody'), '.plan-cell', (cell) => {
    const it = itemsByKey.get(cell.dataset.key);
    return it ? cellTooltipHtml(it) : null;
  });
}
