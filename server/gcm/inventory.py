"""The ME network inventory fed by oc/network_browser.lua: the scan
protocol that builds each snapshot, its persistence and restart
reload, item lookups, and per-item quantity history. The live snapshot
itself is in gcm/state.py; the HTTP side is routes/network.py."""

import logging
import secrets
import time

from gcm import icons, state, store

log = logging.getLogger(__name__)


def start_scan():
    """Begins a new scan, discarding any unfinished one, and returns its
    token."""
    with state.network_lock:
        state.network_buffer.clear()
        state.network["in_progress"] = True
        state.network["scan_started_at"] = time.time()
        state.network["chunks_received"] = 0
        token = secrets.token_hex(8)
        state.network["current_scan_token"] = token
    return token


def _is_current_scan_locked(token):
    """True if token belongs to the currently active scan. Caller holds
    state.network_lock."""
    current = state.network.get("current_scan_token")
    return bool(current) and token == current


def add_batch(token, items):
    """Buffers one batch of scanned items. Returns (buffered item count,
    chunks received so far), or None if the batch belongs to a scan
    that isn't the active one - see finish_scan() for why."""
    items = icons.attach_item_icons(items)
    with state.network_lock:
        if not _is_current_scan_locked(token):
            return None
        state.network_buffer.extend(items)
        state.network["chunks_received"] += 1
        return len(state.network_buffer), state.network["chunks_received"]


def finish_scan(token, chunks_sent, total_errors):
    """Ends the scan: promotes its buffer to the live snapshot if the
    scan is provably complete, then records history and persists the
    snapshot. Returns the response body for network_browser.lua."""
    with state.network_lock:
        # Primary defense #1: does this scan/finish actually belong to
        # the scan this process currently thinks is active? A structural
        # check, not a heuristic - it directly answers "is this data
        # really from a complete, uninterrupted scan cycle" rather than
        # guessing from how much data showed up. Catches a server
        # restart mid-scan (a fresh process has no memory of any
        # previously-issued token, so nothing from the old scan can ever
        # match) AND a newer scan overlapping the tail of an older one on
        # the SAME process with no restart at all (start_scan() always
        # mints a brand new token, immediately invalidating whatever the
        # previous scan was using).
        if not _is_current_scan_locked(token):
            state.network["in_progress"] = False
            state.network["scan_started_at"] = None
            log.warning(
                "Rejected scan/finish: stale or missing scan_token - the server "
                "likely restarted mid-scan, or a newer scan already started. "
                "Keeping the previous snapshot."
            )
            return {
                "ok": False,
                "error": "stale_scan_token",
                "item_count": state.network["item_count"],
            }

        # Primary defense #2: did every chunk network_browser.lua
        # attempted to send actually arrive, and did nothing else go
        # wrong along the way? chunks_received is counted independently
        # HERE, server-side, from real accepted scan/batch calls - never
        # just trusted from whatever Lua claims - and compared against
        # chunks_sent, which Lua increments on every attempt (success OR
        # failure, before the outcome is even known - counting only
        # successes would let a failed POST silently vanish from both
        # sides' tallies, defeating the whole check). total_errors
        # (already tracked end-to-end in run_scan() for its own debug
        # logging, just never sent before now) catches the other place
        # data can go missing that a chunk-count comparison alone can't
        # see at all: a getItemsInNetworkById() call failing outright
        # for some candidate batch never produces a chunk to send in the
        # first place, so there's nothing for a transit-only check to
        # notice as lost.
        #
        # This replaces an earlier retention-fraction heuristic (reject
        # if the scan came back under some % of the last snapshot's
        # size) that was deliberately removed: arbitrary threshold,
        # genuinely no way to pick a number that's simultaneously loose
        # enough to accept a real, deliberate mass removal and tight
        # enough to reliably catch a partial scan - and it could get
        # permanently stuck rejecting a legitimate smaller network
        # forever, since a correctly-tokened, complete-but-smaller scan
        # would just keep getting compared against the same stale,
        # larger snapshot it could never update past. This mutual-
        # verification approach has no threshold to tune at all: a
        # scan is either provably complete or it isn't, and a genuine
        # mass removal - every query and POST succeeding, zero errors,
        # counts matching - is correctly accepted rather than guessed at.
        chunks_received = state.network["chunks_received"]
        if (
            chunks_sent is None
            or total_errors is None
            or total_errors != 0
            or chunks_received != chunks_sent
        ):
            state.network["in_progress"] = False
            state.network["scan_started_at"] = None
            log.warning(
                "Rejected a network scan as incomplete: chunks_received=%s chunks_sent=%s "
                "total_errors=%s - keeping the previous snapshot rather than recording "
                "every missing item as vanished.",
                chunks_received,
                chunks_sent,
                total_errors,
            )
            return {
                "ok": True,
                "item_count": state.network["item_count"],
                "rejected": True,
                "reason": f"incomplete scan (received {chunks_received}/{chunks_sent} chunks, "
                f"{total_errors} errors) - discarded",
            }

        old_items = state.network["items"]
        new_items = list(state.network_buffer)
        state.network["items"] = new_items
        state.network["item_count"] = len(new_items)
        state.network["updated_at"] = time.time()
        state.network["in_progress"] = False
        state.network["is_reconstructed"] = False  # this is a real, live scan now
        item_count = state.network["item_count"]

    # Outside the lock - a SQLite write shouldn't hold up anyone reading
    # the live snapshot at the same moment. Both wrapped defensively -
    # neither is the actual scan result the website depends on (that's
    # already committed to state.network above), just best-effort
    # extras (history charts, restart recovery) - a failure in either
    # one is logged, not allowed to turn into a 500 that makes
    # network_browser.lua think the whole scan failed when it didn't.
    try:
        store.items.record_changes(old_items, new_items)
    except Exception as e:
        log.exception("item history recording failed (scan itself still succeeded): %s", e)
    try:
        store.items.save_snapshot(new_items)
    except Exception as e:
        log.exception(
            "network snapshot persistence failed (scan itself still succeeded): %s", e
        )

    return {"ok": True, "item_count": item_count}


def load_snapshot():
    """Called once at server startup - seeds the live network state from
    whatever was last persisted, so the Network tab has SOMETHING to show
    immediately rather than sitting empty until the next real scan
    completes. Marks is_reconstructed=True; finish_scan() clears it the
    moment a real scan actually completes."""
    items, updated_at = store.items.load_snapshot()
    if not items:
        return

    # network_snapshot deliberately doesn't store an icon path - it's a
    # pure function of mod/internal/damage/label, always re-derivable,
    # so storing it separately would just be a cache that could drift if
    # icons_lookup.json itself ever changes between restarts. But that
    # means it has to be resolved HERE explicitly - live-scanned items
    # only ever get their icon attached inside add_batch(), a path
    # reconstructed items never go through, so without this call every
    # icon on a freshly-restarted server's grid would silently be
    # missing (confirmed as a real, reported bug, not a hypothetical).
    icons.attach_item_icons(items)

    with state.network_lock:
        state.network["items"] = items
        state.network["item_count"] = len(items)
        state.network["updated_at"] = updated_at
        state.network["is_reconstructed"] = True


def item_display_info(mod, internal, damage, kind):
    """Returns (label, size) for OG-tag purposes. Tries the live network
    snapshot first (freshest); falls back to item_history's most recent
    row if the item isn't currently in the snapshot (e.g. it dropped to
    genuinely zero stock and isn't craftable, so it's absent from the
    last scan entirely) - same fallback chain the frontend's history
    popup already relies on, just done server-side here."""
    with state.network_lock:
        for it in state.network["items"]:
            if (
                (it.get("mod") or None) == (mod or None)
                and it.get("internal") == internal
                and (it.get("damage") if it.get("damage") is not None else None)
                == (damage if damage is not None else None)
                and (it.get("kind") or "item") == (kind or "item")
            ):
                return it.get("name"), it.get("size")

    return store.items.last_recorded(store.items.item_key(mod, internal, damage, kind))


HISTORY_RANGE_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "lifetime": None,
}


HISTORY_MAX_POINTS = 800


def downsample_steps(rows, max_points=HISTORY_MAX_POINTS):
    """rows: list of (recorded_at, size), ascending. Unlike power's
    bucket-AVERAGING, this picks the LAST real recorded value in each
    bucket rather than an average - item quantity is a step function,
    so averaging would invent sizes that were never actually true at
    any real moment. Preserves genuine observed values instead."""
    n = len(rows)
    if n <= max_points:
        return rows
    bucket_size = n / max_points
    out = []
    i = 0.0
    while int(i) < n:
        start = int(i)
        end = min(n, max(start + 1, int(i + bucket_size)))
        out.append(rows[end - 1])
        i += bucket_size
    return out


def history(mod, internal, damage, kind, range_key):
    """(range_key, rows) of one item's quantity history for a chart range
    name, downsampled; unknown names mean "day"."""
    if range_key not in HISTORY_RANGE_SECONDS:
        range_key = "day"
    seconds = HISTORY_RANGE_SECONDS[range_key]
    since = None if seconds is None else time.time() - seconds
    rows = store.items.history(store.items.item_key(mod, internal, damage, kind), since)
    return range_key, downsample_steps(rows)
