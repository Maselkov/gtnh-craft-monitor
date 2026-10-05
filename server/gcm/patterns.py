"""The ME network's patterns, fed by oc/network_browser.lua through
GTNH 2.9's ME Interface Terminal driver (me_interface_terminal): what
each pattern takes and makes, and which machine or interface holds it -
the data a recipe tree is built from. Scans use the same
start/batch/finish protocol as the item scan (gcm/inventory.py): batches
are buffered and only replace the last scan once the scan is known
complete. The live list is in gcm/state.py; the HTTP side is in
routes/network.py.

What OC reports, as seen on a real network (oc/pattern_dump.lua):
processing patterns carry real sizes, but crafting-table patterns
report every size as 0 - AE2's converter reads its own "Cnt" field,
and crafting patterns store a vanilla "Count". The pattern's own NBT
(`tag`, sent hex-encoded for those patterns) has the counts, read here
the way AE2 itself reads them. Without it (allowItemStackNBTTags off in
OpenComputers.cfg), a crafting input is still one per grid slot, but an
output's count is unknown: size None, and the pattern isn't `exact`.

Fluids arrive two ways: as fluid entries (amount, no damage), or as
ae2fc "fluid drop" items whose NBT names the fluid - turned into fluid
entries here. Thaumic essentia comes as an amount with no item id."""

import logging
import re
import secrets
import time

from gcm import icons, inventory, nbt, state, store

log = logging.getLogger(__name__)

KINDS = ("item", "fluid", "essentia")


def start_scan():
    """Begins a new pattern scan, discarding any unfinished one, and
    returns its token."""
    with state.patterns_lock:
        state.pattern_buffer.clear()
        state.patterns["in_progress"] = True
        state.patterns["chunks_received"] = 0
        token = secrets.token_hex(8)
        state.patterns["current_scan_token"] = token
    return token


def _is_current_scan_locked(token):
    current = state.patterns["current_scan_token"]
    return bool(current) and token == current


def add_batch(token, raw_patterns):
    """Buffers one batch. Returns (buffered pattern count, chunks
    received), or None if the batch isn't from the active scan."""
    patterns = [normalize(raw) for raw in raw_patterns if isinstance(raw, dict)]
    for p in patterns:
        _attach_icons(p)
    with state.patterns_lock:
        if not _is_current_scan_locked(token):
            return None
        state.pattern_buffer.extend(patterns)
        state.patterns["chunks_received"] += 1
        return len(state.pattern_buffer), state.patterns["chunks_received"]


def finish_scan(token, chunks_sent, total_errors):
    """Ends the scan, replacing the last one only if every chunk arrived
    and nothing failed - a partial scan would make patterns look gone.
    Returns the response body for network_browser.lua, shaped like the
    item scan's."""
    with state.patterns_lock:
        if not _is_current_scan_locked(token):
            state.patterns["in_progress"] = False
            return {"ok": False, "error": "stale_scan_token"}
        state.patterns["in_progress"] = False
        state.patterns["current_scan_token"] = None
        received = state.patterns["chunks_received"]
        if chunks_sent is None or total_errors != 0 or received != chunks_sent:
            state.pattern_buffer.clear()
            log.warning(
                "Rejected a pattern scan as incomplete: chunks_received=%s chunks_sent=%s total_errors=%s",
                received, chunks_sent, total_errors,
            )
            return {
                "ok": True,
                "rejected": True,
                "reason": f"incomplete scan (received {received}/{chunks_sent} chunks, "
                f"{total_errors} errors) - discarded",
            }
        patterns = list(state.pattern_buffer)
        state.pattern_buffer.clear()
        updated_at = time.time()
        state.patterns["patterns"] = patterns
        state.patterns["updated_at"] = updated_at

    # Best effort, as the item scan's snapshot: the scan already landed.
    try:
        store.patterns.save_snapshot([_without_icons(p) for p in patterns], updated_at)
    except Exception:
        log.exception("pattern snapshot persistence failed (the scan itself succeeded)")
    return {"ok": True, "pattern_count": len(patterns)}


def load_snapshot():
    """At startup: the last saved scan, so there are patterns before the
    next one."""
    patterns, updated_at = store.patterns.load_snapshot()
    for p in patterns:
        _attach_icons(p)
    with state.patterns_lock:
        state.patterns["patterns"] = patterns
        state.patterns["updated_at"] = updated_at


def refresh_icons():
    """Re-resolves every icon after the game data changed."""
    with state.patterns_lock:
        for p in state.patterns["patterns"] + state.pattern_buffer:
            for entry in p["inputs"] + p["outputs"]:
                entry.pop("icon", None)
            _attach_icons(p)


def snapshot():
    """What GET /api/network/patterns serves."""
    with state.patterns_lock:
        patterns = state.patterns["patterns"]
        return {
            "patterns": patterns,
            "summary": summarize(patterns),
            "updated_at": state.patterns["updated_at"],
            "in_progress": state.patterns["in_progress"],
        }


def current():
    """(the live pattern list, when it was scanned). The list is
    replaced, never changed in place, by each scan."""
    with state.patterns_lock:
        return state.patterns["patterns"], state.patterns["updated_at"]


def version():
    """Changes whenever snapshot() could, for an ETag."""
    with state.patterns_lock:
        return f"{state.patterns['updated_at']}-{state.patterns['in_progress']}"


def summarize(patterns):
    crafting = [p for p in patterns if p["crafting"]]
    return {
        "patterns": len(patterns),
        "crafting": len(crafting),
        "processing": len(patterns) - len(crafting),
        # Patterns with a size nobody could tell (see the module docstring).
        "inexact": sum(1 for p in patterns if not p["exact"]),
        "providers": len({_provider_key(p["provider"]) for p in patterns}),
    }


def _provider_key(provider):
    # Several interfaces can share a block (parts on one cable), and OC
    # doesn't report which side each is on - their names tell them apart.
    return tuple(provider.get(k) for k in ("dim", "x", "y", "z", "name"))


# --------------------------------------------------------- normalizing


def normalize(raw):
    """One pattern as network_browser.lua sent it -> {provider, slot,
    crafting, substitute, exact, inputs, outputs}. Entries for the same
    stack are merged, sizes summed."""
    crafting = bool(raw.get("crafting"))
    counts = _tag_counts(raw.get("tag"))
    # A crafting input fills one grid slot each, so counting the slots
    # gives its size even without the tag.
    inputs, inputs_exact = _entries(raw.get("inputs"), counts and counts["in"], count_slots=crafting)
    outputs, outputs_exact = _entries(raw.get("outputs"), counts and counts["out"], count_slots=False)
    pattern = {
        "provider": _provider(raw.get("provider")),
        "slot": raw.get("slot") if _is_number(raw.get("slot")) else None,
        "crafting": crafting,
        "exact": inputs_exact and outputs_exact,
        "inputs": inputs,
        "outputs": outputs,
    }
    if counts:
        pattern["substitute"] = counts["substitute"]
    return pattern


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _provider(raw):
    raw = raw if isinstance(raw, dict) else {}
    provider = {"name": str(raw.get("name") or "?")[:200]}
    for key in ("x", "y", "z", "dim"):
        if _is_number(raw.get(key)):
            provider[key] = int(raw[key])
    return provider


def _tag_counts(tag_hex):
    """{in, out: {(id, damage): count}, substitute} from a pattern's NBT,
    or None if there's none or it can't be read."""
    if not isinstance(tag_hex, str) or not tag_hex:
        return None
    try:
        tree = nbt.plain(nbt.COMPOUND, nbt.parse_hex(tag_hex))
    except nbt.NbtError as e:
        log.debug("unreadable pattern NBT: %s", e)
        return None

    def counts(stacks):
        out = {}
        for s in stacks if isinstance(stacks, list) else []:
            if not isinstance(s, dict) or "id" not in s:
                continue  # an empty grid slot
            # As AE2's PatternHelper reads a stack: Count, or Cnt if 0.
            n = s.get("Count") or s.get("Cnt") or 0
            key = (s["id"], s.get("Damage", 0))
            out[key] = out.get(key, 0) + n
        return out

    return {
        "in": counts(tree.get("in")),
        "out": counts(tree.get("out")),
        "substitute": bool(tree.get("substitute")),
    }


def _entries(raw_entries, tag_counts, count_slots):
    """Normalized, merged entries and whether every size is known."""
    merged = {}
    for raw in raw_entries if isinstance(raw_entries, list) else []:
        if not isinstance(raw, dict):
            continue
        entry, ids = _entry(raw)
        if entry is None:
            continue
        key = store.items.key_of(entry)
        m = merged.get(key)
        if m is None:
            m = merged[key] = {"entry": entry, "size": 0, "slots": 0, "ids": set()}
        m["size"] += entry["size"]
        m["slots"] += 1
        m["ids"] |= ids

    exact = True
    out = []
    for m in merged.values():
        entry = m["entry"]
        size = m["size"]
        if not size:
            # Two NBT variants of one item id and damage in the same
            # pattern would each get both counts - the tag's stacks are
            # matched by id and damage only.
            tagged = sum(tag_counts.get(i, 0) for i in m["ids"]) if tag_counts else 0
            if tagged:
                size = tagged
            elif count_slots:
                size = m["slots"]
            else:
                size = None
                exact = False
        entry["size"] = size
        out.append(entry)
    return out, exact


_DROP_PREFIX = re.compile(r"^drop of\s+", re.IGNORECASE)


def _entry(raw):
    """(entry, ids) for one stack: ids are what the pattern's NBT may
    call it, (numeric id or "mod:name", damage). (None, ...) if it has
    no name."""
    internal = raw.get("internal")
    if not isinstance(internal, str) or not internal:
        return None, set()
    kind = raw.get("kind") if raw.get("kind") in KINDS else "item"
    mod = raw.get("mod") if isinstance(raw.get("mod"), str) else None
    damage = int(raw["damage"]) if kind == "item" and _is_number(raw.get("damage")) else None
    entry = {
        "mod": mod,
        "internal": internal,
        "damage": damage,
        "kind": kind,
        "name": str(raw.get("name") or internal),
        "size": raw.get("size") if _is_number(raw.get("size")) and raw.get("size") > 0 else 0,
    }
    if kind != "item":
        return entry, set()

    ids = {(f"{mod}:{internal}" if mod else internal, damage or 0)}
    if _is_number(raw.get("id")):
        ids.add((int(raw["id"]), damage or 0))
    entry["hasTag"] = raw.get("hasTag")
    entry["tag"] = raw.get("tag")
    if mod == "ae2fc" and internal == "fluid_drop":
        fluid = _fluid_drop(entry)
        if fluid:
            return fluid, set()
    inventory.assign_variant(entry)
    return entry, ids


def _fluid_drop(entry):
    """ae2fc's stand-in item for a fluid, as the fluid it stands for:
    its NBT's "Fluid" is the registry name, its size the mB."""
    tag = entry.get("tag")
    if not isinstance(tag, str):
        return None
    try:
        root = nbt.parse_hex(tag)
    except nbt.NbtError:
        return None
    fluid = root.get("Fluid")
    if not fluid or fluid[0] != nbt.STRING or not fluid[1]:
        return None
    return {
        "mod": None,
        "internal": fluid[1].lower(),
        "damage": None,
        "kind": "fluid",
        "name": _DROP_PREFIX.sub("", entry["name"]),
        "size": entry["size"],
    }


def _attach_icons(pattern):
    icons.attach_item_icons(pattern["inputs"])
    icons.attach_item_icons(pattern["outputs"])


def _without_icons(pattern):
    # Icons are re-resolved on load (the game data may change between).
    return {
        **pattern,
        "inputs": [{k: v for k, v in e.items() if k != "icon"} for e in pattern["inputs"]],
        "outputs": [{k: v for k, v in e.items() if k != "icon"} for e in pattern["outputs"]],
    }
