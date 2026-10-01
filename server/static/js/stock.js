// Stock rules (gcm/stock.py): your low-stock alerts and the base's
// keep-in-stock targets. Shown three ways - the "Stock rules" list on
// the Network tab, a badge on each grid cell with a rule, and a panel
// in the item popup where they're edited.

import { evaluateAmountExpression } from './amount.js';
import { AUTH_USER, isOperator } from './auth.js';
import { showToast } from './craft-actions.js';
import { openItemHistory, redrawItemHistoryChart } from './history.js';
import { lastNetworkData, networkItemKey, rerenderNetworkList } from './network.js';
import { delegateActions, escapeHtml, formatQty, formatRelativeTime, iconClass, iconUrl } from './util.js';

let targets = new Map();  // item key -> target, for the whole base
let alerts = new Map();   // item key -> this user's alert
let loaded = false;
let panelItem = null;     // the item the popup's panel is for
let panelFilled = false;  // whether its inputs have had the saved rules put in

export function itemKeyOf(it) {
  return networkItemKey(it.mod, it.internal, it.damage, it.kind, it.variant);
}

// "10k", "2.5M", "1000" -> a positive number, or null.
export function parseStockAmount(text) {
  const result = evaluateAmountExpression(String(text || '').trim());
  return result.ok && result.value > 0 ? result.value : null;
}

// The other way, for putting a saved amount back in an input: exact,
// so saving again never changes it. 20000 -> "20k", 12345 -> "12345".
export function stockAmountInput(n) {
  for (const [unit, size] of [['T', 1e12], ['G', 1e9], ['M', 1e6], ['k', 1e3]]) {
    if (n >= size && Number.isInteger(n / size)) return (n / size) + unit;
  }
  return String(n);
}

function amountText(n, kind) {
  return formatQty(n) + (kind === 'fluid' ? ' mB' : '');
}

const STATUS_TEXT = {
  ok: 'ok',
  low: 'low',
  requested: 'craft requested',
  crafting: 'crafting',
  waiting: 'waiting for a free CPU',
  failed: 'craft failed',
  off: 'paused',
};

// Whether a target's state needs someone to look at it.
function targetIsLow(t) {
  return ['low', 'waiting', 'failed'].includes(t.status);
}

export function stockBadgeFor(it) {
  const key = itemKeyOf(it);
  const target = targets.get(key);
  const alert = alerts.get(key);
  if (!target && !alert) return null;
  return {
    symbol: target ? '♻' : '🔔',
    low: Boolean((target && targetIsLow(target)) || (alert && alert.low)),
  };
}

// Dashed lines for the item's chart: [{label, value}].
export function stockThresholdsFor(it) {
  if (!it) return [];
  const key = itemKeyOf(it);
  const lines = [];
  if (targets.has(key)) lines.push({ label: 'Keep at least', value: targets.get(key).keep_at_least });
  if (alerts.has(key)) lines.push({ label: 'Alert below', value: alerts.get(key).below });
  return lines;
}

export async function fetchStockRules() {
  let data;
  try {
    const res = await fetch('/api/stock/rules');
    if (!res.ok) return;
    data = await res.json();
  } catch (e) {
    return;
  }
  const before = alerts;
  targets = new Map(data.targets.map(t => [t.key, t]));
  alerts = new Map(data.alerts.map(a => [a.key, a]));
  // The page's own word that an alert went off, for anyone without
  // notifications turned on - only for changes seen while open.
  if (loaded) {
    for (const [key, a] of alerts) {
      if (a.low && before.has(key) && !before.get(key).low) {
        showToast(`${a.label} is low: ${amountText(a.current, a.kind)} (below ${amountText(a.below, a.kind)})`, true);
      }
    }
  }
  loaded = true;
  renderOverview();
  rerenderNetworkList();
  if (panelItem) renderPanel();
  redrawItemHistoryChart();
}

// ---------- Network tab: the overview list ----------

function rowsByItem() {
  const rows = new Map();
  for (const t of targets.values()) rows.set(t.key, { item: t, target: t, alert: null });
  for (const a of alerts.values()) {
    if (rows.has(a.key)) rows.get(a.key).alert = a;
    else rows.set(a.key, { item: a, target: null, alert: a });
  }
  const needsLook = (r) => (r.target && targetIsLow(r.target)) || (r.alert && r.alert.low);
  return Array.from(rows.values()).sort((a, b) =>
    (needsLook(b) - needsLook(a)) || a.item.label.localeCompare(b.item.label));
}

function renderRow({ item, target, alert }) {
  const icon = item.icon
    ? `<img class="${iconClass('craft-icon', item.icon)}" src="${iconUrl(item.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '<span class="craft-icon"></span>';
  const parts = [];
  let chip = '';
  let threshold = null;
  if (target) {
    parts.push(`keep &ge; ${escapeHtml(amountText(target.keep_at_least, item.kind))} &rarr; ${escapeHtml(amountText(target.refill_to, item.kind))}`);
    const status = STATUS_TEXT[target.status] || target.status;
    const where = target.status === 'crafting' && target.cpu ? ` on ${target.cpu}` : '';
    const why = target.status === 'failed' && target.reason ? `: ${target.reason}` : '';
    chip = `<span class="stock-chip ${escapeHtml(target.status)}" title="${escapeHtml(status + where + why)}">${escapeHtml(status + where)}</span>`;
    threshold = target.keep_at_least;
  }
  if (alert) {
    parts.push(`alert &lt; ${escapeHtml(amountText(alert.below, item.kind))}`);
    if (!target) chip = `<span class="stock-chip ${alert.low ? 'low' : 'ok'}">${alert.low ? 'low' : 'ok'}</span>`;
    threshold = threshold ?? alert.below;
  }
  const current = item.current == null ? '?' : amountText(item.current, item.kind);
  return `
    <div class="history-row stock-row" data-action="stock-open-item" data-key="${escapeHtml(item.key)}">
      ${icon}
      <div class="history-row-main">
        <div class="history-row-name">${escapeHtml(item.label)}${chip}</div>
        <div class="cpu-id">${parts.join(' &middot; ')}</div>
      </div>
      <div class="stock-row-amount">
        <div>${escapeHtml(current)}</div>
        <div class="stock-bar"><div class="stock-bar-fill" data-fill="${item.current == null ? 0 : Math.min(100, Math.round(item.current / threshold * 100))}"></div></div>
      </div>
    </div>
  `;
}

function renderOverview() {
  const section = document.getElementById('stockRules');
  const rows = rowsByItem();
  section.hidden = rows.length === 0;
  const lowCount = rows.filter(r => (r.target && targetIsLow(r.target)) || (r.alert && r.alert.low)).length;
  document.getElementById('stockRulesSummary').textContent =
    `Stock rules (${rows.length})` + (lowCount ? ` · ${lowCount} low` : '');
  const list = document.getElementById('stockRulesList');
  list.innerHTML = rows.map(renderRow).join('');
  for (const bar of list.querySelectorAll('.stock-bar-fill')) {
    bar.style.width = `${Number(bar.dataset.fill)}%`;
    bar.classList.toggle('low', Number(bar.dataset.fill) < 100);
  }
}

function openItemForKey(key) {
  const rule = targets.get(key) || alerts.get(key);
  if (!rule) return;
  const items = (lastNetworkData && lastNetworkData.items) || [];
  const it = items.find(i => itemKeyOf(i) === key);
  openItemHistory(it || {
    name: rule.label, mod: rule.mod, internal: rule.internal, damage: rule.damage,
    kind: rule.kind, variant: rule.variant, icon: rule.icon, size: rule.current, isCraftable: false,
  });
}

// ---------- Item popup: the editing panel ----------

export function showStockPanel(it) {
  panelItem = it;
  panelFilled = false;
  document.getElementById('stockAlertBelow').value = '';
  document.getElementById('stockKeep').value = '';
  document.getElementById('stockRefill').value = '';
  setError('');
  renderPanel(false);
}

export function hideStockPanel() {
  panelItem = null;
  document.getElementById('stockPanel').hidden = true;
}

// fillInputs: put the saved rule's numbers in the inputs (after
// saving, and the first time the rules are known while the popup is
// open) - not on a background refresh, which mustn't overwrite what
// someone is typing.
function renderPanel(fillInputs) {
  const it = panelItem;
  if (!panelFilled && loaded) fillInputs = panelFilled = true;
  const key = itemKeyOf(it);
  const target = targets.get(key);
  const alert = alerts.get(key);
  const canTarget = isOperator() && it.isCraftable;
  const panel = document.getElementById('stockPanel');
  panel.hidden = !AUTH_USER && !target;

  const alertRow = document.getElementById('stockAlertRow');
  alertRow.hidden = !AUTH_USER;
  document.getElementById('stockAlertDelete').hidden = !alert;
  if (fillInputs && alert) document.getElementById('stockAlertBelow').value = stockAmountInput(alert.below);

  document.getElementById('stockTargetRow').hidden = !canTarget;
  document.getElementById('stockTargetDelete').hidden = !target;
  if (fillInputs && target) {
    document.getElementById('stockKeep').value = stockAmountInput(target.keep_at_least);
    document.getElementById('stockRefill').value = stockAmountInput(target.refill_to);
  }

  const note = document.getElementById('stockTargetNote');
  note.hidden = !target;
  if (target) {
    const rule = canTarget ? '' : `Kept in stock: at least ${amountText(target.keep_at_least, it.kind)}, refilled to ${amountText(target.refill_to, it.kind)}. `;
    let last = '';
    if (target.last_requested_at) {
      const how = target.last_status === 'failed'
        ? `failed${target.last_reason ? ': ' + target.last_reason : ''}`
        : (target.last_status || 'requested');
      last = `Last auto-craft ${formatRelativeTime(target.last_requested_at)}: ${how}.`;
    } else {
      last = 'No auto-craft yet.';
    }
    note.textContent = rule + last;
  }
}

function setError(message) {
  const el = document.getElementById('stockError');
  el.textContent = message;
  el.hidden = !message;
}

function identity(it) {
  return {
    label: it.name, mod: it.mod || null, internal: it.internal,
    damage: it.damage ?? null, kind: it.kind || 'item', variant: it.variant || null,
  };
}

async function post(url, body) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.ok) return null;
    return (await res.json().catch(() => ({}))).error || `HTTP ${res.status}`;
  } catch (e) {
    return 'Could not reach server.';
  }
}

async function save(url, body) {
  const error = await post(url, body);
  setError(error || '');
  if (error) return;
  await fetchStockRules();
  if (panelItem) renderPanel(true);
}

function saveAlert() {
  if (!panelItem) return;
  const below = parseStockAmount(document.getElementById('stockAlertBelow').value);
  if (below == null) return setError('Enter an amount, like 500 or 10k.');
  save('/api/stock/alert', { ...identity(panelItem), below });
}

function saveTarget() {
  if (!panelItem) return;
  const keep = parseStockAmount(document.getElementById('stockKeep').value);
  const refill = parseStockAmount(document.getElementById('stockRefill').value);
  if (keep == null || refill == null) return setError('Enter both amounts, like 10k and 20k.');
  if (refill <= keep) return setError('Refill to has to be more than keep at least.');
  save('/api/stock/target', { ...identity(panelItem), keep_at_least: keep, refill_to: refill });
}

export function setupStockActions() {
  delegateActions(document.getElementById('stockRules'), {
    'stock-open-item': (el) => openItemForKey(el.dataset.key),
  });
  const panel = document.getElementById('stockPanel');
  delegateActions(panel, {
    'stock-save-alert': () => saveAlert(),
    'stock-delete-alert': () => panelItem && save('/api/stock/alert/delete', identity(panelItem)),
    'stock-save-target': () => saveTarget(),
    'stock-delete-target': () => panelItem && save('/api/stock/target/delete', identity(panelItem)),
  });
  panel.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    if (e.target.closest('#stockAlertRow')) saveAlert();
    else if (e.target.closest('#stockTargetRow')) saveTarget();
  });
}
