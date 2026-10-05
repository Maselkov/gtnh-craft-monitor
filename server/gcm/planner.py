"""A crafting plan: what asking AE2 for some amount of an item would take,
worked out from the network's patterns (gcm/patterns.py) and the last
scan's stock, for the craft-request dialog to show before the request
is sent. AE2 doesn't tell OpenComputers its own plan, so this is an
estimate made the way AE2 plans:

- The requested item itself is always crafted in full - AE2 doesn't
  take the requested item from storage.
- Everything below it comes out of storage first; only the rest is
  crafted. Storage is shared, so an item two branches need is only
  counted once, by whichever branch reaches it first (depth-first, as
  AE2 walks it).
- A step runs its pattern in whole batches: 10 needed from a pattern
  making 4 is 3 batches, 12 made.
- Byproducts are listed but not counted as stock, as in AE2.

Where it differs: no ore-dictionary substitutions, and where several
patterns make an item, AE2 picks by priority, which OC doesn't report -
here the default is the first pattern with the item as its main
output, and the dialog can pick another."""

import hashlib
import math
import threading

from gcm import store

# Past this many steps a plan stops expanding (give or take the
# siblings of the step it stopped at) and says it was cut short; it
# would be too long to read anyway.
MAX_NODES = 2000

_index_lock = threading.Lock()
_index_cache = (None, None)  # (the pattern list it was built from, index)


def pattern_id(pattern, occurrence):
    """Stable for a pattern within one scan: where it is (provider and
    slot), plus a count to tell apart the patterns of two same-named
    interfaces on one block, which OC can't."""
    p = pattern["provider"]
    text = f"{p.get('dim')}|{p.get('x')}|{p.get('y')}|{p.get('z')}|{p.get('name')}|{pattern.get('slot')}|{occurrence}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def index(patterns):
    """{output item key: [(pattern id, pattern, output entry)]}, best
    default first: patterns that make the item as their main (first)
    output, then ones that don't use up the item themselves, then scan
    order. Cached for the current pattern list."""
    global _index_cache
    with _index_lock:
        if _index_cache[0] is patterns:
            return _index_cache[1]
    seen = {}
    by_key = {}
    for order, pattern in enumerate(patterns):
        where = (tuple(sorted(pattern["provider"].items())), pattern.get("slot"))
        seen[where] = seen.get(where, 0) + 1
        pid = pattern_id(pattern, seen[where])
        input_keys = {store.items.key_of(e) for e in pattern["inputs"]}
        for position, out in enumerate(pattern["outputs"]):
            key = store.items.key_of(out)
            rank = (position != 0, key in input_keys, order)
            by_key.setdefault(key, []).append((rank, pid, pattern, out))
    built = {
        key: [(pid, pattern, out) for _, pid, pattern, out in sorted(entries, key=lambda e: e[0])]
        for key, entries in by_key.items()
    }
    with _index_lock:
        _index_cache = (patterns, built)
    return built


def find_output(patterns, key):
    """The output entry (name, icon...) of the item key, if a pattern
    makes it."""
    candidates = index(patterns).get(key)
    return candidates[0][2] if candidates else None


def _item_fields(entry):
    out = {"key": store.items.key_of(entry), "name": entry.get("name"), "kind": entry.get("kind") or "item"}
    for field in ("icon", "variant_name"):
        if entry.get(field):
            out[field] = entry[field]
    return out


class _Planner:
    def __init__(self, patterns, stock, choices):
        self.index = index(patterns)
        self.available = dict(stock)
        self.stock = stock
        self.choices = choices
        self.nodes = 0
        self.truncated = False
        self.items = {}  # key -> totals for the list view

    def totals(self, entry, key):
        t = self.items.get(key)
        if t is None:
            t = self.items[key] = {
                **_item_fields(entry),
                "available": self.stock.get(key, 0),
                "need": 0, "from_stock": 0, "craft": 0, "missing": 0,
            }
        return t

    def node(self, entry, need, path, is_root=False):
        key = store.items.key_of(entry)
        self.nodes += 1
        node = {**_item_fields(entry), "need": need, "from_stock": 0}
        totals = self.totals(entry, key)
        totals["need"] += need

        rest = need
        if not is_root:
            have = self.available.get(key, 0)
            taken = min(have, need)
            self.available[key] = have - taken
            node["from_stock"] = taken
            totals["from_stock"] += taken
            rest = need - taken
        if rest <= 0:
            node["status"] = "stock"
            return node

        candidates = self.index.get(key, [])
        if not candidates or key in path:
            # Nothing makes it, or making it needs itself further up:
            # either way, the rest isn't there.
            node["status"] = "cycle" if candidates else "missing"
            node["missing"] = rest
            totals["missing"] += rest
            return node

        chosen = next((c for c in candidates if c[0] == self.choices.get(key)), candidates[0])
        pid, pattern, out = chosen
        per_batch = out.get("size") or 1
        batches = math.ceil(rest / per_batch)
        node.update(
            status="craft",
            craft=batches * per_batch,
            batches=batches,
            pattern={
                "id": pid,
                "provider": pattern["provider"].get("name"),
                "crafting": pattern["crafting"],
            },
        )
        totals["craft"] += batches * per_batch
        if not out.get("size"):
            node["inexact"] = True
        if len(candidates) > 1:
            node["alternatives"] = [
                {
                    "id": cpid,
                    "provider": cpattern["provider"].get("name"),
                    "inputs": ", ".join(f"{_qty(e.get('size') or 1)} {e.get('name')}" for e in cpattern["inputs"]),
                }
                for cpid, cpattern, _ in candidates
            ]
        also = [
            {**_item_fields(o), "amount": (o.get("size") or 1) * batches}
            for o in pattern["outputs"]
            if store.items.key_of(o) != key
        ]
        if also:
            node["also_makes"] = also

        if self.nodes >= MAX_NODES:
            # Its siblings still get a row each, unexpanded.
            self.truncated = True
            node["truncated"] = True
            node["children"] = []
            return node
        child_path = path | {key}
        node["children"] = [
            self.node(e, (e.get("size") or 1) * batches, child_path) for e in pattern["inputs"]
        ]
        return node


def _qty(n):
    return f"{n:g}" if isinstance(n, float) else str(n)


def plan(patterns, stock, key, amount, choices=None, rules=None):
    """The plan for `amount` of the item `key`, or None if no pattern
    makes it. stock: {key: size}. choices: {key: pattern id} picking a
    pattern other than the default. rules: [(key, threshold, text)] -
    a stock rule's level, warned about if the plan would take the item
    below it."""
    target = find_output(patterns, key)
    if target is None:
        return None
    planner = _Planner(patterns, stock, choices or {})
    root = planner.node(target, amount, frozenset(), is_root=True)

    items = list(planner.items.values())
    for item in items:
        left = item["available"] - item["from_stock"]
        warnings = [
            {"rule": text, "threshold": threshold}
            for rule_key, threshold, text in rules or []
            if rule_key == item["key"] and item["from_stock"] > 0 and left < threshold
        ]
        if warnings:
            item["left"] = left
            item["warnings"] = warnings
    return {
        "root": root,
        "items": items,
        "missing": [i for i in items if i["missing"] > 0],
        "steps": planner.nodes,
        "truncated": planner.truncated,
    }
