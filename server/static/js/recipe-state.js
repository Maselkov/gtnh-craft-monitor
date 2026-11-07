// Recipe URLs and trail operations, independent of dialogs and fetches.
export function recipeItemKey(item) {
  if (!item || item.kind === 'essentia' || !item.internal) return null;
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
