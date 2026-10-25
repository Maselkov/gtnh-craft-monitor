"""The ore dictionary, from the game data bundle's ore_dict.json (written
by tools/icon-export/), for the crafting plan's substitutions (a
crafting pattern with Substitute ticked takes, for an input, anything
sharing an ore-dictionary name with it, as AE2 does: OreReference), and
for listing an item's names (the page's $ search and item dialog).

ore_dict.json is {ore name: ["mod:internal:damage", ...]}, damage "*"
for every damage of the item. Bundles from before it existed have none,
and nothing substitutes; older ones only have names with more than one
item, which is all substituting needs. It's read from the bundle's index
(gcm/gamedata_index.py), not held in memory."""

import threading

_lock = threading.Lock()
_index = None  # the live bundle's gamedata_index.Index, or None
_loaded = False
_version = 0   # bumped on every load, for caches built from it


def load(index):
    """Makes the ore dictionary in index (a gamedata_index.Index) the live
    one; none if index is None."""
    global _index, _loaded, _version
    loaded = bool(index and index.has_ore_dict())
    with _lock:
        _index, _loaded = index, loaded
        _version += 1


def version():
    return _version


def loaded():
    return _loaded


def _split(key):
    """(mod, internal, damage) of an item key, or None for a fluid or
    essentia (store/items.py item_key: mod|internal|damage|kind[|variant])."""
    parts = key.split("|")
    if len(parts) < 4 or parts[3] != "item" or not parts[0]:
        return None
    try:
        damage = int(parts[2]) if parts[2] else 0
    except ValueError:
        return None
    return parts[0], parts[1], damage


def names(mod, internal, damage):
    """An item's ore-dictionary names, in ore dictionary order."""
    if not (mod and internal):
        return []
    try:
        damage = int(damage) if damage is not None else 0
    except (TypeError, ValueError):
        return []
    index = _index
    if index is None:
        return []
    return list(dict.fromkeys(index.ore_names(mod, internal, damage)))


class Known:
    """The item keys a plan can draw on (stock, pattern outputs), grouped
    for ore-dictionary lookups: an ore entry names an item and damage,
    and matches every NBT variant of it."""

    def __init__(self, keys):
        self.by_exact = {}
        self.by_item = {}
        for key in keys:
            split = _split(key)
            if split:
                self.by_exact.setdefault(split, []).append(key)
                self.by_item.setdefault(split[:2], []).append(key)


def equivalents(key, known):
    """The keys in `known` that share an ore-dictionary name with the
    item key, in ore dictionary order, the key itself left out."""
    split = _split(key)
    if split is None:
        return []
    index = _index
    if index is None:
        return []
    members = [index.ore_members(name) for name in index.ore_names(*split)]
    out, seen = [], {key}
    for group in members:
        for mod, internal, damage in group:
            found = known.by_item.get((mod, internal), []) if damage is None \
                else known.by_exact.get((mod, internal, damage), [])
            for k in found:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
    return out
