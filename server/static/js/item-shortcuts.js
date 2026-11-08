// Item identity is supplied by the view, so shortcuts never guess from a name
// or reuse a tooltip's stale snapshot after a live grid redraw.
import { topDialog } from './dialogs.js';
import { recipeItemKey } from './recipe-state.js';

const bindings = [];

export function bindItemRecipeTarget(container, selector, itemForCell) {
  bindings.push({ container, selector, itemForCell });
}

function visible(cell, scope) {
  return scope.contains(cell) && !cell.closest('[hidden], [inert]') && cell.getClientRects().length > 0;
}

function moreSpecific(found, cell, binding) {
  return !found || (found.cell !== cell && found.cell.contains(cell))
    || (found.cell === cell && found.binding.container.contains(binding.container));
}

function target(scope, focused = null) {
  let found = null;
  for (const binding of bindings) {
    const { container, selector } = binding;
    const cells = focused ? [focused.closest?.(selector)].filter(Boolean)
      : container.querySelectorAll(`:is(${selector}):hover`);
    for (const cell of cells) {
      if (!container.contains(cell) || !visible(cell, scope)) continue;
      if (moreSpecific(found, cell, binding)) found = { cell, binding };
    }
  }
  return found && found.binding.itemForCell(found.cell);
}

export function recipeShortcutDirection(event) {
  if (event.defaultPrevented || event.repeat || event.isComposing
      || event.ctrlKey || event.metaKey || event.altKey
      || event.target?.isContentEditable
      || event.target?.closest?.('input, textarea, select, [contenteditable]')) return null;
  const key = event.key?.toLowerCase();
  return key === 'r' ? 'making' : key === 'u' ? 'using' : null;
}

export function setupItemRecipeShortcuts(openRecipes) {
  window.addEventListener('keydown', event => {
    const direction = recipeShortcutDirection(event);
    if (!direction) return;
    const scope = topDialog() || document;
    const item = target(scope) || target(scope, document.activeElement);
    const key = typeof item === 'string' ? item : recipeItemKey(item);
    if (!key) return; // Unknown identities and essentia have no recipe book.
    event.preventDefault();
    openRecipes(key, direction);
  });
}
