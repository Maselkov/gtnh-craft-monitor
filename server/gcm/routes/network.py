"""ME network browser endpoints: scan ingestion from
oc/network_browser.lua, the live item list, per-item quantity history
and item pins. The logic behind them is in gcm/inventory.py."""

import secrets

from flask import abort, Blueprint, g, jsonify, request, Response

from gcm import auth, charts, inventory, state, store


bp = Blueprint("network", __name__)


@bp.route("/api/network/scan/start", methods=["POST"])
@auth.api_key_required
def network_scan_start():
    return jsonify({"ok": True, "scan_token": inventory.start_scan()})


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
    return jsonify(
        inventory.finish_scan(
            payload.get("scan_token"), payload.get("chunks_sent"), payload.get("total_errors")
        )
    )


# Changes whenever the served snapshot could: a new process (icons are
# re-resolved on reload) or any of the fields below.
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
                state.network["updated_at"],
                state.network["in_progress"],
                state.network["scan_started_at"],
                state.network["is_reconstructed"],
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
    if not internal:
        abort(400)

    range_key, rows = inventory.history(
        mod, internal, damage, kind, request.args.get("range", "day")
    )
    png_bytes = charts.cached_png(
        ("network", mod, internal, damage, kind, range_key),
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

    if not internal:
        return jsonify({"error": "missing internal"}), 400

    range_key, rows = inventory.history(
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
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    store.items.pin(user_id, mod, internal, damage, kind)
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
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    store.items.unpin(user_id, mod, internal, damage, kind)
    return jsonify({"ok": True})
