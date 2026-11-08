// Shared item tooltip content; each view supplies its own quantity/context.
import { descriptionHtml, nameHtml, nameText } from './description.js';
import { escapeHtml, kindTooltipHtml } from './util.js';

export function itemTooltipHtml(item, info = {}, options = {}) {
  const description = (options.shiftHeld && info.shift_description) || info.description;
  const rarity = typeof info.rarity === 'string' && /^[0-9a-f]$/i.test(info.rarity) ? ` mc-${info.rarity.toLowerCase()}` : '';
  const mod = options.modName || info.mod_name || item.mod;
  return `<div class="network-tooltip-name${rarity}">${nameHtml(item.name || '?')}</div>
    ${item.variant_name ? `<div class="network-tooltip-variant">${escapeHtml(nameText(item.variant_name))}</div>` : ''}
    ${info.shift_description && !options.shiftHeld ? '<div class="network-tooltip-hint">[shift]</div>' : ''}
    ${options.stat ? `<div class="network-tooltip-stat">${escapeHtml(options.stat)}</div>` : ''}
    ${options.details || ''}
    ${kindTooltipHtml(item.kind)}
    ${item.isCraftable ? '<div class="network-tooltip-craftable">Craftable</div>' : ''}
    ${description ? `<div class="game-text network-tooltip-description">${descriptionHtml(description)}</div>` : ''}
    ${mod ? `<div class="network-tooltip-mod">${escapeHtml(mod)}</div>` : ''}`;
}
