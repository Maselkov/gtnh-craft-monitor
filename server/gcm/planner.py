"""A crafting plan: what asking AE2 for some amount of an item would take,
worked out from the network's patterns (gcm/patterns.py) and the last
scan's stock, for the craft-request dialog to show before the request
is sent. AE2 doesn't tell OpenComputers its own plan, so this is an
estimate, worked out the way GTNH's AE2 plans (appeng/crafting/v2):

- The requested item itself is always crafted in full - AE2 doesn't
  take the requested item from storage.
- Everything below it comes first from what earlier steps made too much
  of (a batch's extra output, a pattern's other outputs), then out of
  storage; only the rest is crafted. Both are shared, so an item two
  branches need is only counted once, by whichever branch reaches it
  first (depth-first, as AE2 walks it).
- Patterns are tried in AE2's order: by slot, the last first (interface
  priority comes into it too, but OC doesn't report it). Each makes as
  many whole batches as its inputs can be had for; the rest goes to the
  next. What none of them can cover is planned with the first, and what
  that lacks is missing. So one item can come from two patterns.
- A step runs its pattern in whole batches: 10 needed from a pattern
  making 4 is 3 batches, 12 made, 2 left over.
- A pattern isn't used again below a step that uses it (AE2's guard
  against loops); a pattern needing what it makes asks for that once.
- A crafting pattern with Substitute ticked takes, for each input, the
  item itself first, then anything sharing an ore-dictionary name with
  it (gcm/oredict.py): from leftovers and stock, then from its own
  patterns, then from patterns marked "can be substituted" making one
  of those alternatives.

Where it differs: AE2 also checks a substitute against the recipe's
slot, where this takes any item sharing an ore-dictionary name, and
pattern order ignores interface priority, which OC doesn't report. The
dialog can pick one pattern for an item instead."""

import hashlib
import math
import threading
from collections import OrderedDict, deque

from gcm import oredict, store

# A safety limit, not a display one: past this many steps a plan stops
# expanding (give or take the siblings of the step it stopped at) and
# says it was cut short. The biggest real plan seen, a GTNH endgame
# multiblock planned with nothing in stock, was about 130,000 steps,
# planned in well under a second; this only stops a broken pattern set
# from taking the server down.
MAX_NODES = 300_000
# The same for the steps tried and taken back again (see _Planner): a
# step with several patterns that comes up short is planned again with
# the next. Past this much work, the first pattern is used, short or not.
MAX_WORK = 3 * MAX_NODES

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


class _Pattern:
    """A pattern as the planner uses it: its inputs and outputs merged by
    item, as AE2 merges them. An item on both sides (a catalyst, or a
    loop) only counts by its difference per batch, and is asked for once
    - what's put in back on each batch (`once`)."""

    __slots__ = ("id", "pattern", "inputs", "outputs", "once", "substitute", "be_substitute")

    def __init__(self, pid, pattern, key_of):
        self.id = pid
        self.pattern = pattern
        self.substitute = bool(pattern.get("substitute"))
        self.be_substitute = bool(pattern.get("be_substitute"))
        inputs, outputs = {}, {}
        for side, merged in ((pattern["inputs"], inputs), (pattern["outputs"], outputs)):
            for e in side:
                k = key_of(e)
                entry, size = merged.get(k, (e, 0))
                merged[k] = (entry, size + (e.get("size") or 1))
        self.once = []
        for k in list(inputs):
            if k in outputs:
                (ie, isize), (oe, osize) = inputs[k], outputs[k]
                self.once.append((k, ie, min(isize, osize)))
                if osize > isize:
                    del inputs[k]
                    outputs[k] = (oe, osize - isize)
                else:
                    del outputs[k]
                    if isize > osize:
                        inputs[k] = (ie, isize - osize)
                    else:
                        del inputs[k]
        self.inputs = [(k, e, size) for k, (e, size) in inputs.items()]
        self.outputs = {k: (e, size) for k, (e, size) in outputs.items()}


class _Index:
    """The pattern list prepared for planning, built once per scan:
    candidates - {output item key: [_Pattern]}, in the order AE2 tries
    them: by slot, the last first, then scan order.
    keys - every input and output entry's item key, by id(entry), as
    working it out again at every step of a big plan was a third of
    the time.
    alternatives - for an item more than one pattern makes, what the
    dialog's dropdown lists, shared by every step making that item.
    stand_ins - {output item key: [_Pattern]}: the candidates marked "can
    be substituted", which AE2 may use to make an ore-dictionary
    alternative for an input taking one."""

    def __init__(self, patterns):
        self.keys = {}
        self.entries = {}  # item key -> an entry naming it (name, icon)
        seen = {}
        by_key = {}
        for order, pattern in enumerate(patterns):
            where = (tuple(sorted(pattern["provider"].items())), pattern.get("slot"))
            seen[where] = seen.get(where, 0) + 1
            for entry in pattern["inputs"] + pattern["outputs"]:
                self.keys[id(entry)] = store.items.key_of(entry)
            p = _Pattern(pattern_id(pattern, seen[where]), pattern, self.key)
            for e in pattern["outputs"] + pattern["inputs"]:
                self.entries.setdefault(self.keys[id(e)], e)
            slot = pattern.get("slot")
            rank = (-(slot if isinstance(slot, int) else -1), order)
            for key in p.outputs:
                by_key.setdefault(key, []).append((rank, p))
        self.candidates = {
            key: [p for _, p in sorted(entries, key=lambda e: e[0])]
            for key, entries in by_key.items()
        }
        self.stand_ins = {
            key: [p for p in candidates if p.be_substitute]
            for key, candidates in self.candidates.items()
            if any(p.be_substitute for p in candidates)
        }
        self.alternatives = {
            key: [
                {
                    "id": p.id,
                    "provider": p.pattern["provider"].get("name"),
                    "inputs": ", ".join(f"{_qty(e.get('size') or 1)} {e.get('name')}" for e in p.pattern["inputs"]),
                }
                for p in candidates
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
    return candidates[0].outputs[key][0] if candidates else None


def _item_fields(entry, key):
    # The icon is read from the entry each time, not kept in the index:
    # new game data replaces it in place.
    out = {"key": key, "name": entry.get("name"), "kind": entry.get("kind") or "item"}
    for field in ("icon", "variant_name"):
        if entry.get(field):
            out[field] = entry[field]
    return out


_ABSENT = object()


class _Journal:
    """Every change to the planner's running state, so a pattern tried
    and found short can be taken back (rollback to a mark())."""

    def __init__(self):
        self.log = []

    def set(self, d, k, v):
        self.log.append((d, k, d.get(k, _ABSENT)))
        d[k] = v

    def add(self, d, k, n):
        self.set(d, k, d.get(k, 0) + n)

    def mark(self):
        return len(self.log)

    def rollback(self, mark):
        log = self.log
        while len(log) > mark:
            d, k, old = log.pop()
            if old is _ABSENT:
                del d[k]
            else:
                d[k] = old


class _Planner:
    """Works the plan out as AE2 does (see the module docstring). Each
    step is a generator that yields the inputs it needs - (entry, amount,
    patterns in use above) - and is sent back (node, how much of it can
    really be had), so run() can drive any depth without recursion."""

    def __init__(self, patterns, stock, choices, describe=None, storage=None, prefer=None):
        """storage(key) and prefer(pattern), for rebuilding a running
        job's tree (reconstruct()): whether a step's item came out of
        storage in full rather than being crafted, and whether a pattern
        is one the job is using."""
        self.describe = describe or (lambda key: None)
        self.storage = storage
        self.prefer = prefer
        self.index = index(patterns)
        self.available = dict(stock)
        self.leftovers = {}
        self.stock = stock
        self.choices = choices
        self.journal = _Journal()
        self.count = {"nodes": 0}  # in the journal: tried steps taken back don't count
        self.work = 0              # every step planned, kept or not
        self.truncated = False
        self.items = {}  # key -> totals for the list view
        self._known = None
        self._equivalents = {}

    def equivalents(self, key):
        """The items sharing an ore-dictionary name with key that the plan
        could have: in stock, or made by a pattern."""
        if not oredict.loaded():
            return []
        found = self._equivalents.get(key)
        if found is None:
            if self._known is None:
                self._known = oredict.Known(set(self.stock) | set(self.index.candidates))
            found = self._equivalents[key] = oredict.equivalents(key, self._known)
        return found

    @property
    def nodes(self):
        return self.count["nodes"]

    def totals(self, entry, key):
        t = self.items.get(key)
        if t is None:
            t = {
                **_item_fields(entry, key),
                "available": self.stock.get(key, 0),
                "need": 0, "from_stock": 0, "from_leftovers": 0, "craft": 0, "missing": 0,
            }
            self.journal.set(self.items, key, t)
        return t

    def run(self, target, amount):
        stack = [self.resolve(target, amount, frozenset(), is_root=True)]
        value = None
        while stack:
            try:
                request = stack[-1].send(value)
            except StopIteration as done:
                stack.pop()
                value = done.value
                continue
            value = None
            stack.append(self.resolve(*request))
        return value[0]

    def take(self, pool, key, field, node, totals, rest):
        have = pool.get(key, 0)
        taken = min(have, rest)
        if taken > 0:
            self.journal.set(pool, key, have - taken)
            node[field] = node.get(field, 0) + taken
            self.journal.add(totals, field, taken)
        return rest - taken

    def take_substitutes(self, equivalents, node, rest):
        """AE2's fuzzy extraction: each alternative from leftovers, then
        each from stock. Listed on the node (`substitutes`), and counted
        in the alternative's own totals."""
        for pool, field in ((self.leftovers, "from_leftovers"), (self.available, "from_stock")):
            for k in equivalents:
                if rest <= 0:
                    return rest
                have = pool.get(k, 0)
                if have <= 0:
                    continue
                taken = min(have, rest)
                self.journal.set(pool, k, have - taken)
                rest -= taken
                entry = self.index.entries.get(k) or self.describe(k) or {"name": k.split("|")[1]}
                totals = self.totals(entry, k)
                self.journal.add(totals, "need", taken)
                self.journal.add(totals, field, taken)
                node.setdefault("substitutes", []).append({**_item_fields(entry, k), field: taken})
        return rest

    def resolve(self, entry, need, used, fuzzy=False, is_root=False):
        """One step: `need` of entry - or, with fuzzy, of anything sharing
        an ore-dictionary name with it. Returns (node, how much of it can
        be had without anything missing)."""
        key = self.index.key(entry)
        j = self.journal
        j.add(self.count, "nodes", 1)
        self.work += 1
        node = {**_item_fields(entry, key), "need": need, "from_stock": 0}
        totals = self.totals(entry, key)
        j.add(totals, "need", need)

        rest = need
        equivalents = self.equivalents(key) if fuzzy and self.storage is None else []
        if not is_root and self.storage is not None:
            if self.storage(key):
                node["from_stock"] = need
                j.add(totals, "from_stock", need)
                rest = 0
            else:
                rest = self.take(self.leftovers, key, "from_leftovers", node, totals, rest)
        elif not is_root:
            rest = self.take(self.leftovers, key, "from_leftovers", node, totals, rest)
            rest = self.take(self.available, key, "from_stock", node, totals, rest)
            if rest > 0 and equivalents:
                rest = self.take_substitutes(equivalents, node, rest)
        if rest <= 0:
            node["status"] = "stock"
            return node, need
        got = need - rest

        every = [(p, key) for p in self.index.candidates.get(key, [])]
        # Then, as AE2 does for a substitutable input, the patterns marked
        # "can be substituted" making an alternative, in their own order.
        stand_ins = sorted(
            ((p, k) for k in equivalents for p in self.index.stand_ins.get(k, [])),
            key=lambda c: -(c[0].pattern.get("slot") if isinstance(c[0].pattern.get("slot"), int) else -1),
        )
        seen = {p.id for p, _ in every}
        for p, k in stand_ins:
            if p.id not in seen:
                seen.add(p.id)
                every.append((p, k))
        candidates = [(p, k) for p, k in every if p.id not in used]
        chosen = self.choices.get(key)
        if chosen and any(p.id == chosen for p, _ in candidates):
            candidates = [(p, k) for p, k in candidates if p.id == chosen]
        elif self.prefer is not None:
            candidates.sort(key=lambda c: not self.prefer(c[0]))  # stable: AE2's order otherwise
        if not candidates:
            # Nothing makes it, or only patterns already in use above
            # (making it needs itself): either way, the rest isn't there.
            node["status"] = "cycle" if every else "missing"
            node["missing"] = rest
            j.add(totals, "missing", rest)
            return node, got

        parts = []
        for i, (p, out_key) in enumerate(candidates):
            if rest <= 0:
                break
            mark = j.mark()
            part, can = yield from self.craft(p, out_key, used, amount=rest)
            if part["made_for"] == rest and can >= rest:
                parts.append(part)
                rest = 0
                got += can
                break
            if len(candidates) == 1 or self.work > MAX_WORK:
                # Short, with nothing else to try: as AE2 does, the first
                # pattern takes it, and what it lacks is missing.
                if self.work > MAX_WORK:
                    self.truncated = True
                if i == 0:
                    parts.append(part)
                    got += can
                    rest = 0
                    break
                j.rollback(mark)
                break
            # Short: keep only the whole batches it can make, and leave
            # the rest to the next pattern.
            j.rollback(mark)
            per = p.outputs[out_key][1]
            batches = can // per
            if batches > 0:
                part, can = yield from self.craft(p, out_key, used, batches=batches)
                parts.append(part)
                rest -= part["made_for"]
                got += min(can, part["made_for"])
        if rest > 0:
            part, can = yield from self.craft(candidates[0][0], candidates[0][1], used, amount=rest)
            parts.append(part)
            got += min(can, part["made_for"])

        node["status"] = "craft"
        # A part making an alternative is always its own row, to show which.
        if len(parts) == 1 and parts[0]["makes"] == key:
            part = parts[0]
            del part["made_for"], part["makes"]
            node.update(part)
            if key in self.index.alternatives:
                node["alternatives"] = self.index.alternatives[key]
        else:
            children = []
            for part in parts:
                makes = part.pop("makes")
                del part["made_for"]
                fields = _item_fields(entry, key) if makes == key else {
                    **_item_fields(self.index.entries[makes], makes), "substitute": True}
                children.append({**fields, "status": "via", **part})
            node.update(craft=sum(part["craft"] for part in parts), split=True, children=children)
        return node, got

    def craft(self, p, key, used, amount=None, batches=None):
        """Plans pattern p making key: enough batches for `amount`, or
        exactly `batches`. Returns (part, how much of key it can really
        make): whole batches its inputs can be had for."""
        j = self.journal
        per = p.outputs[key][1]
        if batches is None:
            batches = math.ceil(amount / per)
        made = batches * per
        made_for = made if amount is None else min(made, amount)
        pattern = p.pattern
        part = {
            "craft": made,
            "batches": batches,
            "pattern": {"id": p.id, "provider": pattern["provider"].get("name"), "crafting": pattern["crafting"]},
            "children": [],
            "made_for": made_for,
            "makes": key,
        }
        totals = self.totals(p.outputs[key][0], key)
        j.add(totals, "craft", made)
        if made > made_for:
            j.add(self.leftovers, key, made - made_for)
        if not p.outputs[key][0].get("size"):
            part["inexact"] = True
        also = []
        for k, (e, size) in p.outputs.items():
            if k != key:
                also.append({**_item_fields(e, k), "amount": size * batches})
                j.add(self.leftovers, k, size * batches)
        if also:
            part["also_makes"] = also

        if self.nodes >= MAX_NODES:
            # Its siblings still get a row each, unexpanded.
            self.truncated = True
            part["truncated"] = True
            return part, made
        below = used | {p.id}
        can = batches
        for k, e, size in p.once:
            child, got = yield (e, size, below, p.substitute)
            part["children"].append(child)
            if got < size:
                can = 0
        for k, e, size in p.inputs:
            child, got = yield (e, size * batches, below, p.substitute)
            part["children"].append(child)
            can = min(can, got // size)
        return part, can * per


def _qty(n):
    return f"{n:g}" if isinstance(n, float) else str(n)


def plan(patterns, stock, key, amount, choices=None, rules=None, describe=None):
    """The plan for `amount` of the item `key`, or None if no pattern
    makes it. stock: {key: size}. choices: {key: pattern id} picking one
    pattern for an item instead of AE2's order. rules: [(key, threshold,
    text)] - a stock rule's level, warned about if the plan would take
    the item from at or above it to below it. One already below isn't:
    the plan didn't cause that. describe(key): an entry (name, icon) for
    an item only in stock, for a substitute taken from there."""
    target = find_output(patterns, key)
    if target is None:
        return None
    planner = _Planner(patterns, stock, choices or {}, describe)
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


def reconstruct(patterns, key, amount, storage, prefer):
    """A running job's tree, rebuilt from the patterns: AE2 doesn't tell
    OC a job's own plan, only the items it's crafting and holding (see
    gcm/job_tree.py). storage(key): whether the job took that item from
    storage rather than crafting it - crafted ones are crafted in full.
    prefer(pattern): whether the job is using that pattern, tried before
    AE2's order. None if no pattern makes the item."""
    target = find_output(patterns, key)
    if target is None:
        return None
    planner = _Planner(patterns, {}, {}, storage=storage, prefer=prefer)
    return planner.run(target, amount), planner.nodes, planner.truncated


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


def cached_plan(patterns, stock, stock_version, key, amount, choices=None, rules=None, describe=None):
    """plan(), reusing one of the last few if nothing it depends on has
    changed: the pattern list, the stock (stock_version says when it
    last changed), and the request itself."""
    choices = choices or {}
    rules = rules or []
    cache_key = (stock_version, oredict.version(), key, amount, tuple(sorted(choices.items())), tuple(rules))
    with _plan_lock:
        hit = _plan_cache.get(cache_key)
        if hit is not None and hit[0] is patterns:
            _plan_cache.move_to_end(cache_key)
            return hit[1]
    result = plan(patterns, stock, key, amount, choices, rules, describe)
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
