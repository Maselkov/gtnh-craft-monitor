// Shared recipe rendering. Pattern checks use static stacks; the viewer
// opts into navigation, stock links and grid slots. No setup on import.
import { escapeHtml, iconClass, iconUrl } from './util.js';

export function amountText(e) {
  if (e.size == null) return '?';
  const n = Number(e.size).toLocaleString('en-US');
  if (e.kind === 'fluid') return `${n} mB`;
  if (e.size === 0) return 'not used up';
  return `${n}x`;
}

export function stackHtml(e, options = {}) {
  const name = e.name || '?';
  const chance = e.chance ? ` <span class="recipe-chance">${escapeHtml(String(e.chance))}%</span>` : '';
  const icon = e.icon
    ? `<img class="${iconClass('recipe-stack-icon', e.icon)}" src="${iconUrl(e.icon)}" alt="" loading="lazy" data-remove-on-error>`
    : options.interactive ? `<span class="recipe-stack-placeholder" aria-hidden="true">${escapeHtml(name.charAt(0))}</span>` : '';
  const content = `${icon}<span class="recipe-stack-amount">${escapeHtml(amountText(e))}</span>`
    + `<span class="recipe-stack-name">${escapeHtml(name)}</span>${chance}`;
  if (!options.interactive || !e.key) return `<span class="recipe-stack" title="${escapeHtml(name)}">${content}</span>`;
  const key = escapeHtml(e.key);
  const stock = options.hasStock && options.hasStock(e)
    ? `<button class="recipe-stock-link" data-action="recipe-stock" data-key="${key}" aria-label="Open stock page for ${escapeHtml(name)}">Stock</button>` : '';
  return `<span class="recipe-stack-wrap${options.compact ? ' recipe-stack-compact' : ''}">
    <button class="recipe-stack recipe-stack-control" data-action="recipe-open" data-key="${key}" title="${escapeHtml(name)}${e.wildcard ? ' (any damage)' : ''}" aria-label="Recipes for ${escapeHtml(name)}">${content}</button>
    <span class="recipe-stack-links"><button data-action="recipe-open" data-dir="using" data-key="${key}" aria-label="Uses for ${escapeHtml(name)}">Uses</button>${stock}</span>
  </span>`;
}

export function machineText(step) {
  const [first, ...rest] = step.machines || [];
  if (!first) return step.map;
  const name = typeof first === 'string' ? first : first.name;
  return rest.length ? `${name} (or ${rest.length} more)` : name;
}

export function gridHtml(step, options = {}) {
  const grid = step.grid;
  const slots = grid.slots.map((slot, index) => {
    if (!slot) return `<span class="recipe-grid-hole" data-id="slot-${index}" aria-hidden="true"></span>`;
    const count = slot.alternatives_total;
    const note = [slot.ore, count > 1 ? `${count} alternatives${count > slot.alternatives.length ? ` (showing ${slot.alternatives.length})` : ''}` : ''].filter(Boolean).join(' · ');
    return `<span class="recipe-choice" data-id="slot-${index}" data-choice="${step.id}:${index}" title="${escapeHtml(note)}">${stackHtml(slot.alternatives[0], { ...options, compact: true })}</span>`;
  });
  return `<div class="recipe-grid-group"><div class="recipe-grid recipe-grid-w${grid.w}">${slots.join('')}</div>
    <div class="recipe-grid-note">${grid.shapeless ? 'Shapeless · any order' : `${grid.w} × ${grid.h}${grid.mirrored ? ' · may be mirrored' : ''}`}</div></div>`;
}

export function stepHtml(step, options = {}) {
  const details = [step.tier, options.interactive && step.eut != null ? `${Number(step.eut).toLocaleString('en-US')} EU/t` : '',
    options.interactive && step.ticks != null ? `${step.ticks / 20} s` : '',
    step.heat && step.special ? `${Number(step.special).toLocaleString('en-US')} K` : '',
    options.interactive && !step.heat && step.special ? `Special: ${step.special}` : ''].filter(Boolean).join(' · ');
  const machine = options.interactive
    ? `<button class="recipe-category-link" data-action="recipe-filter" data-map="${escapeHtml(step.map)}">${escapeHtml(machineText(step))}</button>`
    : escapeHtml(machineText(step));
  const inputs = step.grid ? gridHtml(step, options) : `<div class="recipe-side">${step.inputs.map(e => stackHtml(e, options)).join('')}</div>`;
  return `<div class="recipe-step">
    <div class="recipe-step-machine">${machine}<span class="recipe-step-details">${escapeHtml(details)}</span></div>
    <div class="recipe-step-sides">${inputs}<div class="recipe-arrow" aria-hidden="true">&rarr;</div>
      <div class="recipe-side recipe-outputs">${step.outputs.map(e => stackHtml(e, { ...options, compact: !!step.grid })).join('')}</div>
    </div>
  </div>`;
}
