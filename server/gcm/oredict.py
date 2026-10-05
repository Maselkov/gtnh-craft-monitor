"""The ore dictionary, from the game data bundle's ore_dict.json (written
by tools/icon-export/), for the crafting plan's substitutions: a
crafting pattern with Substitute ticked takes, for an input, anything
sharing an ore-dictionary name with it, as AE2 does (OreReference).

ore_dict.json is {ore name: ["mod:internal:damage", ...]}, damage "*"
for every damage of the item; only names with more than one item, as a
name with one can't stand in for anything. Bundles from before it
existed have none, and nothing substitutes."""

import json
import logging
import os
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_by_item = {}  # (mod, internal, damage) -> ore names, damage None for "*"
_members = {}  # ore name -> [(mod, internal, damage or None)]
_version = 0   # bumped on every load, for caches built from it


def load(path):
    """Makes the ore dictionary at path the live one; none if path is
    None or missing."""
    global _by_item, _members, _version
    members, by_item = {}, {}
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("unreadable ore dictionary %s: %s", path, e)
            raw = {}
        for name, entries in raw.items() if isinstance(raw, dict) else ():
            parsed = []
            for entry in entries if isinstance(entries, list) else ():
                mod, sep, rest = str(entry).partition(":")
                internal, sep2, damage = rest.rpartition(":")
                if not (sep and sep2 and mod and internal):
                    continue
                if damage == "*":
                    parsed.append((mod, internal, None))
                elif damage.lstrip("-").isdigit():
                    parsed.append((mod, internal, int(damage)))
            if parsed:
                members[name] = parsed
                for m in parsed:
                    by_item.setdefault(m, []).append(name)
    with _lock:
        _members, _by_item = members, by_item
        _version += 1


def version():
    return _version


def loaded():
    return bool(_members)


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
    with _lock:
        names = _by_item.get(split, []) + _by_item.get((split[0], split[1], None), [])
        members = [_members[n] for n in names]
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
