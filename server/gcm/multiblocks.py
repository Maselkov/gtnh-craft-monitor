"""Multiblock kits: every block a GregTech multiblock is built from, for
the parts it's built with (its coils, its glass, its length), so all of
them can be requested at once. From the selected game data's
structures.json.gz (tools/icon-export's Structures.java describes it).

A multiblock's channels are what the hologram projector's tier settings
pick: a coil or glass channel's tiers are the blocks it takes, in order;
a length or height channel's are its sizes. Hatches, buses, mufflers and
maintenance hatches aren't in a kit: the spots that take either a hatch
or a casing count as casings, so a kit's casings are the most it can
need, and players take off what their hatches and shared walls save.

layout() puts together where each block goes, for the page's 3D view,
from layouts.json.gz (Layouts.java describes it), drawn with the
textures in layouts.png."""

import gzip
import json
import logging
import os
import threading

from gcm import gamedata, icons, state, store

log = logging.getLogger(__name__)

FILE = "structures.json.gz"
LAYOUTS = "layouts.json.gz"
ATLAS = "layouts.png"
# The most of one multiblock a kit is for.
MAX_COUNT = 64

_lock = threading.Lock()
_loaded = {}  # file name -> {path, mtime, data}


def _load(name):
    """The selected version's name (a gzipped JSON file with multiblocks),
    or None. Kept until the file changes."""
    version = gamedata.selected_version()
    path = gamedata.bundle_file(version, name) if version else None
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        loaded = _loaded.get(name)
        if loaded and loaded["path"] == path and loaded["mtime"] == mtime:
            return loaded["data"]
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("multiblocks"), dict):
            raise ValueError("no multiblocks")
    except (OSError, ValueError) as e:
        log.error("Couldn't read %s: %s", path, e)
        data = None
    with _lock:
        _loaded[name] = {"path": path, "mtime": mtime, "data": data}
    return data


def _data():
    """The selected version's structures ({names, multiblocks}), or None."""
    return _load(FILE)


def reset():
    with _lock:
        _loaded.clear()


def game_key(mod, internal, damage):
    """The game data's key for an item: mod:internal:damage."""
    return f"{mod}:{internal}:{damage}"


def _split(key):
    """(mod, internal, damage) of a game data key; damage an int."""
    mod, rest = key.split(":", 1)
    internal, damage = rest.rsplit(":", 1)
    try:
        damage = int(damage)
    except ValueError:
        pass
    return mod, internal, damage


def available(mod, internal, damage):
    """Whether there's a kit for this item: it's a multiblock's controller."""
    data = _data()
    return bool(data and game_key(mod, internal, damage) in data["multiblocks"])


def controllers():
    """[(key, name)] of every multiblock with a kit, by name."""
    data = _data()
    if not data:
        return []
    names = data.get("names") or {}
    return sorted(((k, names.get(k) or k) for k in data["multiblocks"]), key=lambda kn: kn[1].lower())


def _channel_options(channel, changes, names):
    """A channel's tiers as [{tier, label, key?}]: a block channel's are
    named after the block each tier puts in (key: that block), a size
    channel's are numbers. What every tier adds alike (the glass around
    each capacitor) doesn't name one."""
    tiers = [str(t) for t in range(channel["min"], channel["max"] + 1)]
    added = {t: {k for k, n in (changes.get(t) or {}).items() if n > 0} for t in tiers}
    non_empty = [s for s in added.values() if s]
    common = set.intersection(*non_empty) if len(non_empty) > 1 else set()
    # The block base already has, that the other tiers take out.
    taken = [{k for k, n in (changes.get(t) or {}).items() if n < 0} for t in tiers]
    base_blocks = set.intersection(*[s for s in taken if s]) if any(taken) else set()
    options = []
    for t in tiers:
        own = sorted(added[t] - common) or (sorted(base_blocks) if not added[t] else [])
        option = {"tier": int(t)}
        if own:
            option["label"] = " + ".join(names.get(k) or k for k in own)
            option["key"] = own[0]
        else:
            option["label"] = t
        options.append(option)
    return options


def _counts(entry, tiers):
    """{key: n} of one multiblock built at tiers ({channel: tier})."""
    channels = entry.get("channels") or []
    combos = entry.get("combos")
    if combos:
        name = ",".join(f"{c['name']}={tiers[c['name']]}" for c in channels)
        if name in combos:
            return dict(combos[name])
    counts = dict(entry.get("base") or {})
    for c in channels:
        change = ((entry.get("tiers") or {}).get(c["name"]) or {}).get(str(tiers[c["name"]])) or {}
        for k, n in change.items():
            counts[k] = counts.get(k, 0) + n
    return {k: n for k, n in counts.items() if n > 0}


def _snapshot():
    """{(mod, internal, damage as str): network item} for plain items."""
    with state.network_lock:
        items = list(state.network["items"])
    out = {}
    for it in items:
        if (it.get("kind") or "item") != "item" or it.get("variant"):
            continue
        out[(it.get("mod"), it.get("internal"), str(it.get("damage")))] = it
    return out


def kit(key, tiers=None, count=1):
    """The kit for the multiblock whose controller is key (mod:internal:
    damage), built at tiers ({channel: tier}; a channel left out is at
    its lowest), count of them. None if there's no such multiblock.

    {controller: {key, name, icon}, count, approximate, channels: [{name,
    kind: "block"|"size", min, max, tier, options: [{tier, label, key?,
    icon?}]}], rows: [{key, item, mod, internal, damage, name, icon, need,
    have, craftable}]}. A row's item is the network's item key, for the
    other tools; have and craftable come from the last scan."""
    data = _data()
    entry = data and data["multiblocks"].get(key)
    if not entry:
        return None
    names = data.get("names") or {}
    tiers = tiers or {}
    count = max(1, min(MAX_COUNT, int(count or 1)))
    chosen = {}
    channels = []
    for c in entry.get("channels") or []:
        try:
            tier = int(tiers.get(c["name"], c["min"]))
        except (TypeError, ValueError):
            tier = c["min"]
        tier = max(c["min"], min(c["max"], tier))
        chosen[c["name"]] = tier
        changes = (entry.get("tiers") or {}).get(c["name"]) or {}
        options = _channel_options(c, changes, names)
        for option in options:
            if option.get("key"):
                mod, internal, damage = _split(option["key"])
                option["icon"] = icons.resolve_icon(mod, internal, damage, option["label"])
        channels.append({
            "name": c["name"],
            "kind": "block" if any(o.get("key") for o in options) else "size",
            "min": c["min"],
            "max": c["max"],
            "tier": tier,
            "options": options,
        })

    network = _snapshot()
    rows = []
    for k, n in _counts(entry, chosen).items():
        mod, internal, damage = _split(k)
        it = network.get((mod, internal, str(damage)))
        name = (it and it.get("name")) or names.get(k) or k
        rows.append({
            "key": k,
            "item": store.items.item_key(mod, internal, damage, "item"),
            "mod": mod,
            "internal": internal,
            "damage": damage,
            "name": name,
            "icon": (it and it.get("icon")) or icons.resolve_icon(mod, internal, damage, name),
            "need": n * count,
            "have": (it and it.get("size")) or 0,
            "craftable": bool(it and it.get("isCraftable")),
        })
    rows.sort(key=lambda r: (-r["need"], r["name"].lower()))
    mod, internal, damage = _split(key)
    name = names.get(key) or key
    return {
        "controller": {"key": key, "name": name, "icon": icons.resolve_icon(mod, internal, damage, name)},
        "count": count,
        "approximate": bool(entry.get("approximate")),
        "layout": layout_available(key),
        "channels": channels,
        "rows": rows,
    }


# Layouts.pack(): a position relative to the controller in one number.
_OFFSET = 512


def _unpack(p):
    return (p >> 20) - _OFFSET, ((p >> 10) & 1023) - _OFFSET, (p & 1023) - _OFFSET


def _positions(deltas):
    """Layouts.java's delta-coded positions, as packed numbers."""
    out, last = [], 0
    for d in deltas:
        last += d
        out.append(last)
    return out


def _apply(blocks, change, swaps):
    """Applies a change's set and clear to blocks ({position: block key})
    and adds its swaps to swaps, which go last."""
    for p in _positions(change.get("clear") or []):
        blocks.pop(p, None)
    for key, deltas in (change.get("set") or {}).items():
        for p in _positions(deltas):
            blocks[p] = key
    swaps.extend((change.get("swap") or {}).items())


def layout_available(key):
    data = _load(LAYOUTS)
    return bool(data and key in data["multiblocks"])


def atlas_path():
    """The selected game data's block texture atlas, or None."""
    version = gamedata.selected_version()
    return gamedata.bundle_file(version, ATLAS) if version else None


def layout(key, tiers=None):
    """Where each block of the multiblock goes, built at tiers (as kit()
    takes them), for the page's 3D view. None without layouts for it.

    {tile, columns, blocks: [{key, name, icon, mesh?, faces?, tint?,
    translucent?, controller?, positions: [x, y, z, x, y, z...]}]}:
    positions relative to the controller (y up), sorted from the bottom
    layer up. mesh is the geometry the game draws for the block (see
    BlockMeshes.java); a block without one has faces, the atlas tiles of
    its down, up, north, south, west and east faces (-1 for none), drawn as
    a cube; and one with neither is drawn with its item icon. ctm: the
    connected textures that apply to its blocks, which the page joins up
    (Layouts.java's connectedTextures())."""
    data = _load(LAYOUTS)
    entry = data and data["multiblocks"].get(key)
    structures = _data()
    if not entry or not structures or key not in structures["multiblocks"]:
        return None
    tiers = tiers or {}
    chosen = {}
    for c in structures["multiblocks"][key].get("channels") or []:
        try:
            tier = int(tiers.get(c["name"], c["min"]))
        except (TypeError, ValueError):
            tier = c["min"]
        chosen[c["name"]] = max(c["min"], min(c["max"], tier))

    blocks = {}
    for block_key, deltas in (entry.get("base") or {}).items():
        for p in _positions(deltas):
            blocks[p] = block_key
    swaps = []
    combo = ",".join(f"{name}={tier}" for name, tier in chosen.items())
    if entry.get("combos") and combo in entry["combos"]:
        _apply(blocks, entry["combos"][combo], swaps)
    else:
        for name, tier in chosen.items():
            _apply(blocks, ((entry.get("tiers") or {}).get(name) or {}).get(str(tier)) or {}, swaps)
    for old, new in swaps:
        for p, block_key in blocks.items():
            if block_key == old:
                blocks[p] = new

    by_key = {}
    for p, block_key in blocks.items():
        by_key.setdefault(block_key, []).append(_unpack(p))
    names = structures.get("names") or {}
    palette = data.get("blocks") or {}
    out = []
    for block_key, positions in sorted(by_key.items()):
        info = palette.get(block_key) or {}
        item = info.get("item")
        name = names.get(item) if item else None
        mod, internal, damage = _split(item) if item else (None, None, None)
        positions.sort(key=lambda xyz: (xyz[1], xyz[0], xyz[2]))
        row = {
            "key": block_key,
            "name": name or block_key,
            "icon": icons.resolve_icon(mod, internal, damage, name) if item else None,
            "positions": [n for xyz in positions for n in xyz],
        }
        for field in ("mesh", "faces", "tint", "translucent"):
            if field in info:
                row[field] = info[field]
        if (0, 0, 0) in positions:
            row["controller"] = True
        out.append(row)
    # The connected textures for the blocks it has (Layouts.java's ctm).
    keys = {row["key"] for row in out}
    sprites = {t for row in out for t in (row.get("mesh") or {}).get("t", [])}
    ctm = [d for d in data.get("ctm") or []
           if keys.intersection(d.get("blocks") or []) or sprites.intersection(d.get("sprites") or [])]
    return {"tile": data.get("tile", 16), "columns": data.get("columns", 1), "blocks": out, "ctm": ctm}


def plan_root(key, count):
    """The kit's own step at the top of its crafting plan
    (planner.plan_many): {key, name, icon}, named "3 × Electric Blast
    Furnace" for more than one."""
    data = _data()
    names = (data or {}).get("names") or {}
    try:
        count = max(1, int(count or 1))
    except (TypeError, ValueError):
        count = 1
    name = names.get(key) or "Multiblock kit"
    root = {"key": f"kit|{key}", "name": f"{count} × {name}" if count > 1 else name}
    if key in names and key.count(":") >= 2:
        mod, internal, damage = _split(key)
        icon = icons.resolve_icon(mod, internal, damage, name)
        if icon:
            root["icon"] = icon
    return root
