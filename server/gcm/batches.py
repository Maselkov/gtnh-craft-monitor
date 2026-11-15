"""Batches: several items requested together, to be planned as one job.

AE2 plans each requested craft on its own, against what's in storage as
it starts, so two components that need the same circuits can each plan
fine and one then come up short; nor does it share leftovers between
them. A batch goes through the same kit pattern a multiblock kit does
(commands.queue_pattern_job): its inputs are the batch's items, its
output the first item named "<that item> batch kit", so AE2 plans them
all as one job. Without a kit interface, or when the player asks, each
item is queued as its own craft instead.

Each account has one batch being put together, kept on the server
(store.batches) so it follows them between browsers: rows of {key,
amount, name?, from?}, where key is the network's item key (items and
fluids, no NBT variants: a kit pattern's inputs can't carry NBT),
amount how many to craft, name the name it had when added (for an item
the network no longer has), and from the kit it was added from, shown
beside it.
"""

from gcm import commands, icons, mctext, state, store

# A kit pattern takes up to 512 inputs; the plan and the separate
# requests take 100 (routes/network.py, routes/craft_requests.py).
MAX_ROWS = 100
MAX_AMOUNT = 10**12
MAX_TEXT = 200


def split_key(key):
    """(mod, internal, damage, kind) of a batchable item key, or None."""
    if not isinstance(key, str):
        return None
    parts = key.split("|")
    if len(parts) != 4 or not parts[1] or parts[3] not in ("item", "fluid"):
        return None
    mod, internal, damage, kind = parts
    if kind == "item":
        if not mod:
            return None
        try:
            damage = int(damage or 0)
        except ValueError:
            return None
    else:
        damage = None
    return mod or None, internal, damage, kind


def parse(raw):
    """The rows of a batch as sent ([{key, amount, name?, from?}]),
    merged by key, and an error or None."""
    if not isinstance(raw, list):
        return None, "items must be a list"
    rows = {}
    for i, r in enumerate(raw):
        if not isinstance(r, dict) or split_key(r.get("key")) is None:
            return None, f"item {i + 1} isn't a batchable item key"
        amount = r.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= MAX_AMOUNT:
            return None, f"item {i + 1}: amount must be a whole number from 1"
        row = rows.get(r["key"])
        if row:
            row["amount"] = min(MAX_AMOUNT, row["amount"] + amount)
            continue
        row = rows[r["key"]] = {"key": r["key"], "amount": amount}
        for field in ("name", "from"):
            if isinstance(r.get(field), str) and r[field].strip():
                row[field] = r[field].strip()[:MAX_TEXT]
    if len(rows) > MAX_ROWS:
        return None, f"a batch holds at most {MAX_ROWS} items"
    return list(rows.values()), None


def merge(rows, added):
    """rows with added put on the end, amounts of an item already there
    added up; and an error or None."""
    return parse([*rows, *added])


def _network():
    with state.network_lock:
        items = list(state.network["items"])
    return {store.items.key_of(it): it for it in items if not it.get("variant")}


def describe(rows):
    """The rows with what the page shows of them: mod, internal, damage,
    kind, name, icon, have (the last scan's stock) and craftable."""
    network = _network()
    out = []
    for r in rows:
        mod, internal, damage, kind = split_key(r["key"])
        it = network.get(r["key"])
        name = (it and it.get("name")) or r.get("name") or internal
        out.append({
            **r,
            "mod": mod,
            "internal": internal,
            "damage": damage,
            "kind": kind,
            "name": name,
            "icon": (it and it.get("icon")) or icons.resolve_icon(mod, internal, damage, name, kind=kind),
            "have": (it and it.get("size")) or 0,
            "craftable": bool(it and it.get("isCraftable")),
        })
    return out


def capabilities():
    """What the game can do with a batch: {kit_slots, kit_fluids}."""
    return {"kit_slots": state.kit_slots(), "kit_fluids": state.kit_fluids()}


def check(rows, one_job):
    """Why these described rows can't be requested as asked, or None."""
    if not rows:
        return "nothing to request"
    for r in rows:
        if not r["craftable"]:
            return f"{mctext.plain(r['name'])}: the network has no pattern for it"
    if not one_job:
        return None
    if not state.kit_slots():
        return ("The game has no kit interface to request a batch through as one job - "
                "request each item on its own instead")
    if not any(r["kind"] == "item" for r in rows):
        return "a batch made as one job needs at least one item, not only fluids"
    if not state.kit_fluids() and any(r["kind"] == "fluid" for r in rows):
        return ("this craft_monitor.lua can't put fluids in a batch: update it, "
                "or request each item on its own")
    return None


def job_name(rows):
    """The plain name a batch's job goes by, before " kit": its first
    item's, as that's its output."""
    first = next(r for r in rows if r["kind"] == "item")
    return f"{mctext.plain(first['name'])} batch"


def queue_job(user_id, rows, source="manual"):
    """Queues described rows as one job; returns (request id, name).

    AE2 takes a pattern's inputs from storage first and crafts only the
    rest, so each input is what's in stock of the item plus how many to
    craft: all of the stock, or what's in stock would be used instead of
    crafted. It comes back into the network with the rest when the job
    ends. The output is the first item (craft_monitor.lua names it), so
    the Crafts tab shows the job with its icon."""
    first = next(r for r in rows if r["kind"] == "item")
    inputs = [{**r, "amount": r["have"] + r["amount"]} for r in rows]
    name = job_name(rows)
    req_id = commands.queue_pattern_job(user_id, name, first["mod"], first["internal"], first["damage"], inputs,
                                        source)
    return req_id, commands.kit_label(name)


def queue_separately(user_id, rows, source="manual"):
    """Queues each described row as its own craft; returns their ids."""
    return [
        commands.queue_craft(user_id, mctext.plain(r["name"]), r["mod"], r["internal"], r["damage"], r["amount"],
                             r["kind"], source=source)
        for r in rows
    ]
