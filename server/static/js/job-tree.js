// The live crafting tree in a busy CPU's details: the job step by step,
// rebuilt on the server from the network's patterns (gcm/job_tree.py),
// with how far each step has got from the CPU's own reports.
//
// The tree is drawn once, and again only when its shape changes (a
// step opened or shut, a branch loaded, a branch finished and folding
// away). Each report in between only changes numbers and classes in
// place, so the bars' CSS transitions carry them smoothly from one
// report to the next instead of jumping.

import { ensureLoaded, flashRow, nodeAt, openAbove } from './tree-nav.js';
import { delegateActions, escapeHtml, formatDuration, formatQty, iconClass, iconUrl } from './util.js';

let cpu = null;            // the CPU shown, null when the tree isn't
let treeData = null;       // the last /api/crafts/<cpu>/tree answer
let steps = {};            // item key -> {name, total, left, crafting, moved_at, state}
let clockSkew = 0;         // server's clock minus ours, for "no progress for"
let openState = new Map(); // tree path -> open, where the user toggled it
let branchesLoading = new Set();
let doneShape = '';        // the finished steps the tree was last drawn with
let jump = null;           // { key, index } of the stuck chip last clicked
let fetching = false;

// ---------- pure helpers (tested under server/tests/js/) ----------

// "1.2k / 4k" made of the step's total.
export function stepText(s) {
  return `${formatQty(Math.max(0, s.total - s.left))} / ${formatQty(s.total)}`;
}

export function stepPercent(s) {
  return s.total > 0 ? Math.min(100, Math.max(0, (1 - s.left / s.total) * 100)) : 0;
}

// What's under a shut step, from its loaded steps: "2 active · 1 stuck".
export function branchSummary(node, stepsByKey) {
  const counts = { active: 0, stuck: 0, waiting: 0 };
  const seen = new Set();
  const stack = [...(node.children || [])];
  while (stack.length) {
    const n = stack.pop();
    const s = stepsByKey[n.step];
    if (s && !seen.has(n.step) && s.state in counts) {
      seen.add(n.step);
      counts[s.state]++;
    }
    stack.push(...(n.children || []));
  }
  return ['stuck', 'active', 'waiting']
    .filter(k => counts[k] > 0)
    .map(k => `${counts[k]} ${k}`)
    .join(' · ');
}

// Whether a crafted step and every loaded one under it are finished.
export function branchDone(node, stepsByKey) {
  const stack = [node];
  while (stack.length) {
    const n = stack.pop();
    const s = stepsByKey[n.step];
    if (n.status !== 'stock' && (!s || s.state !== 'done')) return false;
    stack.push(...(n.children || []));
  }
  return true;
}

// ---------- drawing ----------

function segment(node, positions) {
  return node.status === 'via' ? `${node.key}#${positions.split('.').pop()}` : node.key;
}

function nodeHtml(node, parentPath, depth, positions) {
  const seg = segment(node, positions);
  const path = parentPath ? parentPath + ' > ' + seg : seg;
  const children = node.children || [];
  const unfetched = !node.children && node.more > 0;
  // Finished branches fold away on their own, unless opened by hand.
  const open = openState.has(path) ? openState.get(path) : depth < 2 && !branchDone(node, steps);
  if (open && unfetched) loadBranchSoon(positions);
  const caret = children.length || unfetched
    ? `<button class="plan-caret" data-action="job-tree-toggle" data-path="${escapeHtml(path)}" aria-expanded="${open}" title="${open ? 'Collapse' : 'Expand'}">${open ? '▾' : '▸'}</button>`
    : '<span class="plan-caret"></span>';
  const icon = node.icon
    ? `<img class="${iconClass('plan-icon', node.icon)}" src="${iconUrl(node.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : '';
  const storage = node.status === 'stock';
  const details = [];
  if (node.pattern) details.push(escapeHtml(node.pattern.provider || '?'));
  if (storage) details.push('from storage');
  return `<li class="plan-node job-node${storage ? ' job-storage' : ''}" data-pos="${escapeHtml(positions)}" data-step="${escapeHtml(node.step || '')}">
    <div class="plan-row">
      ${caret}${icon}
      <div class="plan-text">
        <div><span class="plan-name">${escapeHtml(node.name || '?')}</span> <span class="plan-qty job-step-text"></span></div>
        <div class="plan-details"><span class="job-provider">${details.join(' · ')}</span><span class="job-step-note"></span></div>
        ${storage ? '' : '<div class="job-bar"><div class="job-bar-fill"></div></div>'}
      </div>
    </div>
    ${open && unfetched ? '<ul><li class="plan-loading">Loading…</li></ul>' : ''}
    ${open && children.length ? `<ul>${children.map((c, i) =>
    nodeHtml(c, path, depth + 1, positions === '' ? String(i) : `${positions}.${i}`)).join('')}</ul>` : ''}
  </li>`;
}

function doneSignature() {
  return Object.keys(steps).filter(k => steps[k].state === 'done').sort().join(',');
}

function render() {
  const body = document.getElementById('ingredientsTree');
  if (!treeData || !treeData.tree) return;
  const scrollTop = body.scrollTop;
  body.innerHTML = `<ul class="plan-tree job-tree">${nodeHtml(treeData.tree.root, '', 0, '')}</ul>`;
  body.scrollTop = scrollTop;
  doneShape = doneSignature();
  patch();
}

// Numbers, bars and states in place: no redraw, so transitions run.
function patch() {
  const body = document.getElementById('ingredientsTree');
  const now = Date.now() / 1000 + clockSkew;
  for (const li of body.querySelectorAll('li.job-node')) {
    const s = steps[li.dataset.step];
    const node = nodeAt(treeData.tree.root, li.dataset.pos);
    const storage = li.classList.contains('job-storage');
    const state = storage ? 'storage' : (s ? s.state : 'unknown');
    for (const name of ['done', 'active', 'stuck', 'waiting']) li.classList.toggle(`job-${name}`, state === name);
    const row = li.querySelector(':scope > .plan-row');
    const text = row.querySelector('.job-step-text');
    const note = row.querySelector('.job-step-note');
    const fill = row.querySelector('.job-bar-fill');
    text.textContent = s && !storage ? stepText(s) : '';
    if (fill) fill.style.width = `${s ? stepPercent(s) : 0}%`;
    const notes = [];
    if (state === 'stuck' && s.moved_at) notes.push(`no progress for ${formatDuration(now - s.moved_at)}`);
    const shut = node && node.children && node.children.length && !li.querySelector(':scope > ul');
    if (shut) {
      const summary = branchSummary(node, steps);
      if (summary) notes.push(summary);
    }
    note.textContent = notes.join(' · ');
  }
  renderStuck();
}

function renderStuck() {
  const strip = document.getElementById('ingredientsStuck');
  const stuck = Object.entries(steps).filter(([, s]) => s.state === 'stuck');
  strip.hidden = stuck.length === 0;
  if (!stuck.length) {
    strip.innerHTML = '';
    return;
  }
  const at = (treeData && treeData.tree && treeData.tree.at) || {};
  strip.innerHTML = `<span class="plan-missing-label">Stuck ${stuck.length}:</span> ` + stuck.map(([key, s]) => {
    const places = at[key] || [];
    const counter = jump && jump.key === key && places.length > 1
      ? ` <span class="plan-jump-count">${jump.index + 1}/${places.length}</span>` : '';
    return `<button class="plan-chip job-stuck-chip" data-action="job-tree-jump" data-key="${escapeHtml(key)}"${places.length ? '' : ' disabled'} title="Show it in the tree"><b>${
      formatQty(s.left)}</b> ${escapeHtml(s.name || '?')}${counter}</button>`;
  }).join('');
}

// ---------- fetching ----------

function treeUrl(params) {
  return `/api/crafts/${encodeURIComponent(cpu)}/tree` + (params ? '?' + new URLSearchParams(params) : '');
}

function showNote(text) {
  const body = document.getElementById('ingredientsTree');
  body.innerHTML = `<div class="network-note">${escapeHtml(text)}</div>`;
}

async function fetchTree() {
  const forCpu = cpu;
  const res = await fetch(treeUrl());
  const data = await res.json().catch(() => ({}));
  if (forCpu !== cpu) return;
  treeData = data;
  branchesLoading = new Set();
  if (!data.tree) {
    showNote(data.reason || 'No tree for this job.');
    return;
  }
  render();
}

function loadBranchSoon(positions) {
  if (branchesLoading.has(positions)) return;
  branchesLoading.add(positions);
  setTimeout(() => loadBranch(positions, treeData), 0);
}

async function loadBranch(positions, forTree) {
  try {
    const res = await fetch(treeUrl({ path: positions, version: forTree.version }));
    const data = await res.json().catch(() => ({}));
    if (forTree !== treeData) return false;
    if (data.stale) {
      fetchTree();
      return false;
    }
    const node = res.ok && data.node ? nodeAt(forTree.tree.root, positions) : null;
    if (!node) return false;
    node.children = data.node.children || [];
    delete node.more;
    render();
    return true;
  } catch (e) {
    return false;  // left "Loading…"; shutting and opening the step tries again
  } finally {
    if (forTree === treeData) branchesLoading.delete(positions);
  }
}

// After every crafts poll while the tree is shown: the steps' numbers,
// and the tree again if the job changed shape (new steps, a new job,
// new patterns).
export async function refreshJobTree() {
  if (!cpu || fetching) return;
  fetching = true;
  const forCpu = cpu;
  try {
    const res = await fetch(`/api/crafts/${encodeURIComponent(cpu)}/steps`);
    const data = await res.json().catch(() => ({}));
    if (forCpu !== cpu || !data.steps) return;  // idle now: keep the last look
    steps = data.steps;
    if (typeof data.now === 'number') clockSkew = data.now - Date.now() / 1000;
    if (!treeData || (treeData.tree && data.version !== treeData.version) || (!treeData.tree && data.version)) {
      await fetchTree();
      return;
    }
    if (!treeData.tree) return;
    if (doneSignature() !== doneShape) render(); else patch();
  } catch (e) {
    // Kept as it was; the next poll tries again.
  } finally {
    fetching = false;
  }
}

async function jumpTo(key) {
  const places = (treeData && treeData.tree && treeData.tree.at[key]) || [];
  if (!places.length) return;
  jump = jump && jump.key === key ? { key, index: (jump.index + 1) % places.length } : { key, index: 0 };
  const positions = places[jump.index];
  const forTree = treeData;
  const loaded = await ensureLoaded(forTree.tree.root, positions, (prefix) => {
    branchesLoading.add(prefix);
    return loadBranch(prefix, forTree);
  });
  if (!loaded || forTree !== treeData) return;
  openAbove(forTree.tree.root, positions, openState, segment);
  render();
  flashRow(document.getElementById('ingredientsTree'), positions);
}

export function showJobTree(cpuName) {
  if (cpuName !== cpu) {
    cpu = cpuName;
    treeData = null;
    steps = {};
    openState = new Map();
    jump = null;
    doneShape = '';
    document.getElementById('ingredientsStuck').hidden = true;
    showNote('Working out the tree…');
  }
  refreshJobTree();
}

export function hideJobTree() {
  cpu = null;
  treeData = null;
  steps = {};
}

export function setupJobTreeActions() {
  delegateActions(document.getElementById('ingredientsTreeWrap'), {
    'job-tree-toggle': (el) => {
      openState.set(el.dataset.path, el.getAttribute('aria-expanded') !== 'true');
      render();
    },
    'job-tree-jump': (el) => {
      jumpTo(el.dataset.key);
    },
  });
}
