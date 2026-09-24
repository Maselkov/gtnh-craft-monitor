"""Craft requests and cancellations submitted from the browser and
carried out by craft_monitor.lua, via the queues in gcm/commands.py.

Two-sided auth:
  - Lua-facing endpoints (poll pending, report results) use the same
    API_KEY every other Lua<->server call already uses.
  - Browser-facing endpoints use the signed-in user's session;
    submitting or cancelling a craft additionally requires the
    operator or admin role."""

import time

from flask import Blueprint, g, jsonify, request

from gcm import auth, commands, icons, state, store, tracking


bp = Blueprint("craft_requests", __name__)


@bp.route("/api/craft/request", methods=["POST"])
@auth.login_required
@auth.operator_required
def craft_request_post():
    user_id = g.user["id"]

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

    req_id = commands.craft_requests.add(
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
        },
        lambda req_id: store.requests.record_request(
            req_id, user_id, label, mod, internal, damage, amount, kind, created_at
        ),
    )
    return jsonify({"ok": True, "id": req_id})


@bp.route("/api/craft/requests", methods=["GET"])
@auth.login_required
def craft_requests_get():
    user_id = g.user["id"]
    # "accepted" requests aren't returned here at all - the moment one
    # is accepted, a real pin is created and it's the pin (existing
    # infrastructure) that represents it from then on, not this
    # ephemeral request record.
    mine = commands.craft_requests.select(
        lambda r: r["user_id"] == user_id and r["status"] in ("pending", "failed")
    )
    mine.sort(key=lambda r: r["created_at"], reverse=True)
    return jsonify({"requests": mine})


@bp.route("/api/craft/requests/<int:req_id>/dismiss", methods=["POST"])
@auth.login_required
def craft_request_dismiss(req_id):
    commands.craft_requests.dismiss(req_id, g.user["id"])
    return jsonify({"ok": True})


@bp.route("/api/craft/requests/pending", methods=["GET"])
@auth.api_key_required
def craft_requests_pending():
    # Lua polling for work - every user's pending requests at once.
    return jsonify({"requests": commands.craft_requests.claim_pending()})


@bp.route("/api/craft/requests/<int:req_id>/result", methods=["POST"])
@auth.api_key_required
def craft_request_result(req_id):
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ("accepted", "failed"):
        return jsonify({"error": "status must be accepted or failed"}), 400

    cpu_name = payload.get("cpu_name")
    reason = None if status == "accepted" else payload.get("reason") or "request failed"

    # The audit row first: if writing it fails, the request stays pending
    # in memory and its expiry still gets to close the row.
    store.requests.resolve_request(req_id, status, reason, cpu_name)

    if status == "accepted":
        # Accepted requests leave the queue: the CPU pin takes over.
        req = commands.craft_requests.resolve(req_id, status, remove=True, cpu_name=cpu_name)
    else:
        req = commands.craft_requests.resolve(req_id, status, reason=reason)
    if req is None:
        return jsonify({"error": "unknown request id"}), 404

    if status == "accepted" and cpu_name:
        tracking.start_requested_job(
            req["user_id"], cpu_name, req.get("label"), req.get("icon"), req["created_at"]
        )

    return jsonify({"ok": True})


@bp.route("/api/craft/cancel", methods=["POST"])
@auth.login_required
@auth.operator_required
def craft_cancel_post():
    user_id = g.user["id"]

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

    # A CPU runs one job after another, so the cancel names the job too:
    # what it's making, as the browser showed it and as the latest status
    # report has it. If those already differ, the job the user meant to
    # cancel is gone. Lua checks it once more against the CPU itself
    # right before cancelling. None when the CPU has no Crafting Monitor
    # to report its output - then only the CPU name is known.
    expected_output = tracking.output_identity(job)
    shown = payload.get("expected_output")
    if isinstance(shown, dict) and expected_output and not tracking.same_output(
        expected_output, shown
    ):
        return jsonify({"error": "a different job is now running on this CPU - not cancelled"}), 409

    created_at = time.time()
    req_id = commands.cancel_requests.add(
        {
            "user_id": user_id,
            "cpu_name": cpu_name,
            "expected_output": expected_output,
            # status: pending -> resolved
            "success": None,
            "reason": None,
            "created_at": created_at,
        },
        lambda req_id: store.requests.record_cancel(req_id, user_id, cpu_name, created_at),
    )
    return jsonify({"ok": True, "id": req_id})


@bp.route("/api/craft/cancel/<int:req_id>", methods=["GET"])
@auth.login_required
def craft_cancel_get(req_id):
    user_id = g.user["id"]
    found = commands.cancel_requests.select(
        lambda r: r["id"] == req_id and r["user_id"] == user_id
    )
    if not found:
        return jsonify({"error": "unknown request id"}), 404
    return jsonify(found[0])


@bp.route("/api/craft/cancel/pending", methods=["GET"])
@auth.api_key_required
def craft_cancel_pending():
    return jsonify({"requests": commands.cancel_requests.claim_pending()})


@bp.route("/api/craft/cancel/<int:req_id>/result", methods=["POST"])
@auth.api_key_required
def craft_cancel_result(req_id):
    payload = request.get_json(silent=True) or {}
    success = bool(payload.get("success"))
    reason = payload.get("reason")

    store.requests.resolve_cancel(req_id, success, reason)

    req = commands.cancel_requests.resolve(req_id, "resolved", success=success, reason=reason)
    if req is None:
        return jsonify({"error": "unknown request id"}), 404

    return jsonify({"ok": True})
