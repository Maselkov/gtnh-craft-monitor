// Paint NEI's fixed palette as one image, like the recipe cards themselves.
// DOM controls retain their labels, focus rings and normal event handling.
export function setupRecipeShell(book) {
  const canvas = book.querySelector('.recipe-book-canvas');
  let pending = false;

  function paint() {
    pending = false;
    if (!book.offsetWidth || !document.fonts.check('16px "GCM Recipe"')) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const origin = book.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(origin.width * dpr);
    canvas.height = Math.round(origin.height * dpr);
    ctx.scale(dpr, dpr);
    const style = getComputedStyle(book);
    const colour = name => style.getPropertyValue(`--recipe-${name}`).trim();
    const rect = element => {
      const r = element.getBoundingClientRect();
      return {x:Math.round(r.x-origin.x), y:Math.round(r.y-origin.y), w:Math.round(r.width), h:Math.round(r.height)};
    };
    function fill(r, ink) { ctx.fillStyle = ink; ctx.fillRect(r.x, r.y, r.w, r.h); }
    function inset(r, amount) { return {x:r.x+amount, y:r.y+amount, w:r.w-2*amount, h:r.h-2*amount}; }
    function bevel(r, depth, face, raised = true, corners = true) {
      const light = colour(raised ? 'frame-light' : 'frame-mid');
      const shadow = colour(raised ? 'frame-mid' : 'frame-light');
      const cut = corners ? Math.max(1, Math.round(depth/2)) : 0;
      ctx.beginPath();
      ctx.moveTo(r.x+cut, r.y); ctx.lineTo(r.x+r.w-cut, r.y);
      ctx.lineTo(r.x+r.w, r.y+cut); ctx.lineTo(r.x+r.w, r.y+r.h-cut);
      ctx.lineTo(r.x+r.w-cut, r.y+r.h); ctx.lineTo(r.x+cut, r.y+r.h);
      ctx.lineTo(r.x, r.y+r.h-cut); ctx.lineTo(r.x, r.y+cut); ctx.closePath();
      ctx.save(); ctx.clip();
      fill(r, colour('frame-outline'));
      const edge = inset(r, 1);
      fill(edge, shadow);
      ctx.fillStyle = light;
      ctx.beginPath(); ctx.moveTo(edge.x,edge.y); ctx.lineTo(edge.x+edge.w,edge.y);
      ctx.lineTo(edge.x+edge.w-depth,edge.y+depth); ctx.lineTo(edge.x+depth,edge.y+depth);
      ctx.lineTo(edge.x+depth,edge.y+edge.h-depth); ctx.lineTo(edge.x,edge.y+edge.h); ctx.fill();
      fill(inset(r, depth), face);
      // A second pale rim gives the broad NEI frame its stepped edge.
      if (depth >= 5) {
        const inner = inset(r, depth-2);
        ctx.strokeStyle = colour('frame-inner'); ctx.lineWidth = 2;
        ctx.strokeRect(inner.x+1, inner.y+1, inner.w-2, inner.h-2);
      }
      ctx.restore();
    }
    function label(element, ink, shadow = false, boundsElement = element) {
      const r = rect(boundsElement), css = getComputedStyle(element);
      const text = element.textContent.trim();
      const size = parseFloat(css.fontSize);
      ctx.font = `${size}px "GCM Recipe", monospace`;
      // Measure first, reducing only unusually long category names.
      const available = r.w-6;
      if (ctx.measureText(text).width > available) {
        ctx.font = `${Math.max(8, Math.floor(size*available/ctx.measureText(text).width))}px "GCM Recipe", monospace`;
      }
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      if (shadow) {
        ctx.fillStyle = colour('frame-shadow'); ctx.fillText(text,r.x+r.w/2+2,r.y+r.h/2+2);
      }
      ctx.fillStyle = ink; ctx.fillText(text,r.x+r.w/2,r.y+r.h/2);
    }

    const paper = rect(book.querySelector('.recipe-book-paper'));
    bevel(paper, 8, colour('canvas'));
    for (const heading of book.querySelectorAll('.recipe-book-heading')) fill(rect(heading), colour('heading'));

    const sidebar = book.querySelector('.recipe-machine-sidebar');
    if (sidebar) {
      const rail = rect(sidebar);
      bevel(rail, 7, colour('canvas'));
      // Open the rail into the paper instead of drawing two stacked borders.
      fill({x:paper.x, y:rail.y+7, w:8, h:rail.h-14}, colour('canvas'));
      bevel(inset(rail, 8), 2, colour('slot'), false, false);
      const grid = book.querySelector('.recipe-machine-grid'), bounds = rect(grid);
      ctx.save(); ctx.beginPath(); ctx.rect(bounds.x,bounds.y,bounds.w,bounds.h); ctx.clip();
      for (const machine of grid.querySelectorAll('.recipe-machine-control:not(:has(img))')) {
        const fallback = machine.querySelector('.recipe-machine-fallback');
        if (fallback) label(fallback, colour('canvas-ink'), false, machine);
      }
      ctx.restore();
    }

    const strip = book.querySelector('.recipe-map-tabs');
    const bounds = rect(strip);
    ctx.save(); ctx.beginPath(); ctx.rect(bounds.x, bounds.y, bounds.w, bounds.h); ctx.clip();
    for (const tab of strip.querySelectorAll('.recipe-map-tab')) {
      const r = rect(tab), active = tab.getAttribute('aria-selected') === 'true';
      bevel(r, 6, colour('canvas'));
      if (active) fill({x:r.x+6, y:paper.y, w:r.w-12, h:8}, colour('canvas'));
      const fallback = tab.querySelector('.recipe-map-fallback');
      if (!tab.querySelector('img') && fallback) label(fallback, colour('canvas-ink'), false, tab);
    }
    ctx.restore();
    const selected = strip.querySelector('[aria-selected="true"]');
    if (selected) {
      const r = rect(selected), left = Math.max(bounds.x, r.x+6), right = Math.min(bounds.x+bounds.w, r.x+r.w-6);
      if (right > left) fill({x:left, y:paper.y, w:right-left, h:8}, colour('canvas'));
    }

    for (const button of book.querySelectorAll('.recipe-game-button')) {
      if (!button.offsetWidth) continue;
      const r = rect(button);
      bevel(r, 4, colour('control'), true, false);
      label(button, colour(button.disabled ? 'control-disabled' : 'count-text'));
    }
    for (const heading of book.querySelectorAll('.recipe-book-heading > span')) label(heading, colour('count-text'), true);
    book.classList.add('recipe-shell-painted');
  }

  function schedule() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(paint);
  }
  const resize = new ResizeObserver(schedule);
  resize.observe(book);
  const changes = new MutationObserver(schedule);
  changes.observe(book, {subtree:true, childList:true, characterData:true, attributes:true,
    attributeFilter:['disabled', 'hidden', 'aria-selected']});
  book.addEventListener('scroll', schedule, {capture:true, passive:true});
  document.fonts.addEventListener('loadingdone', schedule);
  return schedule;
}
