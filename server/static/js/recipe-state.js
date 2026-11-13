// Recipe URLs and trail operations, independent of dialogs and fetches.

// The book's icon grid: as many columns of at least `minimum` px as fit.
export function recipeCatalogGrid(width, gap = 4, minimum = 36) {
  const columns = Math.max(1, Math.floor((width + gap) / (minimum + gap)));
  return {columns, cell:(width - (columns - 1) * gap) / columns};
}

export function recipeCatalogCapacity(width, height, reserved = 0, gap = 4, minimum = 36) {
  const {columns, cell} = recipeCatalogGrid(width, gap, minimum);
  const rows = Math.max(1, Math.floor((height - reserved + gap) / (cell + gap)));
  return Math.min(1024, columns * rows);
}

// Enough to fill the recent strip's two rows at the book's widest.
export const RECIPE_RECENT_LIMIT = 24;

export function parseRecipeRecents(raw) {
  try {
    const value = JSON.parse(raw);
    if (typeof value?.version !== 'string' || !Array.isArray(value.keys)) throw new Error();
    return {version:value.version.slice(0,64), keys:[...new Set(value.keys.filter(key =>
      typeof key === 'string' && key.length <= 1024 && !/\s/.test(key)
      && /^(?:fluid:.+|[^:]+:.+:(?:\d+|\*)|[^:]+:.+:\d+#[A-Za-z0-9._-]{1,128})$/.test(key)))].slice(0,RECIPE_RECENT_LIMIT)};
  } catch { return {version:'', keys:[]}; }
}

// Which of NEI's item groups the book has open, for one game data version:
// all (every group open or shut, null for NEI's defaults), then single
// groups opened or shut since.
const RECIPE_GROUP_LIMIT = 256;
const EMPTY_GROUPS = {version:'', all:null, open:[], shut:[]};

export function parseRecipeGroups(raw) {
  try {
    const value = JSON.parse(raw);
    if (typeof value?.version !== 'string') throw new Error();
    const ids = list => Array.isArray(list) ? [...new Set(list.filter(id => Number.isInteger(id) && id >= 0))].slice(0,RECIPE_GROUP_LIMIT) : [];
    return {version:value.version.slice(0,64), all:typeof value.all === 'boolean' ? value.all : null,
      open:ids(value.open), shut:ids(value.shut)};
  } catch { return {...EMPTY_GROUPS}; }
}

export function setRecipeGroup(state, id, open) {
  const without = list => list.filter(other => other !== id);
  const next = {...state, open:without(state.open), shut:without(state.shut)};
  (open ? next.open : next.shut).push(id);
  // The oldest choices go first; a long session can't grow the request without end.
  for (const list of [next.open, next.shut]) list.splice(0, Math.max(0, list.length - RECIPE_GROUP_LIMIT/2));
  return next;
}

export function setAllRecipeGroups(state, open) {
  return {version:state.version, all:open, open:[], shut:[]};
}

// The search request's part for this state: groups=open|shut, open=, shut=.
export function recipeGroupQuery(state) {
  const query = [];
  if (state.all !== null) query.push(['groups', state.all ? 'open' : 'shut']);
  for (const id of state.open) query.push(['open', String(id)]);
  for (const id of state.shut) query.push(['shut', String(id)]);
  return query;
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
  const key = `${item.mod}:${item.internal}:${item.damage == null ? '*' : item.damage}`;
  // A bee, sapling... of one species: its own recipes (breeding), not every species'.
  return item.species && item.damage != null ? `${key}#${item.species}` : key;
}

export function recipeUrl(frame) {
  const query = new URLSearchParams({ key: frame.key });
  if (frame.dir === 'using') query.set('dir', 'using');
  if (frame.map) query.set('map', frame.map);
  // Pages count from 1 in the address, as the book shows them.
  if (frame.page > 0) query.set('page', String(frame.page + 1));
  return '/recipes?' + query;
}

export function recipeFromUrl(url) {
  if (url.pathname !== '/recipes' || !url.searchParams.get('key')) return null;
  const page = Number(url.searchParams.get('page'));
  return { key: url.searchParams.get('key'), dir: url.searchParams.get('dir') === 'using' ? 'using' : 'making',
    map: url.searchParams.get('map') || '', page: Number.isSafeInteger(page) && page > 1 ? page - 1 : 0 };
}

export function trailDistance(stack, index) {
  return Number.isInteger(index) && index >= 0 && index < stack.length ? index - stack.length + 1 : 0;
}

export function sameRecipeDataset(a, b) {
  return !!a && !!b && !!a.fingerprint && !!a.version && a.version === b.version && a.fingerprint === b.fingerprint && a.assets === b.assets;
}
