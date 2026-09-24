"""ME network browser, fed by oc/network_browser.lua: scan ingestion,
the live item list, per-item quantity history and item pins."""

import time
import secrets

from flask import abort, Blueprint, current_app, jsonify, request, Response

from gcm import auth, charts, db, icons, state


bp = Blueprint("network", __name__)


@bp.route("/api/network/scan/start", methods=["POST"])
def network_scan_start():
    if not auth.require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    with state.network_lock:
        state.network_buffer.clear()
        state.network["in_progress"] = True
        state.network["scan_started_at"] = time.time()
        state.network["chunks_received"] = 0
        token = secrets.token_hex(8)
        state.network["current_scan_token"] = token
    return jsonify({"ok": True, "scan_token": token})


def _check_scan_token(payload):
    """True if payload's scan_token matches the currently active scan.
    Must be called with _network_lock already held."""
    token = payload.get("scan_token") if isinstance(payload, dict) else None
    current = state.network.get("current_scan_token")
    return bool(current) and token == current


@bp.route("/api/network/scan/batch", methods=["POST"])
def network_scan_batch():
    if not auth.require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400
    items = icons.attach_item_icons(payload.get("items", []))
    with state.network_lock:
        # See network_scan_finish() for the full reasoning - this batch
        # only gets accepted if it actually belongs to the scan this
        # process currently thinks is active. A 200 status either way,
        # not 409/etc - request_with_timeout on the Lua side never
        # inspects the HTTP status code at all (confirmed directly from
        # its source: it only reads the response body via OC's request
        # iterator, which doesn't expose status), so the rejection
        # signal has to live in the JSON body itself for Lua to actually
        # see it, not in a status code nothing on that side ever checks.
        if not _check_scan_token(payload):
            return jsonify(
                {
                    "ok": False,
                    "error": "stale_scan_token",
                    "detail": "This batch doesn't belong to the currently active scan "
                    "(the server may have restarted, or a newer scan already "
                    "started) - discarding it rather than accumulating a partial result.",
                }
            )
        state.network_buffer.extend(items)
        state.network["chunks_received"] += 1
        buffered = len(state.network_buffer)
        chunks_received = state.network["chunks_received"]
    return jsonify(
        {"ok": True, "buffered": buffered, "chunks_received": chunks_received}
    )


@bp.route("/api/network/scan/finish", methods=["POST"])
def network_scan_finish():
    if not auth.require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    with state.network_lock:
        # Primary defense #1: does this scan/finish actually belong to
        # the scan this process currently thinks is active? A structural
        # check, not a heuristic - it directly answers "is this data
        # really from a complete, uninterrupted scan cycle" rather than
        # guessing from how much data showed up. Catches a server
        # restart mid-scan (a fresh process has no memory of any
        # previously-issued token, so nothing from the old scan can ever
        # match) AND a newer scan overlapping the tail of an older one on
        # the SAME process with no restart at all (scan/start always
        # mints a brand new token, immediately invalidating whatever the
        # previous scan was using).
        if not _check_scan_token(payload):
            state.network["in_progress"] = False
            state.network["scan_started_at"] = None
            current_app.logger.warning(
                "Rejected scan/finish: stale or missing scan_token - the server "
                "likely restarted mid-scan, or a newer scan already started. "
                "Keeping the previous snapshot."
            )
            return jsonify(
                {
                    "ok": False,
                    "error": "stale_scan_token",
                    "item_count": state.network["item_count"],
                }
            )

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
        chunks_sent = payload.get("chunks_sent")
        total_errors = payload.get("total_errors")
        chunks_received = state.network["chunks_received"]
        if (
            chunks_sent is None
            or total_errors is None
            or total_errors != 0
            or chunks_received != chunks_sent
        ):
            state.network["in_progress"] = False
            state.network["scan_started_at"] = None
            current_app.logger.warning(
                "Rejected a network scan as incomplete: chunks_received=%s chunks_sent=%s "
                "total_errors=%s - keeping the previous snapshot rather than recording "
                "every missing item as vanished.",
                chunks_received,
                chunks_sent,
                total_errors,
            )
            return jsonify(
                {
                    "ok": True,
                    "item_count": state.network["item_count"],
                    "rejected": True,
                    "reason": f"incomplete scan (received {chunks_received}/{chunks_sent} chunks, "
                    f"{total_errors} errors) - discarded",
                }
            )

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
    # already committed to _network_state above), just best-effort
    # extras (history charts, restart recovery) - a failure in either
    # one is logged, not allowed to turn into a 500 that makes
    # network_browser.lua think the whole scan failed when it didn't.
    try:
        _record_item_history_changes(old_items, new_items)
    except Exception as e:
        current_app.logger.exception(
            "item history recording failed (scan itself still succeeded): %s", e
        )
    try:
        _persist_network_snapshot(new_items)
    except Exception as e:
        current_app.logger.exception(
            "network snapshot persistence failed (scan itself still succeeded): %s", e
        )

    return jsonify({"ok": True, "item_count": item_count})


@bp.route("/api/network", methods=["GET"])
def network_get():
    with state.network_lock:
        return jsonify(
            {
                "items": state.network["items"],
                "item_count": state.network["item_count"],
                "updated_at": state.network["updated_at"],
                "in_progress": state.network["in_progress"],
                "scan_started_at": state.network["scan_started_at"],
                "is_reconstructed": state.network["is_reconstructed"],
            }
        )


# ---------------------------------------------------------------------
# Item/fluid quantity history (item_history.db). Change-only storage -
# see the item_history section in gcm/db.py for why.
def _persist_network_snapshot(items):
    """Wholesale replace, not change-only - this is a MIRROR of the live
    snapshot, not a history log. Runs after every real scan; the DELETE+
    INSERT happens in one transaction so a reader never sees a half-
    written table.

    INSERT OR REPLACE, not a plain INSERT - confirmed from a real
    production crash: a scan's item list can apparently contain two or
    more entries resolving to the same item_key (exact cause not
    pinned down - a plain INSERT just surfaces it as an uncaught
    IntegrityError instead of handling it). Duplicates aren't corruption
    worth treating as fatal either way, so REPLACE just lets the later
    occurrence in the list win, matching ordinary "last write wins"
    semantics rather than crashing the entire scan/finish request over
    what's genuinely a best-effort persistence step, not the actual
    scan result the website itself depends on."""
    now = time.time()
    rows = []
    for it in items:
        key = db.item_key(
            it.get("mod"), it.get("internal"), it.get("damage"), it.get("kind")
        )
        rows.append(
            (
                key,
                it.get("mod"),
                it.get("internal"),
                it.get("damage"),
                it.get("kind") or "item",
                it.get("name"),
                it.get("size", 0) or 0,
                1 if it.get("isCraftable") else 0,
                now,
            )
        )
    conn = db.item_history_db()
    try:
        conn.execute("DELETE FROM network_snapshot")
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO network_snapshot "
                "(item_key, mod, internal, damage, kind, name, size, is_craftable, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def load_network_snapshot():
    """Called once at server startup - seeds _network_state from whatever
    was last persisted, so the Network tab has SOMETHING to show
    immediately rather than sitting empty until the next real scan
    completes. Marks is_reconstructed=True; network_scan_finish() clears
    it the moment a real scan actually completes."""
    conn = db.item_history_db()
    try:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind, name, size, is_craftable, updated_at FROM network_snapshot"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return

    items = []
    max_updated = 0.0
    for r in rows:
        items.append(
            {
                "mod": r[0],
                "internal": r[1],
                "damage": r[2],
                "kind": r[3],
                "name": r[4],
                "size": r[5],
                "isCraftable": bool(r[6]),
            }
        )
        max_updated = max(max_updated, r[7])

    # network_snapshot deliberately doesn't store an icon path - it's a
    # pure function of mod/internal/damage/label, always re-derivable,
    # so storing it separately would just be a cache that could drift if
    # icons_lookup.json itself ever changes between restarts. But that
    # means it has to be resolved HERE explicitly - live-scanned items
    # only ever get their icon attached inside network_scan_batch(), a
    # path reconstructed items never go through, so without this call
    # every icon on a freshly-restarted server's grid would silently be
    # missing (confirmed as a real, reported bug, not a hypothetical).
    icons.attach_item_icons(items)

    with state.network_lock:
        state.network["items"] = items
        state.network["item_count"] = len(items)
        state.network["updated_at"] = max_updated
        state.network["is_reconstructed"] = True


def _record_item_history_changes(old_items, new_items):
    """old_items/new_items: lists of item dicts (mod, internal, damage,
    kind, name, size). Writes one row per item whose size differs from
    the last-known value, including items present before but absent now
    (size implicitly 0 - they only vanish from a scan by genuinely
    having zero stock and no craftable pattern, since getItemsInNetworkById/
    getFluidsInNetwork only ever return entries AE2 itself considers
    present)."""
    old_by_key = {}
    for it in old_items or []:
        key = db.item_key(
            it.get("mod"), it.get("internal"), it.get("damage"), it.get("kind")
        )
        old_by_key[key] = it.get("size", 0)

    now = time.time()
    changes = []
    seen_keys = set()
    for it in new_items or []:
        key = db.item_key(
            it.get("mod"), it.get("internal"), it.get("damage"), it.get("kind")
        )
        seen_keys.add(key)
        new_size = it.get("size", 0)
        if old_by_key.get(key) != new_size:
            changes.append((key, it.get("name"), new_size, now))

    for key, old_size in old_by_key.items():
        if key not in seen_keys and old_size != 0:
            changes.append((key, None, 0, now))

    if not changes:
        return

    conn = db.item_history_db()
    try:
        conn.executemany(
            "INSERT INTO item_history (item_key, label, size, recorded_at) VALUES (?, ?, ?, ?)",
            changes,
        )
        conn.commit()
    finally:
        conn.close()


ITEM_HISTORY_RANGE_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "lifetime": None,
}


ITEM_HISTORY_MAX_POINTS = 800


def downsample_steps(rows, max_points=ITEM_HISTORY_MAX_POINTS):
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


@bp.route("/api/network/history/chart.png", methods=["GET"])
def network_history_chart_png():
    rate_limited = charts.rate_limit_response()
    if rate_limited:
        return rate_limited
    mod = request.args.get("mod") or None
    internal = request.args.get("internal") or None
    damage_raw = request.args.get("damage")
    damage = None
    if damage_raw not in (None, ""):
        try:
            damage = int(damage_raw)
        except ValueError:
            damage = damage_raw
    kind = request.args.get("kind") or "item"
    if not internal:
        abort(400)

    range_key, rows = _fetch_item_history_rows(
        mod, internal, damage, kind, request.args.get("range", "day")
    )
    png_bytes = charts.cached_png(
        ("network", mod, internal, damage, kind, range_key),
        lambda: charts.render_png(rows, stepped=True).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


def _fetch_item_history_rows(mod, internal, damage, kind, range_key):
    key = db.item_key(mod, internal, damage, kind)
    if range_key not in ITEM_HISTORY_RANGE_SECONDS:
        range_key = "day"
    seconds = ITEM_HISTORY_RANGE_SECONDS[range_key]

    conn = db.item_history_db()
    try:
        if seconds is None:
            cur = conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_key = ? ORDER BY recorded_at ASC",
                (key,),
            )
            rows = cur.fetchall()
        else:
            since = time.time() - seconds
            cur = conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_key = ? AND recorded_at >= ? ORDER BY recorded_at ASC",
                (key, since),
            )
            rows = cur.fetchall()

            # A step chart starting mid-air (no point until the first
            # change INSIDE the range) looks wrong/misleading - prepend
            # the last known value from BEFORE the range started, if any,
            # so the line correctly holds its prior value from the very
            # left edge of the chart.
            lead = conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_key = ? AND recorded_at < ? "
                "ORDER BY recorded_at DESC LIMIT 1",
                (key, since),
            ).fetchone()
            if lead:
                rows = [(since, lead[1])] + rows
    finally:
        conn.close()

    return range_key, downsample_steps(rows)


@bp.route("/api/network/history", methods=["GET"])
def network_history_get():
    mod = request.args.get("mod") or None
    internal = request.args.get("internal") or None
    damage_raw = request.args.get("damage")
    damage = None
    if damage_raw not in (None, ""):
        try:
            damage = int(damage_raw)
        except ValueError:
            damage = damage_raw
    kind = request.args.get("kind") or "item"

    if not internal:
        return jsonify({"error": "missing internal"}), 400

    range_key, rows = _fetch_item_history_rows(
        mod, internal, damage, kind, request.args.get("range", "day")
    )
    latest = rows[-1] if rows else None

    return jsonify(
        {
            "range": range_key,
            "points": [{"t": r[0], "size": r[1]} for r in rows],
            "latest": {"t": latest[0], "size": latest[1]} if latest else None,
        }
    )


# ---------------------------------------------------------------------
# Item pins (Network tab "favorite this item" feature) - purely a
# personal browse preference, no in-game consequence, so unlike craft
# requests/cancellation this doesn't require the operator role - any
# signed-in user can pin items.
@bp.route("/api/network/pins", methods=["GET"])
def network_item_pins_get():
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    conn = db.craft_db()
    try:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind FROM user_item_pins WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify(
        {
            "pins": [
                {"mod": r[0], "internal": r[1], "damage": r[2], "kind": r[3]}
                for r in rows
            ]
        }
    )


@bp.route("/api/network/pins", methods=["POST"])
def network_item_pins_post():
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    key = db.item_key(mod, internal, damage, kind)
    conn = db.craft_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_item_pins (user_id, item_key, mod, internal, damage, kind, pinned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, key, mod, internal, damage, kind, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/api/network/pins/unpin", methods=["POST"])
def network_item_pins_unpin():
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    key = db.item_key(mod, internal, damage, kind)
    conn = db.craft_db()
    try:
        conn.execute(
            "DELETE FROM user_item_pins WHERE user_id = ? AND item_key = ?",
            (user_id, key),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


def lookup_item_display_info(mod, internal, damage, kind):
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

    key = db.item_key(mod, internal, damage, kind)
    conn = db.item_history_db()
    try:
        row = conn.execute(
            "SELECT label, size FROM item_history WHERE item_key = ? AND label IS NOT NULL "
            "ORDER BY recorded_at DESC LIMIT 1",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return row[0], row[1]
    return None, None
