// Batches: several items to request together (gcm/batches.py). Each
// operator has one batch being put together, kept on the server so it
// follows them between browsers. Items are added from the Batch dialog's
// search, the craft dialog's "Add to batch", or a multiblock kit's.
//
// Where the game has a kit interface, a batch is requested as one job
// the same way a kit is (/api/craft/request-batch-job): AE2 plans the
// items together, so what they share is made once. Otherwise, or when
// the viewer ticks "separately", each item is its own request.

import { isOperator } from './auth.js';
import { closeCraftPlan, kitPlanOpen, openKitPlan } from './craft-plan.js';
import { fetchCraftRequests, showToast } from './craft-actions.js';
import { nameText } from './description.js';
import { lookupItemTooltipHtml, onShiftChange } from './item-tooltip.js';
import { setupKitPickers } from './kit-picker.js';
import { fetchNetwork, lastNetworkData, networkItemKey } from './network.js';
import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import {
  delegateActions, docsUrl, escapeHtml, formatQty, iconClass, iconUrl, onBackdropClick, TERMINAL_DOCS_URL, updateHtml,
} from './util.js';

const SEPARATE_KEY = 'gtnhCraftMonitor.batchSeparate';
const KIT_INTERFACE_DOCS_URL = docsUrl('in-game.md#kit-interface');
const SAVE_DELAY_MS = 400;
// The most matches the item search lists: it's typed into, not scrolled.
const SEARCH_LIMIT = 60;

let batch = null;        // the last /api/batch answer
let reviewing = false;
let planStatus = 'idle';
let submitting = false;
let separate = false;
let saveTimer = null;
let editSeq = 0;         // bumped by each local change, so an older answer doesn't undo it
let pickers = null;
let finder = null;
let tooltipIcon = null;

function readSeparate() {
  try { return localStorage.getItem(SEPARATE_KEY) === '1'; } catch (e) { return false; }
}

function writeSeparate(on) {
  try {
    if (on) localStorage.setItem(SEPARATE_KEY, '1'); else localStorage.removeItem(SEPARATE_KEY);
  } catch (e) { /* per-viewer nicety only */ }
}

function rows() {
  return batch?.items || [];
}

// Whether Request makes one job of the batch.
function oneJob() {
  return !!(batch && batch.kit_slots > 0) && !separate;
}

function plural(n, word) {
  return `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
}

function qty(n, kind) {
  return kind === 'fluid' ? `${formatQty(n)} mB` : formatQty(n);
}

// Why the batch can't be requested as it is, or null.
function problem() {
  const list = rows();
  if (!list.length) return null;
  const missing = list.filter(r => !r.craftable);
  if (missing.length) {
    return `No pattern for ${missing.map(r => nameText(r.name)).join(', ')}: remove ${missing.length === 1 ? 'it' : 'them'} to request the batch.`;
  }
  if (oneJob()) {
    if (!list.some(r => r.kind === 'item')) return 'As one job, a batch needs at least one item, not only fluids.';
    if (!batch.kit_fluids && list.some(r => r.kind === 'fluid')) {
      return 'Your craft_monitor.lua can\'t put fluids in a batch: update it, or craft each item separately.';
    }
  }
  return null;
}

function updateButton() {
  const btn = document.getElementById('networkBatchButton');
  btn.hidden = !isOperator();
  const n = rows().length;
  btn.querySelector('button').textContent = n ? `Batch (${n})` : 'Batch';
}

// The Network tab's button and its count. Called on sign-in and out.
export async function refreshBatch() {
  if (!isOperator()) {
    batch = null;
    updateButton();
    return;
  }
  const seq = editSeq;
  try {
    const res = await fetch('/api/batch');
    if (!res.ok) return;
    const data = await res.json();
    if (seq === editSeq) batch = data;
  } catch (e) {
    return;
  } finally {
    updateButton();
  }
  if (batchOpen()) render();
}

// Adds items ([{key, amount, name, from?}]) to the batch; `what` names
// them in the toast. Returns whether they were added.
export async function addToBatch(items, what) {
  if (!items.length) return false;
  ++editSeq;
  if (saveTimer) await saveNow();
  try {
    const res = await fetch('/api/batch/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(data.error || 'Could not add to the batch.', true);
      return false;
    }
    batch = data;
    updateButton();
    if (batchOpen()) render();
    showToast(`Added ${what} to the batch (${plural(rows().length, 'item')}). Open it from the Network tab.`);
    return true;
  } catch (e) {
    showToast('Could not reach server.', true);
    return false;
  }
}

function saveBody() {
  return JSON.stringify({ items: rows().map(r => ({ key: r.key, amount: r.amount, name: nameText(r.name), from: r.from })) });
}

function scheduleSave() {
  ++editSeq;
  updateButton();
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, SAVE_DELAY_MS);
}

async function saveNow() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = null;
  const seq = editSeq;
  try {
    const res = await fetch('/api/batch', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: saveBody() });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(data.error || 'Could not save the batch.');
      return;
    }
    // Keep what was typed since; take the server's stock and names.
    if (seq === editSeq) {
      batch = data;
      if (batchOpen()) render();
    }
  } catch (e) {
    showError('Could not reach server.');
  }
}

function showError(text) {
  const el = document.getElementById('batchError');
  el.textContent = text || '';
  el.hidden = !text;
}

export function batchOpen() {
  return document.getElementById('batchModal').style.display !== 'none';
}

async function openBatch() {
  reviewing = false;
  submitting = false;
  planStatus = 'idle';
  separate = readSeparate();
  showError(null);
  document.getElementById('batchModal').style.display = 'flex';
  render();
  document.getElementById('batchFinder').focus();
  await refreshBatch();
  if (!lastNetworkData) await fetchNetwork();
  updateFinderOptions();
}

export function closeBatch() {
  if (!batchOpen()) return;
  pickers.close();
  if (saveTimer) saveNow();
  document.getElementById('batchModal').style.display = 'none';
  if (tooltipVisible(document.getElementById('batchRows'))) hideTooltip();
  tooltipIcon = null;
  reviewing = false;
  if (kitPlanOpen()) closeCraftPlan();
}

// The search's options: what the network can craft. Not NBT variants: a
// kit pattern's inputs can't carry NBT.
function updateFinderOptions() {
  if (!finder) return;
  const items = (lastNetworkData?.items || [])
    .filter(it => it.isCraftable && !it.variant && ['item', 'fluid'].includes(it.kind || 'item'))
    .map(it => ({
      value: networkItemKey(it.mod, it.internal, it.damage, it.kind),
      label: nameText(it.name || it.internal) + (it.kind === 'fluid' ? ' (fluid)' : ''),
      icon: it.icon,
      name: nameText(it.name || it.internal),
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
  finder.setOptions(items, lastNetworkData ? 'Nothing craftable matches.' : 'Loading the network…');
}

function pickItem(key) {
  const it = (lastNetworkData?.items || []).find(i => !i.variant && networkItemKey(i.mod, i.internal, i.damage, i.kind) === key);
  finder.setValue(null);
  if (!it) return;
  const existing = rows().find(r => r.key === key);
  if (existing) {
    // Already in: go to its amount rather than adding it twice.
    document.querySelector(`#batchRows input[data-batch-row="${CSS.escape(key)}"]`)?.focus();
    return;
  }
  batch = batch || { items: [], kit_slots: 0, kit_fluids: false, plan_available: false };
  batch.items = [...rows(), {
    key, amount: 1, name: it.name || it.internal, icon: it.icon, have: it.size || 0, craftable: true,
    kind: it.kind || 'item', mod: it.mod, internal: it.internal, damage: it.damage,
  }];
  scheduleSave();
  render();
  document.querySelector(`#batchRows input[data-batch-row="${CSS.escape(key)}"]`)?.select();
}

function rowHtml(r) {
  const icon = r.icon
    ? `<img class="${iconClass('kit-icon', r.icon)}" src="${escapeHtml(iconUrl(r.icon))}" alt="" data-item="${escapeHtml(r.key)}">`
    : `<span class="kit-icon" data-item="${escapeHtml(r.key)}"></span>`;
  const note = !r.craftable ? '<span class="batch-note kit-no-pattern">no pattern</span>'
    : r.from ? `<span class="batch-note">from ${escapeHtml(nameText(r.from))}</span>` : '';
  const label = escapeHtml(nameText(r.name));
  return `<div class="kit-row batch-row${r.craftable ? '' : ' kit-short'}" data-id="${escapeHtml(r.key)}">
    ${icon}
    <span class="batch-name"><span class="kit-name">${label}</span>${note}</span>
    <span class="kit-num kit-have">${qty(r.have, r.kind)}</span>
    <input class="modal-amount-input kit-request" type="number" min="1" step="1" data-batch-row="${escapeHtml(r.key)}"
      value="${r.amount}" aria-label="How many ${label} to craft${r.kind === 'fluid' ? ', in mB' : ''}">
    <button class="batch-remove" data-action="batch-remove" data-key="${escapeHtml(r.key)}"
      title="Remove from the batch" aria-label="Remove ${label}">&times;</button>
  </div>`;
}

function render() {
  if (!batchOpen()) return;
  const list = rows();
  document.getElementById('batchConfiguration').hidden = reviewing;
  document.getElementById('batchReview').hidden = !reviewing;
  const rowsEl = document.getElementById('batchRows');
  const head = `<div class="kit-row batch-row kit-head" data-id="head">
    <span></span><span>Item</span><span class="kit-num kit-have">In network</span><span class="kit-num">Craft</span><span></span>
  </div>`;
  // A box being typed in keeps what's typed: updateHtml leaves its value.
  updateHtml(rowsEl, list.length ? head + list.map(rowHtml).join('')
    : '<p class="modal-note batch-empty" data-id="empty">Nothing in the batch yet. Find items above, or use '
      + '<strong>Add to batch</strong> in an item\'s craft dialog or a multiblock kit.</p>');
  rowsEl.querySelectorAll('input[data-batch-row]').forEach(input => {
    const r = list.find(x => x.key === input.dataset.batchRow);
    if (r && document.activeElement !== input) input.value = String(r.amount);
  });
  const note = document.getElementById('batchPlanUnavailable');
  note.hidden = !batch || batch.plan_available || !list.length;
  if (!note.hidden) {
    note.textContent = batch.plan_unavailable_reason || 'A pattern scan is needed to preview the crafting plan.';
    if (batch.needs_terminal) {
      const link = Object.assign(document.createElement('a'),
        { href: TERMINAL_DOCS_URL, target: '_blank', rel: 'noopener', textContent: 'How to connect one' });
      note.append(' ', link);
    }
  }
  updateControls();
}

function updateMode() {
  const box = document.getElementById('batchMode');
  box.hidden = reviewing || !batch || !rows().length;
  if (box.hidden) return;
  const can = batch.kit_slots > 0;
  const input = document.getElementById('batchSeparate');
  input.checked = !oneJob();
  input.disabled = !can;
  const line = document.getElementById('batchModeLine');
  line.classList.toggle('kit-mode-explanations', can);
  if (!can) {
    updateHtml(line, 'No kit slots detected. Items will be requested separately. '
      + `<a href="${escapeHtml(KIT_INTERFACE_DOCS_URL)}" target="_blank" rel="noopener">Set up a kit interface</a>`);
  } else {
    const explanations = [
      'As one job: AE2 plans the items together, making what they share once, and puts them in the network when they\'re all done.',
      'One craft per item, each in the network as soon as it\'s done.',
    ];
    updateHtml(line, explanations.map((text, i) =>
      `<span data-id="mode-${i}" aria-hidden="${i !== Number(!oneJob())}">${escapeHtml(text)}</span>`).join(''));
  }
}

function updateControls() {
  const n = rows().length;
  const blocked = problem();
  const planReady = ['ready', 'unavailable'].includes(planStatus);
  const review = document.getElementById('batchReviewBtn');
  review.hidden = !batch?.plan_available || reviewing;
  review.disabled = !n || !!blocked || submitting;
  review.textContent = n ? 'Review & request' : 'Nothing to request';
  const btn = document.getElementById('batchRequestBtn');
  btn.hidden = !batch || (!reviewing && batch.plan_available);
  btn.disabled = !n || !!blocked || submitting || (reviewing && !planReady);
  btn.textContent = submitting ? 'Submitting…' : !n ? 'Nothing to request'
    : reviewing && planStatus === 'loading' ? 'Loading plan…'
    : oneJob() ? `Request as one job (${plural(n, 'item')})` : `Request ${plural(n, 'craft')}`;
  const back = document.getElementById('batchBackBtn');
  back.hidden = !reviewing;
  back.disabled = submitting;
  document.getElementById('batchRetryBtn').hidden = !reviewing || planStatus !== 'error';
  document.getElementById('batchClearBtn').hidden = reviewing || !n;
  document.getElementById('batchClearBtn').disabled = submitting;
  const problemEl = document.getElementById('batchProblem');
  problemEl.textContent = blocked || '';
  problemEl.hidden = !blocked || reviewing;
  updateMode();
  updatePlan();
}

function updatePlan() {
  if (!reviewing || !batch) {
    if (kitPlanOpen() && batchOpen() && document.getElementById('craftPlan').closest('#batchModal')) closeCraftPlan();
    return;
  }
  const list = rows();
  document.getElementById('batchReviewSummary').textContent =
    `${plural(list.length, 'item')} to craft · ${oneJob() ? 'As one job' : 'One craft per item'}`;
  openKitPlan({ kit: 'batch', batch: true, targets: list.map(r => [r.key, r.amount]), reserve: oneJob() },
    document.getElementById('batchReviewEnd'), status => {
      if (status === planStatus) return;
      planStatus = status;
      updateControls();
    });
}

function reviewBatch() {
  if (!batch?.plan_available || !rows().length || problem()) return;
  pickers.close();
  if (saveTimer) saveNow();
  reviewing = true;
  planStatus = 'idle';
  render();
  document.getElementById('batchReviewTitle').focus({ preventScroll: true });
  document.getElementById('batchReview').scrollTop = 0;
}

function backToBatch() {
  if (!reviewing || submitting) return;
  reviewing = false;
  render();
  document.getElementById('batchReviewBtn').focus({ preventScroll: true });
}

async function submitBatch() {
  const list = rows();
  if (!list.length || problem() || submitting || (batch.plan_available && !reviewing)
      || (reviewing && !['ready', 'unavailable'].includes(planStatus))) return;
  const together = oneJob();
  submitting = true;
  showError(null);
  updateControls();
  try {
    const res = await fetch('/api/craft/request-batch-job', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: list.map(r => ({ key: r.key, amount: r.amount })), separate: !together }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showError(data.error || 'Request failed.');
      return;
    }
    ++editSeq;
    batch = { ...batch, items: [] };
    showToast(together ? `Requested ${data.label || 'the batch'} as one job`
      : `Requested ${plural(list.length, 'craft')} from the batch`);
    updateButton();
    submitting = false;
    closeBatch();
    fetchCraftRequests();
  } catch (e) {
    showError('Could not reach server.');
  } finally {
    submitting = false;
    if (batchOpen()) updateControls();
  }
}

function rowOf(el) {
  return rows().find(r => r.key === el.dataset.item) || null;
}

function iconTooltipHtml(el) {
  const row = rowOf(el);
  if (!row) return null;
  tooltipIcon = el;
  return lookupItemTooltipHtml(row, { stockAmount: null }, refreshTooltip);
}

function refreshTooltip() {
  const rowsEl = document.getElementById('batchRows');
  if (!tooltipVisible(rowsEl) || !tooltipIcon?.isConnected) return;
  const html = iconTooltipHtml(tooltipIcon);
  if (html) showTooltip(html, undefined, undefined, rowsEl);
}

export function setupBatchActions() {
  const modal = document.getElementById('batchModal');
  pickers = setupKitPickers(modal, 'batch-choice');
  finder = pickers.mount(document.getElementById('batchFinderControl'), {
    id: 'batchFinder', label: 'Add an item', searchable: true, onSelect: pickItem,
    placeholder: 'Find a craftable item to add…', footer: 'Items your network can craft', limit: SEARCH_LIMIT,
  });
  bindCellTooltip(document.getElementById('batchRows'), '.kit-icon[data-item]', iconTooltipHtml, rowOf);
  onShiftChange(refreshTooltip);
  delegateActions(modal, {
    'close-batch': () => closeBatch(),
    'batch-remove': (el) => {
      batch.items = rows().filter(r => r.key !== el.dataset.key);
      scheduleSave();
      render();
    },
    'batch-clear': () => {
      if (!rows().length) return;
      batch.items = [];
      scheduleSave();
      render();
    },
    'batch-review': () => reviewBatch(),
    'batch-back': () => backToBatch(),
    'batch-request': () => submitBatch(),
    'batch-retry-plan': () => {
      closeCraftPlan();
      planStatus = 'idle';
      updateControls();
    },
  });
  delegateActions(document.getElementById('networkBatchButton'), {
    'open-batch': () => openBatch(),
  });
  modal.addEventListener('keydown', e => {
    if (e.key === 'Escape' && reviewing && !submitting) {
      e.preventDefault();
      e.stopPropagation();
      backToBatch();
    }
  });
  onBackdropClick('batchModal', closeBatch);
  document.getElementById('batchSeparate').addEventListener('change', (e) => {
    separate = e.target.checked;
    writeSeparate(separate);
    updateControls();
  });
  document.getElementById('batchRows').addEventListener('input', (e) => {
    const key = e.target.dataset.batchRow;
    const row = key && rows().find(r => r.key === key);
    if (!row) return;
    const n = Math.round(Number(e.target.value));
    if (!Number.isFinite(n) || n < 1) return;  // fixed on change, below
    row.amount = n;
    scheduleSave();
    updateControls();
  });
  document.getElementById('batchRows').addEventListener('change', (e) => {
    const row = e.target.dataset.batchRow && rows().find(r => r.key === e.target.dataset.batchRow);
    if (row) e.target.value = String(row.amount);
  });
  window.addEventListener('gcm-networkchange', () => { if (batchOpen()) updateFinderOptions(); });
}
