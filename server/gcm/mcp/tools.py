"""The tools /mcp offers an AI assistant: what the page shows, as compact
JSON a language model reads well. Each one runs the same code the
page's routes do (gcm.stock, gcm.planner, gcm.inventory...), with the
signed-in user's role.

Outputs are kept small on purpose: they land in the model's context. So
icons are dropped, lists are capped, histories are cut down to a few
dozen points, and times are ISO 8601 (UTC) strings. Items are named by
their item key (store.items.item_key: "mod|internal|damage|kind" and
"|variant" for an NBT variant), which search_items returns: the other
tools take that, never a name, so a request can't land on the wrong one
of several items called alike."""

import datetime
import difflib
import json
import logging
import math
import re
import time

from gcm import (
    auth, bottlenecks, commands, config, craft_stats, gamedata, inventory, item_info, job_links, patterns, planner, rates,
    mctext, multiblocks, recipe_index, recipes, search, state, stock, store, tracking,
)

log = logging.getLogger(__name__)


class ToolError(Exception):
    """A tool call that can't be answered: its message goes back to the
    model as the tool's result, marked as an error."""


# ---------------------------------------------------------------------
# Helpers shared by the tools.

def _iso(t):
    if t is None:
        return None
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat(timespec="seconds")


def _unit(kind):
    return {"fluid": "mB", "essentia": "essentia"}.get(kind, "items")


def _round(n, places=2):
    return None if n is None else round(n, places)


def _compact_numbers(value):
    """Numbers as a model needs them: whole ones without a ".0", the
    rest to about three significant figures (sizes are whole numbers;
    rates and EU totals don't need sixteen digits). And text without
    Minecraft's formatting codes."""
    if isinstance(value, dict):
        return {k: _compact_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_compact_numbers(v) for v in value]
    if isinstance(value, str) and "\u00a7" in value:
        # Minecraft's colour codes (§a...), which names can carry.
        return mctext.plain(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        if value.is_integer() or abs(value) >= 1000:
            return int(round(value))
        return float(f"{value:.3g}")
    return value


def _without_icons(value):
    if isinstance(value, dict):
        return {k: _without_icons(v) for k, v in value.items() if not k.endswith("icon")}
    if isinstance(value, list):
        return [_without_icons(v) for v in value]
    return value


def _parse_key(key):
    """(mod, internal, damage, kind, variant) from an item key."""
    if not isinstance(key, str):
        raise ToolError("item must be an item key from search_items")
    parts = key.split("|", 4)
    if len(parts) < 4 or not parts[1]:
        raise ToolError(f"{key!r} isn't an item key: use the `item` value search_items returns")
    mod, internal, damage, kind = parts[:4]
    variant = parts[4] if len(parts) == 5 else None
    if damage == "":
        damage = None
    else:
        try:
            damage = int(damage)
        except ValueError:
            raise ToolError(f"{key!r} isn't an item key: use the `item` value search_items returns") from None
    return mod or None, internal, damage, kind or "item", variant or None


def _network_item(key):
    """The last scan's entry for an item key, or None."""
    return inventory.describe(key)


def _item_summary(item, key=None):
    """How an item from the network snapshot is shown to the model."""
    out = {
        "item": key or store.items.key_of(item),
        "name": item.get("name"),
        "amount": item.get("size") or 0,
        "unit": _unit(item.get("kind")),
        "craftable": bool(item.get("isCraftable")),
    }
    if item.get("variant_name"):
        out["variant_name"] = item["variant_name"]
    return out


def _trend(trend, per="hour"):
    """rates' {per_second, seconds_to_empty...} as per-hour numbers."""
    if not trend:
        return None
    out = {f"per_{per}": _round(trend["per_second"] * (3600 if per == "hour" else 1))}
    for field in ("seconds_to_empty", "seconds_to_full"):
        if trend.get(field) is not None:
            out[field.replace("seconds_", "hours_")] = _round(trend[field] / 3600, 1)
    return out


def _thin(rows, max_points):
    """Every nth row, so at most max_points are left, always keeping the
    last (the current value)."""
    if len(rows) <= max_points:
        return rows
    step = len(rows) / max_points
    picked = [rows[int(i * step)] for i in range(max_points - 1)]
    return picked + [rows[-1]]


def _arg(args, name, kind, default=None, choices=None, minimum=None, maximum=None, required=False):
    value = args.get(name, default)
    if value is None:
        if required:
            raise ToolError(f"{name} is required")
        return None
    if kind in ("integer", "number"):
        # JSON allows 1e400, which Python reads as infinity.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ToolError(f"{name} must be a number")
        if kind == "integer":
            if value != int(value):
                raise ToolError(f"{name} must be a whole number")
            value = int(value)
    elif kind == "string":
        if not isinstance(value, str):
            raise ToolError(f"{name} must be a string")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ToolError(f"{name} must be true or false")
    if choices is not None and value not in choices:
        raise ToolError(f"{name} must be one of: {', '.join(choices)}")
    if minimum is not None and value < minimum:
        raise ToolError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ToolError(f"{name} must be at most {maximum}")
    return value


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


ITEM_ARG = {"type": "string", "description": "An item key, the `item` value search_items returns."}


def _range_arg(choices, default, what):
    return {"type": "string", "enum": list(choices), "default": default, "description": what}


# ---------------------------------------------------------------------
# Network (ME storage)

SEARCH_LIMIT_MAX = 50
_DEEP_SEARCH_KINDS = {"tooltip", "ore", "formula"}


def _rank(item, query):
    name = mctext.plain(item.get("name") or "").lower()
    q = query.strip().lower()
    if name == q:
        return 0
    if name.startswith(q):
        return 1
    if q in name:
        return 2
    return 3


def search_items(user, args):
    query = _arg(args, "query", "string", required=True)
    limit = _arg(args, "limit", "integer", 20, minimum=1, maximum=SEARCH_LIMIT_MAX)
    craftable_only = _arg(args, "craftable_only", "boolean", False)
    terms = search.parse(query)
    if not terms:
        raise ToolError("query is empty")
    # Tooltips and ore names are only looked up for a query that asks
    # about them: a plain one matches names, as people mean it.
    deep = any(kind in _DEEP_SEARCH_KINDS for kind, _, _ in terms)
    with state.network_lock:
        items = state.network["items"]
        scanned_at = state.network["updated_at"]
    found = []
    for item in items:
        if craftable_only and not item.get("isCraftable"):
            continue
        info = item_info.describe(item) if deep else {}
        if search.matches(
            item.get("name"), item.get("mod"), terms, info.get("description"), info.get("ore", ()),
            item_info.mod_name(item.get("mod")), info.get("formula"),
        ):
            found.append(item)
    found.sort(key=lambda it: (_rank(it, query), len(mctext.plain(it.get("name") or "")), -(it.get("size") or 0)))
    approximate = False
    if not found and all(kind in ("text", "or") and not negate for kind, negate, _ in terms):
        found = _approximate(items, query, craftable_only)
        approximate = bool(found)
    out = {
        "scanned_at": _iso(scanned_at),
        "matches": len(found),
        "items": [_item_summary(it) for it in found[:limit]],
    }
    if approximate:
        out["approximate"] = True
        out["note"] = "Nothing matched exactly; these are close. Check it's the item meant before using it."
    return out


_SQUASH = re.compile(r"[\s\-_'.]+")
APPROXIMATE_WORD_RATIO = 0.8


def _words(text):
    return [w for w in re.split(r"[\s\-_]+", mctext.plain(text or "").lower()) if w]


def _approximate(items, query, craftable_only):
    """Items close to a query nothing matched: its words joined or split
    differently ("inconel625" for "Inconel-625"), or misspelt
    ("tungstenstel"). Best first."""
    query_words = _words(query)
    squashed_words = [_SQUASH.sub("", w) for w in query_words]
    scored = []
    for item in items:
        if craftable_only and not item.get("isCraftable"):
            continue
        name = mctext.plain(item.get("name") or "").lower()
        if all(w in _SQUASH.sub("", name) for w in squashed_words):
            scored.append((0, item))
            continue
        name_words = _words(name)
        ratios = [max((difflib.SequenceMatcher(None, w, n).ratio() for n in name_words), default=0)
                  for w in query_words]
        if ratios and min(ratios) >= APPROXIMATE_WORD_RATIO:
            scored.append((1 - sum(ratios) / len(ratios), item))
    scored.sort(key=lambda s: (s[0], len(s[1].get("name") or "")))
    return [item for _, item in scored]


def get_item(user, args):
    key = _arg(args, "item", "string", required=True)
    mod, internal, damage, kind, variant = _parse_key(key)
    item = _network_item(key)
    if item is None:
        raise ToolError("That item isn't in the network's last scan. item_history still has its past amounts.")
    out = _item_summary(item, key)
    info = item_info.describe(item)
    if info.get("description"):
        out["description"] = info["description"][:600]
    if info.get("ore"):
        out["ore_dictionary"] = info["ore"][:10]
    out["mod_name"] = item_info.mod_name(mod)
    _, _, trend = inventory.history(mod, internal, damage, kind, "day", variant)
    out["trend"] = _trend(trend)
    target = next((t for t in store.stock.targets() if t["key"] == key), None)
    if target:
        target = {k: target[k] for k in ("keep_at_least", "refill_to", "enabled")}
    alert = next((a for a in store.stock.alerts_for_user(user["id"]) if a["key"] == key), None)
    out["keep_in_stock_target"] = target
    out["your_low_stock_alert"] = {"below": alert["below"]} if alert else None
    with state.crafts_lock:
        jobs = list(state.crafts["jobs"])
    identity = {"mod": mod, "internal": internal, "damage": damage}
    out["crafting_on_cpus"] = [
        j.get("name") for j in jobs
        if j.get("busy") and commands.output_identity(j) and commands.same_output(commands.output_identity(j), identity)
    ]
    # How it's kept stocked: AE2 jobs that made it (by hand, by a target,
    # or by a Level Maintainer, whose jobs run on CPUs too). Rising with
    # none of these means something outside AE2's crafting makes it.
    count, auto, last_at = store.crafts.item_crafts_since(mod, internal, damage, time.time() - 7 * 86400)
    out["crafts_last_week"] = {"jobs": count, "by_keep_in_stock_target": auto, "last_ended_at": _iso(last_at)}
    return out


HISTORY_RANGES = ("hour", "day", "week", "month", "lifetime")
HISTORY_POINTS = 48


def item_history(user, args):
    key = _arg(args, "item", "string", required=True)
    range_key = _arg(args, "range", "string", "day", choices=HISTORY_RANGES)
    mod, internal, damage, kind, variant = _parse_key(key)
    range_key, rows, trend = inventory.history(mod, internal, damage, kind, range_key, variant)
    item = _network_item(key)
    sizes = [size for _, size in rows]
    return {
        "item": key,
        "name": item.get("name") if item else None,
        "unit": _unit(kind),
        "range": range_key,
        "now": item.get("size") if item else None,
        "min": min(sizes) if sizes else None,
        "max": max(sizes) if sizes else None,
        "trend": _trend(trend),
        "points": [{"t": _iso(t), "amount": size} for t, size in _thin(rows, HISTORY_POINTS)],
    }


CHANGE_RANGES = ("day", "week")
CHANGE_SORTS = ("falling", "rising", "biggest")


def inventory_changes(user, args):
    range_key = _arg(args, "range", "string", "day", choices=CHANGE_RANGES)
    sort = _arg(args, "sort", "string", "biggest", choices=CHANGE_SORTS)
    limit = _arg(args, "limit", "integer", 25, minimum=1, maximum=100)
    range_key, changes = inventory.changes(range_key)
    # One pass over the snapshot: looking each item up would go through
    # all of it again for every changed item.
    with state.network_lock:
        by_key = {store.items.key_of(it): it for it in state.network["items"]}
    rows = []
    for key, change in changes.items():
        item = by_key.get(key)
        now = (item.get("size") or 0) if item else 0
        delta = now - change["from"]
        if (sort == "falling" and delta >= 0) or (sort == "rising" and delta <= 0):
            continue
        row = {
            "item": key,
            "name": item.get("name") if item else None,
            "unit": _unit(item.get("kind") if item else None),
            "was": change["from"],
            "now": now,
            "change": delta,
        }
        if change["seconds_to_empty"] is not None:
            row["hours_to_empty"] = _round(change["seconds_to_empty"] / 3600, 1)
        rows.append(row)
    if sort == "falling":
        rows.sort(key=lambda r: r["change"])
    elif sort == "rising":
        rows.sort(key=lambda r: -r["change"])
    else:
        rows.sort(key=lambda r: -abs(r["change"]))
    return {"range": range_key, "changed_items": len(rows), "items": rows[:limit]}


# ---------------------------------------------------------------------
# Crafting

def _started_by(cpu):
    """Who asked for the job on a CPU, as far as the server knows."""
    who = tracking.started_by(cpu)
    if who is None:
        return None
    kind, user_id = who
    if kind == "auto":
        return "a keep-in-stock target"
    return store.users.display_name(user_id) or "a deleted user"


def _job_link(cpu, first_seen):
    """The job's page, to share (gcm/job_links.py), or None."""
    return job_links.url(cpu, first_seen) if cpu and first_seen is not None else None


def current_crafts(user, args):
    with state.crafts_lock:
        jobs = list(state.crafts["jobs"])
        received_at = state.crafts["received_at"]
    busy, idle = [], []
    for job in jobs:
        if not job.get("busy"):
            idle.append(job.get("name"))
            continue
        busy.append({
            "cpu": job.get("name"),
            "making": job.get("final_output"),
            "job_id": _job_id(job),
            "started_by": _started_by(job.get("name")),
            "progress_percent": job.get("progress_percent"),
            "steps_done": job.get("steps_done"),
            "steps_total": job.get("steps_total"),
            "running_seconds": _round(job.get("running_seconds"), 0),
            "eta_seconds": _round(job.get("eta_seconds"), 0),
            "link": _job_link(job.get("name"), job.get("started_at")),
        })
    return {
        "reported_at": _iso(received_at),
        "report_age_seconds": _round(time.time() - received_at, 0) if received_at else None,
        "busy_cpus": busy,
        "idle_cpus": idle,
    }


JOB_STEPS_MAX = 40
_STEP_ORDER = {s: i for i, s in enumerate(bottlenecks.HELD + ("active", "waiting", "done"))}


def craft_job(user, args):
    cpu = _arg(args, "cpu", "string", required=True)
    with state.crafts_lock:
        job = next((j for j in state.crafts["jobs"] if j.get("name") == cpu), None)
    if job is None:
        raise ToolError(f"No CPU called {cpu!r}. current_crafts lists them.")
    steps = bottlenecks.steps(cpu) if job.get("busy") else None
    if steps is None:
        return {"cpu": cpu, "busy": False}
    rows = []
    for s in sorted(steps.values(), key=lambda s: (_STEP_ORDER.get(s.get("state"), 9), -(s.get("left") or 0))):
        row = {"name": s["name"], "state": s.get("state"), "total": s["total"], "left": s["left"], "crafting": s["crafting"]}
        if s.get("since"):
            row["held_up_since"] = _iso(s["since"])
        if s.get("machines"):
            row["machines"] = s["machines"][:5]
        if s.get("with"):
            row["machine_busy_with"] = s["with"]
        rows.append(row)
    return {
        "cpu": cpu,
        "busy": True,
        "making": job.get("final_output"),
        "progress_percent": job.get("progress_percent"),
        "states": "stalled/busy/blocked: held up (by a machine that isn't working, is busy for "
                  "another job, or has its output blocked); active: crafting; waiting: needs "
                  "earlier steps; done.",
        "steps_total": len(rows),
        "steps": rows[:JOB_STEPS_MAX],
    }


CRAFT_HISTORY_MAX = 50


def craft_history(user, args):
    query = _arg(args, "query", "string", "")
    status = _arg(args, "status", "string", None, choices=("auto", "incomplete"))
    limit = _arg(args, "limit", "integer", 20, minimum=1, maximum=CRAFT_HISTORY_MAX)
    events, more = store.crafts.history(limit=limit, query=query[:200], status=status)
    return {
        "events": [
            {
                "item": e.get("itemName"),
                "cpu": e.get("cpu"),
                "status": e.get("status"),
                "auto_craft": bool(e.get("auto")),
                "progress_percent": e.get("progress"),
                "started_at": _iso(e.get("startedAt")),
                "ended_at": _iso(e.get("finishedAt")),
                "link": _job_link(e.get("cpu"), e.get("firstSeenAt")),
            }
            for e in events
        ],
        "more": more,
    }


STATS_RANGES = {"day": 86400, "week": 7 * 86400, "month": 30 * 86400, "all": None}


def craft_stats_tool(user, args):
    range_key = _arg(args, "range", "string", "week", choices=tuple(STATS_RANGES))
    now = time.time()
    since = None if STATS_RANGES[range_key] is None else now - STATS_RANGES[range_key]
    stats = _without_icons(craft_stats.summarize(since, now))
    machines = [
        {
            "machine": b["name"],
            "at": {k: b[k] for k in ("x", "y", "z", "dim")},
            "jobs": b["jobs"],
            "held_jobs_up_hours": _round(b["critical"] / 3600, 2),
            "stalled_hours": _round(b["stalled"] / 3600, 2),
            "busy_hours": _round(b["busy"] / 3600, 2),
            "blocked_hours": _round(b["blocked"] / 3600, 2),
        }
        for b in bottlenecks.summary(since)
    ]
    return {"range": range_key, **stats, "slowest_machines": machines}


# ---------------------------------------------------------------------
# Craft plans and stock rules

def _plan(user, key, amount):
    """The crafting plan for amount of an item, as the model sees it.
    ToolError when there's none: no pattern scan yet, or no pattern
    makes the item."""
    pattern_list, patterns_at = patterns.current()
    if patterns_at is None:
        raise ToolError(patterns.missing_reason("crafting plan"))
    stock_levels, stock_at, stock_version = inventory.stock_levels()
    result = planner.cached_plan(
        pattern_list, stock_levels, stock_version, key, amount, None,
        planner.rules_for_user(user["id"]), describe=inventory.describe,
    )
    if result is None:
        raise ToolError("None of the network's patterns makes this item.")
    root = next((i for i in result["items"] if i["key"] == key), None)
    items = sorted(
        (i for i in result["items"] if i["key"] != key),
        key=lambda i: (-(i["missing"] > 0), -i["craft"], i["name"] or ""),
    )
    return {
        "can_craft": not result["missing"],
        "requested": _plan_row(root, requested=True) if root else None,
        "missing": [_plan_row(i) for i in result["missing"]],
        "ingredients": [_plan_row(i) for i in items[:60]],
        "ingredients_total": len(items),
        "steps": result["steps"],
        "truncated": result["truncated"],
        "patterns_scanned_at": _iso(patterns_at),
        "stock_scanned_at": _iso(stock_at),
        "note": ("An estimate from the network's patterns and last scan, worked out the way AE2 plans. Each "
                 "row is a total over the whole craft; its `plan` sentence says how it's covered. AE2 always "
                 "crafts the requested item in full and never takes it from storage, even with some in stock."),
    }


def _amount(n, kind):
    n = int(n) if float(n).is_integer() else n
    return f"{n:,}" + (" mB" if kind == "fluid" else "")


def _plan_row(i, requested=False):
    """One item's totals in a plan, with a sentence saying how its need
    is covered, so the numbers can't be read the wrong way round."""
    kind = i["kind"]
    used_crafted = i["need"] - i["from_stock"] - i["from_leftovers"] - i["missing"]
    left_over = max(0, i["craft"] - used_crafted)
    parts = []
    if i["from_stock"]:
        whole = " (all of it)" if i["from_stock"] >= i["available"] else f" (of {_amount(i['available'], kind)})"
        parts.append(f"{_amount(i['from_stock'], kind)} from storage{whole}")
    if i["from_leftovers"]:
        parts.append(f"{_amount(i['from_leftovers'], kind)} left over from other steps")
    if i["craft"]:
        crafted = f"{_amount(i['craft'], kind)} crafted"
        if left_over:
            crafted += f" in whole batches, {_amount(left_over, kind)} left over"
        parts.append(crafted)
    if i["missing"]:
        parts.append(f"{_amount(i['missing'], kind)} missing")
    if requested:
        sentence = f"crafts {_amount(i['craft'], kind)}"
        if left_over:
            sentence += f" in whole batches ({_amount(left_over, kind)} more than asked)"
        if i["available"]:
            sentence += f"; the {_amount(i['available'], kind)} already in storage aren't used, AE2 never takes the requested item from storage"
    else:
        sentence = f"needs {_amount(i['need'], kind)} in all: " + ", ".join(parts)
    out = {
        "name": i["name"],
        "plan": sentence,
        "needed": i["need"],
        "in_storage": i["available"],
        "from_storage": i["from_stock"],
        "from_leftovers": i["from_leftovers"],
        "crafted": i["craft"],
        "unit": _unit(kind),
    }
    if i["missing"]:
        out["missing"] = i["missing"]
    if i.get("warnings"):
        out["would_go_below"] = [w["rule"] + f" ({w['threshold']})" for w in i["warnings"]]
    return out


def plan_craft(user, args):
    key = _arg(args, "item", "string", required=True)
    amount = _arg(args, "amount", "integer", required=True, minimum=1, maximum=10**12)
    _parse_key(key)
    return {"item": key, "amount": amount, **_plan(user, key, amount)}


def stock_rules_tool(user, args):
    rules = stock.rules(user)
    set_by = {t["key"]: t["updated_by"] for t in store.stock.targets()}
    names = {uid: store.users.display_name(uid) for uid in set(set_by.values()) if uid}
    for t in rules["targets"]:
        t["set_by"] = names.get(set_by.get(t["key"]))
    keep = ("key", "label", "kind", "current", "keep_at_least", "refill_to", "enabled", "status", "reason",
            "cpu", "below", "low", "set_by")
    return {
        "keep_in_stock_targets": [
            {("item" if k == "key" else k): v for k, v in t.items() if k in keep} for t in rules["targets"]
        ],
        "your_low_stock_alerts": [
            {("item" if k == "key" else k): v for k, v in a.items() if k in keep} for a in rules["alerts"]
        ],
    }


# ---------------------------------------------------------------------
# Power

POWER_RANGES = ("hour", "day", "week", "month")
POWER_POINTS = 48
# What a machine without a name (one power_monitor.lua reports before
# names, or with none set) is called here.
UNNAMED = "(unnamed)"


def power(user, args):
    wanted = _arg(args, "machine", "string", None)
    if wanted == UNNAMED:
        wanted = ""
    range_key = _arg(args, "range", "string", "day", choices=POWER_RANGES)
    machine, machines = store.power.pick(wanted)
    if not machines:
        raise ToolError("No power readings yet: power_monitor.lua hasn't reported.")
    range_key, rows = store.power.range_readings(range_key, machine)
    latest = store.power.latest(machine)
    now = time.time()
    recent = store.power.readings(now - rates.POWER_WINDOW_SECONDS, store.power.MAX_POINTS, machine)
    trend = rates.power_trend(recent, now)
    stored = [r[1] for r in rows]
    out = {
        "machine": machine or UNNAMED,
        "machines": [name or UNNAMED for name, _ in machines],
        "range": range_key,
        "unit": "EU",
        "min_stored": min(stored) if stored else None,
        "max_stored": max(stored) if stored else None,
        "points": [{"t": _iso(r[0]), "stored": r[1]} for r in _thin(rows, POWER_POINTS)],
    }
    if latest:
        out["latest"] = {
            "t": _iso(latest[0]),
            "stored": latest[1],
            "capacity": latest[2],
            "percent_full": _round(100 * latest[1] / latest[2], 1) if latest[2] else None,
            "average_in_last_5s": _round(latest[3]),
            "average_out_last_5s": _round(latest[4]),
        }
        if latest[5]:
            out["latest"]["wireless_threshold"] = latest[5]
            out["latest"]["percent_of_wireless_threshold"] = _round(100 * latest[1] / latest[5], 1)
            trade = rates.wireless_trade(recent, now)
            if trade is not None:
                out["latest"]["sent_to_wireless_eu_per_second_last_hour"] = _round(trade)
    # An LSC in wireless mode is refilled every 5 minutes, so its drift
    # between refills isn't a trend.
    if trend and not (latest and latest[5]):
        out["trend"] = {
            "eu_per_second": _round(trend["per_second"]),
            "hours_to_empty": _round(trend["seconds_to_empty"] / 3600, 1) if trend.get("seconds_to_empty") else None,
            "hours_to_full": _round(trend["seconds_to_full"] / 3600, 1) if trend.get("seconds_to_full") else None,
        }
    return out


# ---------------------------------------------------------------------
# The game itself: GregTech recipes and item tooltips from the selected
# GTNH version's game data (gcm/recipes.py, gcm/item_info.py), and the
# base's own patterns. Models misremember GTNH recipes; these are the
# version the base runs.

GAME_MATCHES_MAX = 10
RECIPES_MAX = 10
TOOLTIP_MAX = 3000


def _recipe_index(required=True):
    """(version, RecipeIndex) for the selected GTNH version. Without
    recipes: a ToolError saying why, or (version, None) if not required."""
    version = gamedata.selected_version()
    if not version:
        if not required:
            return None, None
        raise ToolError("No GTNH version is set in the game data, so there are no recipes: an admin picks one "
                        "on the Game data page.")
    try:
        idx = recipes.index(version)
    except ValueError as e:
        if not required:
            return version, None
        raise ToolError(str(e)) from None
    if idx is None and required:
        raise ToolError(f"The game data for GTNH {version} has no recipes.")
    return version, idx


def _recipe_key(key):
    """An item key as the recipe index keys it: (kind, key)."""
    mod, internal, damage, kind, _ = _parse_key(key)
    if kind == "fluid":
        return recipe_index.FLUID, internal
    if kind != "item":
        raise ToolError("Only items and fluids have recipes.")
    return recipe_index.ITEM, f"{mod}:{internal}:{'*' if damage is None else damage}"


def _key_from_recipe(kind, rkey):
    """The recipe index's key as an item key (the other tools' form)."""
    if kind == recipe_index.FLUID:
        return store.items.item_key(None, rkey, None, "fluid")
    mod, _, rest = rkey.partition(":")
    internal, _, damage = rest.rpartition(":")
    try:
        damage = None if damage == "*" else int(damage)
    except ValueError:
        return None
    return store.items.item_key(mod, internal, damage, "item")


def _game_matches(query, idx):
    """[(item key, name)] for what's named like query in the game data's
    recipes and in the network, best match first."""
    q = query.strip()
    found = {}
    if idx is not None and q:
        for kind, rkey, name in idx.find_names(q, 300):
            key = _key_from_recipe(kind, rkey)
            if key:
                found.setdefault(key, mctext.plain(name))
    with state.network_lock:
        items = state.network["items"]
    lowered = q.lower()
    for it in items:
        name = mctext.plain(it.get("name") or "")
        if lowered in name.lower():
            found.setdefault(store.items.key_of(it), name)
    return sorted(found.items(), key=lambda kv: (_rank({"name": kv[1]}, q), len(kv[1]), kv[1]))


def _pick(query, idx):
    """The one item a query names: (key, None), or (None, a "which one?"
    answer listing the candidates)."""
    if "|" in query:
        _parse_key(query)
        return query, None
    matches = _game_matches(query, idx)
    if not matches:
        raise ToolError(f"Nothing in the game data or the network is called anything like {query!r}.")
    exact = [m for m in matches if m[1].lower() == query.strip().lower()]
    if len(exact) == 1 or len(matches) == 1:
        return (exact or matches)[0][0], None
    return None, {
        "matches": [{"item": k, "name": n} for k, n in matches[:GAME_MATCHES_MAX]],
        "matches_total": len(matches),
        "note": "Several items match: call again with the item key of the one meant.",
    }


def _stacks(stacks, kind, names, chances=False):
    rows = []
    for s in stacks:
        row = {"name": mctext.plain(names.get(s[0]) or s[0]), "amount": s[1], "unit": "mB" if kind == "fluid" else "items"}
        if chances and len(s) > 2 and s[2] is not None and s[2] < 10000:
            row["chance_percent"] = s[2] / 100
        rows.append(row)
    return rows


def find_recipes(user, args):
    query = _arg(args, "item", "string", required=True)
    limit = _arg(args, "limit", "integer", 5, minimum=1, maximum=RECIPES_MAX)
    version, idx = _recipe_index()
    key, ask = _pick(query, idx)
    if ask:
        return ask
    kind, rkey = _recipe_key(key)
    found = idx.making(kind, rkey)
    found.sort(key=lambda r: (abs(r[2]["e"]), r[2]["d"]))
    item_keys = {s[0] for _, _, b in found[:limit] for side in ("i", "o") for s in b[side]}
    fluid_keys = {s[0] for _, _, b in found[:limit] for side in ("f", "g") for s in b[side]}
    item_names = idx.names(recipe_index.ITEM, item_keys | ({rkey} if kind == recipe_index.ITEM else set()))
    fluid_names = idx.names(recipe_index.FLUID, fluid_keys | ({rkey} if kind == recipe_index.FLUID else set()))
    rows = []
    for _, map_name, b in found[:limit]:
        machines = [m["name"] for m in idx.machines(map_name)]
        row = {
            "machine": machines[0] if machines else map_name,
            "inputs": _stacks(b["i"], "item", item_names) + _stacks(b["f"], "fluid", fluid_names),
            "outputs": _stacks(b["o"], "item", item_names, chances=True) + _stacks(b["g"], "fluid", fluid_names),
            "eu_per_tick": b["e"],
            "tier": recipes._tier(b["e"]),
            "seconds": round(b["d"] / 20, 2),
        }
        if len(machines) > 1:
            row["other_machines"] = machines[1:4]
        if b.get("s"):
            row["coil_heat" if map_name in recipes.HEAT_MAPS else "special_value"] = b["s"]
        rows.append(row)
    used_in = []
    for map_name in idx.using(kind, rkey):
        machines = idx.machines(map_name)
        used_in.append(machines[0]["name"] if machines else map_name)
    names = item_names if kind == recipe_index.ITEM else fluid_names
    return {
        "item": key,
        "name": mctext.plain(names.get(rkey) or query),
        "gtnh_version": version,
        "recipes": rows,
        "recipes_total": len(found),
        "used_in_machines": sorted(set(used_in))[:20],
        "note": ("GregTech machine recipes from the game data, cheapest first. Crafting-table and other "
                 "mods' recipes aren't included." if found else
                 "No GregTech machine makes it; it may be crafted on a crafting table or by another mod."),
    }


def game_item(user, args):
    query = _arg(args, "item", "string", required=True)
    version, idx = _recipe_index(required=False)
    key, ask = _pick(query, idx)
    if ask:
        return ask
    mod, internal, damage, kind, variant = _parse_key(key)
    network = _network_item(key)
    name = network.get("name") if network else None
    if name is None and idx is not None:
        rkind, rkey = _recipe_key(key) if kind in ("item", "fluid") else (None, None)
        if rkind is not None:
            name = idx.names(rkind, [rkey]).get(rkey)
    info = item_info.describe({"mod": mod, "internal": internal, "damage": damage, "kind": kind, "variant": variant,
                               "name": name})
    out = {"item": key, "name": mctext.plain(name) if name else None, "mod": item_info.mod_name(mod) or mod,
           "gtnh_version": version}
    for field, label in (("description", "tooltip"), ("shift_description", "shift_tooltip")):
        if info.get(field):
            text = info[field]
            out[label] = text if len(text) <= TOOLTIP_MAX else text[:TOOLTIP_MAX] + "…"
    if info.get("ore"):
        out["ore_dictionary"] = info["ore"][:10]
    if not (info.get("description") or info.get("shift_description")):
        out["note"] = "The game data has no tooltip for it."
    else:
        out["note"] = ("The shift tooltip is what the game shows holding Shift: for a multiblock controller, "
                       "its structure, hatches and limits.")
    out["in_network"] = (network.get("size") or 0) if network else None
    return out


KIT_OPTIONS_MAX = 80


def _kit_controller(query):
    """The game data key of the multiblock a query names (a network item
    key or a name), or a ToolError listing the candidates."""
    if query.count("|") >= 3:
        mod, internal, damage, _, _ = _parse_key(query)
        key = multiblocks.game_key(mod, internal, damage)
        if not multiblocks.available(mod, internal, damage):
            raise ToolError(f"{query} isn't a multiblock controller with a kit")
        return key
    found = multiblocks.controllers()
    if not found:
        raise ToolError("The game data has no multiblock structures (it's older than kits).")
    q = query.strip().lower()
    exact = [k for k, n in found if mctext.plain(n).lower() == q]
    if exact:
        return exact[0]
    near = [(k, n) for k, n in found if q in mctext.plain(n).lower()]
    if len(near) == 1:
        return near[0][0]
    if not near:
        names = [n for _, n in found]
        near = [(None, n) for n in difflib.get_close_matches(query, names, n=10, cutoff=0.5)]
    raise ToolError(f"Which multiblock? {'; '.join(mctext.plain(n) for _, n in near[:10]) or 'none matched'}")


def multiblock_kit(user, args):
    query = _arg(args, "multiblock", "string", required=True)
    count = _arg(args, "count", "integer", default=1, minimum=1, maximum=multiblocks.MAX_COUNT)
    wanted = args.get("tiers") or {}
    if not isinstance(wanted, dict):
        raise ToolError("tiers must be an object: {channel: tier number or block name}")
    key = _kit_controller(query)
    first = multiblocks.kit(key)
    tiers = {}
    for channel in first["channels"]:
        value = wanted.get(channel["name"])
        if value is None:
            continue
        if isinstance(value, str) and not value.strip().isdigit():
            match = [o["tier"] for o in channel["options"] if mctext.plain(o["label"]).lower() == value.strip().lower()]
            if not match:
                raise ToolError(f"{channel['name']} has no {value!r}; see its options")
            value = match[0]
        tiers[channel["name"]] = int(value)
    unknown = sorted(set(wanted) - {c["name"] for c in first["channels"]})
    if unknown:
        raise ToolError(f"{query} has no channel {', '.join(unknown)}; its channels: "
                        f"{', '.join(c['name'] for c in first['channels']) or 'none'}")
    kit = multiblocks.kit(key, tiers, count)
    channels = []
    for c in kit["channels"]:
        chosen = next(o for o in c["options"] if o["tier"] == c["tier"])
        out = {"name": c["name"], "kind": c["kind"], "tier": c["tier"], "chosen": mctext.plain(chosen["label"])}
        if c["kind"] == "block":
            out["options"] = [f"{o['tier']}: {mctext.plain(o['label'])}" for o in c["options"]][:KIT_OPTIONS_MAX]
        else:
            out["range"] = [c["min"], c["max"]]
        channels.append(out)
    return {
        "multiblock": mctext.plain(kit["controller"]["name"]),
        "count": kit["count"],
        "channels": channels,
        "blocks": [
            {"item": r["item"], "name": mctext.plain(r["name"]), "need": r["need"], "in_network": r["have"],
             "craftable": r["craftable"]}
            for r in kit["rows"]
        ],
        "note": (
            "Hatches, buses, mufflers and maintenance hatches aren't included: the player picks those. Spots "
            "that take a hatch or a casing count as casings, so casings are the most needed; walls shared with "
            "another multiblock need fewer. Ask the user before requesting, then request_craft each block "
            "still short." + (" Channels interact here; counts are approximate." if kit["approximate"] else "")
        ),
    }


def base_patterns(user, args):
    key = _arg(args, "item", "string", required=True)
    _parse_key(key)
    pattern_list, patterns_at = patterns.current()
    if patterns_at is None:
        raise ToolError(patterns.missing_reason("list of patterns"))
    found = planner.index(pattern_list).candidates.get(key) or []
    rows = []
    for c in found[:20]:
        provider = c.pattern.get("provider") or {}
        rows.append({
            "machine": provider.get("name"),
            "at": {k: provider.get(k) for k in ("x", "y", "z", "dim")},
            "kind": "crafting" if c.pattern.get("crafting") else "processing",
            "makes": [{"name": mctext.plain(e.get("name") or k), "amount": size} for k, (e, size) in c.outputs.items()],
            "takes": [{"name": mctext.plain(e.get("name") or k), "amount": size} for k, e, size in c.inputs],
        })
    return {
        "item": key,
        "patterns": rows,
        "patterns_total": len(found),
        "patterns_read_at": _iso(patterns_at),
        "note": ("The network's own patterns for it, in the order AE2 tries them, with the machine (or "
                 "interface) each is in." if found else "No pattern in the network makes it."),
    }


PATTERN_CHECK_MAX = 40
_CHECK_ORDER = {"broken": 0, "moved": 1, "changed": 2}


def pattern_check(user, args):
    _require_operator(user, "see the pattern check")
    from_version, to_version = recipes.default_versions()
    if not to_version:
        raise ToolError("There's no GTNH update to check: the game data has had one version. After an update "
                        "the check compares the old version's recipes with the new one's.")
    status, body = recipes.result(from_version, to_version)
    if status == "preparing":
        return {"state": "preparing", "from": from_version, "to": to_version,
                "note": "The recipes are being downloaded and indexed: call again in a minute."}
    if status in ("no_patterns", "error"):
        raise ToolError(body)
    rows = sorted((r for r in body["patterns"] if r["status"] in _CHECK_ORDER),
                  key=lambda r: _CHECK_ORDER[r["status"]])
    out_rows = []
    for r in rows[:PATTERN_CHECK_MAX]:
        provider = r.get("provider") or {}
        row = {
            "status": r["status"],
            "machine": provider.get("name"),
            "at": {k: provider.get(k) for k in ("x", "y", "z", "dim")},
            "makes": [mctext.plain(o.get("name") or "") for o in r.get("outputs") or []],
        }
        for field in ("changes", "moved_to", "removed"):
            if r.get(field):
                row[field] = _without_icons(r[field])
        out_rows.append(row)
    return {
        "from": body.get("from"), "to": body.get("to"), "counts": body.get("counts"),
        "patterns": out_rows, "patterns_listed": len(out_rows), "patterns_needing_attention": len(rows),
        "note": ("broken: the new version has no matching recipe; moved: the recipe is in another machine now; "
                 "changed: same machine, different amounts, fluid, circuit or coil."),
    }


# ---------------------------------------------------------------------
# Changes: stock rules, and crafts in the game. Each checks the role the
# page's route checks (gcm.auth), and runs the same code it does.

def _require_operator(user, what):
    if not auth.is_operator(user):
        raise ToolError(f"Only operators and admins can {what}; you're a {user['role']}.")


def _rule_item(key):
    """The item a new stock rule is about, from the last scan (a rule
    keeps the item's name, which only the scan has)."""
    mod, internal, damage, kind, variant = _parse_key(key)
    item = _network_item(key)
    if item is None:
        raise ToolError("That item isn't in the network's last scan.")
    return item, {"label": item.get("name") or internal, "mod": mod, "internal": internal, "damage": damage,
                  "kind": kind, "variant": variant}


def set_stock_alert(user, args):
    key = _arg(args, "item", "string", required=True)
    below = _arg(args, "below", "number", required=True)
    item, rule = _rule_item(key)
    error = stock.set_alert(user["id"], rule, below)
    if error:
        raise ToolError(error)
    return {
        "saved": True, "item": key, "name": rule["label"], "below": below, "now": item.get("size") or 0,
        "note": "You get a notification when it drops below this, then again only after it has been back above.",
    }


def remove_stock_alert(user, args):
    key = _arg(args, "item", "string", required=True)
    _parse_key(key)
    if not any(a["key"] == key for a in store.stock.alerts_for_user(user["id"])):
        raise ToolError("You have no alert on that item.")
    store.stock.delete_alert(user["id"], key)
    return {"removed": True, "item": key}


def set_stock_target(user, args):
    _require_operator(user, "change keep-in-stock targets")
    key = _arg(args, "item", "string", required=True)
    keep = _arg(args, "keep_at_least", "number", required=True)
    refill = _arg(args, "refill_to", "number", required=True)
    enabled = _arg(args, "enabled", "boolean", True)
    item, rule = _rule_item(key)
    error = stock.set_target(user["id"], rule, keep, refill, enabled)
    if error:
        raise ToolError(error)
    now = item.get("size") or 0
    return {
        "saved": True, "item": key, "name": rule["label"], "keep_at_least": keep, "refill_to": refill,
        "enabled": enabled, "now": now,
        "note": ("It's below that now: the server asks AE2 for more after the next check, while a CPU is free."
                 if enabled and now < keep else
                 "The server asks AE2 for more whenever it drops below keep_at_least."),
    }


def remove_stock_target(user, args):
    _require_operator(user, "change keep-in-stock targets")
    key = _arg(args, "item", "string", required=True)
    _parse_key(key)
    if not any(t["key"] == key for t in store.stock.targets()):
        raise ToolError("There's no keep-in-stock target on that item.")
    store.stock.delete_target(key)
    return {"removed": True, "item": key}


def _game_silence():
    """How long craft_monitor.lua has been silent, in seconds, when that's
    longer than the page calls stale (None while it reports): a request
    or cancel then expires unanswered."""
    with state.crafts_lock:
        received_at = state.crafts["received_at"]
        poll_seconds = state.crafts["poll_seconds"]
    if received_at is None:
        return float("inf")
    silent = time.time() - received_at
    return silent if silent > state.stale_after(config.STALE_AFTER_SECONDS, poll_seconds) else None


def _require_game(what):
    silent = _game_silence()
    if silent is None:
        return
    since = "hasn't reported since the server started" if silent == float("inf") else (
        f"hasn't reported for {round(silent / 60)} min" if silent >= 90 else f"hasn't reported for {int(silent)} s")
    raise ToolError(f"craft_monitor.lua {since}, so the game wouldn't see {what} and it would expire "
                    "unanswered. Check the computer running it in the game.")


def _already_underway(key, mod, internal, damage):
    """What's already getting the base this item: pending requests for
    it, a keep-in-stock refill on its way, or busy CPUs making it."""
    found = []
    pending = commands.craft_requests.select(
        lambda r: r["status"] == "pending" and r.get("source") != "auto" and store.items.item_key(
            r.get("mod"), r.get("internal"), r.get("damage"), r.get("kind"), r.get("variant")) == key
    )
    for r in pending:
        found.append(f"a request for {_amount(r['amount'], r.get('kind'))} is waiting for the game")
    if key in stock.pending_auto_requests():
        found.append("its keep-in-stock target has asked for a refill")
    identity = {"mod": mod, "internal": internal, "damage": damage}
    with state.crafts_lock:
        jobs = list(state.crafts["jobs"])
    for job in jobs:
        output = commands.output_identity(job)
        if job.get("busy") and output and commands.same_output(output, identity):
            found.append(f"a crafting CPU is making it now ({job.get('progress_percent') or 0}% done)")
    return found


def request_craft(user, args):
    _require_operator(user, "request crafts")
    key = _arg(args, "item", "string", required=True)
    amount = _arg(args, "amount", "integer", required=True, minimum=1, maximum=10**12)
    force = _arg(args, "force", "boolean", False)
    allow_duplicate = _arg(args, "allow_duplicate", "boolean", False)
    mod, internal, damage, kind, variant = _parse_key(key)
    item = _network_item(key)
    if item is None:
        raise ToolError("That item isn't in the network's last scan.")
    if not item.get("isCraftable"):
        raise ToolError("The network has no pattern for that item, so AE2 can't craft it.")
    label = item.get("name") or internal
    error = commands.craft_request_error(label, internal, amount, kind, variant)
    if error:
        raise ToolError(error)
    _require_game("the request")

    # Asked twice (a retry, or two people at once) is a double craft.
    underway = _already_underway(key, mod, internal, damage)
    if underway and not allow_duplicate:
        return {
            "queued": False,
            "item": key, "name": label, "amount": amount, "unit": _unit(kind),
            "already": underway,
            "note": ("Not started: the base is already getting this item. Tell the user. Only call again with "
                     "allow_duplicate=true if they want another craft on top."),
        }

    # The plan first: a request AE2 can't cover just fails in-game, so
    # say what's missing instead, unless asked to go ahead anyway.
    plan = None
    try:
        plan = _plan(user, key, amount)
    except ToolError as e:
        plan_note = f"Couldn't check the ingredients first: {e}"
    else:
        plan_note = None
        if not plan["can_craft"] and not force:
            return {
                "queued": False,
                "item": key, "name": label, "amount": amount, "unit": _unit(kind),
                "missing": plan["missing"],
                "note": ("Not started: the plan says these are missing. Tell the user. Only call again with "
                         "force=true if they want to try anyway (AE2 then fails the request if they're "
                         "still missing)."),
            }

    req_id = commands.queue_craft(
        user["id"], label, mod, internal, damage, amount, kind, variant, item.get("variant_name"),
        source="assistant",
    )
    out = {
        "queued": True,
        "request_id": req_id,
        "item": key, "name": label, "amount": amount, "unit": _unit(kind), "in_stock": item.get("size") or 0,
        "note": ("Sent to the game, which asks AE2 for it within a few seconds; AE2 starts it on whichever "
                 "CPU is free. request_status says whether it started."),
    }
    if plan_note:
        out["plan_note"] = plan_note
    elif not plan["can_craft"]:
        out["plan_note"] = "Started with force: the plan said ingredients were missing."
    return out


def _job_id(job):
    """What a busy CPU is making, as cancel_craft takes it back, or None
    when the CPU doesn't report it (no Crafting Monitor)."""
    output = commands.output_identity(job)
    if not output:
        return None
    damage = output["damage"]
    return f"{output['mod'] or ''}|{output['internal']}|{'' if damage is None else damage}"


def cancel_craft(user, args):
    _require_operator(user, "cancel crafts")
    cpu = _arg(args, "cpu", "string", required=True)
    job_id = _arg(args, "job_id", "string", None)
    shown = None
    if job_id:
        parts = job_id.split("|")
        if len(parts) != 3 or not parts[1]:
            raise ToolError("job_id must be the job_id current_crafts gave for this CPU")
        try:
            damage = int(parts[2]) if parts[2] else None
        except ValueError:
            raise ToolError("job_id must be the job_id current_crafts gave for this CPU") from None
        shown = {"mod": parts[0] or None, "internal": parts[1], "damage": damage}
    if shown is None:
        # Without it, a CPU that moved on to another job since the user
        # agreed would have that one cancelled instead.
        with state.crafts_lock:
            job = next((j for j in state.crafts["jobs"] if j.get("name") == cpu), None)
        if job is not None and job.get("busy") and _job_id(job):
            raise ToolError("Pass the job_id current_crafts gave for this CPU, so only that job is cancelled.")
    _require_game("the cancel")
    req_id, error = commands.request_cancel(user["id"], cpu, shown, source="assistant")
    if error:
        raise ToolError(error[0])
    return {
        "queued": True, "request_id": req_id, "cpu": cpu,
        "note": "Sent to the game, which cancels it within a few seconds. request_status says whether it worked.",
    }


def request_status(user, args):
    req_id = _arg(args, "request_id", "integer", required=True)
    kind = _arg(args, "type", "string", "craft", choices=("craft", "cancel"))
    # A request the game never picked up is only marked failed when its
    # queue is next looked at (the game's poll, the page): look now.
    queue = commands.craft_requests if kind == "craft" else commands.cancel_requests
    queue.select(lambda r: False)
    found = store.requests.find(kind, req_id, user["id"])
    if found is None:
        raise ToolError(f"You have no {kind} request with that id.")
    if kind == "craft":
        out = {"request_id": req_id, "name": found["label"], "amount": found["amount"], "status": found["status"]}
        if found["status"] == "accepted":
            out["cpu"] = found["cpu_name"]
            out["note"] = "AE2 started it. To follow it, craft_job takes the cpu given here."
        elif found["status"] == "failed":
            out["reason"] = found["reason"]
        else:
            out["note"] = "Still waiting for the game to ask AE2."
        return out
    out = {"request_id": req_id, "cpu": found["cpu_name"], "making": found["label"]}
    if found["status"] == "pending":
        out.update(status="pending", note="Still waiting for the game.")
    else:
        out.update(status="cancelled" if found["success"] else "not cancelled", reason=found["reason"])
    return out


# ---------------------------------------------------------------------
# The registry

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
# Changes only this server's settings (your own alerts).
CHANGES = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
# Changes what happens in the game: clients ask the user before these.
GAME_CHANGES = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}

TOOLS = [
    {
        "name": "search_items",
        "title": "Search the ME network",
        "description": (
            "Find items, fluids and essentia in the base's ME network by name, with their current amount. "
            "Use this first: the other tools take the `item` key it returns. Accepts NEI-style search: "
            "plain words match names, @mod limits to a mod, #word searches tooltips, $name searches "
            "ore-dictionary names, -word excludes, a|b matches either."
        ),
        "inputSchema": _schema({
            "query": {"type": "string", "description": "What to search for, e.g. 'tungstensteel plate'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": SEARCH_LIMIT_MAX, "default": 20},
            "craftable_only": {"type": "boolean", "default": False,
                               "description": "Only items the network has a pattern for."},
        }, ["query"]),
        "annotations": READ_ONLY,
        "handler": search_items,
    },
    {
        "name": "get_item",
        "title": "Item details",
        "description": (
            "One item's current amount, trend over the last day, tooltip, stock rules and the CPUs "
            "crafting it now."
        ),
        "inputSchema": _schema({"item": ITEM_ARG}, ["item"]),
        "annotations": READ_ONLY,
        "handler": get_item,
    },
    {
        "name": "item_history",
        "title": "Item amount history",
        "description": "How one item's amount in the network changed over a time range, with its trend.",
        "inputSchema": _schema({
            "item": ITEM_ARG,
            "range": _range_arg(HISTORY_RANGES, "day", "How far back to look."),
        }, ["item"]),
        "annotations": READ_ONLY,
        "handler": item_history,
    },
    {
        "name": "inventory_changes",
        "title": "What changed in storage",
        "description": (
            "Items whose amount changed over the last day or week: what's being used up or "
            "piling up, with hours until empty at the current rate."
        ),
        "inputSchema": _schema({
            "range": _range_arg(CHANGE_RANGES, "day", "Compare with the amount this long ago."),
            "sort": {"type": "string", "enum": list(CHANGE_SORTS), "default": "biggest",
                     "description": "falling: used up the most; rising: gained the most; biggest: either."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
        }),
        "annotations": READ_ONLY,
        "handler": inventory_changes,
    },
    {
        "name": "current_crafts",
        "title": "Crafting CPUs now",
        "description": (
            "What each AE2 crafting CPU is making right now, how far along it is, and which are idle. "
            "Each job's link is its page, to share: it keeps working after the job ends."
        ),
        "inputSchema": _schema({}),
        "annotations": READ_ONLY,
        "handler": current_crafts,
    },
    {
        "name": "craft_job",
        "title": "A running job's steps",
        "description": (
            "The steps of the job running on one crafting CPU, held-up steps first, with the "
            "machines holding them up. Use it to find why a craft is slow."
        ),
        "inputSchema": _schema({"cpu": {"type": "string", "description": "A CPU name from current_crafts."}}, ["cpu"]),
        "annotations": READ_ONLY,
        "handler": craft_job,
    },
    {
        "name": "craft_history",
        "title": "Finished crafts",
        "description": "Crafting jobs that ended, newest first, optionally filtered by item name.",
        "inputSchema": _schema({
            "query": {"type": "string", "description": "NEI-style search on the item made."},
            "status": {"type": "string", "enum": ["auto", "incomplete"],
                       "description": "auto: only keep-in-stock crafts; incomplete: only jobs that stopped early."},
            "limit": {"type": "integer", "minimum": 1, "maximum": CRAFT_HISTORY_MAX, "default": 20},
        }),
        "annotations": READ_ONLY,
        "handler": craft_history,
    },
    {
        "name": "craft_stats",
        "title": "Crafting statistics",
        "description": (
            "Totals over a time range: jobs finished, the most crafted items, and the machines that "
            "held jobs up the longest (CPU use too, for when the user asks about CPUs)."
        ),
        "inputSchema": _schema({"range": _range_arg(tuple(STATS_RANGES), "week", "The time range.")}),
        "annotations": READ_ONLY,
        "handler": craft_stats_tool,
    },
    {
        "name": "plan_craft",
        "title": "Plan a craft",
        "description": (
            "What crafting an amount of an item would take, worked out from the network's patterns "
            "and stock: what comes from storage, what gets crafted, and what's missing. Doesn't "
            "start anything."
        ),
        "inputSchema": _schema({
            "item": ITEM_ARG,
            "amount": {"type": "integer", "minimum": 1, "description": "How many (mB for a fluid)."},
        }, ["item", "amount"]),
        "annotations": READ_ONLY,
        "handler": plan_craft,
    },
    {
        "name": "stock_rules",
        "title": "Stock rules",
        "description": (
            "The base's keep-in-stock targets (the server asks AE2 to craft more when an item drops "
            "below one) and your own low-stock alerts, with each item's current amount."
        ),
        "inputSchema": _schema({}),
        "annotations": READ_ONLY,
        "handler": stock_rules_tool,
    },
    {
        "name": "power",
        "title": "Power storage",
        "description": (
            "EU stored in a power storage machine (an LSC, batteries, wireless) over time, its trend and EU/t "
            "in and out. An LSC in wireless mode keeps only its wireless threshold and moves the rest to the "
            "wireless network every 5 minutes, so its own fill near empty is normal: judge it by "
            "percent_of_wireless_threshold (under 100% between rebalances means it's draining faster than "
            "it's refilled; near 0 means it needs more or higher-tier capacitors) and "
            "sent_to_wireless_eu_per_second_last_hour (negative when it draws from the network), and the "
            "base's power by the wireless network."
        ),
        "inputSchema": _schema({
            "machine": {
                "type": "string",
                "description": "Which machine; the wireless network while it reports, else the first one, if left out.",
            },
            "range": _range_arg(POWER_RANGES, "day", "How far back to look."),
        }),
        "annotations": READ_ONLY,
        "handler": power,
    },
    {
        "name": "find_recipes",
        "title": "Look up GregTech recipes",
        "description": (
            "How the game makes an item or fluid: the GregTech machine recipes for it in the base's GTNH "
            "version (machine, inputs, outputs with chances, EU/t, voltage tier, time, coil heat), "
            "cheapest first, and which machines use it. Use this instead of remembering recipes. Takes an "
            "item key or a name; it can be any item in the game, not just ones in the network."
        ),
        "inputSchema": _schema({
            "item": {"type": "string", "description": "An item key (from search_items or game_item), or a name."},
            "limit": {"type": "integer", "minimum": 1, "maximum": RECIPES_MAX, "default": 5},
        }, ["item"]),
        "annotations": READ_ONLY,
        "handler": find_recipes,
    },
    {
        "name": "game_item",
        "title": "Item tooltip",
        "description": (
            "Any item's tooltip from the game data, including the shift tooltip, which for a GregTech "
            "multiblock controller is its structure: size, casings, hatches, coils and limits. Also its "
            "ore-dictionary names and the amount in the network. Takes an item key or a name."
        ),
        "inputSchema": _schema({
            "item": {"type": "string", "description": "An item key, or a name like 'Electric Blast Furnace'."},
        }, ["item"]),
        "annotations": READ_ONLY,
        "handler": game_item,
    },
    {
        "name": "multiblock_kit",
        "title": "Blocks to build a multiblock",
        "description": (
            "Every block a GregTech multiblock is built from, with the amount the network has and whether "
            "it's craftable: its controller, casings, coils, glass and so on, for the tiers chosen (a coil or glass "
            "channel's tiers are the blocks it takes; a length or height channel's are sizes). Without "
            "tiers, each channel is at its lowest; the result lists each channel's options."
        ),
        "inputSchema": _schema({
            "multiblock": {"type": "string",
                           "description": "The controller: a name like 'Electric Blast Furnace', or an item key."},
            "tiers": {"type": "object", "additionalProperties": {"type": ["integer", "string"]},
                      "description": "{channel: tier number or block name}, e.g. {\"coil\": \"Nichrome Coil Block\"}."},
            "count": {"type": "integer", "minimum": 1, "maximum": multiblocks.MAX_COUNT, "default": 1,
                      "description": "How many of the multiblock."},
        }, ["multiblock"]),
        "annotations": READ_ONLY,
        "handler": multiblock_kit,
    },
    {
        "name": "base_patterns",
        "title": "The base's patterns for an item",
        "description": (
            "The network's own patterns that make an item: which machine or interface each is in (with "
            "coordinates), crafting or processing, and what it takes and makes. How this base makes it, "
            "where find_recipes says how the game can."
        ),
        "inputSchema": _schema({"item": ITEM_ARG}, ["item"]),
        "annotations": READ_ONLY,
        "handler": base_patterns,
    },
    {
        "name": "pattern_check",
        "title": "Patterns a GTNH update broke",
        "description": (
            "After a GTNH update: the base's patterns whose GregTech recipe the new version broke, moved "
            "to another machine, or changed (amounts, fluid, circuit, coil), with each pattern's machine "
            "and coordinates. The same check as the page's Pattern check."
        ),
        "inputSchema": _schema({}),
        "annotations": READ_ONLY,
        "operator": True,
        "handler": pattern_check,
    },
    {
        "name": "request_status",
        "title": "Request status",
        "description": (
            "Whether one of your craft requests or cancels went through: AE2 started it, it failed (and "
            "why), or it's still waiting for the game."
        ),
        "inputSchema": _schema({
            "request_id": {"type": "integer", "description": "The request_id request_craft or cancel_craft returned."},
            "type": {"type": "string", "enum": ["craft", "cancel"], "default": "craft"},
        }, ["request_id"]),
        "annotations": READ_ONLY,
        "handler": request_status,
    },
    {
        "name": "set_stock_alert",
        "title": "Set a low-stock alert",
        "description": (
            "Your own alert on an item: a notification when it drops below an amount. Changes an "
            "existing alert's amount."
        ),
        "inputSchema": _schema({
            "item": ITEM_ARG,
            "below": {"type": "number", "exclusiveMinimum": 0, "description": "Notify below this amount (mB for a fluid)."},
        }, ["item", "below"]),
        "annotations": CHANGES,
        "handler": set_stock_alert,
    },
    {
        "name": "remove_stock_alert",
        "title": "Remove a low-stock alert",
        "description": "Removes your low-stock alert on an item.",
        "inputSchema": _schema({"item": ITEM_ARG}, ["item"]),
        "annotations": CHANGES,
        "handler": remove_stock_alert,
    },
    {
        "name": "set_stock_target",
        "title": "Set a keep-in-stock target",
        "description": (
            "The base's target for an item: whenever it drops below keep_at_least, the server asks AE2 "
            "to craft it back up to refill_to. Starts real crafts in the game, for everyone's base: "
            "confirm the item and both amounts with the user first. Craftable items only. Changes an "
            "existing target."
        ),
        "inputSchema": _schema({
            "item": ITEM_ARG,
            "keep_at_least": {"type": "number", "exclusiveMinimum": 0},
            "refill_to": {"type": "number", "exclusiveMinimum": 0, "description": "More than keep_at_least."},
            "enabled": {"type": "boolean", "default": True},
        }, ["item", "keep_at_least", "refill_to"]),
        "annotations": GAME_CHANGES,
        "operator": True,
        "handler": set_stock_target,
    },
    {
        "name": "remove_stock_target",
        "title": "Remove a keep-in-stock target",
        "description": "Removes the base's keep-in-stock target on an item. Confirm with the user first.",
        "inputSchema": _schema({"item": ITEM_ARG}, ["item"]),
        "annotations": GAME_CHANGES,
        "operator": True,
        "handler": remove_stock_target,
    },
    {
        "name": "request_craft",
        "title": "Request a craft",
        "description": (
            "Asks AE2 to craft an amount of an item: a real craft in the game, using up the base's "
            "materials. Before calling, tell the user the item's name and the amount and get their OK. "
            "It works out the crafting plan first and doesn't start if ingredients are missing: it "
            "returns what's missing instead. force=true starts it anyway; only use it when the user "
            "asks to after seeing what's missing. AE2 picks a free CPU itself: there's no CPU to "
            "choose, and the user needn't hear which one. Returns a request_id for request_status."
        ),
        "inputSchema": _schema({
            "item": ITEM_ARG,
            "amount": {"type": "integer", "minimum": 1,
                       "description": "How many items, or mB for a fluid (1000 mB is a bucket)."},
            "force": {"type": "boolean", "default": False,
                      "description": "Start even though the plan says ingredients are missing."},
            "allow_duplicate": {"type": "boolean", "default": False,
                                "description": "Start even though the base is already getting this item."},
        }, ["item", "amount"]),
        "annotations": GAME_CHANGES,
        "operator": True,
        "handler": request_craft,
    },
    {
        "name": "cancel_craft",
        "title": "Cancel a craft",
        "description": (
            "Cancels the job running on a crafting CPU. Confirm the CPU and what it's making with the "
            "user first. Pass the job_id current_crafts gave: if the CPU has moved on to another job "
            "since, nothing is cancelled."
        ),
        "inputSchema": _schema({
            "cpu": {"type": "string", "description": "A CPU name from current_crafts."},
            "job_id": {"type": "string", "description": (
                "The job_id current_crafts gave for that CPU's job. Required unless current_crafts "
                "gave none (the CPU has no Crafting Monitor)."
            )},
        }, ["cpu"]),
        "annotations": GAME_CHANGES,
        "operator": True,
        "handler": cancel_craft,
    },
]

_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def listing(user):
    """tools/list's tools for a user, in a fixed order: a viewer isn't
    offered the ones only operators may use."""
    return [
        {k: v for k, v in tool.items() if k not in ("handler", "operator")}
        for tool in TOOLS
        if not tool.get("operator") or auth.is_operator(user)
    ]


def exists(name):
    return name in _BY_NAME


def call(name, args, user):
    """Runs a tool (exists(name)) for a user and logs the call
    (store.assistant). Returns (result, is_error): result is the tool's
    JSON-able answer, or an error message for the model."""
    started = time.monotonic()
    answer, is_error = _run(name, args, user)
    ms = int((time.monotonic() - started) * 1000)
    size = len(answer) if isinstance(answer, str) else len(json.dumps(answer, default=str))
    log.info("MCP %s by %s: %s%s (%d ms)", name, user.get("display_name"), "error" if is_error else "ok",
             f" - {answer}" if is_error else "", ms)
    try:
        store.assistant.record_call(
            user.get("id"), name, args, is_error, answer if is_error else None, size, ms,
        )
    except Exception:
        log.exception("couldn't log MCP call %s", name)
    return answer, is_error


def _run(name, args, user):
    tool = _BY_NAME[name]
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return "arguments must be an object", True
    unknown = set(args) - set(tool["inputSchema"]["properties"])
    if unknown:
        return f"unknown argument(s): {', '.join(sorted(unknown))}", True
    try:
        return _compact_numbers(tool["handler"](user, args)), False
    except ToolError as e:
        return str(e), True
    except Exception:
        # A bug, not the model's mistake: logged, and the model told so
        # rather than the client getting a 500 it can't read.
        log.exception("MCP tool %s failed", name)
        return "The server hit an error running this tool. Its log has the details.", True
