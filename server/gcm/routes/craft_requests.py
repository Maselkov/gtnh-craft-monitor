"""Craft requests and cancellations submitted from the browser and
carried out by craft_monitor.lua, via the queues in gcm/state.py.

Two-sided auth:
  - Lua-facing endpoints (poll pending, report results) use the same
    API_KEY every other Lua<->server call already uses.
  - Browser-facing endpoints use the signed-in user's session;
    submitting or cancelling a craft additionally requires the
    operator or admin role."""

import time

from flask import Blueprint, jsonify, request

from gcm import auth, db, history, icons, state


bp = Blueprint("craft_requests", __name__)


@bp.route("/api/craft/request", methods=["POST"])
def craft_request_post():
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    if not auth.is_operator(auth.session_user()):
        return jsonify({"error": "operator access required"}), 403

    payload = request.get_json(silent=True) or {}
    label = payload.get("label")
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    amount = payload.get("amount")
    kind = payload.get("kind") or "item"

    if not label or not internal:
        return jsonify({"error": "missing label/internal"}), 400
    if not isinstance(amount, (int, float)) or amount <= 0:
        return jsonify({"error": "amount must be a positive number"}), 400
    if kind not in ("item", "fluid"):
        return jsonify({"error": "kind must be item or fluid"}), 400

    icon = icons.resolve_icon(mod, internal, damage, label)
    created_at = time.time()

    req_id = state.craft_requests.add(
        {
            "user_id": user_id,
            "label": label,
            "mod": mod,
            "internal": internal,
            "damage": damage,
            "amount": amount,
            "kind": kind,  # "item" or "fluid" - craft_monitor.lua's request
            # loop needs this to pick the right getCraftables()
            # filter shape (confirmed via a real successful
            # fluid request: items filter by name+damage,
            # fluids only matched when filtered by label)
            "icon": icon,
            # status: pending -> accepted (removed from list, real pin
            # takes over) | failed (stays until dismissed)
            "reason": None,
            "cpu_name": None,
            "created_at": created_at,
        }
    )
    history.record_craft_request(
        req_id, user_id, label, mod, internal, damage, amount, kind, created_at
    )
    return jsonify({"ok": True, "id": req_id})


@bp.route("/api/craft/requests", methods=["GET"])
def craft_requests_get():
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    # "accepted" requests aren't returned here at all - the moment one
    # is accepted, a real pin is created and it's the pin (existing
    # infrastructure) that represents it from then on, not this
    # ephemeral request record.
    mine = state.craft_requests.select(
        lambda r: r["user_id"] == user_id and r["status"] in ("pending", "failed")
    )
    mine.sort(key=lambda r: r["created_at"], reverse=True)
    return jsonify({"requests": mine})


@bp.route("/api/craft/requests/<int:req_id>/dismiss", methods=["POST"])
def craft_request_dismiss(req_id):
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    with state.craft_requests.lock:
        req = state.craft_requests.requests.get(req_id)
        if req and req["user_id"] == user_id:
            del state.craft_requests.requests[req_id]
    return jsonify({"ok": True})


@bp.route("/api/craft/requests/pending", methods=["GET"])
def craft_requests_pending():
    # Lua polling for work - every user's pending requests at once.
    if not auth.require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"requests": state.craft_requests.claim_pending()})


def _create_pin_bypassing_busy_check(user_id, cpu_name):
    # Deliberately NOT going through the normal pin-creation path (which
    # requires the CPU to already show busy=true in _state["jobs"]) -
    # that state only updates on craft_monitor.lua's regular 5s poll,
    # and Lua's craft-request thread can report "accepted, CPU X" faster
    # than that next regular poll lands. The trust here comes directly
    # from Lua's own confirmation that it just watched this CPU get
    # assigned, not from re-deriving it against a possibly-stale cache.
    conn = db.craft_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_pins (user_id, cpu_name, pinned_at) VALUES (?, ?, ?)",
            (user_id, cpu_name, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


@bp.route("/api/craft/requests/<int:req_id>/result", methods=["POST"])
def craft_request_result(req_id):
    if not auth.require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ("accepted", "failed"):
        return jsonify({"error": "status must be accepted or failed"}), 400

    with state.craft_requests.lock:
        req = state.craft_requests.requests.get(req_id)
        if not req:
            return jsonify({"error": "unknown request id"}), 404
        if status == "accepted":
            req["cpu_name"] = payload.get("cpu_name")
            user_id = req["user_id"]
            cpu_name = req["cpu_name"]
            del state.craft_requests.requests[req_id]  # existing pin infra takes over now
        else:
            req["status"] = "failed"
            req["reason"] = payload.get("reason") or "request failed"
            req["failed_at"] = time.time()
            user_id = None
            cpu_name = None

    if status == "accepted" and user_id and cpu_name:
        _create_pin_bypassing_busy_check(user_id, cpu_name)
        # Also seed the transition-detection state the main status loop
        # relies on - without this, a craft that completes faster than
        # craft_monitor.lua's own 5s poll interval is invisible to
        # _process_craft_transitions entirely: it never observes a
        # busy=true sample to compare against the later busy=false one,
        # so the completion is never detected, the pin is never cleaned
        # up, and it just sits there forever pointing at an idle CPU.
        # Seeding "last known busy=true" here means the NEXT real poll -
        # even if it's the first one that ever samples this CPU - can
        # still correctly detect the transition retroactively. progress
        # is left unknown (None) rather than guessed, which _classify_status
        # now correctly reads as "finished" for exactly this reason.
        with state.tracking_lock:
            state.cpu_last_busy[cpu_name] = True
            entry = state.cpu_last_known.setdefault(
                cpu_name, {"label": None, "icon": None, "progress": None}
            )
            req_label = req.get("label")
            req_icon = req.get("icon")
            if req_label:
                entry["label"] = req_label
            if req_icon:
                entry["icon"] = req_icon

    history.update_craft_request(
        req_id, status, payload.get("reason"), payload.get("cpu_name")
    )

    return jsonify({"ok": True})


@bp.route("/api/craft/cancel", methods=["POST"])
def craft_cancel_post():
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    if not auth.is_operator(auth.session_user()):
        return jsonify({"error": "operator access required"}), 403

    payload = request.get_json(silent=True) or {}
    cpu_name = payload.get("cpu_name")
    if not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400

    # Can only cancel a CPU that's actually busy right now - same
    # reasoning as craft-request's own "only a busy CPU can be pinned"
    # check, and it doesn't make sense to queue a cancel for something
    # with nothing running on it.
    with state.crafts_lock:
        jobs = state.crafts.get("jobs", [])
    job = next((j for j in jobs if j.get("name") == cpu_name), None)
    if job is None:
        return jsonify({"error": "unknown cpu_name"}), 404
    if not job.get("busy"):
        return jsonify({"error": "CPU is not currently busy - nothing to cancel"}), 400

    created_at = time.time()
    req_id = state.cancel_requests.add(
        {
            "user_id": user_id,
            "cpu_name": cpu_name,
            # status: pending -> resolved
            "success": None,
            "reason": None,
            "created_at": created_at,
        }
    )
    history.record_craft_cancel(req_id, user_id, cpu_name, created_at)
    return jsonify({"ok": True, "id": req_id})


@bp.route("/api/craft/cancel/<int:req_id>", methods=["GET"])
def craft_cancel_get(req_id):
    user_id = auth.require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    found = state.cancel_requests.select(
        lambda r: r["id"] == req_id and r["user_id"] == user_id
    )
    if not found:
        return jsonify({"error": "unknown request id"}), 404
    return jsonify(found[0])


@bp.route("/api/craft/cancel/pending", methods=["GET"])
def craft_cancel_pending():
    if not auth.require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"requests": state.cancel_requests.claim_pending()})


@bp.route("/api/craft/cancel/<int:req_id>/result", methods=["POST"])
def craft_cancel_result(req_id):
    if not auth.require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    success = bool(payload.get("success"))
    reason = payload.get("reason")

    with state.cancel_requests.lock:
        req = state.cancel_requests.requests.get(req_id)
        if not req:
            return jsonify({"error": "unknown request id"}), 404
        req["status"] = "resolved"
        req["success"] = success
        req["reason"] = reason
        req["resolved_at"] = time.time()

    history.update_craft_cancel(req_id, success, reason)

    return jsonify({"ok": True})
