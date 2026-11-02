"""Crafts tab statistics: totals, the most crafted items and how busy
each CPU was, from the craft_events log of jobs that ended. Jobs still
running count once they end, and a job's time only counts from when
the server saw it start (started_at), clipped to the range."""

import statistics

from gcm import icons, store

TOP_ITEMS = 10


def summarize(since, now):
    ended = store.crafts.ended_stats(since, TOP_ITEMS)
    if since is None:
        start = ended["first_start"] if ended["total"] else now
    else:
        start = since
    window = max(now - start, 1)

    top = []
    for item in ended["items"]:
        durations = item["durations"]
        top.append({
            "itemName": item["label"], "mod": item["mod"], "internal": item["internal"],
            "damage": item["damage"], "count": item["count"], "incomplete": item["incomplete"],
            "icon": icons.current_path(item["icon"]),
            "medianSeconds": statistics.median(durations) if durations else None,
        })
    cpu_list = [{"cpu": cpu, "count": count, "busySeconds": busy} for cpu, count, busy in ended["cpus"]]
    cpu_list.sort(key=lambda c: (-c["busySeconds"], c["cpu"]))
    for entry in cpu_list:
        entry["busyFraction"] = min(1.0, entry["busySeconds"] / window)
    return {
        "windowSeconds": window,
        "total": ended["total"],
        "incomplete": ended["incomplete"],
        "auto": ended["auto"],
        "items": top,
        "distinctItems": ended["distinct_items"],
        "cpus": cpu_list,
    }
