// Category tabs use the viewer's existing filter/history state.
import { nameHtml, nameText } from './description.js';
import { bindCellTooltip, hideTooltip, showTooltip, tooltipVisible } from './tooltip.js';
import { escapeHtml, iconClass, iconUrl, updateHtml } from './util.js';

const ALL_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3h7v7H3zm11 0h7v7h-7zM3 14h7v7H3zm11 0h7v7h-7z" fill="currentColor"/></svg>';
const tabId = id => `recipe-category-${id ? 'map-' + encodeURIComponent(id) : 'all'}`;
const total = counts => Object.values(counts).reduce((sum, n) => sum + n, 0);
const countText = n => `${n.toLocaleString('en-US')} ${n === 1 ? 'recipe' : 'recipes'}`;

export function categoryTabsHtml(maps, counts, selected) {
  return [{ id: '', name: 'All categories', machines: [] }, ...maps].map(map => {
    const count = map.id ? counts[map.id] : total(counts);
    const icon = map.machines.find(machine => machine.icon)?.icon;
    const fallback = nameText(map.name).split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase();
    const active = map.id === selected;
    const badge = count >= 1000 ? `${+(count / 1000).toFixed(1)}k` : String(count);
    return `<button id="${escapeHtml(tabId(map.id))}" class="recipe-map-tab" role="tab" data-id="map-${escapeHtml(map.id)}" data-action="recipe-filter" data-map="${escapeHtml(map.id)}" aria-controls="recipeViewerList" aria-selected="${active}" tabindex="${active ? 0 : -1}" aria-label="${escapeHtml(map.name)}, ${countText(count)}">
      ${map.id ? `<span class="recipe-map-fallback" aria-hidden="true">${escapeHtml(fallback)}</span>${icon ? `<img class="${iconClass('recipe-map-icon', icon)}" src="${iconUrl(icon)}" alt="" data-remove-on-error>` : ''}` : ALL_ICON}
      <span class="recipe-map-count" aria-hidden="true">${badge}</span>
    </button>`;
  }).join('');
}

export function categoryTooltipHtml(map, count) {
  return `<div class="network-tooltip-name">${nameHtml(map.name)}</div>
    <div class="network-tooltip-stat">${countText(count)}</div>
    ${map.mod_name ? `<div class="network-tooltip-mod">${escapeHtml(map.mod_name)}</div>` : ''}`;
}

export function setupRecipeTabs() {
  const nav = document.getElementById('recipeCategoryNav');
  const tabs = document.getElementById('recipeViewerMaps');
  const previous = document.getElementById('recipeTabsPrevious');
  const next = document.getElementById('recipeTabsNext');
  let categories = new Map();
  let selection = null;

  function updateOverflow() {
    const max = tabs.scrollWidth - tabs.clientWidth;
    previous.hidden = next.hidden = max <= 1;
    previous.disabled = tabs.scrollLeft <= 1;
    next.disabled = tabs.scrollLeft >= max - 1;
  }

  function reveal(tab) {
    if (!tab) return;
    const bounds = tabs.getBoundingClientRect();
    const rect = tab.getBoundingClientRect();
    if (rect.left < bounds.left) tabs.scrollLeft -= bounds.left - rect.left;
    else if (rect.right > bounds.right) tabs.scrollLeft += rect.right - bounds.right;
  }

  function tooltip(tab) {
    const category = categories.get(tab.dataset.map);
    return category ? categoryTooltipHtml(category.map, category.count) : null;
  }
  function focusTooltip(tab) {
    const rect = tab.getBoundingClientRect();
    showTooltip(tooltip(tab), rect.right - 8, rect.bottom - 8, tabs);
  }
  bindCellTooltip(tabs, '[role="tab"]', tooltip);
  tabs.addEventListener('focusin', event => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab) return;
    for (const other of tabs.querySelectorAll('[role="tab"]')) other.tabIndex = other === tab ? 0 : -1;
    reveal(tab);
    if (tab.matches(':focus-visible')) focusTooltip(tab);
  });
  tabs.addEventListener('focusout', () => { if (tooltipVisible(tabs)) hideTooltip(); });
  tabs.addEventListener('keydown', event => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab || event.altKey || event.ctrlKey || event.metaKey) return;
    const buttons = [...tabs.querySelectorAll('[role="tab"]')];
    const index = buttons.indexOf(tab);
    const direction = getComputedStyle(tabs).direction === 'rtl' ? -1 : 1;
    const step = event.key === 'ArrowRight' ? direction : event.key === 'ArrowLeft' ? -direction : 0;
    const target = step ? (index + step + buttons.length) % buttons.length
      : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
    if (target < 0) return; // Enter/Space keep the button's native activation.
    event.preventDefault(); event.stopPropagation();
    buttons[target].focus({ preventScroll: true });
  });
  tabs.addEventListener('scroll', () => {
    updateOverflow();
    const focused = tabs.querySelector('[role="tab"]:focus-visible');
    if (focused) focusTooltip(focused);
    else if (tooltipVisible(tabs)) hideTooltip();
  }, { passive: true });
  const observer = new ResizeObserver(updateOverflow);
  observer.observe(tabs);

  return {
    render(maps, counts, selected, frameId) {
      nav.hidden = maps === null;
      if (maps === null) { categories.clear(); updateHtml(tabs, ''); selection = null; return; }
      categories = new Map(maps.map(map => [map.id, { map, count: counts[map.id] }]));
      categories.set('', { map: { name: 'All categories' }, count: total(counts) });
      const focused = tabs.contains(document.activeElement) ? document.activeElement : null;
      updateHtml(tabs, categoryTabsHtml(maps, counts, selected));
      if (focused?.isConnected) {
        for (const tab of tabs.querySelectorAll('[role="tab"]')) tab.tabIndex = tab === focused ? 0 : -1;
      }
      const active = categories.get(selected) || categories.get('');
      document.getElementById('recipeCategoryName').textContent = nameText(active.map.name);
      document.getElementById('recipeCategoryCount').textContent = countText(active.count);
      document.getElementById('recipeViewerList').setAttribute('aria-labelledby', tabId(selected));
      const nextSelection = JSON.stringify([frameId, selected, maps.map(map => map.id)]);
      const changed = nextSelection !== selection;
      selection = nextSelection;
      requestAnimationFrame(() => {
        // Showing the overflow buttons changes the viewport width. Reveal
        // the active tab against that final width, not the wider old row.
        updateOverflow();
        if (changed) reveal(tabs.querySelector('[aria-selected="true"]'));
        updateOverflow();
      });
    },
    scroll(direction) {
      tabs.scrollBy({ left: direction * Math.max(60, tabs.clientWidth * .75),
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    },
  };
}
