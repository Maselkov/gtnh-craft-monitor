// Read-only tooltip quantities from the latest network report. Kept separate
// from the Network UI so every item view can use it without import cycles.
let snapshot = null;
let requestSnapshot = null, pending = null, lastRequest = -Infinity;

export function setNetworkStock(data) { snapshot = data; lastRequest = data ? Date.now() : -Infinity; }
export function setupNetworkStock(request) { requestSnapshot = request; }

export function ensureNetworkStock() {
  if (!requestSnapshot || pending || Date.now()-lastRequest < 60000) return pending;
  lastRequest = Date.now();
  pending = Promise.resolve().then(requestSnapshot).catch(() => {}).finally(() => { pending = null; });
  return pending;
}

export function amountInSystem(item, {aggregateVariants = false} = {}) {
  if (snapshot?.updated_at == null) return null;
  if (!item.internal && typeof item.key === 'string' && item.key.includes('|')) {
    const [mod,internal,damage,kind,variant] = item.key.split('|');
    item = {...item,mod:mod || null,internal,damage:damage ? Number(damage) : 0,kind,variant:variant || null};
  }
  if (!item.internal) return null; // Legacy name-only craft reports.
  const kind = item.kind || (item.mod ? 'item' : 'fluid');
  let amount = 0;
  for (const stock of snapshot.items || []) {
    if ((stock.kind || 'item') !== kind || (stock.mod || null) !== (item.mod || null)
        || stock.internal !== item.internal) continue;
    if (kind === 'item' && !item.wildcard && (stock.damage ?? 0) !== (item.damage ?? 0)) continue;
    if ((!aggregateVariants || item.variant) && (stock.variant || null) !== (item.variant || null)) continue;
    amount += Number(stock.size) || 0;
  }
  return amount;
}

// Whether AE2 has a pattern for the item (any of its NBT variants), as
// the latest network report says; false before there is one.
export function craftableInSystem(item) {
  const kind = item.kind || (item.mod ? 'item' : 'fluid');
  return !!item.internal && (snapshot?.items || []).some(stock => stock.isCraftable
    && (stock.kind || 'item') === kind && (stock.mod || null) === (item.mod || null)
    && stock.internal === item.internal && (kind !== 'item' || (stock.damage ?? 0) === (item.damage ?? 0)));
}
