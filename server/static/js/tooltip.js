// Instant cursor tooltip shared by item grids (the network browser and
// the craft ingredients modal).
//
// Deliberately not the native `title` attribute - that has a real,
// noticeable OS-level hover delay before it appears, which is exactly
// what a "like in-game" browse experience shouldn't have. This shows
// immediately on mouseover instead, positioned at the cursor.
let tooltipEl = null;
// Last cursor position over a bound cell, so a grid that re-renders
// under a still cursor can put the tooltip back in the same place.
let lastX = 0;
let lastY = 0;

function ensureTooltip() {
  if (!tooltipEl) {
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'network-tooltip';
    tooltipEl.style.display = 'none';
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}

export function tooltipVisible() {
  return !!tooltipEl && tooltipEl.style.display !== 'none';
}

export function hideTooltip() {
  if (tooltipEl) tooltipEl.style.display = 'none';
}

function positionTooltip(x, y) {
  const tip = tooltipEl;
  const offset = 16;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const rect = tip.getBoundingClientRect();
  let left = x + offset;
  let top = y + offset;
  // Clamp to the viewport so it can't run off-screen near an edge -
  // flip to the other side of the cursor instead of clipping.
  if (left + rect.width > vw - 8) left = x - rect.width - offset;
  if (top + rect.height > vh - 8) top = y - rect.height - offset;
  tip.style.left = Math.max(4, left) + 'px';
  tip.style.top = Math.max(4, top) + 'px';
}

// Shows html at (x, y), or at the last known cursor position if those
// are left out.
export function showTooltip(html, x = lastX, y = lastY) {
  const tip = ensureTooltip();
  tip.innerHTML = html;
  tip.style.display = 'block';
  positionTooltip(x, y);
}

// Event delegation on the (stable) container, not per-cell listeners -
// grids replace their inner content wholesale on every render, so
// per-cell listeners would just be discarded each time anyway.
// htmlForCell returns the tooltip markup, or null for no tooltip.
export function bindCellTooltip(container, cellSelector, htmlForCell) {
  container.addEventListener('mouseover', (e) => {
    const cell = e.target.closest(cellSelector);
    if (!cell) return;
    lastX = e.clientX;
    lastY = e.clientY;
    const html = htmlForCell(cell);
    if (html) showTooltip(html, lastX, lastY); else hideTooltip();
  });
  container.addEventListener('mousemove', (e) => {
    const cell = e.target.closest(cellSelector);
    if (!cell) return;
    lastX = e.clientX;
    lastY = e.clientY;
    if (tooltipVisible()) positionTooltip(lastX, lastY);
  });
  container.addEventListener('mouseout', (e) => {
    const cell = e.target.closest(cellSelector);
    if (!cell) return;
    // Only actually hide if the cursor left the cell itself, not just
    // moved onto a child element (the icon/qty span) within it.
    if (cell.contains(e.relatedTarget)) return;
    hideTooltip();
  });
}
