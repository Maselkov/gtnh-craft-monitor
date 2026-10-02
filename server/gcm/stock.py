"""Stock rules: per-user low-stock alerts (a push when an item drops
below a threshold) and the base's keep-in-stock targets (an AE2 craft
request when it does). Checked after every accepted network scan, and
after network_browser.lua's quicker checks of just the rules' items
between scans (see watch_list()). The
rules themselves are stored by gcm/store/stock.py; routes/stock.py
edits them.

Only the game's numbers are trusted: nothing here runs between a full
scan or a check of the watched items and the next.

Alerts fire once when an item goes below their threshold, then stay
quiet until it's back at or above it.

A target below its keep-at-least amount asks for (refill_to - current),
unless a craft for it is already under way: its own request still
waiting on the game, a CPU busy making the item, or a job that finished
making it since this scan began (the scan may not have counted that
output). Auto-crafts only start while more than
config.AUTOCRAFT_KEEP_IDLE_CPUS crafting CPUs are idle; when that can't
cover every low target, the emptiest go first. A target whose request
failed (usually missing ingredients) waits config.AUTOCRAFT_RETRY_SECONDS
before asking again."""

import logging
import time

from gcm import charts, commands, config, icons, push, state, store, tracking

log = logging.getLogger(__name__)


def evaluate_scan(started_at=None):
    """Checks every rule against the snapshot, just updated by a full
    scan, or by a check of the rules' own items begun at started_at."""
    with state.network_lock:
        items = state.network["items"]
        started_at = started_at or state.network["scan_started_at"]
    sizes = current_sizes(items)
    _check_alerts(sizes)
    _restock(sizes, started_at or time.time())


def watch_list():
    """What network_browser.lua checks between full scans: every item
    with an alert or an enabled target, as {items: [{name: "mod:internal",
    damage}], fluids: [name]}. getItemsInNetworkById() takes the name;
    the damage picks out this item from the others sharing that id."""
    items, fluids = set(), set()
    rules = [t for t in store.stock.targets() if t["enabled"]] + store.stock.all_alerts()
    for rule in rules:
        if rule["kind"] == "fluid":
            fluids.add(rule["internal"])
        elif rule["mod"]:
            items.add((f"{rule['mod']}:{rule['internal']}", rule["damage"]))
    return {
        "items": [{"name": name, "damage": damage} for name, damage in sorted(items, key=str)],
        "fluids": sorted(fluids),
    }


def current_sizes(items):
    return {store.items.key_of(it): it.get("size") or 0 for it in items}


def _amount_text(n, kind):
    return charts.format_qty(n) + (" mB" if kind == "fluid" else "")


def _check_alerts(sizes):
    changes = []
    for alert in store.stock.all_alerts():
        current = sizes.get(alert["key"], 0)  # gone from the network: none left
        low = current < alert["below"]
        if low and alert["armed"]:
            changes.append((alert["user_id"], alert["key"], False))
            push.notify_users([alert["user_id"]], push.stock_message(
                alert["key"],
                alert["label"],
                icons.resolve_icon(alert["mod"], alert["internal"], alert["damage"], alert["label"], alert["variant"]),
                f"{alert['label']} is at {_amount_text(current, alert['kind'])} "
                f"(below {_amount_text(alert['below'], alert['kind'])}).",
            ))
        elif not low and not alert["armed"]:
            changes.append((alert["user_id"], alert["key"], True))
    if changes:
        store.stock.set_armed(changes)


def _identity(target):
    return {"mod": target["mod"], "internal": target["internal"], "damage": target["damage"]}


def busy_cpu_making(target, jobs):
    """The name of a busy CPU whose job makes the target's item, or None."""
    for job in jobs:
        output = tracking.output_identity(job)
        if job.get("busy") and output and tracking.same_output(output, _identity(target)):
            return job.get("name")
    return None


def pending_auto_requests():
    """Auto requests still waiting on the game, by target item key."""
    return {
        r["target_key"]: r
        for r in commands.craft_requests.select(lambda r: r["status"] == "pending" and r.get("source") == "auto")
    }


def _cpu_budget(jobs, received_at):
    """How many auto-crafts may start now: idle CPUs past the ones kept
    free, less every request (anyone's) the game hasn't started yet."""
    if received_at is None or time.time() - received_at > config.STALE_AFTER_SECONDS:
        return 0  # no fresh word on which CPUs are idle
    idle = sum(1 for job in jobs if not job.get("busy"))
    waiting = len(commands.craft_requests.select(lambda r: r["status"] == "pending"))
    return idle - config.AUTOCRAFT_KEEP_IDLE_CPUS - waiting


def _restock(sizes, scan_started_at):
    low = []
    for target in store.stock.targets():
        current = sizes.get(target["key"], 0)
        if target["enabled"] and current < target["keep_at_least"]:
            low.append((current / target["keep_at_least"], current, target))
    if not low:
        return
    low.sort(key=lambda entry: entry[0])

    with state.crafts_lock:
        jobs = list(state.crafts["jobs"])
        received_at = state.crafts["received_at"]
    pending = pending_auto_requests()
    just_made = store.crafts.items_ended_since(scan_started_at)
    budget = _cpu_budget(jobs, received_at)
    now = time.time()

    for _, current, target in low:
        key = target["key"]
        if key in pending or busy_cpu_making(target, jobs):
            continue
        if (target["mod"], target["internal"], target["damage"]) in just_made:
            continue
        if (
            target["last_status"] == "failed"
            and target["last_requested_at"] is not None
            and now - target["last_requested_at"] < config.AUTOCRAFT_RETRY_SECONDS
        ):
            continue
        if budget <= 0:
            store.stock.record_waiting(key, "waiting for a free crafting CPU")
            continue
        req_id = commands.queue_craft(
            target["updated_by"],
            target["label"],
            target["mod"],
            target["internal"],
            target["damage"],
            target["refill_to"] - current,
            target["kind"],
            target["variant"],
            source="auto",
            target_key=key,
        )
        store.stock.record_auto_request(key, req_id, now)
        budget -= 1
        log.info("Requested %s x%s to keep it in stock", target["label"], target["refill_to"] - current)


def auto_request_result(req, status, reason):
    """The game's answer to a target's auto request: noted on the target,
    and a failure pushed to whoever has an alert on that item."""
    key = store.stock.record_auto_result(req["id"], status, reason)
    if key is None or status != "failed":
        return
    push.notify_users(store.stock.alert_holders(key), {
        **push.stock_message(key, req.get("label"), req.get("icon"), ""),
        "title": "Auto-craft failed",
        "body": f"Couldn't restock {req.get('label')}: {reason}",
    })
