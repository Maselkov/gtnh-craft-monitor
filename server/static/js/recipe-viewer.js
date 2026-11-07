// One recipe dialog, with browser history as its navigation authority.
// Fetched pages/scroll live here, not in the crafts tab's redrawn markup.
import { descriptionHtml, nameText } from './description.js';
import { findNetworkItem, openItemHistory } from './history.js';
import { activeTab } from './tabs.js';
import { setPageView } from './page-title.js';
import { stackHtml, stepHtml } from './recipe-render.js';
import { recipeFromUrl, recipeUrl, sameRecipeDataset, trailDistance } from './recipe-state.js';
import { delegateActions, escapeHtml, iconClass, iconUrl, onBackdropClick, updateHtml } from './util.js';

let state = null;
let closing = false;
let serial = 0;
let generation = 0;
let requestController = null;
let cycleTimer = null;
const cache = new Map();
const choices = new Map();
const entries = new Map();

export function recipeViewerOpen() { return !!state; }
const current = () => state && state.recipe.stack.at(-1);
const list = () => document.getElementById('recipeViewerList');

function rememberScroll() {
  const frame = current();
  const model = frame && cache.get(frame.id);
  if (model) model.scroll = list().scrollTop;
}

export function hideRecipeViewer() {
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
  cache.delete(frame.id);
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
  const tabs = [{ id: '', name: 'All', machines: [] }, ...maps].map(m => {
    const icon = m.machines[0]?.icon;
    return `<button data-id="map-${escapeHtml(m.id)}" data-action="recipe-filter" data-map="${escapeHtml(m.id)}" aria-pressed="${frame.map === m.id}">${icon ? `<img src="${iconUrl(icon)}" alt="" data-remove-on-error>` : ''}<span>${escapeHtml(m.name)}</span><span class="recipe-map-count">${m.id ? counts[m.id] : total(counts)}</span></button>`;
  });
  updateHtml(document.getElementById('recipeViewerMaps'), meta ? tabs.join('') : '');
  const full = meta?.coverage.handlers?.includes('crafting');
  document.getElementById('recipeViewerCoverage').textContent = !meta ? '' : full
    ? 'Supported GregTech, crafting-table and furnace recipes. Custom recipes may be missing or approximate.'
    : 'This older bundle contains GregTech recipes only. Crafting-table, furnace and other handlers are not included.';
  const info = document.getElementById('recipeViewerInfo');
  const ore = meta?.info.ore || [];
  updateHtml(info, (meta?.info.description ? descriptionHtml(meta.info.description) : '') + (ore.length ? `<div class="recipe-grid-note">Ore dictionary: ${escapeHtml(ore.join(', '))}</div>` : ''));
  document.getElementById('recipeViewerDetails').hidden = !meta?.info.description && !ore.length;
  entries.clear(); choices.clear();
  const rows = model?.recipes || [];
  for (const row of rows) {
    for (const e of [...row.inputs, ...row.outputs]) entries.set(e.key, e);
    row.grid?.slots.forEach((slot, index) => {
      if (!slot) return;
      choices.set(`${row.id}:${index}`, slot.alternatives);
      for (const e of slot.alternatives) entries.set(e.key, e);
    });
  }
  updateHtml(list(), rows.map(row => `<article class="recipe-viewer-row" data-id="recipe-${row.id}">${stepHtml(row, { interactive: true, hasStock: stockItem })}${row.approximate?.length ? '<div class="recipe-approximation">Approximate: NBT or custom matching rules may differ.</div>' : ''}</article>`).join(''));
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
      updateHtml(el, stackHtml(alternatives[next], { interactive: true, compact: true, hasStock: stockItem }));
    }
  }, 1000);
}

export function setupRecipeViewer() {
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
    'recipe-open': (el, e) => openRecipeViewer(el.dataset.key, el.dataset.dir || (e.shiftKey || e.altKey ? 'using' : 'making')),
    'recipe-stock': el => { const it = stockItem(entries.get(el.dataset.key)); if (it) { hideRecipeViewer(); openItemHistory(it); } },
    'recipe-filter': el => select(current().dir, el.dataset.map || ''),
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
  modal.addEventListener('keydown', e => {
    const el = e.target.closest('[data-action="recipe-open"]');
    if (el && !e.ctrlKey && !e.metaKey && ['r', 'u'].includes(e.key.toLowerCase())) {
      e.preventDefault(); openRecipeViewer(el.dataset.key, e.key.toLowerCase() === 'u' ? 'using' : 'making');
    }
  });
}
