// One recipe dialog, with browser history as its navigation authority.
// Fetched pages/scroll live here, not in the crafts tab's redrawn markup.
import { descriptionHtml, nameText } from './description.js';
import { itemTooltipHtml } from './item-tooltip.js';
import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import { findNetworkItem, openItemHistory } from './history.js';
import { activeTab } from './tabs.js';
import { setPageView } from './page-title.js';
import { craftingTitle, displayChoices, layoutRecipePreviews, machineSidebarHtml, stackHtml, stepHtml } from './recipe-render.js';
import { setupRecipeTabs } from './recipe-tabs.js';
import { setupRecipeShell } from './recipe-shell.js';
import { setupRecipeWheel } from './recipe-wheel.js';
import { moveRecipePage, recipeFromUrl, recipeUrl, sameRecipeDataset } from './recipe-state.js';
import { delegateActions, escapeHtml, onBackdropClick, updateHtml } from './util.js';

let state = null;
let closing = false;
let serial = 0;
let generation = 0;
let requestController = null;
let cycleTimer = null;
let categoryTabs = null;
let rendered = null;
const cache = new Map();
const choices = new Map();
const tooltipEntries = new Map();
const tooltipIds = new Map();
let tooltipCell = null;
let shiftHeld = false;

function tooltipKey(entry) {
  const identity = JSON.stringify([entry.key, entry.display_id, entry.name, entry.size, entry.chance, entry.machine]);
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
  const stock = stockItem(entry);
  return itemTooltipHtml({...entry, isCraftable:stock?.isCraftable}, entry.info, { shiftHeld,
    aggregateStockVariants:true,
    details: notes ? `<div class="network-tooltip-stat">${escapeHtml(notes)}</div>` : '' });
}

function refreshTooltip() {
  if (!tooltipVisible(book()) || !tooltipCell?.isConnected) return;
  const html = recipeTooltipHtml(tooltipCell);
  if (html) showTooltip(html, undefined, undefined, book());
}

export function recipeViewerOpen() { return !!state; }
const current = () => state && state.recipe.stack.at(-1);
const list = () => document.getElementById('recipeViewerList');
const book = () => document.querySelector('.recipe-viewer-box');
const frameKey = frame => JSON.stringify([frame.id, frame.dir, frame.map]);
const cached = frame => frame && cache.get(frameKey(frame));
const displayingCurrent = () => rendered && current() && frameKey(rendered.frame) === frameKey(current());

function rememberScroll() {
  const model = rendered?.model;
  if (model) {
    model.scroll = list().scrollTop;
  }
}

export function hideRecipeViewer() {
  clearTooltip();
  tooltipEntries.clear(); tooltipIds.clear();
  rememberScroll();
  rendered = null;
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
  if (current().dir === dir && current().map === map) return;
  rememberScroll();
  const frame = { ...current(), dir, map };
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
  let model = !force && cached(frame);
  // A pending selection must not replace a complete view with empty rows or
  // zero counts. Cached selections can display immediately while revalidated.
  if (model?.loaded || !rendered) render(model);
  book().setAttribute('aria-busy', 'true');
  document.getElementById('recipeViewerBack').disabled = state.recipe.stack.length < 2;
  if (model?.loaded) updatePageButtons(model);
  else {
    document.getElementById('recipePagePrevious').disabled = true;
    document.getElementById('recipePageNext').disabled = true;
  }
  status(model?.loaded ? '' : 'Loading recipes…');
  try {
    const metadata = await get('/api/recipes/item?' + new URLSearchParams({ key: frame.key }), signal);
    if (token !== generation) return;
    if (!frame.map || !metadata[frame.dir][frame.map]) {
      frame.map = metadata.maps.find(map => metadata[frame.dir][map.id])?.id || '';
    }
    const stored = !force && cached(frame);
    model = stored && sameRecipeDataset(stored.metadata, metadata) ? stored
      : {metadata, recipes:[], total:metadata[frame.dir][frame.map] || 0, scroll:0, page:0, loaded:false};
    model.metadata = metadata;
    cache.set(frameKey(frame), model);
    // Bound the cache by item/category selections, including their pages.
    // Evicted ancestors/categories simply fetch again when revisited.
    if (cache.size > 100) cache.delete(cache.keys().next().value);
    frame.name = metadata.item.name;
    writeState(true);
    // On first open, metadata can supply the tabs before recipes arrive.
    // Once a recipe is visible, commit its successor only as a complete view.
    if (!rendered?.model?.loaded) render(model);
    if (!model.loaded) await page(model, frame, token, signal);
    if (token !== generation) return;
    render(model);
    startCycling();
  } catch (e) {
    if (token !== generation || e.name === 'AbortError') return;
    if (e.code === 'stale_recipes' && !force) { loadFrame(true); return; }
    status(e.message, true);
  } finally {
    if (token === generation) {
      book().setAttribute('aria-busy', 'false');
      if (displayingCurrent()) updatePageButtons(rendered.model);
    }
  }
}

async function page(model, frame, token, signal, offset = 0) {
  const query = new URLSearchParams({ key: frame.key, dir: frame.dir, offset: String(offset), limit: '20',
    version: model.metadata.version, fingerprint: model.metadata.fingerprint, assets: model.metadata.assets || '' });
  if (frame.map) query.set('map', frame.map);
  const data = await get('/api/recipes?' + query, signal);
  if (token !== generation) return;
  if (!sameRecipeDataset(model.metadata, data)) throw Object.assign(new Error('Game data changed. Reload recipes to continue.'), { code: 'stale_recipes' });
  data.recipes.forEach((recipe,index) => { model.recipes[offset+index] = recipe; });
  model.machines = {...model.machines, ...data.machines};
  model.total = data.total;
  model.loaded = true;
}

async function turnPage(direction, wrap = false) {
  const frame = current();
  const model = cached(frame);
  if (!displayingCurrent() || !model?.loaded || !model.total) return;
  const next = moveRecipePage(model.requestedPage ?? model.page ?? 0, direction, model.total, wrap);
  if (next === (model.requestedPage ?? model.page ?? 0)) return;
  // Already fetched pages remain usable during background revalidation.
  // Fetching another batch waits until its dataset has been checked.
  if (book().getAttribute('aria-busy') === 'true' && !model.recipes[next]) return;
  model.requestedPage = next;
  // Keep wheel travel received while another batch is loading. The latest
  // target wins, without starting parallel requests or showing empty pages.
  if (model.paging) return;
  const token = generation;
  model.paging = true;
  document.getElementById('recipePageNext').disabled = true;
  document.getElementById('recipePagePrevious').disabled = true;
  try {
    while (token === generation && !model.recipes[model.requestedPage] && model.requestedPage < model.total) {
      status('Loading recipes…');
      // Wrapping to the last page must not download every intervening page.
      const offset = Math.floor(model.requestedPage / 20) * 20;
      await page(model, frame, token, requestController.signal, offset);
    }
    if (token === generation) {
      model.page = Math.max(0, Math.min(model.requestedPage, model.total - 1)); model.scroll = 0;
      render(model); startCycling();
    }
  } catch (e) {
    if (token !== generation || e.name === 'AbortError') return;
    if (e.code === 'stale_recipes') { loadFrame(true); return; }
    status(e.message, true);
  } finally {
    model.paging = false;
    delete model.requestedPage;
    if (token === generation) updatePageButtons(model);
  }
}

function updatePageButtons(model) {
  const index = model?.page || 0;
  const pending = book().getAttribute('aria-busy') === 'true';
  document.getElementById('recipePagePrevious').disabled = !model?.loaded || index <= 0 || !!model.paging;
  document.getElementById('recipePageNext').disabled = !model?.loaded || index + 1 >= model.total || !!model.paging
    || (pending && !model.recipes[index+1]);
  document.getElementById('recipeViewerPage').textContent = model
    ? `Page ${model.total ? index + 1 : 0}/${model.total}` : 'Page …';
}

function turnCategory(direction) {
  const frame = current();
  const metadata = cached(frame)?.metadata || (rendered?.frame.key === frame?.key ? rendered.model?.metadata : null);
  const maps = metadata?.maps.filter(map => metadata[frame.dir][map.id]) || [];
  const index = maps.findIndex(map => map.id === frame?.map);
  if (maps.length > 1) select(frame.dir, maps[(index + direction % maps.length + maps.length) % maps.length].id);
}

function stockItem(entry) {
  if (!entry || entry.wildcard) return null;
  return findNetworkItem(entry.mod, entry.internal, entry.damage, entry.kind);
}

function currentStockItem() {
  return stockItem(rendered?.model?.metadata.item);
}

function updateStockButton() {
  const item = currentStockItem();
  const button = document.getElementById('recipeViewerStock');
  button.hidden = !item;
  if (item) button.setAttribute('aria-label', `Open stock page for ${nameText(item.name)}`);
  else button.removeAttribute('aria-label');
}

function render(model) {
  const frame = current();
  if (!frame) return;
  rendered = {frame:{...frame}, model};
  const meta = model?.metadata;
  const row = model?.recipes[model.page || 0];
  const name = nameText(meta?.item.name || frame.name || frame.key);
  book().dataset.itemName = name;
  book().dataset.direction = frame.dir;
  book().setAttribute('aria-label', `${frame.dir === 'using' ? 'Uses' : 'Recipes'} for ${name}`);
  setPageView('recipes', `${frame.dir === 'using' ? 'Uses' : 'Recipes'} · ${name}`);
  document.getElementById('recipeViewerBack').disabled = state.recipe.stack.length < 2;
  updateStockButton();
  const counts = meta?.[frame.dir] || {};
  const maps = (meta?.maps || []).filter(m => counts[m.id]).map(map =>
    row?.grid && row.map === map.id ? {...map,name:craftingTitle(row)} : map);
  categoryTabs.render(meta ? maps : null, counts, frame.map, frame.id);
  for (const id of ['recipeCategoryPrevious', 'recipeCategoryNext']) document.getElementById(id).disabled = maps.length < 2;
  const full = meta?.coverage.handlers?.includes('crafting');
  document.getElementById('recipeViewerCoverage').textContent = !meta ? '' : full
    ? 'Supported GregTech, crafting-table and furnace recipes; Assembly Line and NEI displays when supplied by the bundle. Custom recipes may be missing or approximate.'
    : 'This older bundle contains GregTech recipes only. Crafting-table, furnace and other handlers are not included.';
  const info = document.getElementById('recipeViewerInfo');
  const ore = meta?.info.ore || [];
  updateHtml(info, (meta?.info.description ? descriptionHtml(meta.info.description) : '') + (ore.length ? `<div class="recipe-grid-note">Ore dictionary: ${escapeHtml(ore.join(', '))}</div>` : '')
    + (row?.approximate?.some(flag => flag !== 'nei_display') ? '<div class="recipe-approximation">Approximate: NBT or custom matching rules may differ.</div>' : ''));
  clearTooltip();
  choices.clear(); tooltipEntries.clear(); tooltipIds.clear();
  const rows = model?.recipes.slice(model.page || 0, (model.page || 0) + 1) || [];
  updateHtml(document.getElementById('recipeViewerMachines'), machineSidebarHtml(model?.machines?.[frame.map] || [], {tooltipKey}));
  for (const row of rows) {
    row.preview?.slots.forEach((slot, index) => {
      const variants = displayChoices(slot.alternatives);
      choices.set(`${row.id}:preview-${index}`, variants);
    });
    row.groups?.forEach((group, index) => {
      choices.set(`${row.id}:group-${index}`, group.alternatives);
    });
    row.grid?.slots.forEach((slot, index) => {
      if (!slot) return;
      choices.set(`${row.id}:${index}`, displayChoices(slot.alternatives));
    });
  }
  updateHtml(list(), rows.map(row => `<article class="recipe-viewer-row" data-id="recipe-${row.id}">${stepHtml(row, {interactive:true, tooltipKey, machineSidebar:true})}</article>`).join(''));
  layoutRecipePreviews(list());
  if (model) list().scrollTop = model.scroll;
  updatePageButtons(model);
  status(model?.loaded ? model.total ? '' : 'No supported recipes in this bundle for this item.' : 'Loading recipes…');
}

function startCycling() {
  clearInterval(cycleTimer);
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  cycleTimer = setInterval(() => {
    if (document.hidden || !state) return;
    let changed = false;
    for (const el of list().querySelectorAll('[data-choice]')) {
      const alternatives = choices.get(el.dataset.choice);
      if (!alternatives || alternatives.length < 2 || el.contains(document.activeElement) || el.matches(':hover')) continue;
      const next = (Number(el.dataset.frame || 0) + 1) % alternatives.length;
      el.dataset.frame = String(next);
      updateHtml(el.querySelector('.recipe-preview-choice') || el, stackHtml(alternatives[next], { interactive: true, compact: true, preview: el.classList.contains('recipe-preview-slot'), tooltipKey }));
      changed = true;
    }
    if (changed) layoutRecipePreviews(list());
  }, 1000);
}

export function setupRecipeViewer() {
  categoryTabs = setupRecipeTabs();
  setupRecipeShell(book());
  setupRecipeWheel(book(), {isOpen:recipeViewerOpen, page:steps => turnPage(steps,true), category:turnCategory});
  let previewWidth = 0;
  const resize = new ResizeObserver(([entry]) => {
    if (entry.contentRect.width === previewWidth) return;
    previewWidth = entry.contentRect.width;
    if (state) requestAnimationFrame(() => layoutRecipePreviews(list()));
  });
  resize.observe(list());
  document.fonts.addEventListener('loadingdone', () => { if (state) layoutRecipePreviews(list()); });
  bindCellTooltip(book(), '[data-recipe-tooltip]', recipeTooltipHtml, cell => tooltipEntries.get(cell.dataset.recipeTooltip)?.key);
  const setShift = held => { if (shiftHeld !== held) { shiftHeld = held; refreshTooltip(); } };
  window.addEventListener('keydown', e => { if (e.key === 'Shift') setShift(true); });
  window.addEventListener('keyup', e => { if (e.key === 'Shift') setShift(false); });
  window.addEventListener('blur', () => { shiftHeld = false; if (state) clearTooltip(); });
  book().addEventListener('scroll', clearTooltip, { passive: true, capture: true });
  book().addEventListener('focusin', e => {
    const cell = e.target.closest('[data-recipe-tooltip]');
    if (!cell?.matches(':focus-visible')) return;
    const html = recipeTooltipHtml(cell);
    const r = cell.getBoundingClientRect();
    if (html) showTooltip(html, r.right - 8, r.bottom - 8, book());
  });
  book().addEventListener('focusout', clearTooltip);
  window.addEventListener('gcm-networkchange', () => {
    if (!state) return;
    // A snapshot may arrive after a direct recipe link opened. Update the
    // page's Stock action without disturbing recipes, focus or scroll.
    updateStockButton();
    refreshTooltip();
  });
  delegateActions(document, {
    'recipe-open': el => openRecipeViewer(el.dataset.key, el.dataset.dir || 'making'),
    'recipe-stock': () => { const it = currentStockItem(); if (it) { hideRecipeViewer(); openItemHistory(it); } },
    'recipe-filter': el => select(current().dir, el.dataset.map || ''),
    'recipe-tabs-previous': () => categoryTabs.scroll(-1),
    'recipe-tabs-next': () => categoryTabs.scroll(1),
    'recipe-category-previous': () => turnCategory(-1),
    'recipe-category-next': () => turnCategory(1),
    'recipe-page-previous': () => turnPage(-1),
    'recipe-page-next': () => turnPage(1),
    'recipe-retry': () => loadFrame(true),
    'recipe-back': () => { if (state?.recipe.stack.length > 1) history.back(); },
    'recipe-close': closeRecipeViewer,
  });
  onBackdropClick('recipeViewerModal', closeRecipeViewer);
  const modal = document.getElementById('recipeViewerModal');
  modal.addEventListener('contextmenu', e => {
    const el = e.target.closest('[data-action="recipe-open"]');
    if (el) { e.preventDefault(); e.stopPropagation(); openRecipeViewer(el.dataset.key, 'using'); }
  });
}
