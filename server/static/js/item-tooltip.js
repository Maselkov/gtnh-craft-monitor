// Shared item tooltip content; each view supplies its own quantity/context.
import { descriptionHtml, nameHtml, nameText } from './description.js';
import { escapeHtml, kindTooltipHtml } from './util.js';
import { amountInSystem, craftableInSystem, ensureNetworkStock } from './network-stock.js';

// Game data for items outside the network list (a craft's ingredients, a
// plan's rows), fetched by item key on first hover and kept for the page.
const infoCache = new Map();  // item key -> info, or a Promise while it loads
let infoWaiters = [];

export function itemInfoKey(item) {
  const kind = item.kind || (item.mod ? 'item' : 'fluid');
  const damage = kind === 'fluid' ? '' : (item.damage ?? 0);
  const key = `${item.mod || ''}|${item.internal || ''}|${damage}|${kind}`;
  return item.variant ? `${key}|${item.variant}` : key;
}

// The item's info if it's here; otherwise null, fetching it and calling
// onLoad once it arrives (to redraw the tooltip).
export function cachedItemInfo(item, onLoad) {
  if (!item.internal) return {};
  const key = itemInfoKey(item);
  const got = infoCache.get(key);
  if (got && !(got instanceof Promise)) return got;
  if (onLoad) infoWaiters.push(onLoad);
  if (!got) {
    infoCache.set(key, fetch('/api/network/item-info?key=' + encodeURIComponent(key))
      .then(res => res.ok ? res.json() : Promise.reject(new Error(`item-info: ${res.status}`)))
      .then(data => { infoCache.set(key, data.info || {}); }, () => { infoCache.delete(key); })
      .then(() => { const waiters = infoWaiters; infoWaiters = []; waiters.forEach(fn => fn()); }));
  }
  return null;
}

// Shift held: tooltips show their Shift text, as in the game. For views
// that don't track it themselves; onChange redraws their tooltip.
let shiftHeld = false;
const shiftListeners = new Set();
export function shiftIsHeld() { return shiftHeld; }
export function onShiftChange(fn) {
  if (!shiftListeners.size) {
    const set = held => { if (held !== shiftHeld) { shiftHeld = held; shiftListeners.forEach(f => f()); } };
    window.addEventListener('keydown', e => { if (e.key === 'Shift') set(true); });
    window.addEventListener('keyup', e => { if (e.key === 'Shift') set(false); });
    window.addEventListener('blur', () => set(false));
  }
  shiftListeners.add(fn);
}

// An item as the tooltip wants it, from an item key
// (mod|internal|damage|kind|variant) and whatever else is known of it.
export function itemOfKey(key, fields = {}) {
  const [mod, internal, damage, kind, variant] = String(key || '').split('|');
  return { ...fields, mod: mod || null, internal: internal || null,
    damage: damage === '' || damage == null ? null : Number(damage),
    kind: fields.kind || kind || 'item', variant: variant || null };
}

// The full game-style tooltip for an item that may not be in the
// network: its info fetched on demand (onLoad redraws once it's here),
// Craftable from the latest network report. stat/details as below.
export function lookupItemTooltipHtml(item, options = {}, onLoad = null) {
  const info = cachedItemInfo(item, onLoad) || {};
  return itemTooltipHtml({...item, isCraftable:craftableInSystem(item)}, info,
    {shiftHeld, aggregateStockVariants:true, modName:info.mod_name, ...options});
}

export function systemStockHtml(item, {amount, aggregateVariants = false} = {}) {
  if (amount === undefined) ensureNetworkStock();
  const quantity = amount === undefined ? amountInSystem(item, {aggregateVariants}) : amount;
  if (quantity == null || !Number.isFinite(Number(quantity))) return '';
  const text = Number(quantity).toLocaleString('en-US') + (item.kind === 'fluid' ? ' mB' : '');
  return `<div class="network-tooltip-stat network-tooltip-stock">Amount in system: <span class="mc-6">${escapeHtml(text)}</span></div>`;
}

export function itemTooltipHtml(item, info = {}, options = {}) {
  const description = (options.shiftHeld && info.shift_description) || info.description;
  const rarity = typeof info.rarity === 'string' && /^[0-9a-f]$/i.test(info.rarity) ? ` mc-${info.rarity.toLowerCase()}` : '';
  const mod = options.modName || info.mod_name || item.mod;
  return `<div class="network-tooltip-name${rarity}">${nameHtml(item.name || '?')}</div>
    ${item.variant_name ? `<div class="network-tooltip-variant">${escapeHtml(nameText(item.variant_name))}</div>` : ''}
    ${info.shift_description && !options.shiftHeld && !options.hideShiftHint ? '<div class="network-tooltip-hint">[shift]</div>' : ''}
    ${options.stat ? `<div class="network-tooltip-stat">${escapeHtml(options.stat)}</div>` : ''}
    ${options.details || ''}
    ${kindTooltipHtml(item.kind)}
    ${item.isCraftable ? '<div class="network-tooltip-craftable">Craftable</div>' : ''}
    ${description ? `<div class="game-text network-tooltip-description">${descriptionHtml(description)}</div>` : ''}
    ${systemStockHtml(item, {amount:options.stockAmount, aggregateVariants:options.aggregateStockVariants})}
    ${mod ? `<div class="network-tooltip-mod">${escapeHtml(mod)}</div>` : ''}`;
}
