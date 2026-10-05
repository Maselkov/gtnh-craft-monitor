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
from collections import OrderedDict, deque

from gcm import store

# A safety limit, not a display one: past this many steps a plan stops
# expanding (give or take the siblings of the step it stopped at) and
# says it was cut short. The biggest real plan seen, a GTNH endgame
# multiblock planned with nothing in stock, was about 130,000 steps,
# planned in well under a second; this only stops a broken pattern set
# from taking the server down.
MAX_NODES = 300_000

# How many recent plans are kept, so opening steps of a big tree (sent
# a level at a time, see trim()) doesn't plan it all again. Few, as the
# biggest take tens of MB.
PLAN_CACHE_SIZE = 2

# A missing item's places in the tree (`at`) listed for the dialog to
# jump to, at most; `places` says how many there are in all.
MAX_JUMPS = 50
# How many steps past the levels asked for trim() sends to lead the way
# to what's missing, so the dialog can show it without asking for every
# branch in between. Past this, branches come as they're opened.
MISSING_PATH_BUDGET = 600

_index_lock = threading.Lock()
_index_cache = (None, None)  # (the pattern list it was built from, _Index)
_plan_lock = threading.Lock()
_plan_cache = OrderedDict()  # cache key -> (pattern list, plan)


def pattern_id(pattern, occurrence):
    """Stable for a pattern within one scan: where it is (provider and
    slot), plus a count to tell apart the patterns of two same-named
    interfaces on one block, which OC can't."""
    p = pattern["provider"]
    text = f"{p.get('dim')}|{p.get('x')}|{p.get('y')}|{p.get('z')}|{p.get('name')}|{pattern.get('slot')}|{occurrence}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


class _Index:
    """The pattern list prepared for planning, built once per scan:
    candidates - {output item key: [(pattern id, pattern, output entry)]},
    best default first: patterns that make the item as their main
    (first) output, then ones that don't use up the item themselves,
    then scan order.
    keys - every input and output entry's item key, by id(entry), as
    working it out again at every step of a big plan was a third of
    the time.
    alternatives - for an item more than one pattern makes, what the
    dialog's dropdown lists, shared by every step making that item."""

    def __init__(self, patterns):
        self.keys = {}
        seen = {}
        by_key = {}
        for order, pattern in enumerate(patterns):
            where = (tuple(sorted(pattern["provider"].items())), pattern.get("slot"))
            seen[where] = seen.get(where, 0) + 1
            pid = pattern_id(pattern, seen[where])
            for entry in pattern["inputs"] + pattern["outputs"]:
                self.keys[id(entry)] = store.items.key_of(entry)
            input_keys = {self.keys[id(e)] for e in pattern["inputs"]}
            for position, out in enumerate(pattern["outputs"]):
                key = self.keys[id(out)]
                rank = (position != 0, key in input_keys, order)
                by_key.setdefault(key, []).append((rank, pid, pattern, out))
        self.candidates = {
            key: [(pid, pattern, out) for _, pid, pattern, out in sorted(entries, key=lambda e: e[0])]
            for key, entries in by_key.items()
        }
        self.alternatives = {
            key: [
                {
                    "id": pid,
                    "provider": pattern["provider"].get("name"),
                    "inputs": ", ".join(f"{_qty(e.get('size') or 1)} {e.get('name')}" for e in pattern["inputs"]),
                }
                for pid, pattern, _ in candidates
            ]
            for key, candidates in self.candidates.items()
            if len(candidates) > 1
        }

    def key(self, entry):
        k = self.keys.get(id(entry))
        return k if k is not None else store.items.key_of(entry)


def index(patterns):
    """The _Index for this pattern list, cached until the list changes."""
    global _index_cache
    with _index_lock:
        if _index_cache[0] is patterns:
            return _index_cache[1]
    built = _Index(patterns)
    with _index_lock:
        _index_cache = (patterns, built)
    return built


def find_output(patterns, key):
    """The output entry (name, icon...) of the item key, if a pattern
    makes it."""
    candidates = index(patterns).candidates.get(key)
    return candidates[0][2] if candidates else None


def _item_fields(entry, key):
    # The icon is read from the entry each time, not kept in the index:
    # new game data replaces it in place.
    out = {"key": key, "name": entry.get("name"), "kind": entry.get("kind") or "item"}
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
                **_item_fields(entry, key),
                "available": self.stock.get(key, 0),
                "need": 0, "from_stock": 0, "craft": 0, "missing": 0,
            }
        return t

    def run(self, target, amount):
        """The plan's tree for `amount` of target. Depth-first, as AE2
        plans, so stock goes to the first step to reach it - but with a
        stack rather than recursion, as a chain can be deeper than
        Python's recursion limit."""
        root, below = self.step(target, amount, frozenset(), is_root=True)
        # (parent step, input entry, amount needed, items above it),
        # last first, so each branch is finished before the next starts.
        stack = [(root, *b) for b in reversed(below)]
        while stack:
            parent, entry, need, path = stack.pop()
            node, below = self.step(entry, need, path)
            parent["children"].append(node)
            stack.extend((node, *b) for b in reversed(below))
        return root

    def step(self, entry, need, path, is_root=False):
        """One step of the plan, and what's below it to plan next:
        [(input entry, amount needed, items above it)]."""
        key = self.index.key(entry)
        self.nodes += 1
        node = {**_item_fields(entry, key), "need": need, "from_stock": 0}
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
            return node, []

        candidates = self.index.candidates.get(key, [])
        if not candidates or key in path:
            # Nothing makes it, or making it needs itself further up:
            # either way, the rest isn't there.
            node["status"] = "cycle" if candidates else "missing"
            node["missing"] = rest
            totals["missing"] += rest
            return node, []

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
            children=[],
        )
        totals["craft"] += batches * per_batch
        if not out.get("size"):
            node["inexact"] = True
        if key in self.index.alternatives:
            node["alternatives"] = self.index.alternatives[key]
        also = [
            {**_item_fields(o, self.index.key(o)), "amount": (o.get("size") or 1) * batches}
            for o in pattern["outputs"]
            if self.index.key(o) != key
        ]
        if also:
            node["also_makes"] = also

        if self.nodes >= MAX_NODES:
            # Its siblings still get a row each, unexpanded.
            self.truncated = True
            node["truncated"] = True
            return node, []
        below = path | {key}
        return node, [(e, (e.get("size") or 1) * batches, below) for e in pattern["inputs"]]


def _qty(n):
    return f"{n:g}" if isinstance(n, float) else str(n)


def plan(patterns, stock, key, amount, choices=None, rules=None):
    """The plan for `amount` of the item `key`, or None if no pattern
    makes it. stock: {key: size}. choices: {key: pattern id} picking a
    pattern other than the default. rules: [(key, threshold, text)] -
    a stock rule's level, warned about if the plan would take the item
    from at or above it to below it. One already below isn't: the plan
    didn't cause that."""
    target = find_output(patterns, key)
    if target is None:
        return None
    planner = _Planner(patterns, stock, choices or {})
    root = planner.run(target, amount)

    _mark_missing(root, planner.items)

    items = list(planner.items.values())
    for item in items:
        left = item["available"] - item["from_stock"]
        warnings = [
            {"rule": text, "threshold": threshold}
            for rule_key, threshold, text in rules or []
            if rule_key == item["key"] and item["available"] >= threshold > left
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


def _mark_missing(root, items):
    """Sets missing_below on each step that has something short in its
    subtree (itself included): how many steps, as amounts of different
    items don't add up. And on each missing item, `at`: the child
    positions ("0.3.1") of the steps it's short at, in tree order, and
    `places`, how many. Without recursion, as plans can be thousands of
    steps deep."""
    order = []  # pre-order: a step before its own steps
    stack = [(root, "")]
    while stack:
        node, pos = stack.pop()
        order.append(node)
        if node.get("missing"):
            item = items[node["key"]]
            item["places"] = item.get("places", 0) + 1
            if len(item.setdefault("at", [])) < MAX_JUMPS:
                item["at"].append(pos)
        children = node.get("children") or []
        for i in range(len(children) - 1, -1, -1):
            stack.append((children[i], f"{pos}.{i}" if pos else str(i)))
    for node in reversed(order):  # each step after its own steps
        below = (1 if node.get("missing") else 0) + sum(c.get("missing_below", 0) for c in node.get("children") or [])
        if below:
            node["missing_below"] = below


def cached_plan(patterns, stock, stock_version, key, amount, choices=None, rules=None):
    """plan(), reusing one of the last few if nothing it depends on has
    changed: the pattern list, the stock (stock_version says when it
    last changed), and the request itself."""
    choices = choices or {}
    rules = rules or []
    cache_key = (stock_version, key, amount, tuple(sorted(choices.items())), tuple(rules))
    with _plan_lock:
        hit = _plan_cache.get(cache_key)
        if hit is not None and hit[0] is patterns:
            _plan_cache.move_to_end(cache_key)
            return hit[1]
    result = plan(patterns, stock, key, amount, choices, rules)
    with _plan_lock:
        _plan_cache[cache_key] = (patterns, result)
        _plan_cache.move_to_end(cache_key)
        while len(_plan_cache) > PLAN_CACHE_SIZE:
            _plan_cache.popitem(last=False)
    return result


def trim(node, depth, budget=MISSING_PATH_BUDGET):
    """A copy of node with `depth` levels of steps below it, and past
    those, the steps leading to anything missing (missing_below), up to
    `budget` more, nearest first. A step whose own steps are left out
    says how many with `more`, for the dialog to ask for them
    (subtree()) when it's opened."""
    def copy(n):
        return {k: v for k, v in n.items() if k != "children"}

    out = copy(node)
    queue = deque([(node, out, 0)])
    while queue:
        src, dst, level = queue.popleft()
        children = src.get("children")
        if children is None:
            continue
        if not children:
            dst["children"] = []
            continue
        if level >= depth:
            if not src.get("missing_below") or budget < len(children):
                dst["more"] = len(children)
                continue
            budget -= len(children)
        dst["children"] = []
        for c in children:
            cc = copy(c)
            dst["children"].append(cc)
            queue.append((c, cc, level + 1))
    return out


def subtree(root, path):
    """The step at path (child positions from the root), or None if the
    plan has no such step - it was planned differently since."""
    node = root
    for position in path:
        children = node.get("children") or []
        if not 0 <= position < len(children):
            return None
        node = children[position]
    return node


def reset():
    """Forgets cached indexes and plans (tests)."""
    global _index_cache
    with _index_lock:
        _index_cache = (None, None)
    with _plan_lock:
        _plan_cache.clear()
