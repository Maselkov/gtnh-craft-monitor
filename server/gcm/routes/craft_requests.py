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

from gcm import auth, commands, nbt, state, stock, store, tracking


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
    variant = payload.get("variant") or None
    variant_name = payload.get("variant_name") or None

    if not label or not internal:
        return jsonify({"error": "missing label/internal"}), 400
    if not isinstance(amount, (int, float)) or amount <= 0:
        return jsonify({"error": "amount must be a positive number"}), 400
    if kind not in ("item", "fluid"):
        return jsonify({"error": "kind must be item or fluid"}), 400

    if variant is not None and not isinstance(variant, str):
        return jsonify({"error": "variant must be a string"}), 400

    req_id = commands.queue_craft(
        user_id, label, mod, internal, damage, amount, kind, variant, variant_name
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
    # A keep-in-stock target's requests are shown with the target
    # instead (routes/stock.py), not as this user's own.
    mine = commands.craft_requests.select(
        lambda r: r["user_id"] == user_id
        and r["status"] in ("pending", "failed")
        and r.get("source") != "auto"
    )
    mine.sort(key=lambda r: r["created_at"], reverse=True)
    for r in mine:
        r.pop("tag", None)  # only for the game
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


@bp.route("/api/craft/requests/<int:req_id>/match", methods=["POST"])
@auth.api_key_required
def craft_request_match(req_id):
    """Which of the patterns craft_monitor.lua found makes this request's
    NBT variant. It sends each pattern output's NBT tag (hex, or null if
    it couldn't read one); they're compared by the same key-order-
    independent hash that made the variant id, since the same NBT can
    serialize to different bytes. Returns the 1-based index of the match,
    or null, plus each tag's variant id for diagnosing a miss."""
    payload = request.get_json(silent=True) or {}
    tags = payload.get("tags")
    if not isinstance(tags, list):
        return jsonify({"error": "tags must be a list"}), 400
    found = commands.craft_requests.select(lambda r: r["id"] == req_id)
    if not found:
        return jsonify({"error": "unknown request id"}), 404
    variant = found[0].get("variant")

    variants = []
    for tag in tags:
        try:
            variants.append(nbt.canonical_hash(nbt.parse_hex(tag)) if isinstance(tag, str) else None)
        except nbt.NbtError:
            variants.append(None)
    index = next((i + 1 for i, v in enumerate(variants) if v and v == variant), None)
    return jsonify({"index": index, "variant": variant, "variants": variants})


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

    auto = req.get("source") == "auto"
    if auto:
        stock.auto_request_result(req, status, reason)
    if status == "accepted" and cpu_name:
        tracking.start_requested_job(
            req["user_id"], cpu_name, req.get("label"), req.get("icon"), req["created_at"],
            request_id=req_id if payload.get("watching") is True else None,
            auto=auto,
        )

    return jsonify({"ok": True})


@bp.route("/api/craft/requests/<int:req_id>/outcome", methods=["POST"])
@auth.api_key_required
def craft_request_outcome(req_id):
    # How an accepted request's job ended, from its own AE2 crafting link
    # - sent only for requests accepted with watching=true.
    payload = request.get_json(silent=True) or {}
    outcome = payload.get("outcome")
    cpu_name = payload.get("cpu_name")
    if outcome not in ("finished", "cancelled"):
        return jsonify({"error": "outcome must be finished or cancelled"}), 400
    if not isinstance(cpu_name, str) or not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400
    # Not an error when the job is no longer tracked: it was already
    # judged without this, and there's nothing for the game to retry.
    return jsonify({"ok": True, "applied": tracking.report_outcome(req_id, cpu_name, outcome)})


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
