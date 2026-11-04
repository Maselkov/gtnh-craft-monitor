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
import logging
import math
import time

from gcm import (
    bottlenecks, commands, craft_stats, inventory, item_info, patterns, planner, rates,
    mctext, search, state, stock, store,
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
    return {
        "scanned_at": _iso(scanned_at),
        "matches": len(found),
        "items": [_item_summary(it) for it in found[:limit]],
    }


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
            "progress_percent": job.get("progress_percent"),
            "steps_done": job.get("steps_done"),
            "steps_total": job.get("steps_total"),
            "running_seconds": _round(job.get("running_seconds"), 0),
            "eta_seconds": _round(job.get("eta_seconds"), 0),
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

def plan_craft(user, args):
    key = _arg(args, "item", "string", required=True)
    amount = _arg(args, "amount", "integer", required=True, minimum=1, maximum=10**12)
    _parse_key(key)
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
    items = sorted(result["items"], key=lambda i: (-(i["missing"] > 0), -i["craft"], i["name"] or ""))

    def row(i):
        out = {"name": i["name"], "unit": _unit(i["kind"]), "need": i["need"], "in_stock": i["available"],
               "from_stock": i["from_stock"], "craft": i["craft"]}
        if i["missing"]:
            out["missing"] = i["missing"]
        if i.get("warnings"):
            out["would_go_below"] = [w["rule"] + f" ({w['threshold']})" for w in i["warnings"]]
        return out

    return {
        "item": key,
        "amount": amount,
        "can_craft": not result["missing"],
        "missing": [row(i) for i in result["missing"]],
        "steps": result["steps"],
        "truncated": result["truncated"],
        "items": [row(i) for i in items[:60]],
        "patterns_scanned_at": _iso(patterns_at),
        "stock_scanned_at": _iso(stock_at),
        "note": "An estimate from the network's patterns and last scan, worked out the way AE2 plans.",
    }


def stock_rules_tool(user, args):
    rules = stock.rules(user)
    keep = ("key", "label", "kind", "current", "keep_at_least", "refill_to", "enabled", "status", "reason",
            "cpu", "below", "low")
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
    trend = rates.power_trend(
        store.power.readings(now - rates.POWER_WINDOW_SECONDS, store.power.MAX_POINTS, machine), now
    )
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
    if trend:
        out["trend"] = {
            "eu_per_second": _round(trend["per_second"]),
            "hours_to_empty": _round(trend["seconds_to_empty"] / 3600, 1) if trend.get("seconds_to_empty") else None,
            "hours_to_full": _round(trend["seconds_to_full"] / 3600, 1) if trend.get("seconds_to_full") else None,
        }
    return out


# ---------------------------------------------------------------------
# The registry

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

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
        "description": "What each AE2 crafting CPU is making right now, how far along it is, and which are idle.",
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
            "Totals over a time range: jobs finished, the most crafted items, how busy each CPU was, "
            "and the machines that held jobs up the longest."
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
        "description": "EU stored in a power storage machine (an LSC, batteries, wireless) over time, its trend and EU/t in and out.",
        "inputSchema": _schema({
            "machine": {"type": "string", "description": "Which machine; the first one if left out."},
            "range": _range_arg(POWER_RANGES, "day", "How far back to look."),
        }),
        "annotations": READ_ONLY,
        "handler": power,
    },
]

_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def listing():
    """tools/list's tools, in a fixed order."""
    return [{k: v for k, v in tool.items() if k != "handler"} for tool in TOOLS]


def exists(name):
    return name in _BY_NAME


def call(name, args, user):
    """Runs a tool (exists(name)) for a user. Returns (result, is_error):
    result is the tool's JSON-able answer, or an error message for the
    model."""
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
