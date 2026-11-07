// Multiblock kits: every block a multiblock is built from, for the
// coils, glass and size picked, with the network's stock, to request
// the missing ones at once (gcm/multiblocks.py). Opened from an item
// dialog of a controller, or the Network tab's Multiblocks button.
//
// Each row's Request box starts at what's missing and can be lowered by
// hand: the kit counts every spot that takes a hatch or a casing as a
// casing, and walls shared with another multiblock need fewer.
//
// Where the game has a kit interface (craft_monitor.lua reports its kit
// slots), the blocks are requested as one job (/api/craft/request-kit):
// AE2 plans them together, sharing what they have in common. Otherwise,
// or when the viewer ticks "on its own", each block is its own request.

import { AUTH_USER, isOperator } from './auth.js';
import { closeCraftPlan, kitPlanOpen, openKitPlan } from './craft-plan.js';
import { hideLayout, setupLayoutView, showLayout } from './multiblock-view.js';
import { fetchCraftRequests, showToast } from './craft-actions.js';
import { nameText } from './description.js';
import { kitPickerHtml, setupKitPickers } from './kit-picker.js';
import { delegateActions, docsUrl, escapeHtml, formatQty, iconClass, iconUrl, onBackdropClick, TERMINAL_DOCS_URL, updateHtml } from './util.js';

let controllers = null;   // [{key, name, label}] once fetched, for the picker
let pickers = null;
let controllerPicker = null;
let controllerSeq = 0;
let openKey = null;       // the controller shown, null when closed
let kit = null;           // its last fetched kit
let tiers = {};           // channel -> tier picked
let count = 1;
let edits = new Map();    // row key -> request amount typed in
let fetchSeq = 0;
let reviewing = false;
let planStatus = 'idle';
let kitReady = false;
let submitting = false;
let configurationScroll = 0;
let separate = false;
// Whether the 3D view is open: it is unless this viewer closed it.
const VIEW_KEY = 'gtnhCraftMonitor.kitViewClosed';
const KIT_INTERFACE_DOCS_URL = docsUrl('in-game.md#kit-interface');
// Whether Request makes one job of the kit.
function oneJob() {
  return !!(kit && kit.kit_slots > 0) && !separate;
}

function viewWanted() {
  try { return localStorage.getItem(VIEW_KEY) !== '1'; } catch (e) { return true; }
}

function setViewWanted(on) {
  try {
    if (on) localStorage.removeItem(VIEW_KEY); else localStorage.setItem(VIEW_KEY, '1');
  } catch (e) { /* per-viewer nicety only */ }
}

// The 3D view, open where it's wanted and the game data has a layout.
function updateView() {
  const btn = document.getElementById('kitViewBtn');
  const has = !!(kit && kit.layout);
  const open = has && viewWanted();
  btn.hidden = !has;
  btn.setAttribute('aria-pressed', String(open));
  btn.classList.toggle('active', open);
  document.getElementById('kitView').hidden = !open;
  if (open) showLayout(kit.controller.key, kit.channels.reduce((t, c) => ({ ...t, [c.name]: c.tier }), {}));
  else hideLayout();
}

function channelLabel(name) {
  const words = String(name).replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// The picker's entries. A few multiblocks share a name (GregTech's and
// GT++'s Pyrolyse Oven): those are told apart by their id.
async function loadControllers() {
  const seq = ++controllerSeq;
  // A controller can enter or leave the network between opens.
  controllers = [];
  controllerPicker.setOptions([], 'Loading multiblocks…');
  let list;
  try {
    const res = await fetch('/api/multiblocks?network=1');
    if (!res.ok) throw new Error(String(res.status));
    list = (await res.json()).multiblocks || [];
  } catch (e) {
    if (seq === controllerSeq) controllerPicker.setOptions([], 'Could not load multiblocks. Reopen the dialog to retry.');
    return [];
  }
  if (seq !== controllerSeq) return [];
  const seen = new Map();
  for (const c of list) seen.set(nameText(c.name), (seen.get(nameText(c.name)) || 0) + 1);
  controllers = list.map(c => {
    const name = nameText(c.name);
    return { ...c, label: seen.get(name) > 1 ? `${name} (${c.key.split(':').pop()})` : name };
  });
  controllerPicker.setOptions(controllers.map(c => ({ value: c.key, label: c.label, icon: c.icon })),
    'No matching multiblocks in the network.');
  if (kit) controllerPicker.setValue(kit.controller.key, {
    value: kit.controller.key, label: nameText(kit.controller.name), icon: kit.controller.icon,
  });
  return controllers;
}

function labelOf(key) {
  const found = (controllers || []).find(c => c.key === key);
  return found ? found.label : null;
}

// The Network tab's button, only where the game data has kits.
export async function showKitButton() {
  try {
    const res = await fetch('/api/multiblocks');
    if (!res.ok) return;
    const list = (await res.json()).multiblocks || [];
    document.getElementById('networkKitButton').hidden = list.length === 0;
  } catch (e) { /* try again after signing in */ }
}

// key: the controller's game data key (mod:internal:damage), or null to
// pick one first.
export function openKit(key) {
  ++controllerSeq;
  controllers = null;
  openKey = null;
  kit = null;
  tiers = {};
  count = 1;
  separate = false;
  edits = new Map();
  reviewing = false;
  submitting = false;
  kitReady = false;
  planStatus = 'idle';
  pickers.close();
  controllerPicker.setValue(null);
  document.getElementById('kitPickerSection').hidden = !!key;
  document.getElementById('kitCount').value = '1';
  document.getElementById('kitControls').innerHTML = '';
  document.getElementById('kitRows').innerHTML = '';
  document.getElementById('kitError').hidden = true;
  document.getElementById('kitRequestBtn').hidden = true;
  document.getElementById('kitMode').hidden = true;
  document.getElementById('kitTitle').textContent = 'Multiblock kit';
  document.getElementById('kitIcon').hidden = true;
  document.getElementById('kitViewBtn').hidden = true;
  document.getElementById('kitView').hidden = true;
  hideLayout();
  viewDrawn = null;
  updateRequestButton();
  document.getElementById('kitModal').style.display = 'flex';
  if (key) {
    selectController(key);
    document.getElementById('kitCount').focus();
  } else {
    loadControllers();
    document.getElementById('kitPicker').focus();
  }
}

export function closeKit() {
  pickers.close();
  ++controllerSeq;
  ++fetchSeq;
  document.getElementById('kitModal').style.display = 'none';
  hideLayout();
  viewDrawn = null;
  reviewing = false;
  if (kitPlanOpen()) closeCraftPlan();
  openKey = null;
  kit = null;
}

export function kitOpen() {
  return document.getElementById('kitModal').style.display !== 'none';
}

function selectController(key) {
  if (key === openKey) return;
  openKey = key;
  tiers = {};
  count = 1;
  separate = false;
  document.getElementById('kitCount').value = '1';
  edits = new Map();
  fetchKit(true);
}

async function fetchKit(restorePreferences = false) {
  if (!openKey) return;
  kitReady = false;
  updateRequestButton();
  const seq = ++fetchSeq;
  const params = new URLSearchParams({ key: openKey });
  if (!restorePreferences) {
    params.set('count', String(count));
    for (const [name, tier] of Object.entries(tiers)) params.set('tier.' + name, String(tier));
  }
  const errorEl = document.getElementById('kitError');
  try {
    const res = await fetch('/api/multiblocks/kit?' + params.toString());
    if (seq !== fetchSeq) return;
    if (!res.ok) {
      errorEl.textContent = res.status === 404 ? 'The game data has no structure for that multiblock.'
        : 'Could not load the kit.';
      errorEl.hidden = false;
      return;
    }
    const result = await res.json();
    if (seq !== fetchSeq) return;
    kit = result;
    count = kit.count;
    tiers = Object.fromEntries(kit.channels.map(ch => [ch.name, ch.tier]));
    document.getElementById('kitCount').value = String(count);
    if (restorePreferences) separate = kit.preferred_separate;
    kitReady = true;
    errorEl.hidden = true;
    renderKit();
    renderView();
  } catch (e) {
    if (seq !== fetchSeq) return;
    errorEl.textContent = 'Could not reach server.';
    errorEl.hidden = false;
  }
}

// What a row's Request box holds: what was typed, else what's missing
// of a craftable block.
function requestOf(row) {
  if (edits.has(row.key)) return edits.get(row.key);
  return row.craftable ? Math.max(0, row.need - row.have) : 0;
}

// Some channels have no material at the selected tier. Keep those above
// the preview, along with size channels, so every setting stays reachable.
export function inlineChannelRows(channels, rows) {
  const keys = new Set(rows.map(r => r.key));
  return new Map(channels.flatMap(ch => {
    const key = ch.kind === 'block' && ch.options.find(o => o.tier === ch.tier)?.key;
    return key && keys.has(key) ? [[ch.name, key]] : [];
  }));
}

function channelHtml(ch, inline) {
  const label = channelLabel(ch.name), id = 'kitChannel-' + ch.name;
  const picker = `<div class="kit-choice${inline ? ' kit-choice-inline' : ''}"
    data-key="channel-${escapeHtml(ch.name)}" data-channel="${escapeHtml(ch.name)}">
    ${kitPickerHtml({ id, label, inline })}</div>`;
  return inline ? picker : `<div class="kit-control" data-key="control-${escapeHtml(ch.name)}">
    <label class="kit-control-label" for="${escapeHtml(id)}">${escapeHtml(label)}</label>${picker}</div>`;
}

function renderKit() {
  const c = kit.controller;
  document.getElementById('kitTitle').textContent = nameText(c.name);
  const iconEl = document.getElementById('kitIcon');
  if (c.icon) {
    iconEl.src = iconUrl(c.icon);
    iconEl.className = iconClass('modal-icon', c.icon);
    iconEl.hidden = false;
  } else {
    iconEl.hidden = true;
  }
  controllerPicker.setValue(c.key, { value: c.key, label: labelOf(c.key) || nameText(c.name), icon: c.icon });

  const inlineRows = inlineChannelRows(kit.channels, kit.rows);
  const controlsRoot = document.getElementById('kitControls');
  pickers.close(controlsRoot);
  updateHtml(controlsRoot, kit.channels.filter(ch => !inlineRows.has(ch.name)).map(ch => channelHtml(ch, false)).join(''));

  const operator = isOperator();
  const head = `<div class="kit-row kit-head" data-key="head">
      <span></span><span>Block</span><span class="kit-num">Need</span><span class="kit-num">In network</span>
      ${operator ? '<span class="kit-num">Request</span>' : ''}
    </div>`;
  const rows = kit.rows.map(r => {
    const channels = kit.channels.filter(ch => inlineRows.get(ch.name) === r.key);
    const icon = r.icon
      ? `<img class="${iconClass('kit-icon', r.icon)}" src="${escapeHtml(iconUrl(r.icon))}" alt="">`
      : '<span class="kit-icon"></span>';
    const short = r.have < r.need;
    const request = operator
      ? (r.craftable
        ? `<input class="modal-amount-input kit-request" type="number" min="0" data-row="${escapeHtml(r.key)}" value="${requestOf(r)}" aria-label="Request ${escapeHtml(nameText(r.name))}">`
        : '<span class="kit-num kit-no-pattern" title="The network has no pattern for it">no pattern</span>')
      : '';
    // Below a block that can be requested: a slider from none to all the
    // multiblock needs, starting at what's missing.
    const slider = operator && r.craftable
      ? `<input class="kit-slider" type="range" min="0" max="${r.need}" step="1" data-row="${escapeHtml(r.key)}" value="${Math.min(requestOf(r), r.need)}" aria-label="Request ${escapeHtml(nameText(r.name))}">`
      : '';
    const name = channels.length
      ? `<div class="kit-name-cell">${channels.map(ch => channelHtml(ch, true)).join('')}</div>`
      : `<span class="kit-name">${escapeHtml(nameText(r.name))}</span>`;
    // Keep a configurable row's identity when a new tier swaps its block.
    const rowKey = channels.length ? 'channels:' + JSON.stringify(channels.map(ch => ch.name)) : r.key;
    return `<div class="kit-row${short ? ' kit-short' : ''}${channels.length ? ' kit-row-configurable' : ''}" data-key="${escapeHtml(rowKey)}">
      ${icon}${name}
      <span class="kit-num">${r.need.toLocaleString()}</span>
      <span class="kit-num kit-have">${formatQty(r.have)}</span>
      ${request}${slider}
    </div>`;
  }).join('');
  const rowsEl = document.getElementById('kitRows');
  pickers.close(rowsEl);
  rowsEl.classList.toggle('kit-rows-operator', operator);
  updateHtml(rowsEl, head + rows);
  document.querySelectorAll('#kitControls [data-channel], #kitRows [data-channel]').forEach(root => {
    const ch = kit.channels.find(c => c.name === root.dataset.channel);
    const picker = pickers.mount(root, {
      id: 'kitChannel-' + ch.name, label: channelLabel(ch.name), inline: inlineRows.has(ch.name),
      onSelect: value => { tiers[ch.name] = Number(value); fetchKit(); },
    });
    picker.setOptions(ch.options.map(o => ({ value: String(o.tier), label: nameText(o.label), icon: o.icon })));
    picker.setValue(String(ch.tier));
  });
  updateRequestButton();
}

// What the 3D view shows only changes with the multiblock and its tiers,
// not the count.
let viewDrawn = null;

function renderView() {
  const drawn = kit ? JSON.stringify([kit.controller.key, kit.channels.map(c => c.tier)]) : null;
  if (drawn === viewDrawn && !document.getElementById('kitView').hidden) return;
  viewDrawn = drawn;
  updateView();
}

function requests() {
  if (!kit) return [];
  return kit.rows
    .filter(r => r.craftable && requestOf(r) > 0)
    .map(r => ({ label: r.name, mod: r.mod, internal: r.internal, damage: r.damage, amount: requestOf(r) }));
}

// The review occupies the dialog in place of the configuration. Keeping
// those DOM nodes preserves the parts, request amounts and 3D camera.
function updatePlan() {
  document.getElementById('kitConfiguration').hidden = reviewing;
  document.getElementById('kitReview').hidden = !reviewing;
  document.getElementById('kitCount').disabled = reviewing || submitting;
  document.getElementById('kitSubtitle').textContent = reviewing
    ? 'Review the ingredients before starting the craft'
    : "Every block it's built from, for the parts you pick";
  const note = document.getElementById('kitPlanUnavailable');
  note.hidden = !AUTH_USER || !kit || kit.plan_available;
  if (!note.hidden) {
    note.textContent = kit.plan_unavailable_reason || 'A pattern scan is needed to preview the crafting plan.';
    if (kit.needs_terminal) {
      const link = document.createElement('a');
      link.href = TERMINAL_DOCS_URL;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = 'How to connect one';
      note.append(' ', link);
    }
  }
  if (!reviewing || !kit) {
    if (kitPlanOpen()) closeCraftPlan();
    return;
  }
  const targets = kit.rows.filter(r => r.craftable && requestOf(r) > 0).map(r => [r.item, requestOf(r)]);
  document.getElementById('kitReviewSummary').textContent =
    `${kit.count} kit${kit.count === 1 ? '' : 's'} · ${targets.length} material${targets.length === 1 ? '' : 's'} to craft · `
    + (oneJob() ? 'As one job' : 'One craft per material');
  openKitPlan({ kit: kit.controller.key, count: kit.count, targets }, document.getElementById('kitReviewEnd'), status => {
    if (status === planStatus) return;
    planStatus = status;
    updateRequestButton();
  });
}

function reviewKit() {
  if (!kitReady || !kit?.plan_available || !AUTH_USER || !requests().length) return;
  pickers.close();
  configurationScroll = document.getElementById('kitModal').scrollTop;
  reviewing = true;
  planStatus = 'idle';
  updateRequestButton();
  document.getElementById('kitReviewTitle').focus({ preventScroll: true });
  document.getElementById('kitModal').scrollTop = 0;
}

function backToKit() {
  if (!reviewing || submitting) return;
  reviewing = false;
  updateRequestButton();
  document.getElementById('kitReviewBtn').focus({ preventScroll: true });
  document.getElementById('kitModal').scrollTop = configurationScroll;
}

// The "on its own" box: ticked and fixed where the game has no kit
// interface to make one job with.
function updateMode() {
  const box = document.getElementById('kitMode');
  box.hidden = !isOperator() || !kit;
  if (box.hidden) return;
  const can = kit.kit_slots > 0;
  const input = document.getElementById('kitSeparate');
  input.checked = !oneJob();
  input.disabled = !can;
  const line = document.getElementById('kitModeLine');
  if (!can) {
    updateHtml(line, 'No kit slots detected. Materials will be requested separately. '
      + `<a href="${escapeHtml(KIT_INTERFACE_DOCS_URL)}" target="_blank" rel="noopener">Set up a kit interface</a>`);
  } else {
    line.textContent = oneJob()
      ? 'As one job: AE2 plans the blocks together, making what they share once, and puts them in the network when they\'re all done.'
      : 'One craft per block, each in the network as soon as it\'s done.';
  }
}

function updateRequestButton() {
  const btn = document.getElementById('kitRequestBtn');
  const n = requests().length;
  btn.hidden = !isOperator() || !kit || (!reviewing && kit.plan_available);
  btn.disabled = n === 0 || !kitReady || submitting || (reviewing && !['ready', 'unavailable'].includes(planStatus));
  btn.textContent = submitting ? 'Submitting…' : n === 0 ? 'Nothing to request'
    : reviewing && planStatus === 'loading' ? 'Loading plan…'
    : oneJob() ? `Request as one job (${n} block${n === 1 ? '' : 's'})`
      : `Request ${n} craft${n === 1 ? '' : 's'}`;
  const review = document.getElementById('kitReviewBtn');
  review.hidden = !AUTH_USER || !kit?.plan_available || reviewing;
  review.disabled = n === 0 || !kitReady || submitting;
  review.textContent = n === 0 ? 'Nothing to request' : isOperator() ? 'Review & request' : 'Review plan';
  const back = document.getElementById('kitBackBtn');
  back.hidden = !reviewing;
  back.disabled = submitting;
  document.getElementById('kitRetryBtn').hidden = !reviewing || planStatus !== 'error';
  updateMode();
  updatePlan();
}

// What to send: the kit as one job, or each block on its own.
function submission(list) {
  if (!oneJob()) return { url: '/api/craft/request-batch', body: { requests: list } };
  const amounts = {};
  for (const r of kit.rows) {
    if (r.craftable && requestOf(r) > 0) amounts[r.key] = requestOf(r);
  }
  return {
    url: '/api/craft/request-kit',
    body: {
      key: kit.controller.key,
      count: kit.count,
      tiers: Object.fromEntries(kit.channels.map(c => [c.name, c.tier])),
      amounts,
    },
  };
}

async function submitKit() {
  const list = requests();
  if (!list.length || !isOperator() || !kitReady || submitting || (kit.plan_available && !reviewing)
      || (reviewing && !['ready', 'unavailable'].includes(planStatus))) return;
  const submittedKit = kit;
  const submittedUser = AUTH_USER?.id;
  const preferences = {
    key: kit.controller.key, separate,
    tiers: Object.fromEntries(kit.channels.map(ch => [ch.name, ch.tier])),
  };
  const errorEl = document.getElementById('kitError');
  const together = oneJob();
  const { url, body } = submission(list);
  submitting = true;
  updateRequestButton();
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (kit !== submittedKit) return;
      errorEl.textContent = err.error || 'Request failed.';
      errorEl.hidden = false;
      return;
    }
    let saved = true;
    if (AUTH_USER?.id === submittedUser) {
      try {
        const response = await fetch('/api/multiblocks/preferences', {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(preferences),
        });
        saved = response.ok;
      } catch (e) { saved = false; }
    }
    const message = together ? `Requested ${nameText(submittedKit.controller.name)} kit as one job`
      : `Requested ${list.length} craft${list.length === 1 ? '' : 's'} for ${nameText(submittedKit.controller.name)}`;
    showToast(message + (saved ? '' : '. Your kit choices could not be saved.'));
    if (kit === submittedKit) closeKit();
    fetchCraftRequests();
  } catch (e) {
    if (kit !== submittedKit) return;
    errorEl.textContent = 'Could not reach server.';
    errorEl.hidden = false;
  } finally {
    if (kit === submittedKit) {
      submitting = false;
      updateRequestButton();
    }
  }
}

export function setupKitActions() {
  pickers = setupKitPickers(document.getElementById('kitModal'));
  controllerPicker = pickers.mount(document.getElementById('kitPickerControl'), {
    id: 'kitPicker', label: 'Multiblock', searchable: true, onSelect: selectController,
  });
  delegateActions(document.getElementById('kitModal'), {
    'close-kit': () => closeKit(),
    'kit-request': () => submitKit(),
    'kit-review': () => reviewKit(),
    'kit-back': () => backToKit(),
    'kit-retry-plan': () => {
      closeCraftPlan();
      planStatus = 'idle';
      updatePlan();
    },
    'kit-view': () => {
      setViewWanted(!viewWanted());
      viewDrawn = null;
      updateView();
    },
  });
  document.getElementById('kitModal').addEventListener('keydown', e => {
    if (e.key === 'Escape' && reviewing && !submitting) {
      e.preventDefault();
      e.stopPropagation();
      backToKit();
    }
  });
  setupLayoutView();
  onBackdropClick('kitModal', closeKit);
  delegateActions(document.getElementById('networkKitButton'), {
    'open-kit': () => openKit(null),
  });

  document.getElementById('kitSeparate').addEventListener('change', (e) => {
    separate = e.target.checked;
    updateRequestButton();
  });
  document.getElementById('kitCount').addEventListener('change', (e) => {
    const n = Math.max(1, Math.min(64, Math.round(Number(e.target.value)) || 1));
    e.target.value = n;
    if (n !== count) {
      count = n;
      // Amounts picked were for the old count.
      edits = new Map();
      fetchKit();
    }
  });
  // The box and the slider below it follow each other.
  document.getElementById('kitRows').addEventListener('input', (e) => {
    const key = e.target.dataset.row;
    if (!key) return;
    const n = Math.max(0, Math.round(Number(e.target.value)) || 0);
    edits.set(key, n);
    const row = e.target.closest('.kit-row');
    const box = row.querySelector('.kit-request');
    const slider = row.querySelector('.kit-slider');
    if (e.target !== box) box.value = String(n);
    if (e.target !== slider) slider.value = String(Math.min(n, Number(slider.max)));
    updateRequestButton();
  });
}
