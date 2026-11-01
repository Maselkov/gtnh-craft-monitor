"""The Status dialog's Server section: how fresh each in-game feed is,
how big the databases are, and how the background jobs last went. Reads
what the rest of the server already keeps; nothing here is stored."""

import os
import shutil
import time

from gcm import config, gamedata, patterns, retention, state, store, webhooks

STARTED_AT = time.time()

# A full scan can take 10+ minutes on a big network, and the next one
# starts 10 minutes after it ends (network_browser.lua): past these,
# something has stopped.
SCAN_STUCK_SECONDS = 1800
SCAN_STALE_SECONDS = 3600
# power_monitor.lua reports about once a minute.
POWER_STALE_SECONDS = 300


def _age(at, now):
    return None if at is None else max(0.0, now - at)


def _crafts(now):
    with state.crafts_lock:
        received_at = state.crafts["received_at"]
        cpus = len(state.crafts["jobs"])
    age = _age(received_at, now)
    return {
        "key": "crafts",
        "label": "Crafting CPUs",
        "script": "craft_monitor.lua",
        "age_seconds": age,
        "ok": age is not None and age <= config.STALE_AFTER_SECONDS,
        "cpus": cpus,
    }


def _network(now):
    with state.network_lock:
        net = state.network
        updated_at, in_progress, started = net["updated_at"], net["in_progress"], net["scan_started_at"]
        crash, rejected = net["crash"], net["last_rejected"]
        out = {
            "key": "network",
            "label": "ME network",
            "script": "network_browser.lua",
            "age_seconds": _age(updated_at, now),
            "items": net["item_count"],
            "scan_seconds": net["last_scan_seconds"],
            "scanning_for": _age(started, now) if in_progress else None,
            "levels_age_seconds": _age(net["levels_at"], now),
            "crash": crash,
            # Only one turned away since the last scan that was kept.
            "rejected": rejected if rejected and (updated_at is None or rejected["at"] > updated_at) else None,
            "reconstructed": net["is_reconstructed"],
        }
    stuck = out["scanning_for"] is not None and out["scanning_for"] > SCAN_STUCK_SECONDS
    out["ok"] = (
        out["age_seconds"] is not None
        and out["age_seconds"] <= SCAN_STALE_SECONDS
        and not stuck and not crash and not out["rejected"]
    )
    return out


def _patterns(now):
    current, updated_at = patterns.current()
    return {
        "key": "patterns",
        "label": "Patterns",
        "script": "network_browser.lua",
        "age_seconds": _age(updated_at, now),
        "patterns": len(current),
        # Read on demand, not on a schedule: an old reading is fine.
        "ok": updated_at is not None,
        # Said instead of "nothing yet" for a feed that's optional and
        # not set up, with no warning.
        "off": "no ME Interface Terminal connected (optional)" if patterns.terminal_missing() else None,
    }


def _power(now):
    out = []
    for name, last in store.power.machines():
        age = _age(last, now)
        out.append({
            "key": f"power:{name}",
            "label": f"Power: {name or 'Main'}",
            "script": "power_monitor.lua",
            "age_seconds": age,
            "ok": age is not None and age <= POWER_STALE_SECONDS,
        })
    return out


def _storage():
    files = []
    for path in (config.APP_DB_PATH, config.ITEM_HISTORY_DB_PATH, config.POWER_DB_PATH):
        size = 0
        for suffix in ("", "-wal"):
            try:
                size += os.path.getsize(path + suffix)
            except OSError:
                pass
        files.append({"name": os.path.basename(path), "bytes": size})
    try:
        usage = shutil.disk_usage(config.DATA_DIR)
        free, total = usage.free, usage.total
    except OSError:
        free = total = None
    return {"files": files, "free_bytes": free, "total_bytes": total}


def snapshot(now=None):
    now = time.time() if now is None else now
    delivery = webhooks.last_delivery
    thinned = retention.last_run
    return {
        "uptime_seconds": now - STARTED_AT,
        "version": config.SERVER_VERSION,
        "gamedata": gamedata.selected_version(),
        "feeds": [_crafts(now), _network(now), _patterns(now), *_power(now)],
        "storage": _storage(),
        "retention": {
            "enabled": bool(retention.tiers()),
            "last_run": thinned and {"age_seconds": _age(thinned["at"], now), "deleted": thinned["deleted"]},
        },
        "push_subscriptions": store.push.count(),
        "webhooks": {
            "configured": len(webhooks.settings()["urls"]),
            "last": delivery and {
                "age_seconds": _age(delivery["at"], now),
                "failed": [{"host": h, "error": e} for h, e in delivery["results"] if e],
                "sent": sum(1 for _, e in delivery["results"] if not e),
            },
        },
    }
