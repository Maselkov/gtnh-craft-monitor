"""Crafts tab statistics: totals, the most crafted items and how busy
each CPU was, from the craft_events log of jobs that ended. Jobs still
running count once they end, and a job's time only counts from when
the server saw it start (started_at), clipped to the range."""

import statistics

from gcm import icons, store

TOP_ITEMS = 10


def _item_key(label, mod, internal, damage):
    # Rows from before items were recorded only have a name.
    return (mod, internal, damage) if internal else ("", label, None)


def summarize(since, now):
    rows = store.crafts.ended_since(since)
    if since is None:
        starts = [r[5] if r[5] is not None else r[4] for r in rows]
        start = min(starts) if starts else now
    else:
        start = since
    window = max(now - start, 1)

    items, cpus = {}, {}
    incomplete = auto = 0
    for cpu, label, icon, status, ended_at, started_at, mod, internal, damage, is_auto in rows:
        incomplete += status == "incomplete"
        auto += bool(is_auto)
        item = items.setdefault(_item_key(label, mod, internal, damage), {
            "itemName": label, "mod": mod, "internal": internal, "damage": damage,
            "count": 0, "incomplete": 0, "durations": [],
        })
        item["count"] += 1
        item["incomplete"] += status == "incomplete"
        item["icon"] = icon  # the latest one
        entry = cpus.setdefault(cpu, {"cpu": cpu, "count": 0, "busySeconds": 0.0})
        entry["count"] += 1
        if started_at is not None:
            item["durations"].append(ended_at - started_at)
            entry["busySeconds"] += max(0.0, ended_at - max(started_at, start))

    top = sorted(items.values(), key=lambda i: (-i["count"], i["itemName"] or ""))[:TOP_ITEMS]
    for item in top:
        durations = item.pop("durations")
        item["medianSeconds"] = statistics.median(durations) if durations else None
        item["icon"] = icons.current_path(item["icon"])
    cpu_list = sorted(cpus.values(), key=lambda c: (-c["busySeconds"], c["cpu"]))
    for entry in cpu_list:
        entry["busyFraction"] = min(1.0, entry["busySeconds"] / window)
    return {
        "windowSeconds": window,
        "total": len(rows),
        "incomplete": incomplete,
        "auto": auto,
        "items": top,
        "distinctItems": len(items),
        "cpus": cpu_list,
    }
