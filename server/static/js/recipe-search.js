// Bundle-wide lookup, separate from the live Network inventory filter.
import { itemTooltipHtml } from './item-tooltip.js';
import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import { recipeShortcutDirection } from './item-shortcuts.js';
import { moveRecipePage, parseRecipeRecents, recipeCatalogCapacity } from './recipe-state.js';
import { setupPageWheel } from './recipe-wheel.js';
import { topDialog } from './dialogs.js';
import { findNetworkItem, openItemHistory } from './history.js';
import { hideRecipeViewer, openRecipeViewer, recipeViewerOpen } from './recipe-viewer.js';
import { ensureNetworkStock } from './network-stock.js';
import { delegateActions, escapeHtml, iconClass, iconUrl, updateHtml } from './util.js';

const RECENTS_KEY = 'gcm-recipe-lookups-v1';
let overlay, results, input, desktop, recent, last = null;
let opened = false, loading = false, error = '', offset = 0, serial = 0, controller, timer;
let pageSize = 40, fitTimer, queryPending = false;
let entries = new Map(), shiftHeld = false, focusedTooltip = null;

function readRecent() {
  try { return parseRecipeRecents(localStorage.getItem(RECENTS_KEY)); }
  catch { return parseRecipeRecents(null); }
}

function saveRecent() {
  try { localStorage.setItem(RECENTS_KEY, JSON.stringify(recent)); } catch { /* Storage is optional. */ }
}

function remember(item) {
  recent = {version:last.version, keys:[item.key, ...(recent.version === last.version ? recent.keys : [])
    .filter(key => key !== item.key)].slice(0,8)};
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

function capacity() {
  if (!desktop.matches) return 40;
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
  const pages = Math.ceil(last.total/pageSize);
  const next = moveRecipePage(Math.floor(offset/pageSize), steps, pages, wrap)*pageSize;
  if (next === offset) return;
  offset = next;
  // While a request is in flight, keep every wheel step and fetch its final destination.
  if (!loading) load(offset);
}

function close() {
  if (!opened) return;
  opened = false;
  serial++;
  controller?.abort();
  clearTimeout(timer);
  clearTimeout(fitTimer);
  loading = false;
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
  if (!item || loading || !opened) return;
  remember(item);
  hideTooltip();
  if (stock && !item.wildcard) {
    const stored = findNetworkItem(item.mod, item.internal, item.damage, item.kind);
    close();
    if (recipeViewerOpen()) hideRecipeViewer();
    openItemHistory(stored || {...item, size:null, isCraftable:false});
  } else {
    if (!desktop.matches) close();
    openRecipeViewer(item.key, direction);
  }
}

function row(item, section) {
  const key = escapeHtml(item.key);
  const mod = item.info?.mod_name || item.mod || (item.kind === 'fluid' ? 'Fluid' : 'Item');
  return `<li data-id="${section}:${key}" data-recipe-search-key="${key}" class="recipe-search-result">
    <button class="recipe-search-item" data-action="recipe-search-select" data-key="${key}" aria-label="Recipes for ${escapeHtml(item.name)}"${loading ? ' disabled' : ''}>
      <span class="recipe-search-icon">${item.icon ? `<img class="${iconClass('',item.icon)}" src="${iconUrl(item.icon)}" alt="" loading="lazy" data-remove-on-error>` : `<span aria-hidden="true">${escapeHtml(item.name.charAt(0))}</span>`}</span>
      <span class="recipe-search-name"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(mod)}</small></span>
    </button>
    <span class="recipe-search-actions">
      <button class="recipe-search-action" data-action="recipe-search-select" data-key="${key}" aria-label="Recipes for ${escapeHtml(item.name)}"${loading ? ' disabled' : ''}><span aria-hidden="true">←</span> Recipes</button>
      <button class="recipe-search-action recipe-search-uses" data-action="recipe-search-select" data-key="${key}" data-dir="using" aria-label="Uses for ${escapeHtml(item.name)}"${loading ? ' disabled' : ''}>Uses <span aria-hidden="true">→</span></button>
    </span>
  </li>`;
}

function render() {
  const items = last?.items || [], recents = last?.recent || [];
  entries = new Map([...recents,...items].map(item => [item.key,item]));
  results.setAttribute('aria-busy', String(loading));
  updateHtml(results, `${recents.length ? `<section data-id="recent"><div class="recipe-search-section"><h3>Recent lookups</h3><button class="text-btn" data-action="recipe-search-clear-recent"${loading ? ' disabled' : ''}>Clear</button></div><ul>${recents.map(i => row(i,'recent')).join('')}</ul></section>` : ''}
    <section data-id="catalog">${recents.length ? '<h3>All items</h3>' : ''}<ul>${items.map(i => row(i,'catalog')).join('')}</ul>${!items.length && !loading && !error ? '<p class="recipe-search-empty">No matching items or fluids. Try a shorter name or another mod.</p>' : ''}</section>`);
  const total = last?.total || 0;
  const shownOffset = last?.offset ?? offset, shownSize = last?.limit ?? pageSize;
  document.getElementById('recipeSearchStatus').textContent = error || (loading ? 'Searching…' : `${total.toLocaleString()} matching ${total === 1 ? 'entry' : 'entries'}`);
  document.getElementById('recipeSearchRetry').hidden = !error;
  document.getElementById('recipeSearchPrevious').disabled = loading || shownOffset === 0;
  document.getElementById('recipeSearchNext').disabled = loading || shownOffset + shownSize >= total;
  document.getElementById('recipeSearchPage').textContent = total ? `${shownOffset+1}–${Math.min(shownOffset+shownSize,total)} of ${total.toLocaleString()}` : '';
}

async function load(page = 0) {
  if (!opened) return;
  const token = ++serial;
  controller?.abort(); controller = new AbortController();
  offset = page; loading = true; error = ''; render();
  pageSize = capacity();
  page = offset = Math.floor(page/pageSize)*pageSize;
  const requestSize = pageSize;
  const query = new URLSearchParams({q:input.value.trim(),kind:document.getElementById('recipeSearchKind').value,limit:String(requestSize),offset:String(page),recent_version:recent.version});
  for (const key of recent.keys) query.append('key',key);
  try {
    const response = await fetch('/api/recipes/search?'+query, {signal:controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not search recipes.');
    if (!opened || token !== serial) return;
    if (page !== offset) return await load(offset);
    if (data.total && page >= data.total) return await load(0);
    if (recent.version !== data.version) { recent = {version:data.version,keys:[]}; saveRecent(); }
    last = data;
    results.scrollTop = 0;
    render();
    const fitted = capacity();
    if (fitted !== requestSize) return await load(Math.floor(offset/fitted)*fitted);
    queryPending = false;
    return data;
  } catch (e) {
    if (!opened || token !== serial || e.name === 'AbortError') return;
    last = null; queryPending = false; error = e.message || 'Could not search recipes.';
  } finally {
    if (opened && token === serial) { loading = false; render(); }
  }
}

function searchChanged() {
  controller?.abort(); serial++;
  clearTimeout(timer);
  queryPending = true;
  offset = 0; loading = true; error = ''; render();
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
  desktop = window.matchMedia('(min-width:1000px)'); recent = readRecent();
  layout(); desktop.addEventListener('change', layout);
  delegateActions(document, {
    'recipe-search-open':open, 'recipe-search-close':close,
    'recipe-search-retry':() => load(offset),
    'recipe-search-previous':() => turnPages(-1),
    'recipe-search-next':() => turnPages(1),
    'recipe-search-select':(el,event) => activate(el.dataset.key,el.dataset.dir || 'making',event.shiftKey),
    'recipe-search-clear-recent':() => { recent = {version:last.version,keys:[]}; saveRecent(); last.recent = []; render(); scheduleFit(); },
  });
  input.addEventListener('input', searchChanged);
  new ResizeObserver(scheduleFit).observe(results);
  setupPageWheel(overlay, {isOpen:() => opened && desktop.matches, page:steps => turnPages(steps,true)});
  document.getElementById('recipeSearchKind').addEventListener('change', searchChanged);
  overlay.addEventListener('click', e => { if (e.target === overlay && !desktop.matches) close(); });
  results.addEventListener('contextmenu', e => {
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
    const cell = results.querySelector('[data-recipe-search-key]:hover') || document.activeElement?.closest('[data-recipe-search-key]');
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
    const arrows = desktop.matches && e.target !== input ? ['ArrowDown','ArrowUp','ArrowLeft','ArrowRight'] : ['ArrowDown','ArrowUp'];
    if (arrows.includes(e.key) && (cell || e.target === input)) {
      const cells = [...results.querySelectorAll('[data-recipe-search-key]')];
      if (!cells.length || loading) return;
      const anchor = document.activeElement?.closest('[data-recipe-search-key]') || cell;
      const columns = desktop.matches && anchor ? getComputedStyle(anchor.parentElement).gridTemplateColumns.split(' ').length : 1;
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
      close(); last = null; entries.clear(); results.replaceChildren();
    }
  }).observe(document.body,{attributes:true,attributeFilter:['class']});
  const dialogs = new MutationObserver(records => {
    if (opened && records.some(({target}) => target.style.display !== 'none')) close();
  });
  for (const modal of document.querySelectorAll('.modal-overlay:not(#recipeSearchModal):not(#recipeViewerModal)')) {
    dialogs.observe(modal,{attributes:true,attributeFilter:['style']});
  }
  swipe(document.getElementById('recipeSearchEdge'),-1,open);
  swipe(overlay.querySelector('.recipe-search-header'),1,close);
}
