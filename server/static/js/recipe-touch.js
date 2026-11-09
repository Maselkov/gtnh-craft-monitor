// Touch inspection and local recipe zoom. Desktop keeps its direct mouse actions.
import { delegateActions, escapeHtml, iconUrl, updateHtml } from './util.js';
import { hideTooltip } from './tooltip.js';

export const recipeTouchQuery = '(pointer: coarse)';
export const recipeCompactQuery = '(max-width: 700px), (pointer: coarse)';
const clampRecipeZoom = scale => Math.max(1, Math.min(3, scale));

export function setupRecipeTouch(book, {entryForCell, htmlForCell, openRecipes, openStock, layout}) {
  const mode = matchMedia(recipeTouchQuery);
  const modal = document.getElementById('recipeViewerModal');
  const sheet = document.getElementById('recipeInspect');
  const content = document.getElementById('recipeInspectContent');
  const actions = document.getElementById('recipeInspectActions');
  const viewport = document.getElementById('recipeViewerList');
  const fit = document.getElementById('recipeZoomFit');
  let selected = null, entry = null, pinch = null, scale = 1, suppressClickUntil = 0;
  let swipe = null;

  function close(restoreFocus = true) {
    const previous = selected;
    selected?.classList.remove('recipe-inspected');
    selected?.removeAttribute('aria-expanded');
    selected?.removeAttribute('aria-controls');
    selected = null; entry = null; sheet.hidden = true;
    book.classList.remove('recipe-inspecting');
    sheet.classList.remove('recipe-inspect-expanded');
    document.getElementById('recipeInspectMore').setAttribute('aria-pressed','false');
    if (restoreFocus && previous?.isConnected) previous.focus({preventScroll:true});
  }
  function refresh() {
    if (!entry || !selected?.isConnected) return;
    const details = htmlForCell(selected) || '';
    document.getElementById('recipeInspectMore').hidden = !entry.info?.shift_description;
    const amount = entry.size == null ? '' : entry.size === 0 ? 'Not consumed' :
      `Recipe amount: ${Number(entry.size).toLocaleString('en-US')}${entry.kind === 'fluid' ? ' mB' : ''}`;
    updateHtml(content, `${entry.icon ? `<img class="recipe-inspect-icon" src="${iconUrl(entry.icon)}" alt="">` : ''}<div>${details}${amount ? `<div class="network-tooltip-stat">${escapeHtml(amount)}</div>` : ''}</div>`);
    updateHtml(actions, `${entry.key ? '<button data-action="recipe-inspect-recipes">Recipes</button><button data-action="recipe-inspect-uses">Uses</button>' : ''}${entry.internal && !entry.wildcard && !entry.vis ? '<button data-action="recipe-inspect-stock">Stock</button>' : ''}`);
  }
  function inspect(cell) {
    const next = entryForCell(cell);
    if (!next) return;
    const opening = sheet.hidden;
    selected?.classList.remove('recipe-inspected');
    selected?.removeAttribute('aria-expanded');
    selected?.removeAttribute('aria-controls');
    selected = cell; entry = {...next};
    selected.classList.add('recipe-inspected');
    selected.setAttribute('aria-expanded','true');
    selected.setAttribute('aria-controls','recipeInspect');
    sheet.setAttribute('aria-label', `${entry.name} details`);
    hideTooltip(); refresh(); sheet.hidden = false;
    book.classList.add('recipe-inspecting');
    if (opening) document.getElementById('recipeInspectClose').focus({preventScroll:true});
  }
  function setZoom(next, anchor = {x:viewport.clientWidth / 2, y:0}) {
    const previous = scale;
    scale = clampRecipeZoom(next);
    const left = (viewport.scrollLeft + anchor.x) / previous;
    const top = (viewport.scrollTop + anchor.y) / previous;
    for (const row of viewport.querySelectorAll('.recipe-viewer-row')) row.style.width = `${scale * 100}%`;
    fit.textContent = `${Math.round(scale * 100)}%`;
    fit.setAttribute('aria-label', `Zoom ${Math.round(scale * 100)} percent. Fit recipe to window`);
    document.getElementById('recipeZoomOut').disabled = scale <= 1;
    document.getElementById('recipeZoomIn').disabled = scale >= 3;
    layout();
    viewport.scrollLeft = left * scale - anchor.x;
    viewport.scrollTop = top * scale - anchor.y;
  }
  function reset() {
    close(false); hideTooltip(); pinch = null;
    setZoom(1, {x:0,y:0});
    viewport.scrollLeft = 0;
  }
  modal.addEventListener('click', event => {
    if (!mode.matches) return;
    if (performance.now() < suppressClickUntil) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (sheet.contains(event.target)) return;
    const cell = event.target.closest('[data-recipe-tooltip]');
    if (cell && book.contains(cell)) {
      event.preventDefault(); event.stopImmediatePropagation(); inspect(cell); return;
    }
    if (!sheet.hidden) {
      close(false);
      if (event.target === modal) { event.preventDefault(); event.stopImmediatePropagation(); }
    }
  }, true);
  book.addEventListener('keydown', event => {
    if (!mode.matches || !['Enter',' '].includes(event.key)) return;
    const cell = event.target.closest('[data-recipe-tooltip]');
    if (cell && cell.tagName !== 'BUTTON') { event.preventDefault(); inspect(cell); }
  });
  // Touch-generated compatibility mouse events must not leave a floating tooltip behind.
  for (const type of ['mouseover', 'mousemove']) book.addEventListener(type, event => {
    if (mode.matches) event.stopImmediatePropagation();
  }, true);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !sheet.hidden) {
      event.preventDefault(); event.stopImmediatePropagation(); close();
    }
  }, true);
  delegateActions(sheet, {
    'recipe-inspect-close': () => close(),
    'recipe-inspect-recipes': () => { const key = entry?.key; close(false); if (key) { openRecipes(key, 'making'); viewport.focus({preventScroll:true}); } },
    'recipe-inspect-uses': () => { const key = entry?.key; close(false); if (key) { openRecipes(key, 'using'); viewport.focus({preventScroll:true}); } },
    'recipe-inspect-stock': () => { const item = entry; close(false); if (item) openStock(item); },
    'recipe-inspect-more': () => {
      const button = document.getElementById('recipeInspectMore');
      const expanded = button.getAttribute('aria-pressed') !== 'true';
      button.setAttribute('aria-pressed', String(expanded));
      sheet.classList.toggle('recipe-inspect-expanded', expanded);
      refresh();
    },
  });
  delegateActions(book, {
    'recipe-zoom-out': () => setZoom(scale - .5),
    'recipe-zoom-in': () => setZoom(scale + .5),
    'recipe-zoom-fit': () => setZoom(1),
  });
  const distance = touches => Math.hypot(touches[0].clientX-touches[1].clientX, touches[0].clientY-touches[1].clientY);
  viewport.addEventListener('touchstart', event => {
    if (!mode.matches || event.touches.length !== 2) return;
    const rect = viewport.getBoundingClientRect();
    pinch = {distance:distance(event.touches), scale,
      x:(event.touches[0].clientX+event.touches[1].clientX)/2-rect.left,
      y:(event.touches[0].clientY+event.touches[1].clientY)/2-rect.top};
    close(false); event.preventDefault();
  }, {passive:false});
  viewport.addEventListener('touchmove', event => {
    if (!pinch || event.touches.length !== 2) return;
    event.preventDefault();
    setZoom(pinch.scale * distance(event.touches) / Math.max(1, pinch.distance), pinch);
  }, {passive:false});
  for (const type of ['touchend','touchcancel']) viewport.addEventListener(type, () => {
    if (pinch) suppressClickUntil = performance.now()+400;
    pinch = null;
  }, {passive:true});
  const handle = document.getElementById('recipeInspectHandle');
  handle.addEventListener('touchstart', event => {
    swipe = event.touches.length === 1 ? event.touches[0].clientY : null;
  }, {passive:true});
  handle.addEventListener('touchend', event => {
    if (swipe != null && event.changedTouches[0].clientY-swipe > 45) close();
    swipe = null;
  }, {passive:true});
  handle.addEventListener('touchcancel', () => { swipe = null; }, {passive:true});
  mode.addEventListener('change', reset);
  return {reset, refresh, isMobile:() => mode.matches, isSelected:cell => !!selected && cell.contains(selected),
    expanded:() => !sheet.hidden && sheet.classList.contains('recipe-inspect-expanded')};
}
