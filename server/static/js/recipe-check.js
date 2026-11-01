// Pattern check (admins): the network's patterns held against two GTNH
// versions' GregTech recipes, to find the ones an update breaks
// (server/gcm/recipes.py). Opened from the Game data dialog, or from the
// page-wide notice an upgrade that affects patterns brings up.

import { AUTH_USER } from './auth.js';
import {
  closeNote, delegateActions, escapeHtml, iconClass, iconUrl, noteClosed, onBackdropClick,
} from './util.js';

const POLL_MS = 1000;
const NOTICE_MS = 5 * 60 * 1000;
const NOTICE_PENDING_MS = 3000;
const NOTICE_DISMISSED_KEY = 'gtnhCraftMonitor.recipeNoticeDismissed';

// What each status means, in the order they're listed.
const SECTIONS = [
  { status: 'broken', title: 'Broken', note: 'Nothing makes this from these inputs any more. What makes it now is below.' },
  { status: 'moved', title: 'Moved to another machine', note: 'The same recipe, in another machine.' },
  { status: 'changed', title: 'Changed outside the pattern', note: 'The pattern still matches, but something it leaves out changed: check what you supply separately, and where byproducts go.' },
  { status: 'unchecked', title: 'Not checked', note: 'Matched no GregTech recipe in the older version either: another mod\'s machine, or a setup the check can\'t follow.' },
];

let pollTimer = null;
let notice = null;

export function versionsToCheck(picked, current, previous) {
  // Picking the version in use checks the upgrade that led to it.
  if (!picked) return null;
  if (picked !== current) return current ? { from: current, to: picked } : null;
  return previous ? { from: previous, to: current } : null;
}

// Recipe amounts are small enough to show whole: "1,000 mB", not "1.0k".
export function amountText(e) {
  if (e.size == null) return '?';
  const n = Number(e.size).toLocaleString('en-US');
  if (e.kind === 'fluid') return `${n} mB`;
  if (e.size === 0) return 'not used up';
  return `${n}x`;
}

function stackHtml(e) {
  const chance = e.chance ? ` <span class="recipe-chance">${escapeHtml(String(e.chance))}%</span>` : '';
  const icon = e.icon
    ? `<img class="${iconClass('recipe-stack-icon', e.icon)}" src="${iconUrl(e.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  return `<span class="recipe-stack" title="${escapeHtml(e.name || '')}">${icon}`
    + `<span class="recipe-stack-amount">${escapeHtml(amountText(e))}</span>`
    + `<span class="recipe-stack-name">${escapeHtml(e.name || '?')}</span>${chance}</span>`;
}

export function machineText(step) {
  const [first, ...rest] = step.machines || [];
  if (!first) return step.map;
  return rest.length ? `${first} (or ${rest.length} more)` : first;
}

function stepHtml(step) {
  const details = [step.tier, step.heat && step.special ? `${step.special.toLocaleString('en-US')} K` : '']
    .filter(Boolean).join(', ');
  return `<div class="recipe-step">
    <div class="recipe-step-machine">${escapeHtml(machineText(step))}<span class="recipe-step-details">${escapeHtml(details)}</span></div>
    <div class="recipe-step-sides">
      <div class="recipe-side">${step.inputs.map(stackHtml).join('')}</div>
      <div class="recipe-arrow" aria-hidden="true">&rarr;</div>
      <div class="recipe-side">${step.outputs.map(stackHtml).join('')}</div>
    </div>
  </div>`;
}

function recipeHtml(label, steps) {
  return `<div class="recipe-block"><div class="recipe-label">${escapeHtml(label)}</div>${steps.map(stepHtml).join('')}</div>`;
}

export function patternTitle(p) {
  const outputs = p.outputs.map((e) => e.name).join(', ');
  return `${outputs || '?'}`;
}

// A broken pattern whose output no GregTech recipe makes in `to`. Used
// only where an item is taken apart (GTNH's Unpackager recipe for a
// removed item) is as good as removed.
export function removedText(removed, to) {
  if (removed.used) {
    return `Nothing in GregTech makes ${removed.name} in ${to}. It may be made another way now (a crafting table, another mod's machine), or have been removed.`;
  }
  const apart = removed.taken_apart_in.length
    ? ` Any you have left can be taken apart in the ${removed.taken_apart_in.join(' or ')}.`
    : '';
  return `Removed in ${to}: nothing makes ${removed.name} any more, and nothing uses it.${apart}`;
}

function patternHtml(p, from, to) {
  const lines = [];
  if (p.removed) lines.push(removedText(p.removed, to));
  if (p.status === 'moved') lines.push(`Now made in ${p.moved_to.join(', ')}.`);
  for (const change of p.changes || []) lines.push(change);
  if (p.machine_known === false) {
    lines.push(`The machine isn't known (its name matches none GregTech lists), so this was checked against every machine that made it in ${from}.`);
  }
  const recipes = p.old
    ? recipeHtml(`Before (${from})`, p.old)
      + (p.new || []).map((steps, i) => recipeHtml(i === 0 ? `Now (${to})` : 'Or', steps)).join('')
    : '';
  return `<div class="recipe-check-row">
    <div class="recipe-check-pattern">
      <span class="recipe-check-name">${escapeHtml(patternTitle(p))}</span>
      <span class="recipe-check-provider">${escapeHtml(p.provider.name || '?')}</span>
    </div>
    <div class="recipe-side recipe-check-pattern-stacks">${p.inputs.map(stackHtml).join('')}<span class="recipe-arrow" aria-hidden="true">&rarr;</span>${p.outputs.map(stackHtml).join('')}</div>
    ${lines.map((l) => `<div class="recipe-check-line">${escapeHtml(l)}</div>`).join('')}
    ${recipes}
  </div>`;
}

export function summaryText(data) {
  const c = data.counts;
  const parts = [
    `${c.broken} broken`,
    `${c.moved} moved to another machine`,
    `${c.changed} changed outside the pattern`,
    `${c.unchecked} not checked`,
    `${c.ok} unchanged`,
  ];
  const crafting = c.crafting ? ` ${c.crafting} crafting-table patterns aren't checked: AE2 marks those invalid itself.` : '';
  return `From GTNH ${data.from} to ${data.to}: ${parts.join(', ')}.${crafting}`;
}

function render(data) {
  document.getElementById('recipeCheckTitle').textContent = `Pattern check: ${data.from} to ${data.to}`;
  document.getElementById('recipeCheckSummary').textContent = summaryText(data);
  const list = document.getElementById('recipeCheckList');
  list.innerHTML = SECTIONS.map(({ status, title, note }) => {
    const rows = data.patterns.filter((p) => p.status === status);
    if (!rows.length) return '';
    // Not checked is the long, least useful list: closed to begin with.
    return `<details class="recipe-check-section"${status === 'unchecked' ? '' : ' open'}>
      <summary class="status-heading">${escapeHtml(title)} (${rows.length})</summary>
      <div class="modal-note">${escapeHtml(note)}</div>
      ${rows.map((p) => patternHtml(p, data.from, data.to)).join('')}
    </details>`;
  }).join('') || '<div class="empty">No pattern uses a recipe that changed.</div>';
}

function showError(message) {
  const error = document.getElementById('recipeCheckError');
  error.textContent = message || '';
  error.style.display = message ? '' : 'none';
}

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

async function load(from, to) {
  stopPolling();
  const params = new URLSearchParams();
  if (from) params.set('from', from);
  if (to) params.set('to', to);
  let response;
  let data;
  try {
    response = await fetch(`/api/admin/recipe-check?${params}`);
    data = await response.json();
  } catch (e) {
    showError('Could not check the patterns.');
    return;
  }
  if (document.getElementById('recipeCheckModal').style.display === 'none') return;
  if (data.state === 'preparing') {
    document.getElementById('recipeCheckSummary').textContent =
      `Reading the GTNH ${data.from} and ${data.to} recipes and checking your patterns...`;
    pollTimer = setTimeout(() => load(from, to), POLL_MS);
    return;
  }
  if (!response.ok) {
    document.getElementById('recipeCheckSummary').textContent = '';
    showError(data.error || 'Could not check the patterns.');
    return;
  }
  render(data);
}

export function openRecipeCheck(from, to) {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') return;
  showError('');
  document.getElementById('recipeCheckTitle').textContent = 'Pattern check';
  document.getElementById('recipeCheckSummary').textContent = 'Checking...';
  document.getElementById('recipeCheckList').innerHTML = '';
  document.getElementById('recipeCheckModal').style.display = 'flex';
  load(from, to);
}

export function closeRecipeCheck() {
  document.getElementById('recipeCheckModal').style.display = 'none';
  stopPolling();
}

// ---------- the notice after an upgrade ----------

export function noticeText(n) {
  const c = n.counts;
  const parts = [
    c.broken && `${c.broken} broken`,
    c.moved && `${c.moved} moved to another machine`,
    c.changed && `${c.changed} changed`,
  ].filter(Boolean);
  const total = c.broken + c.moved + c.changed;
  return `GTNH ${n.to} changed recipes ${total === 1 ? 'a pattern uses' : `${total} patterns use`} (${parts.join(', ')}), compared with ${n.from}.`;
}

function noticeKey(n) {
  return `${n.from}>${n.to}`;
}

function renderNotice() {
  const el = document.getElementById('recipeNotice');
  const admin = Boolean(AUTH_USER && AUTH_USER.role === 'admin');
  if (!admin || !notice || !notice.to || noteClosed(NOTICE_DISMISSED_KEY, noticeKey(notice))) {
    el.hidden = true;
    return;
  }
  document.getElementById('recipeNoticeText').textContent = noticeText(notice);
  el.hidden = false;
}

export async function updateRecipeNotice() {
  if (!AUTH_USER || AUTH_USER.role !== 'admin') {
    notice = null;
    renderNotice();
    return;
  }
  try {
    const res = await fetch('/api/admin/recipe-notice');
    if (res.ok) notice = await res.json();
  } catch (e) { /* keep what was shown */ }
  renderNotice();
  // Worked out in the background after an upgrade: a few seconds.
  if (notice && notice.pending) setTimeout(updateRecipeNotice, NOTICE_PENDING_MS);
}

export function setupRecipeCheckActions() {
  setInterval(updateRecipeNotice, NOTICE_MS);
  delegateActions(document, {
    'close-recipe-check': () => closeRecipeCheck(),
    'recipe-notice-show': () => openRecipeCheck(notice.from, notice.to),
    'dismiss-recipe-notice': () => {
      closeNote(NOTICE_DISMISSED_KEY, noticeKey(notice));
      renderNotice();
    },
  });
  onBackdropClick('recipeCheckModal', closeRecipeCheck);
}
