"""ME network browser endpoints: scan ingestion from
oc/network_browser.lua, the live item list, per-item quantity history
and item pins. The logic behind them is in gcm/inventory.py."""

import logging
import secrets
import time

from flask import abort, Blueprint, g, jsonify, request, Response, send_file

from gcm import auth, charts, gamedata, icons, inventory, patterns, state, stock, store

log = logging.getLogger(__name__)


bp = Blueprint("network", __name__)


@bp.route("/api/network/scan/start", methods=["POST"])
@auth.api_key_required
def network_scan_start():
    # catalog_version tells network_browser.lua to fetch
    # /api/network/catalog when it doesn't have that version's catalog yet.
    return jsonify(
        {
            "ok": True,
            "scan_token": inventory.start_scan(),
            "catalog_version": gamedata.catalog_version(),
        }
    )


@bp.route("/api/network/catalog", methods=["GET"])
@auth.api_key_required
def network_catalog_get():
    path = gamedata.catalog_path()
    if not path:
        abort(404)
    response = send_file(path, mimetype="text/plain")
    response.headers["X-Catalog-Version"] = gamedata.catalog_version()
    return response


@bp.route("/api/network/scan/batch", methods=["POST"])
@auth.api_key_required
def network_scan_batch():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400
    accepted = inventory.add_batch(payload.get("scan_token"), payload.get("items", []))
    # A 200 status either way, not 409/etc - request_with_timeout on the
    # Lua side never inspects the HTTP status code at all (confirmed
    # directly from its source: it only reads the response body via OC's
    # request iterator, which doesn't expose status), so the rejection
    # signal has to live in the JSON body itself for Lua to actually see
    # it, not in a status code nothing on that side ever checks.
    if accepted is None:
        return jsonify(
            {
                "ok": False,
                "error": "stale_scan_token",
                "detail": "This batch doesn't belong to the currently active scan "
                "(the server may have restarted, or a newer scan already "
                "started) - discarding it rather than accumulating a partial result.",
            }
        )
    buffered, chunks_received = accepted
    return jsonify(
        {"ok": True, "buffered": buffered, "chunks_received": chunks_received}
    )


@bp.route("/api/network/scan/finish", methods=["POST"])
@auth.api_key_required
def network_scan_finish():
    payload = request.get_json(silent=True) or {}
    result = inventory.finish_scan(
        payload.get("scan_token"), payload.get("chunks_sent"), payload.get("total_errors")
    )
    if result.get("ok") and not result.get("rejected"):
        # Like finish_scan()'s own extras: a failure here is logged, and
        # never makes network_browser.lua think the scan failed.
        try:
            stock.evaluate_scan()
        except Exception:
            log.exception("checking stock rules failed (the scan itself succeeded)")
    return jsonify(result)


@bp.route("/api/network/crashed", methods=["POST"])
@auth.api_key_required
def network_crashed_post():
    """network_browser.lua caught an error that killed its scan loop:
    {error, phase, free_memory}."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400
    inventory.record_crash(payload.get("error"), payload.get("phase"), payload.get("free_memory"))
    return jsonify({"ok": True})


@bp.route("/api/network/watch", methods=["GET"])
@auth.api_key_required
def network_watch_get():
    """The items network_browser.lua checks between full scans."""
    return jsonify(stock.watch_list())


@bp.route("/api/network/levels", methods=["POST"])
@auth.api_key_required
def network_levels_post():
    """A check of the watched items: {checked: what was asked for, as
    /watch gave it; items: every stack found, as in a scan batch;
    elapsed: seconds since the check began}. Like scan/batch, a refusal
    is in the body, not the status - http.lua never reads the status."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return jsonify({"error": "invalid payload"}), 400
    found = inventory.apply_levels(payload.get("checked"), payload["items"])
    if found is None:
        return jsonify({"ok": False, "error": "scan_in_progress"})
    elapsed = payload.get("elapsed")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        elapsed = 0
    try:
        stock.evaluate_scan(started_at=time.time() - elapsed)
    except Exception:
        log.exception("checking stock rules failed (the levels were still recorded)")
    return jsonify({"ok": True, "found": found})


# Changes whenever the served snapshot could: a new process (icons are
# re-resolved on reload), new game data (icons re-resolved again) or any
# of the fields below.
_PROCESS_TAG = secrets.token_hex(4)


@bp.route("/api/network", methods=["GET"])
@auth.public
def network_get():
    # Every open tab re-polls the whole item list (thousands of items),
    # but it only changes once per scan - so answer with an ETag and let
    # the browser cache turn most polls into an empty 304.
    with state.network_lock:
        etag = "-".join(
            str(v)
            for v in (
                _PROCESS_TAG,
                icons.data_version(),
                state.network["updated_at"],
                state.network["in_progress"],
                state.network["scan_started_at"],
                state.network["is_reconstructed"],
                state.network["levels_at"],
                (state.network["crash"] or {}).get("at"),
            )
        )
        if request.if_none_match.contains(etag):
            response = Response(status=304)
        else:
            response = jsonify(
                {
                    "items": state.network["items"],
                    "item_count": state.network["item_count"],
                    "updated_at": state.network["updated_at"],
                    "in_progress": state.network["in_progress"],
                    "scan_started_at": state.network["scan_started_at"],
                    "is_reconstructed": state.network["is_reconstructed"],
                    "crash": state.network["crash"],
                }
            )
    response.set_etag(etag)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/api/network/history/chart.png", methods=["GET"])
@auth.public
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
    variant = request.args.get("variant") or None
    if not internal:
        abort(400)

    range_key, rows, _ = inventory.history(
        mod, internal, damage, kind, request.args.get("range", "day"), variant
    )
    png_bytes = charts.cached_png(
        ("network", mod, internal, damage, kind, variant, range_key),
        lambda: charts.render_png(rows, stepped=True).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.route("/api/network/history", methods=["GET"])
@auth.public
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
    variant = request.args.get("variant") or None

    if not internal:
        return jsonify({"error": "missing internal"}), 400

    range_key, rows, trend = inventory.history(
        mod, internal, damage, kind, request.args.get("range", "day"), variant
    )
    latest = rows[-1] if rows else None

    return jsonify(
        {
            "range": range_key,
            "points": [{"t": r[0], "size": r[1]} for r in rows],
            "latest": {"t": latest[0], "size": latest[1]} if latest else None,
            "trend": trend,
        }
    )


# ---------------------------------------------------------------------
# Item pins (Network tab "favorite this item" feature) - purely a
# personal browse preference, no in-game consequence, so unlike craft
# requests/cancellation this doesn't require the operator role - any
# signed-in user can pin items.
@bp.route("/api/network/pins", methods=["GET"])
@auth.login_required
def network_item_pins_get():
    return jsonify({"pins": store.items.pins(g.user["id"])})


@bp.route("/api/network/pins", methods=["POST"])
@auth.login_required
def network_item_pins_post():
    user_id = g.user["id"]
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    variant = payload.get("variant") or None
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    store.items.pin(user_id, mod, internal, damage, kind, variant)
    return jsonify({"ok": True})


@bp.route("/api/network/pins/unpin", methods=["POST"])
@auth.login_required
def network_item_pins_unpin():
    user_id = g.user["id"]
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    variant = payload.get("variant") or None
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    store.items.unpin(user_id, mod, internal, damage, kind, variant)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------
# Pattern scans: network_browser.lua reading every pattern through an
# ME Interface Terminal (gcm/patterns.py). Same protocol and refusals
# as the item scan above.
@bp.route("/api/network/patterns/start", methods=["POST"])
@auth.api_key_required
def network_patterns_start():
    return jsonify({"ok": True, "scan_token": patterns.start_scan()})


@bp.route("/api/network/patterns/batch", methods=["POST"])
@auth.api_key_required
def network_patterns_batch():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("patterns"), list):
        return jsonify({"error": "invalid payload"}), 400
    accepted = patterns.add_batch(payload.get("scan_token"), payload["patterns"])
    if accepted is None:
        return jsonify({"ok": False, "error": "stale_scan_token"})
    buffered, chunks_received = accepted
    return jsonify({"ok": True, "buffered": buffered, "chunks_received": chunks_received})


@bp.route("/api/network/patterns/finish", methods=["POST"])
@auth.api_key_required
def network_patterns_finish():
    payload = request.get_json(silent=True) or {}
    return jsonify(
        patterns.finish_scan(
            payload.get("scan_token"), payload.get("chunks_sent"), payload.get("total_errors")
        )
    )


# Signed-in only, unlike the item list: each pattern says where in the
# world its machine is.
@bp.route("/api/network/patterns", methods=["GET"])
@auth.login_required
def network_patterns_get():
    etag = f"{_PROCESS_TAG}-{icons.data_version()}-{patterns.version()}"
    if request.if_none_match.contains(etag):
        response = Response(status=304)
    else:
        response = jsonify(patterns.snapshot())
    response.set_etag(etag)
    response.headers["Cache-Control"] = "private, no-cache"
    return response
