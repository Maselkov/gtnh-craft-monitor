// Recipe URLs and trail operations, independent of dialogs and fetches.
export function recipeCatalogCapacity(width, height, reserved = 0, gap = 4, minimum = 36) {
  const columns = Math.max(1, Math.floor((width + gap) / (minimum + gap)));
  const cell = (width - (columns - 1) * gap) / columns;
  const rows = Math.max(1, Math.floor((height - reserved + gap) / (cell + gap)));
  return Math.min(1024, columns * rows);
}

export function parseRecipeRecents(raw) {
  try {
    const value = JSON.parse(raw);
    if (typeof value?.version !== 'string' || !Array.isArray(value.keys)) throw new Error();
    return {version:value.version.slice(0,64), keys:[...new Set(value.keys.filter(key =>
      typeof key === 'string' && key.length <= 1024 && !/\s/.test(key)
      && /^(?:fluid:.+|[^:]+:.+:(?:\d+|\*))$/.test(key)))].slice(0,8)};
  } catch { return {version:'', keys:[]}; }
}

export function moveRecipePage(index, delta, total, wrap = false) {
  if (!total) return 0;
  const target = index + delta;
  return wrap ? ((target % total) + total) % total : Math.max(0, Math.min(total - 1, target));
}

export function recipeItemKey(item) {
  if (!item || item.kind === 'essentia') return null;
  // Crafting-plan entries retain the ME storage key instead of repeating
  // its identity fields: mod|internal|damage|kind[|NBT variant].
  if (!item.internal && typeof item.key === 'string') {
    const [mod, internal, damage, kind] = item.key.split('|');
    if (!internal || !['item', 'fluid'].includes(kind) || !/^\d*$/.test(damage)) return null;
    return recipeItemKey({ mod, internal, damage: damage === '' ? 0 : Number(damage), kind });
  }
  if (!item.internal) return null;
  if (item.kind === 'fluid' || !item.mod) return `fluid:${item.internal}`;
  return `${item.mod}:${item.internal}:${item.damage == null ? '*' : item.damage}`;
}

export function recipeUrl(frame) {
  const query = new URLSearchParams({ key: frame.key });
  if (frame.dir === 'using') query.set('dir', 'using');
  if (frame.map) query.set('map', frame.map);
  return '/recipes?' + query;
}

export function recipeFromUrl(url) {
  if (url.pathname !== '/recipes' || !url.searchParams.get('key')) return null;
  return { key: url.searchParams.get('key'), dir: url.searchParams.get('dir') === 'using' ? 'using' : 'making',
    map: url.searchParams.get('map') || '' };
}

export function trailDistance(stack, index) {
  return Number.isInteger(index) && index >= 0 && index < stack.length ? index - stack.length + 1 : 0;
}

export function sameRecipeDataset(a, b) {
  return !!a && !!b && !!a.fingerprint && !!a.version && a.version === b.version && a.fingerprint === b.fingerprint && a.assets === b.assets;
}
