// One recipe dialog, with browser history as its navigation authority.
// Fetched pages/scroll live here, not in the crafts tab's redrawn markup.
import { descriptionHtml, nameText } from './description.js';
import { itemTooltipHtml } from './item-tooltip.js';
import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import { findNetworkItem, openItemHistory } from './history.js';
import { activeTab } from './tabs.js';
import { setPageView } from './page-title.js';
import { amountText, displayChoices, layoutRecipePreviews, stackHtml, stepHtml } from './recipe-render.js';
import { setupRecipeTabs } from './recipe-tabs.js';
import { recipeFromUrl, recipeUrl, sameRecipeDataset, trailDistance } from './recipe-state.js';
import { delegateActions, escapeHtml, iconClass, iconUrl, onBackdropClick, updateHtml } from './util.js';

let state = null;
let closing = false;
let serial = 0;
let generation = 0;
let requestController = null;
let cycleTimer = null;
let categoryTabs = null;
const cache = new Map();
const choices = new Map();
const entries = new Map();
const tooltipEntries = new Map();
const tooltipIds = new Map();
let tooltipCell = null;
let shiftHeld = false;

function tooltipKey(entry) {
  const identity = JSON.stringify([entry.key, entry.display_id, entry.name, entry.size, entry.chance]);
  if (!tooltipIds.has(identity)) {
    const id = String(tooltipIds.size);
    tooltipIds.set(identity, id);
    tooltipEntries.set(id, entry);
  }
  return tooltipIds.get(identity);
}

function clearTooltip() {
  tooltipCell = null;
  hideTooltip();
}

function recipeTooltipHtml(cell) {
  const entry = state && tooltipEntries.get(cell.dataset.recipeTooltip);
  if (!entry) return null;
  tooltipCell = cell;
  const notes = [entry.wildcard ? 'Any damage' : '', cell.dataset.tooltipNote,
    entry.chance ? `${entry.chance}% chance` : ''].filter(Boolean).join(' · ');
  return itemTooltipHtml(entry, entry.info, { shiftHeld,
    stat: entry.size === 0 ? 'Not consumed' : `Amount: ${amountText(entry)}`,
    details: notes ? `<div class="network-tooltip-stat">${escapeHtml(notes)}</div>` : '' });
}

function refreshTooltip() {
  if (!tooltipVisible(list()) || !tooltipCell?.isConnected) return;
  const html = recipeTooltipHtml(tooltipCell);
  if (html) showTooltip(html, undefined, undefined, list());
}

export function recipeViewerOpen() { return !!state; }
const current = () => state && state.recipe.stack.at(-1);
const list = () => document.getElementById('recipeViewerList');

function rememberScroll() {
  const frame = current();
  const model = frame && cache.get(frame.id);
  if (model) model.scroll = list().scrollTop;
}

export function hideRecipeViewer() {
  clearTooltip();
  tooltipEntries.clear(); tooltipIds.clear();
  rememberScroll();
  state = null;
  closing = false;
  generation++;
  requestController?.abort();
  clearInterval(cycleTimer);
  cycleTimer = null;
  document.getElementById('recipeViewerModal').style.display = 'none';
  setPageView('recipes', '');
}

export function closeRecipeViewer() {
  if (state && !closing) { closing = true; history.go(-state.recipe.stack.length); }
}

function writeState(replace) {
  const frame = current();
  history[replace ? 'replaceState' : 'pushState'](state, '', recipeUrl(frame));
}

export function openRecipeViewer(key, dir = 'making', map = '') {
  if (!key) return;
  rememberScroll();
  const frame = { key, dir, map, id: `${Date.now()}-${++serial}` };
  const previous = state;
  state = previous
    ? { ...previous, recipe: { ...previous.recipe, stack: [...previous.recipe.stack, frame] } }
    : { ...(history.state || {}), view: 'recipes', tab: activeTab,
      recipe: { stack: [frame] } };
  writeState(false);
  loadFrame();
}

export function restoreRecipeViewer(next) {
  if (!next?.recipe?.stack?.length) return;
  rememberScroll();
  state = next;
  closing = false;
  loadFrame();
}

export function recipeViewerFromUrl() {
  const frame = recipeFromUrl(new URL(location.href));
  if (!frame) return;
  if (history.state?.view === 'recipes' && history.state.recipe?.stack?.length) {
    restoreRecipeViewer(history.state);
  } else {
    // A fresh shared link gets an in-app origin. Close/Back can then go to
    // Network without accidentally navigating away from the monitor.
    history.replaceState({ view: 'network' }, '', '/network');
    openRecipeViewer(frame.key, frame.dir, frame.map);
  }
}

function select(dir, map) {
  if (!state) return;
  rememberScroll();
  const frame = { ...current(), dir, map };
  const previous = cache.get(frame.id);
  // Keep category metadata while the new list loads, so activating a
  // tab does not remove the tab row and lose keyboard focus.
  if (previous) cache.set(frame.id, { metadata: previous.metadata, recipes: [], total: 0, scroll: 0, loaded: false });
  else cache.delete(frame.id);
  state = { ...state, recipe: { stack: [...state.recipe.stack.slice(0, -1), frame] } };
  writeState(true);
  loadFrame();
}

async function get(url, signal) {
  const response = await fetch(url, { signal });
  const body = await response.json();
  if (!response.ok) throw Object.assign(new Error(body.error || 'Could not load recipes.'), { code: body.code });
  return body;
}

function status(text, error = false) {
  const el = document.getElementById('recipeViewerStatus');
  el.textContent = text;
  el.classList.toggle('modal-error', error);
  document.getElementById('recipeViewerRetry').hidden = !error;
}

async function loadFrame(force = false) {
  clearTooltip();
  const frame = current();
  if (!frame) return;
  const token = ++generation;
  requestController?.abort();
  requestController = new AbortController();
  const signal = requestController.signal;
  clearInterval(cycleTimer);
  document.getElementById('recipeViewerModal').style.display = 'flex';
  let model = !force && cache.get(frame.id);
  render(model);
  status(model ? 'Checking game data…' : 'Loading recipes…');
  try {
    const metadata = await get('/api/recipes/item?' + new URLSearchParams({ key: frame.key }), signal);
    if (token !== generation) return;
    if (!model || !sameRecipeDataset(model.metadata, metadata)) {
      model = { metadata, recipes: [], total: 0, scroll: 0, loaded: false };
    } else model.metadata = metadata;
    cache.set(frame.id, model);
    // A small cache keeps deep trails navigable without retaining every page
    // ever viewed. Evicted ancestors simply fetch again when revisited.
    if (cache.size > 100) cache.delete(cache.keys().next().value);
    if (frame.map && !metadata[frame.dir][frame.map]) frame.map = '';
    frame.name = metadata.item.name;
    writeState(true);
    render(model);
    if (!model.loaded) await page(model, frame, token, signal);
    if (token !== generation) return;
    render(model);
    startCycling();
  } catch (e) {
    if (token !== generation || e.name === 'AbortError') return;
    if (e.code === 'stale_recipes' && !force) { loadFrame(true); return; }
    status(e.message, true);
  }
}

async function page(model, frame, token, signal) {
  const query = new URLSearchParams({ key: frame.key, dir: frame.dir, offset: String(model.recipes.length), limit: '20',
    version: model.metadata.version, fingerprint: model.metadata.fingerprint, assets: model.metadata.assets || '' });
  if (frame.map) query.set('map', frame.map);
  const data = await get('/api/recipes?' + query, signal);
  if (token !== generation) return;
  if (!sameRecipeDataset(model.metadata, data)) throw Object.assign(new Error('Game data changed. Reload recipes to continue.'), { code: 'stale_recipes' });
  model.recipes.push(...data.recipes);
  model.total = data.total;
  model.loaded = true;
}

async function more() {
  const frame = current();
  const model = frame && cache.get(frame.id);
  const button = document.getElementById('recipeViewerMore');
  if (!model || button.disabled) return;
  rememberScroll();
  const token = generation;
  button.disabled = true;
  status('Loading more recipes…');
  try {
    await page(model, frame, token, requestController.signal);
    if (token === generation) { render(model); startCycling(); }
  } catch (e) {
    if (token !== generation || e.name === 'AbortError') return;
    if (e.code === 'stale_recipes') { loadFrame(true); return; }
    status(e.message, true);
  } finally { if (token === generation) button.disabled = false; }
}

function stockItem(entry) {
  if (!entry || entry.wildcard) return null;
  return findNetworkItem(entry.mod, entry.internal, entry.damage, entry.kind);
}

function render(model) {
  const frame = current();
  if (!frame) return;
  const meta = model?.metadata;
  const name = nameText(meta?.item.name || frame.name || frame.key);
  document.getElementById('recipeViewerTitle').textContent = name;
  setPageView('recipes', `Recipes · ${name}`);
  const icon = document.getElementById('recipeViewerIcon');
  icon.hidden = !meta?.item.icon;
  if (meta?.item.icon) { icon.src = iconUrl(meta.item.icon); icon.className = iconClass('modal-icon', meta.item.icon); }
  document.getElementById('recipeViewerIdentity').textContent = meta ? `GTNH ${meta.version} · ${frame.key}` : frame.key;
  document.getElementById('recipeViewerBack').disabled = state.recipe.stack.length < 2;
  updateHtml(document.getElementById('recipeViewerTrail'), state.recipe.stack.map((f, index) =>
    `<button data-id="${escapeHtml(f.id)}" data-action="recipe-ancestor" data-index="${index}"${index === state.recipe.stack.length - 1 ? ' aria-current="page"' : ''}>${escapeHtml(nameText(f.name || f.key))}</button>`).join('<span aria-hidden="true">›</span>'));
  const total = counts => Object.values(counts || {}).reduce((sum, n) => sum + n, 0);
  for (const dir of ['making', 'using']) {
    const el = document.getElementById(dir === 'making' ? 'recipeViewerMaking' : 'recipeViewerUsing');
    el.textContent = `${dir === 'making' ? 'Recipes' : 'Uses'}${meta ? ` (${total(meta[dir])})` : ''}`;
    el.setAttribute('aria-pressed', String(frame.dir === dir));
  }
  const counts = meta?.[frame.dir] || {};
  const maps = (meta?.maps || []).filter(m => counts[m.id]);
  categoryTabs.render(meta ? maps : null, counts, frame.map, frame.id);
  const full = meta?.coverage.handlers?.includes('crafting');
  document.getElementById('recipeViewerCoverage').textContent = !meta ? '' : full
    ? 'Supported GregTech, crafting-table and furnace recipes; Assembly Line and NEI displays when supplied by the bundle. Custom recipes may be missing or approximate.'
    : 'This older bundle contains GregTech recipes only. Crafting-table, furnace and other handlers are not included.';
  const info = document.getElementById('recipeViewerInfo');
  const ore = meta?.info.ore || [];
  updateHtml(info, (meta?.info.description ? descriptionHtml(meta.info.description) : '') + (ore.length ? `<div class="recipe-grid-note">Ore dictionary: ${escapeHtml(ore.join(', '))}</div>` : ''));
  document.getElementById('recipeViewerDetails').hidden = !meta?.info.description && !ore.length;
  clearTooltip();
  entries.clear(); choices.clear(); tooltipEntries.clear(); tooltipIds.clear();
  const rows = model?.recipes || [];
  for (const row of rows) {
    for (const e of [...row.inputs, ...row.outputs]) entries.set(e.key, e);
    if (row.research) entries.set(row.research.item.key, row.research.item);
    row.preview?.slots.forEach((slot, index) => {
      const variants = displayChoices(slot.alternatives);
      choices.set(`${row.id}:preview-${index}`, variants);
      for (const e of variants) entries.set(e.key, e);
    });
    row.groups?.forEach((group, index) => {
      choices.set(`${row.id}:group-${index}`, group.alternatives);
      for (const e of group.alternatives) entries.set(e.key, e);
    });
    row.grid?.slots.forEach((slot, index) => {
      if (!slot) return;
      choices.set(`${row.id}:${index}`, slot.alternatives);
      for (const e of slot.alternatives) entries.set(e.key, e);
    });
  }
  updateHtml(list(), rows.map(row => `<article class="recipe-viewer-row" data-id="recipe-${row.id}">${stepHtml(row, { interactive: true, hasStock: stockItem, tooltipKey })}${row.approximate?.filter(flag => flag !== 'nei_display').length ? '<div class="recipe-approximation">Approximate: NBT or custom matching rules may differ.</div>' : ''}</article>`).join(''));
  layoutRecipePreviews(list());
  if (model) list().scrollTop = model.scroll;
  const button = document.getElementById('recipeViewerMore');
  button.hidden = !model?.loaded || rows.length >= model.total;
  button.disabled = false;
  status(model?.loaded ? model.total ? `${rows.length} of ${model.total} recipes` : 'No supported recipes in this bundle for this selection.' : 'Loading recipes…');
}

function startCycling() {
  clearInterval(cycleTimer);
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  cycleTimer = setInterval(() => {
    if (document.hidden || !state) return;
    for (const el of list().querySelectorAll('[data-choice]')) {
      const alternatives = choices.get(el.dataset.choice);
      if (!alternatives || alternatives.length < 2 || el.contains(document.activeElement) || el.matches(':hover')) continue;
      const next = (Number(el.dataset.frame || 0) + 1) % alternatives.length;
      el.dataset.frame = String(next);
      updateHtml(el.querySelector('.recipe-preview-choice') || el, stackHtml(alternatives[next], { interactive: true, compact: true, preview: el.classList.contains('recipe-preview-slot'), hasStock: stockItem, tooltipKey }));
    }
  }, 1000);
}

export function setupRecipeViewer() {
  categoryTabs = setupRecipeTabs();
  bindCellTooltip(list(), '[data-recipe-tooltip]', recipeTooltipHtml, cell => tooltipEntries.get(cell.dataset.recipeTooltip)?.key);
  const setShift = held => { if (shiftHeld !== held) { shiftHeld = held; refreshTooltip(); } };
  window.addEventListener('keydown', e => { if (e.key === 'Shift') setShift(true); });
  window.addEventListener('keyup', e => { if (e.key === 'Shift') setShift(false); });
  window.addEventListener('blur', () => { shiftHeld = false; if (state) clearTooltip(); });
  list().addEventListener('scroll', clearTooltip, { passive: true });
  list().addEventListener('focusin', e => {
    const cell = e.target.closest('[data-recipe-tooltip]');
    if (!cell?.matches(':focus-visible')) return;
    const html = recipeTooltipHtml(cell);
    const r = cell.getBoundingClientRect();
    if (html) showTooltip(html, r.right - 8, r.bottom - 8, list());
  });
  list().addEventListener('focusout', clearTooltip);
  window.addEventListener('gcm-networkchange', () => {
    if (!state) return;
    // Stock links follow a snapshot that can arrive after a direct recipe
    // link opened. Update only the links, preserving pages, focus and scroll.
    for (const wrap of list().querySelectorAll('.recipe-stack-wrap')) {
      const key = wrap.querySelector('[data-action="recipe-open"]').dataset.key;
      const entry = entries.get(key);
      const links = wrap.querySelector('.recipe-stack-links');
      const existing = links.querySelector('[data-action="recipe-stock"]');
      if (!stockItem(entry)) { existing?.remove(); continue; }
      if (existing) continue;
      const button = document.createElement('button');
      button.className = 'recipe-stock-link';
      button.dataset.action = 'recipe-stock';
      button.dataset.key = key;
      button.textContent = 'Stock';
      button.setAttribute('aria-label', `Open stock page for ${entry.name}`);
      links.appendChild(button);
    }
  });
  delegateActions(document, {
    'recipe-open': el => openRecipeViewer(el.dataset.key, el.dataset.dir || 'making'),
    'recipe-stock': el => { const it = stockItem(entries.get(el.dataset.key)); if (it) { hideRecipeViewer(); openItemHistory(it); } },
    'recipe-filter': el => select(current().dir, el.dataset.map || ''),
    'recipe-tabs-previous': () => categoryTabs.scroll(-1),
    'recipe-tabs-next': () => categoryTabs.scroll(1),
    'recipe-direction': el => select(el.dataset.dir, ''),
    'recipe-more': more,
    'recipe-retry': () => loadFrame(true),
    'recipe-back': () => { if (state?.recipe.stack.length > 1) history.back(); },
    'recipe-ancestor': el => { const distance = trailDistance(state.recipe.stack, Number(el.dataset.index)); if (distance) history.go(distance); },
    'recipe-close': closeRecipeViewer,
  });
  onBackdropClick('recipeViewerModal', closeRecipeViewer);
  const modal = document.getElementById('recipeViewerModal');
  modal.addEventListener('contextmenu', e => {
    const el = e.target.closest('[data-action="recipe-open"]');
    if (el) { e.preventDefault(); e.stopPropagation(); openRecipeViewer(el.dataset.key, 'using'); }
  });
}
