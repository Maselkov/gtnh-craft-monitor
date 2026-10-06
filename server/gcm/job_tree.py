"""The live crafting tree in a busy CPU's details: what the job is
making, step by step, and how far each step has got.

AE2 doesn't tell OpenComputers a running job's plan, only the items its
CPU is crafting (active), still has to craft (pending) and holds
(stored). So the tree is rebuilt from the network's patterns
(planner.reconstruct()), guided by those lists:

- An item the job crafts is one craft_monitor.lua has reported pending
  or active (the job's progress steps, gcm/progress.py); anything else
  came out of storage.
- Where several patterns make an item, the one whose inputs are all
  among the job's items is taken first - the CPU pulled the raw inputs
  in its first report (`first_stored`), and crafts the rest.

Steps are matched to the tree by item: a step's key, (mod, internal,
damage, name), becomes the item key without NBT variant
(store.items.item_key), which every node also carries as `step`. An
item two branches use has one count for both, as the CPU reports it.

Live numbers (steps()) come from the job's tracking entry
(gcm/tracking.py): each step's peak, what's left, and when that last
changed."""

import threading
import time
from collections import OrderedDict

from gcm import config, patterns, planner, progress, state, store

# Positions listed per step for the dialog to jump to, at most.
MAX_JUMPS = planner.MAX_JUMPS
# Jobs whose trees are kept; one per CPU being watched is plenty.
TREE_CACHE_SIZE = 4

_lock = threading.Lock()
_cache = OrderedDict()  # (cpu, started_at, step count) -> (pattern list, tree)


def base_key(key):
    """An item key without its NBT variant - what a CPU's item lists can
    tell apart."""
    return "|".join(key.split("|")[:4])


def step_item_key(step):
    """A progress step key, (mod, internal, damage, name), as an item key.
    A stack with no mod is a fluid, as craft_monitor.lua reports one."""
    mod, internal, damage, _ = step
    return store.items.item_key(mod, internal, damage if mod else None, "item" if mod else "fluid")


def _entry(cpu):
    """A copy of what tracking knows of the CPU's job, or None if idle."""
    with state.tracking_lock:
        if not state.cpu_last_busy.get(cpu):
            return None
        entry = state.cpu_last_known.get(cpu)
        if entry is None:
            return None
        return {
            "started_at": entry["started_at"],
            "output": entry["output"],
            "peaks": dict(entry["peaks"]),
            "last_left": dict(entry["last_left"]),
            "moved_at": dict(entry["moved_at"]),
            "first_stored": dict(entry["first_stored"] or {}),
        }


def version(cpu):
    """Changes whenever tree() would build a different tree: a new job,
    new steps seen, or a new pattern scan. None when the CPU is idle."""
    entry = _entry(cpu)
    if entry is None:
        return None
    _, patterns_at = patterns.current()
    return f"{entry['started_at']}|{len(entry['peaks'])}|{patterns_at}"


def _output_key(entry, index):
    """The item key of the job's output among the patterns' outputs,
    or None."""
    output = entry["output"]
    if not output:
        return None
    _, mod, internal, damage = output
    key = store.items.item_key(mod, internal, damage if mod else None, "item" if mod else "fluid")
    if key in index.candidates:
        return key
    # An NBT variant: the patterns' key has its variant on the end.
    return next((k for k in index.candidates if base_key(k) == key), None)


def _annotate(root):
    """Sets `step` (its base key) on every node, and returns
    {step: [child positions]} of the crafted ones - where the dialog can
    jump to a step. Without recursion, like the rest."""
    at = {}
    stack = [(root, "")]
    while stack:
        node, pos = stack.pop()
        node["step"] = base_key(node["key"])
        if node.get("status") in ("craft", "via") and node.get("pattern"):
            places = at.setdefault(node["step"], [])
            if len(places) < MAX_JUMPS:
                places.append(pos)
        children = node.get("children") or []
        for i in range(len(children) - 1, -1, -1):
            stack.append((children[i], f"{pos}.{i}" if pos else str(i)))
    return at


def build(entry, pattern_list):
    """The job's tree, or None if no pattern makes its output."""
    index = planner.index(pattern_list)
    key = _output_key(entry, index)
    if key is None:
        return None
    crafted = {step_item_key(s) for s in entry["peaks"]}
    items = crafted | {step_item_key(s) for s in entry["first_stored"]}
    output_steps = [p for s, p in entry["peaks"].items() if step_item_key(s) == base_key(key)]
    amount = max(output_steps) if output_steps else 1

    def from_storage(k):
        return base_key(k) not in crafted

    def used_by_job(p):
        return all(base_key(k) in items for k, _, _ in p.inputs) and all(base_key(k) in items for k, _, _ in p.once)

    built = planner.reconstruct(pattern_list, key, amount, from_storage, used_by_job)
    if built is None:
        return None
    root, steps, truncated = built
    return {"root": root, "at": _annotate(root), "steps": steps, "truncated": truncated}


def tree(cpu):
    """(tree, version) for the CPU's job - (None, reason) when there's
    none to show."""
    entry = _entry(cpu)
    if entry is None:
        return None, "This CPU isn't crafting anything."
    pattern_list, patterns_at = patterns.current()
    if patterns_at is None:
        return None, "No pattern scan yet: the tree is rebuilt from the network's patterns."
    if not entry["output"]:
        return None, "The CPU doesn't say what it's making (no Crafting Monitor on it)."
    cache_key = (cpu, entry["started_at"], len(entry["peaks"]))
    with _lock:
        hit = _cache.get(cache_key)
        if hit is not None and hit[0] is pattern_list:
            _cache.move_to_end(cache_key)
            return hit[1], None
    result = build(entry, pattern_list)
    if result is None:
        return None, "None of the network's patterns makes what this CPU is making."
    with _lock:
        _cache[cache_key] = (pattern_list, result)
        _cache.move_to_end(cache_key)
        while len(_cache) > TREE_CACHE_SIZE:
            _cache.popitem(last=False)
    return result, None


def step_state(left, crafting, moved_at, now):
    """done, stuck (crafting, but not moved for CRAFT_STALL_SECONDS),
    active (crafting), or waiting (still to come)."""
    if left <= 0:
        return "done"
    if crafting > 0:
        if moved_at is not None and now - moved_at >= config.CRAFT_STALL_SECONDS:
            return "stuck"
        return "active"
    return "waiting"


def steps(cpu, now=None):
    """{item base key: {name, total, left, crafting, moved_at, state}} for the
    CPU's job, or None if it's idle. left is from the freshest look at
    the job (tracking also folds in craft_monitor.lua's quick checks);
    crafting from the last full report's active list."""
    entry = _entry(cpu)
    if entry is None:
        return None
    now = time.time() if now is None else now
    with state.crafts_lock:
        job = next((j for j in state.crafts["jobs"] if j.get("name") == cpu), None)
    active = progress.amounts((job or {}).get("active"))
    out = {}
    for step, peak in entry["peaks"].items():
        k = step_item_key(step)
        s = out.setdefault(k, {"name": step[3], "total": 0, "left": 0, "crafting": 0, "moved_at": None})
        s["total"] += peak
        s["left"] += entry["last_left"].get(step, 0)
        s["crafting"] += active.get(step, 0)
        moved = entry["moved_at"].get(step)
        if moved is not None and (s["moved_at"] is None or moved > s["moved_at"]):
            s["moved_at"] = moved
    for s in out.values():
        s["state"] = step_state(s["left"], s["crafting"], s["moved_at"], now)
    return out


def reset():
    """Forgets cached trees (tests)."""
    with _lock:
        _cache.clear()
