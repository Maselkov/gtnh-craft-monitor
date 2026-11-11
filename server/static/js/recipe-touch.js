// Touch inspection, recipe zoom and swipe navigation. Desktop keeps its
// direct mouse actions.
import { delegateActions, escapeHtml, iconClass, iconUrl, updateHtml } from './util.js';
import { hideTooltip } from './tooltip.js';

export const recipeTouchQuery = '(pointer: coarse)';
export const recipeCompactQuery = '(max-width: 700px), (pointer: coarse)';
const clampRecipeZoom = scale => Math.max(1, Math.min(3, scale));
// A swipe on a heading bar: far enough, and mostly sideways.
const SWIPE_DISTANCE = 40;
const DOUBLE_TAP_MS = 300;
// The sheet's open/close slide; app.css has the same duration.
const SHEET_MS = 200;

export function setupRecipeTouch(book, {entryForCell, htmlForCell, openRecipes, openStock, layout, turnPage, turnCategory}) {
  const mode = matchMedia(recipeTouchQuery);
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const modal = document.getElementById('recipeViewerModal');
  const sheet = document.getElementById('recipeInspect');
  const content = document.getElementById('recipeInspectContent');
  const actions = document.getElementById('recipeInspectActions');
  const viewport = document.getElementById('recipeViewerList');
  let selected = null, entry = null, pinch = null, scale = 1, suppressClickUntil = 0;
  let more = false, hideTimer = 0, settleTimer = 0, drag = null, lastTap = null, tapStart = null;

  const scroller = () => content.querySelector('.recipe-inspect-text');
  // A fade at the bottom while the text has more below it.
  function updateFade() {
    const text = scroller();
    if (text) text.classList.toggle('recipe-inspect-fade', text.scrollTop + text.clientHeight < text.scrollHeight - 2);
  }
  function close(restoreFocus = true) {
    const previous = selected;
    selected?.classList.remove('recipe-inspected');
    selected?.removeAttribute('aria-expanded');
    selected?.removeAttribute('aria-controls');
    selected = null; entry = null; more = false; drag = null;
    book.classList.remove('recipe-inspecting');
    if (!sheet.hidden && !hideTimer) {
      clearTimeout(settleTimer);
      sheet.style.maxHeight = '';
      if (reducedMotion.matches) hide();
      else {
        sheet.classList.add('recipe-inspect-away');
        hideTimer = setTimeout(hide, SHEET_MS);
      }
    }
    if (restoreFocus && previous?.isConnected) previous.focus({preventScroll:true});
  }
  function hide() {
    clearTimeout(hideTimer); hideTimer = 0;
    sheet.hidden = true;
    sheet.classList.remove('recipe-inspect-away', 'recipe-inspect-tall', 'recipe-inspect-expanded');
    sheet.style.transform = ''; sheet.style.maxHeight = '';
  }
  function refresh() {
    if (!entry || !selected?.isConnected) return;
    const details = htmlForCell(selected) || '';
    const amount = entry.size == null ? '' : entry.size === 0 ? 'Not consumed' :
      `Recipe amount: ${Number(entry.size).toLocaleString('en-US')}${entry.kind === 'fluid' ? ' mB' : ''}`;
    const toggle = entry.info?.shift_description
      ? `<button id="recipeInspectMore" class="recipe-inspect-more" data-action="recipe-inspect-more" aria-pressed="${more}">${more ? 'Less details' : 'More details'}<span aria-hidden="true">${more ? ' ▴' : ' ▾'}</span></button>`
      : '';
    updateHtml(content, `${entry.icon ? `<img class="${iconClass('recipe-inspect-icon', entry.icon)}" src="${iconUrl(entry.icon)}" alt="">` : ''}<div class="recipe-inspect-text">${details}${amount ? `<div class="network-tooltip-stat">${escapeHtml(amount)}</div>` : ''}${toggle}</div>`);
    updateHtml(actions, `${entry.key ? '<button data-action="recipe-inspect-recipes">Recipes</button><button data-action="recipe-inspect-uses">Uses</button>' : ''}${entry.internal && !entry.wildcard && !entry.vis ? '<button data-action="recipe-inspect-stock">Stock</button>' : ''}`);
    sheet.classList.toggle('recipe-inspect-expanded', more);
    updateFade();
  }
  function inspect(cell) {
    const next = entryForCell(cell);
    if (!next) return;
    const opening = sheet.hidden || !!hideTimer;
    selected?.classList.remove('recipe-inspected');
    selected?.removeAttribute('aria-expanded');
    selected?.removeAttribute('aria-controls');
    if (selected !== cell) more = false;
    selected = cell; entry = {...next};
    selected.classList.add('recipe-inspected');
    selected.setAttribute('aria-expanded','true');
    selected.setAttribute('aria-controls','recipeInspect');
    sheet.setAttribute('aria-label', `${entry.name} details`);
    hideTooltip();
    if (hideTimer) {
      // Reopened while sliding away: slide back from where it is.
      clearTimeout(hideTimer); hideTimer = 0;
      sheet.classList.remove('recipe-inspect-away');
    } else if (sheet.hidden) {
      sheet.classList.add('recipe-inspect-away');
      sheet.hidden = false;
      void sheet.offsetHeight; // Start the slide from below the screen.
      sheet.classList.remove('recipe-inspect-away');
    }
    refresh();
    book.classList.add('recipe-inspecting');
    if (opening) document.getElementById('recipeInspectClose').focus({preventScroll:true});
  }
  function setZoom(next, anchor = {x:viewport.clientWidth / 2, y:0}) {
    const previous = scale;
    scale = clampRecipeZoom(next);
    const left = (viewport.scrollLeft + anchor.x) / previous;
    const top = (viewport.scrollTop + anchor.y) / previous;
    for (const row of viewport.querySelectorAll('.recipe-viewer-row')) row.style.width = `${scale * 100}%`;
    viewport.dataset.zoom = String(Math.round(scale * 100));
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
    if (performance.now() < suppressClickUntil) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (!mode.matches) return;
    if (sheet.contains(event.target)) return;
    const cell = event.target.closest('[data-recipe-tooltip]');
    if (cell && book.contains(cell)) {
      event.preventDefault(); event.stopImmediatePropagation(); inspect(cell); return;
    }
    if (!sheet.hidden && !hideTimer) {
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
    if (event.key === 'Escape' && !sheet.hidden && !hideTimer) {
      event.preventDefault(); event.stopImmediatePropagation(); close();
    }
  }, true);
  delegateActions(sheet, {
    'recipe-inspect-close': () => close(),
    'recipe-inspect-recipes': () => { const key = entry?.key; close(false); if (key) { openRecipes(key, 'making'); viewport.focus({preventScroll:true}); } },
    'recipe-inspect-uses': () => { const key = entry?.key; close(false); if (key) { openRecipes(key, 'using'); viewport.focus({preventScroll:true}); } },
    'recipe-inspect-stock': () => { const item = entry; close(false); if (item) openStock(item); },
    'recipe-inspect-more': () => {
      more = !more;
      refresh();
      document.getElementById('recipeInspectMore')?.focus({preventScroll:true});
    },
  });
  content.addEventListener('scroll', updateFade, {capture:true, passive:true});
  new ResizeObserver(updateFade).observe(content);

  // Pinch to zoom; double-tap a zoomed recipe to fit it again.
  const distance = touches => Math.hypot(touches[0].clientX-touches[1].clientX, touches[0].clientY-touches[1].clientY);
  viewport.addEventListener('touchstart', event => {
    if (!mode.matches) return;
    if (event.touches.length === 1) {
      tapStart = {x:event.touches[0].clientX, y:event.touches[0].clientY};
      return;
    }
    tapStart = null; lastTap = null;
    if (event.touches.length !== 2) return;
    const rect = viewport.getBoundingClientRect();
    pinch = {distance:distance(event.touches), scale,
      x:(event.touches[0].clientX+event.touches[1].clientX)/2-rect.left,
      y:(event.touches[0].clientY+event.touches[1].clientY)/2-rect.top};
    close(false); event.preventDefault();
  }, {passive:false});
  viewport.addEventListener('touchmove', event => {
    if (tapStart && Math.hypot(event.touches[0].clientX-tapStart.x, event.touches[0].clientY-tapStart.y) > 10) tapStart = null;
    if (!pinch || event.touches.length !== 2) return;
    event.preventDefault();
    setZoom(pinch.scale * distance(event.touches) / Math.max(1, pinch.distance), pinch);
  }, {passive:false});
  viewport.addEventListener('touchend', event => {
    if (!tapStart || event.touches.length) return;
    const now = performance.now(), point = tapStart;
    tapStart = null;
    if (lastTap && now - lastTap.time < DOUBLE_TAP_MS && Math.hypot(point.x-lastTap.x, point.y-lastTap.y) < 30 && scale > 1) {
      lastTap = null;
      const rect = viewport.getBoundingClientRect();
      close(false);
      setZoom(1, {x:point.x-rect.left, y:point.y-rect.top});
      suppressClickUntil = now + 400;
      return;
    }
    lastTap = {...point, time:now};
  }, {passive:true});
  for (const type of ['touchend','touchcancel']) viewport.addEventListener(type, () => {
    if (pinch) suppressClickUntil = performance.now()+400;
    pinch = null;
  }, {passive:true});

  // Swipe the category or page bar: left for the next one, right for the
  // previous one, as the arrows at its ends do. The tab strip and machine
  // strip above keep scrolling sideways.
  for (const [bar, turn] of [[document.getElementById('recipeCategoryName').parentElement, turnCategory],
    [document.getElementById('recipeViewerPage').parentElement, direction => turnPage(direction, true)]]) {
    let start = null;
    bar.addEventListener('touchstart', event => {
      start = event.touches.length === 1 ? {x:event.touches[0].clientX, y:event.touches[0].clientY} : null;
    }, {passive:true});
    bar.addEventListener('touchend', event => {
      if (!start || event.touches.length) return;
      const dx = event.changedTouches[0].clientX - start.x, dy = event.changedTouches[0].clientY - start.y;
      start = null;
      if (Math.abs(dx) < SWIPE_DISTANCE || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      // The arrow the swipe started on must not also turn.
      suppressClickUntil = performance.now() + 400;
      turn(dx < 0 ? 1 : -1);
    }, {passive:true});
    bar.addEventListener('touchcancel', () => { start = null; }, {passive:true});
  }

  // The sheet follows a finger on its handle: down to close, up to show
  // more of a long tooltip. It settles on the closest of closed, normal and
  // tall, judged by where the drag was heading when the finger lifted.
  const handle = document.getElementById('recipeInspectHandle');
  // Called while dragging, so max-height isn't mid-transition when read.
  function heights() {
    const tall = sheet.classList.contains('recipe-inspect-tall');
    sheet.style.maxHeight = '';
    sheet.classList.remove('recipe-inspect-tall');
    const normal = sheet.offsetHeight;
    sheet.classList.add('recipe-inspect-tall');
    const full = sheet.offsetHeight;
    sheet.classList.toggle('recipe-inspect-tall', tall);
    return {normal, full, start:tall ? full : normal};
  }
  handle.addEventListener('touchstart', event => {
    if (event.touches.length !== 1 || sheet.hidden || hideTimer) { drag = null; return; }
    clearTimeout(settleTimer);
    const y = event.touches[0].clientY;
    // Hold it where it is, mid-settle or not, before measuring.
    const shown = sheet.offsetHeight;
    sheet.classList.add('recipe-inspect-dragging');
    drag = {...heights(), y, moved:false, samples:[{y, time:event.timeStamp}]};
    sheet.style.maxHeight = `${shown}px`;
    drag.start = shown;
  }, {passive:true});
  handle.addEventListener('touchmove', event => {
    if (!drag || event.touches.length !== 1) return;
    event.preventDefault();
    const y = event.touches[0].clientY;
    drag.moved ||= Math.abs(y - drag.y) > 4;
    drag.samples = [...drag.samples, {y, time:event.timeStamp}].slice(-4);
    const height = drag.start - (y - drag.y);
    // Past the tall height it gives a little, then stops.
    const over = Math.max(0, height - drag.full);
    sheet.style.maxHeight = `${Math.max(drag.normal, Math.min(height, drag.full) + Math.min(24, over / 4))}px`;
    sheet.style.transform = height < drag.normal ? `translateY(${drag.normal - height}px)` : '';
  }, {passive:false});
  function release(event) {
    if (!drag) return;
    const current = drag;
    drag = null;
    sheet.classList.remove('recipe-inspect-dragging');
    if (!current.moved) { sheet.style.maxHeight = ''; sheet.style.transform = ''; return; }
    suppressClickUntil = performance.now() + 400;
    const y = event.changedTouches?.[0]?.clientY ?? current.samples.at(-1).y;
    const first = current.samples[0], last = current.samples.at(-1);
    const velocity = last.time > first.time ? (last.y - first.y) / (last.time - first.time) : 0;
    // Where the sheet would come to rest if it kept going for a moment.
    const projected = current.start - (y - current.y) - velocity * 150;
    // Below its normal height, a third of it (100px at most) closes it;
    // above, it settles on whichever height is closer.
    const target = projected < current.normal
      ? (current.normal - projected > Math.min(100, current.normal / 3) ? 0 : current.normal)
      : current.full > current.normal + 24 && projected - current.normal > (current.full - current.normal) / 2
        ? current.full : current.normal;
    if (target === 0) {
      sheet.style.transform = '';
      close();
      return;
    }
    const tall = target === current.full && target !== current.normal;
    sheet.style.transform = '';
    sheet.style.maxHeight = `${target}px`;
    sheet.classList.toggle('recipe-inspect-tall', tall);
    settleTimer = setTimeout(() => { sheet.style.maxHeight = ''; updateFade(); }, SHEET_MS);
  }
  handle.addEventListener('touchend', release, {passive:true});
  handle.addEventListener('touchcancel', release, {passive:true});

  mode.addEventListener('change', reset);
  return {reset, refresh, isMobile:() => mode.matches, isSelected:cell => !!selected && cell.contains(selected),
    expanded:() => !sheet.hidden && more};
}
