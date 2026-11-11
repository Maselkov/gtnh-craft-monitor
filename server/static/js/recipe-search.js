// Bundle-wide lookup, separate from the live Network inventory filter.
import { itemTooltipHtml } from './item-tooltip.js';
import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import { recipeShortcutDirection } from './item-shortcuts.js';
import { RECIPE_RECENT_LIMIT, moveRecipePage, parseRecipeRecents, recipeCatalogCapacity, recipeCatalogGrid } from './recipe-state.js';
import { setupPageWheel } from './recipe-wheel.js';
import { topDialog } from './dialogs.js';
import { findNetworkItem, findNetworkSpecies, hideItemHistory, openItemHistory } from './history.js';
import { hideRecipeViewer, openRecipeViewer, recipeViewerOpen } from './recipe-viewer.js';
import { ensureNetworkStock } from './network-stock.js';
import { delegateActions, escapeHtml, iconClass, iconUrl, updateHtml } from './util.js';

const RECENTS_KEY = 'gcm-recipe-lookups-v1';
const CACHED_PAGES = 24, SLOW_LOAD_MS = 200, PREFETCH_DELAY_MS = 250;
// desktop: wide enough to dock beside the page; icons: a pointer that can
// hover and right-click, so the items are icons, as in NEI, rather than
// named rows with buttons.
let overlay, results, recentStrip, recentList, input, desktop, icons, recent, last = null;
let opened = false, loading = false, error = '', offset = 0, serial = 0, controller, timer;
let pageSize = 40, fitTimer, slowTimer, queryPending = false;
let entries = new Map(), shiftHeld = false, focusedTooltip = null;
// The recent strip's items, as the server named them; null until the
// first search after the page loads asks for them.
let recentItems = null;
// Fetched pages, oldest first, for the game data they came from: turning
// back to one, or onto a prefetched neighbour, draws it without waiting.
const pages = new Map();
let pagesVersion = null, prefetchTimer, prefetchController;
const warming = new Set();

function readRecent() {
  try { return parseRecipeRecents(localStorage.getItem(RECENTS_KEY)); }
  catch { return parseRecipeRecents(null); }
}

function saveRecent() {
  try { localStorage.setItem(RECENTS_KEY, JSON.stringify(recent)); } catch { /* Storage is optional. */ }
}

function remember(item) {
  const same = recent.version === last.version;
  recent = {version:last.version, keys:[item.key, ...(same ? recent.keys : [])
    .filter(key => key !== item.key)].slice(0,RECIPE_RECENT_LIMIT)};
  recentItems = [item, ...(same && recentItems || []).filter(i => i.key !== item.key)].slice(0,RECIPE_RECENT_LIMIT);
  saveRecent();
}

function layout() {
  overlay.dataset.nonmodal = desktop.matches ? 'true' : 'false';
  overlay.querySelector('.modal-box').setAttribute('aria-modal', desktop.matches ? 'false' : 'true');
  document.body.classList.toggle('recipe-search-open', opened && desktop.matches);
  document.querySelector('.recipe-viewer-box').setAttribute('aria-modal', opened && desktop.matches ? 'false' : 'true');
  if (opened && !desktop.matches && !overlay.contains(document.activeElement)) input.focus({preventScroll:true});
  scheduleFit();
}

// The recent strip keeps two rows of cells in the icon grid, whether or not its
// items have arrived yet, so the catalog's page size is right from the
// first fetch and doesn't change as lookups are added.
function sizeRecentStrip() {
  recentStrip.hidden = !(recentItems ?? recent.keys).length;
  if (recentStrip.hidden || !icons.matches) { recentList.style.height = ''; return null; }
  const gap = parseFloat(getComputedStyle(recentList).columnGap) || 4;
  const grid = recipeCatalogGrid(recentList.clientWidth, gap);
  recentList.style.height = `${2*grid.cell + gap}px`;
  return grid;
}

function capacity() {
  if (!icons.matches) return 40;
  sizeRecentStrip();
  const grid = results.querySelector('[data-id="catalog"] ul');
  if (!grid) return 40;
  const r = grid.getBoundingClientRect(), area = results.getBoundingClientRect();
  const gap = parseFloat(getComputedStyle(grid).columnGap) || 4;
  return recipeCatalogCapacity(r.width, results.clientHeight, r.top-area.top+results.scrollTop, gap);
}

function scheduleFit() {
  clearTimeout(fitTimer);
  if (!opened) return;
  fitTimer = setTimeout(() => {
    if (error) return; // An update/error message must not trigger automatic retry loops.
    const next = capacity();
    if (next === pageSize) return;
    pageSize = next;
    offset = Math.floor(offset/pageSize)*pageSize;
    if (!queryPending) load(offset);
  }, 100);
}

function turnPages(steps, wrap = false) {
  if (!last?.total || queryPending) return;
  const count = Math.ceil(last.total/pageSize);
  const next = moveRecipePage(Math.floor(offset/pageSize), steps, count, wrap)*pageSize;
  if (next === offset) return;
  offset = next;
  // While a request is in flight, keep every wheel step and fetch its final destination.
  if (!loading) load(offset);
  else render();
}

function close() {
  if (!opened) return;
  opened = false;
  serial++;
  controller?.abort();
  clearTimeout(timer);
  clearTimeout(fitTimer);
  stopPrefetch();
  setLoading(false);
  overlay.style.display = 'none';
  document.querySelectorAll('[aria-controls="recipeSearchModal"]').forEach(el => el.setAttribute('aria-expanded', 'false'));
  hideTooltip(); focusedTooltip = null;
  layout();
}

function open() {
  if (document.body.classList.contains('private-locked')) return;
  const top = topDialog();
  if (top && !['recipeViewerModal','recipeSearchModal'].includes(top.id)) return;
  if (!opened) {
    opened = true;
    overlay.style.display = 'flex';
    layout();
    document.querySelectorAll('[aria-controls="recipeSearchModal"]').forEach(el => el.setAttribute('aria-expanded', 'true'));
    recent = readRecent();
    ensureNetworkStock();
    load(offset);
  }
  input.focus({preventScroll:true});
}

function activate(key, direction = 'making', stock = false) {
  const item = entries.get(key);
  if (!item || !opened) return;
  remember(item);
  hideTooltip();
  // On desktop the book stays open beside whatever the item opens, as
  // the recipe book in game does.
  if (!desktop.matches) close();
  else render();
  if (stock && !item.wildcard) {
    const stored = item.species ? findNetworkSpecies(item.mod, item.internal, item.damage, item.species)
      : findNetworkItem(item.mod, item.internal, item.damage, item.kind);
    if (recipeViewerOpen()) hideRecipeViewer();
    openItemHistory(stored || {...item, size:null, isCraftable:false});
  } else {
    // Recipes open over a stock page the book opened, as recipes do from it.
    if (document.getElementById('itemHistoryModal').style.display !== 'none') hideItemHistory();
    openRecipeViewer(item.key, direction);
  }
}

function row(item, section) {
  const key = escapeHtml(item.key);
  const mod = item.info?.mod_name || item.mod || (item.kind === 'fluid' ? 'Fluid' : 'Item');
  return `<li data-id="${section}:${key}" data-recipe-search-key="${key}" class="recipe-search-result">
    <button class="recipe-search-item" data-action="recipe-search-select" data-key="${key}" aria-label="Recipes for ${escapeHtml(item.name)}">
      <span class="recipe-search-icon">${item.icon ? `<img class="${iconClass('',item.icon)}" src="${iconUrl(item.icon)}" alt="" loading="lazy" data-remove-on-error>` : `<span aria-hidden="true">${escapeHtml(item.name.charAt(0))}</span>`}</span>
      <span class="recipe-search-name"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(mod)}</small></span>
    </button>
    <span class="recipe-search-actions">
      <button class="recipe-search-action" data-action="recipe-search-select" data-key="${key}" aria-label="Recipes for ${escapeHtml(item.name)}"><span aria-hidden="true">←</span> Recipes</button>
      <button class="recipe-search-action recipe-search-uses" data-action="recipe-search-select" data-key="${key}" data-dir="using" aria-label="Uses for ${escapeHtml(item.name)}">Uses <span aria-hidden="true">→</span></button>
    </span>
  </li>`;
}

function render() {
  const items = last?.items || [];
  const grid = sizeRecentStrip();
  const recents = (recentItems || []).slice(0, grid ? 2*grid.columns : undefined);
  entries = new Map([...recents,...items].map(item => [item.key,item]));
  results.setAttribute('aria-busy', String(loading));
  updateHtml(recentList, recents.map(i => row(i,'recent')).join(''));
  updateHtml(results, `<section data-id="catalog"><ul class="recipe-search-grid">${items.map(i => row(i,'catalog')).join('')}</ul>${!items.length && !loading && !error ? '<p class="recipe-search-empty">No matching items or fluids. Try a shorter name or another mod.</p>' : ''}</section>`);
  // A page turn shows where it's going at once; the page it replaces stays
  // up, and usable, until the new one is ready.
  const total = queryPending ? 0 : last?.total || 0;
  document.getElementById('recipeSearchStatus').textContent = error || (loading && (queryPending || !last) ? 'Searching…' : last ? `${last.total.toLocaleString()} matching ${last.total === 1 ? 'entry' : 'entries'}` : '');
  document.getElementById('recipeSearchRetry').hidden = !error;
  document.getElementById('recipeSearchPrevious').disabled = !total || offset === 0;
  document.getElementById('recipeSearchNext').disabled = !total || offset + pageSize >= total;
  document.getElementById('recipeSearchPage').textContent = total ? `${offset+1}–${Math.min(offset+pageSize,total)} of ${total.toLocaleString()}` : '';
}

function setLoading(value) {
  loading = value;
  clearTimeout(slowTimer);
  if (!value) results.classList.remove('recipe-search-slow');
  else slowTimer = setTimeout(() => results.classList.add('recipe-search-slow'), SLOW_LOAD_MS);
}

function pageKey(params) {
  return JSON.stringify([params.q, params.limit, params.offset]);
}

function cachedPage(params) {
  const key = pageKey(params), data = pages.get(key);
  if (data) { pages.delete(key); pages.set(key, data); }
  return data;
}

function storePage(params, data) {
  const version = `${data.version}|${data.fingerprint}|${data.assets}`;
  if (version !== pagesVersion) { pages.clear(); pagesVersion = version; }
  pages.set(pageKey(params), {items:data.items, total:data.total, offset:data.offset, limit:data.limit, version:data.version});
  while (pages.size > CACHED_PAGES) pages.delete(pages.keys().next().value);
}

async function fetchPage(params, signal, withRecents) {
  const query = new URLSearchParams({q:params.q, limit:String(params.limit), offset:String(params.offset)});
  if (withRecents) {
    query.set('recent_version', recent.version);
    for (const key of recent.keys) query.append('key',key);
  }
  const response = await fetch('/api/recipes/search?'+query, {signal});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not search recipes.');
  return data;
}

async function load(page = 0) {
  if (!opened) return;
  const token = ++serial;
  controller?.abort(); controller = new AbortController();
  stopPrefetch();
  offset = page; error = '';
  pageSize = capacity();
  page = offset = Math.floor(page/pageSize)*pageSize;
  const params = {q:input.value.trim(), limit:pageSize, offset:page};
  // Recents are named once per page load, then kept up here as they change.
  const withRecents = recentItems === null;
  let data = withRecents ? null : cachedPage(params);
  if (!data) {
    setLoading(true); render();
    try {
      data = await fetchPage(params, controller.signal, withRecents);
    } catch (e) {
      if (!opened || token !== serial || e.name === 'AbortError') return;
      last = null; queryPending = false; error = e.message || 'Could not search recipes.';
      setLoading(false); render();
      return;
    }
    if (!opened || token !== serial) return;
    storePage(params, data);
    if (withRecents) {
      recentItems = recent.version === data.version ? data.recent : [];
      // Keys the game data no longer knows drop out of the stored list.
      if (recentItems.length !== recent.keys.length) { recent = {version:data.version, keys:recentItems.map(i => i.key)}; saveRecent(); }
    }
  }
  if (page !== offset) return await load(offset);
  if (data.total && page >= data.total) return await load(0);
  if (recent.version !== data.version) { recent = {version:data.version,keys:[]}; recentItems = []; saveRecent(); }
  last = data;
  setLoading(false);
  results.scrollTop = 0;
  render();
  const fitted = capacity();
  if (fitted !== params.limit) return await load(Math.floor(offset/fitted)*fitted);
  queryPending = false;
  render();
  prefetchTimer = setTimeout(() => prefetch(token, params), PREFETCH_DELAY_MS);
  return data;
}

function stopPrefetch() {
  clearTimeout(prefetchTimer);
  prefetchController?.abort();
}

// Loads each item's icon into the browser's cache, quietly, so the page
// shows complete when it's turned to.
function warmIcons(items) {
  return Promise.all(items.filter(item => item.icon).map(item => new Promise(resolve => {
    const img = new Image();
    img.fetchPriority = 'low';
    warming.add(img);
    img.onload = img.onerror = () => { warming.delete(img); resolve(); };
    img.src = iconUrl(item.icon);
  })));
}

// The next page, then the previous one (both wrap, as the wheel does):
// their results and icons, so turning to them doesn't wait.
async function prefetch(token, shown) {
  prefetchController = new AbortController();
  const signal = prefetchController.signal, count = Math.ceil((last?.total || 0)/shown.limit);
  const at = shown.offset/shown.limit;
  for (const step of [1, -1]) {
    if (count < 2 || token !== serial || signal.aborted) return;
    const params = {...shown, offset:moveRecipePage(at, step, count, true)*shown.limit};
    let data = cachedPage(params);
    if (!data) {
      try { data = await fetchPage(params, signal, false); }
      catch { return; }
      if (token !== serial) return;
      storePage(params, data);
    }
    await warmIcons(data.items);
  }
}

function searchChanged() {
  controller?.abort(); serial++;
  clearTimeout(timer);
  queryPending = true;
  stopPrefetch();
  offset = 0; setLoading(true); error = ''; render();
  timer = setTimeout(() => load(0), 180);
}

function tooltip(cell) {
  const item = entries.get(cell.dataset.recipeSearchKey);
  return item && itemTooltipHtml(item, item.info, {shiftHeld,aggregateStockVariants:true});
}

function refreshTooltip() {
  if (focusedTooltip?.isConnected && tooltipVisible(overlay)) {
    const html = tooltip(focusedTooltip);
    if (html) showTooltip(html, undefined, undefined, overlay);
  }
}

function swipe(element, direction, action) {
  let start = null;
  element.addEventListener('pointerdown', event => {
    if (desktop.matches || event.pointerType === 'mouse' || event.target.closest('input, select, .history-close-btn')) return;
    start = {x:event.clientX,y:event.clientY};
    element.setPointerCapture(event.pointerId);
  });
  element.addEventListener('pointerup', event => {
    if (!start) return;
    const dx = event.clientX-start.x, dy = event.clientY-start.y;
    start = null;
    if (direction*dx > 55 && Math.abs(dx) > 2*Math.abs(dy)) { event.preventDefault(); action(); }
  });
  element.addEventListener('pointercancel', () => { start = null; });
}

export function setupRecipeSearch() {
  overlay = document.getElementById('recipeSearchModal');
  results = document.getElementById('recipeSearchResults'); input = document.getElementById('recipeSearchInput');
  recentStrip = document.getElementById('recipeSearchRecent'); recentList = document.getElementById('recipeSearchRecentList');
  desktop = window.matchMedia('(min-width:1000px)'); icons = window.matchMedia('not all and (pointer: coarse)'); recent = readRecent();
  layout(); desktop.addEventListener('change', layout); icons.addEventListener('change', layout);
  delegateActions(document, {
    'recipe-search-open':open, 'recipe-search-close':close,
    'recipe-search-retry':() => load(offset),
    'recipe-search-previous':() => turnPages(-1),
    'recipe-search-next':() => turnPages(1),
    'recipe-search-select':(el,event) => activate(el.dataset.key,el.dataset.dir || 'making',event.shiftKey),
    'recipe-search-clear-recent':() => { recent = {version:recent.version,keys:[]}; recentItems = []; saveRecent(); render(); scheduleFit(); },
  });
  input.addEventListener('input', searchChanged);
  new ResizeObserver(scheduleFit).observe(results);
  setupPageWheel(overlay, {isOpen:() => opened && icons.matches, page:steps => turnPages(steps,true)});
  overlay.addEventListener('click', e => { if (e.target === overlay && !desktop.matches) close(); });
  overlay.addEventListener('contextmenu', e => {
    const cell = e.target.closest('[data-recipe-search-key]');
    if (cell) { e.preventDefault(); activate(cell.dataset.recipeSearchKey,'using'); }
  });
  bindCellTooltip(overlay,'[data-recipe-search-key]', cell => { focusedTooltip = cell; return tooltip(cell); }, cell => entries.get(cell.dataset.recipeSearchKey));
  overlay.addEventListener('focusin', e => {
    const cell = e.target.closest('[data-recipe-search-key]');
    if (!cell) return;
    focusedTooltip = cell;
    const r = cell.getBoundingClientRect(), html = tooltip(cell);
    if (html) showTooltip(html,r.left,r.bottom,overlay);
  });
  overlay.addEventListener('focusout', () => { hideTooltip(); focusedTooltip = null; });
  document.addEventListener('keydown', e => {
    if (!opened) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); return; }
    if (e.key === 'Shift') { shiftHeld = true; refreshTooltip(); }
    const cell = overlay.querySelector('[data-recipe-search-key]:hover') || document.activeElement?.closest('[data-recipe-search-key]');
    const direction = recipeShortcutDirection(e);
    if (cell && direction) { e.preventDefault(); activate(cell.dataset.recipeSearchKey,direction); return; }
    if (e.target === input && e.key === 'Enter') {
      e.preventDefault();
      const selectFirst = () => {
        const first = results.querySelector('[data-recipe-search-key]');
        if (first) activate(first.dataset.recipeSearchKey);
      };
      if (loading) {
        clearTimeout(timer);
        load(0).then(data => { if (data && opened && !loading) selectFirst(); });
      } else selectFirst();
    }
    const arrows = icons.matches && e.target !== input ? ['ArrowDown','ArrowUp','ArrowLeft','ArrowRight'] : ['ArrowDown','ArrowUp'];
    if (arrows.includes(e.key) && (cell || e.target === input)) {
      // In the order they're shown: recents under the icon grid, above the list.
      const lists = [results, recentList];
      if (!icons.matches) lists.reverse();
      const cells = lists.flatMap(list => [...list.querySelectorAll('[data-recipe-search-key]')]);
      if (!cells.length) return;
      const anchor = document.activeElement?.closest('[data-recipe-search-key]') || cell;
      const columns = icons.matches && anchor ? getComputedStyle(anchor.parentElement).gridTemplateColumns.split(' ').length : 1;
      const step = e.key === 'ArrowDown' ? columns : e.key === 'ArrowUp' ? -columns : e.key === 'ArrowRight' ? 1 : -1;
      const n = e.target === input ? 0 : Math.max(0,Math.min(cells.length-1,cells.indexOf(anchor)+step));
      e.preventDefault(); cells[n].querySelector('button').focus();
    }
  }, true);
  window.addEventListener('keyup', e => { if (e.key === 'Shift') { shiftHeld = false; refreshTooltip(); } });
  window.addEventListener('blur', () => { shiftHeld = false; });
  window.addEventListener('gcm-networkchange', () => { if (opened) refreshTooltip(); });
  new MutationObserver(() => {
    if (document.body.classList.contains('private-locked')) {
      close(); last = null; entries.clear(); pages.clear(); results.replaceChildren(); recentList.replaceChildren();
    }
  }).observe(document.body,{attributes:true,attributeFilter:['class']});
  // Another dialog closes the book, except a stock page on desktop, which
  // sits beside it as recipes do (Shift-click, here or in a recipe).
  const dialogs = new MutationObserver(records => {
    if (opened && records.some(({target}) => target.style.display !== 'none'
      && !(desktop.matches && target.id === 'itemHistoryModal'))) close();
  });
  for (const modal of document.querySelectorAll('.modal-overlay:not(#recipeSearchModal):not(#recipeViewerModal)')) {
    dialogs.observe(modal,{attributes:true,attributeFilter:['style']});
  }
  swipe(document.getElementById('recipeSearchEdge'),-1,open);
  swipe(overlay.querySelector('.recipe-search-header'),1,close);
}
