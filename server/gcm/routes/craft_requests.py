"""Craft requests and cancellations submitted from the browser and
carried out by craft_monitor.lua, via the queues in gcm/commands.py.

Two-sided auth:
  - Lua-facing endpoints (poll pending, report results) use the same
    API_KEY every other Lua<->server call already uses.
  - Browser-facing endpoints use the signed-in user's session;
    submitting or cancelling a craft additionally requires the
    operator or admin role."""


from flask import Blueprint, g, jsonify, request

from gcm import auth, commands, mctext, multiblocks, state, stock, store, tracking


bp = Blueprint("craft_requests", __name__)

# The most requests one batch may queue: a multiblock kit has at most a
# few dozen blocks.
MAX_BATCH = 100


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

    error = commands.craft_request_error(label, internal, amount, kind, variant)
    if error:
        return jsonify({"error": error}), 400

    req_id = commands.queue_craft(
        user_id, label, mod, internal, damage, amount, kind, variant, variant_name
    )
    return jsonify({"ok": True, "id": req_id})


@bp.route("/api/craft/request-batch", methods=["POST"])
@auth.login_required
@auth.operator_required
def craft_request_batch_post():
    """Several craft requests at once (a multiblock kit's): {requests:
    [{label, mod, internal, damage, amount}]}, all items. Each is queued
    as its own request, as if made one by one; none is if any is wrong."""
    payload = request.get_json(silent=True) or {}
    rows = payload.get("requests")
    if not isinstance(rows, list) or not rows:
        return jsonify({"error": "requests must be a non-empty list"}), 400
    if len(rows) > MAX_BATCH:
        return jsonify({"error": f"at most {MAX_BATCH} requests at once"}), 400
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return jsonify({"error": f"request {i + 1} isn't an object"}), 400
        error = commands.craft_request_error(row.get("label"), row.get("internal"), row.get("amount"), "item", None)
        if error:
            return jsonify({"error": f"{row.get('label') or f'request {i + 1}'}: {error}"}), 400
    ids = [
        commands.queue_craft(g.user["id"], row["label"], row.get("mod"), row["internal"], row.get("damage"),
                             row["amount"], "item")
        for row in rows
    ]
    return jsonify({"ok": True, "ids": ids})


@bp.route("/api/craft/request-kit", methods=["POST"])
@auth.login_required
@auth.operator_required
def craft_request_kit_post():
    """A multiblock kit as one craft job (commands.queue_kit): {key,
    count, tiers: {channel: tier}, amounts: {row key: how many to
    craft}}, the kit's rows as /api/multiblocks/kit gives them.

    AE2 takes a pattern's inputs from storage first and crafts only the
    rest, so each input is what's in stock of the block, up to what the
    kit needs, plus how many to craft."""
    payload = request.get_json(silent=True) or {}
    key = payload.get("key")
    tiers = payload.get("tiers") or {}
    amounts = payload.get("amounts")
    if not isinstance(key, str) or not isinstance(tiers, dict) or not isinstance(amounts, dict):
        return jsonify({"error": "key, tiers and amounts are needed"}), 400
    slots = state.kit_slots()
    if not slots:
        return jsonify({"error": "The game has no kit interface to request kits through - "
                                 "request each block on its own instead"}), 409
    try:
        count = int(payload.get("count") or 1)
    except (TypeError, ValueError):
        return jsonify({"error": "count must be a number"}), 400
    result = multiblocks.kit(key, tiers, count) if key.count(":") >= 2 else None
    if result is None:
        return jsonify({"error": "no kit for that item"}), 404
    inputs = []
    for row in result["rows"]:
        n = amounts.get(row["key"], 0)
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            return jsonify({"error": f"{row['name']}: amount must be a whole number"}), 400
        if n == 0:
            continue
        if not row["craftable"]:
            return jsonify({"error": f"{row['name']}: the network has no pattern for it"}), 400
        inputs.append({**row, "amount": min(row["have"], row["need"]) + n})
    if not inputs:
        return jsonify({"error": "nothing to request"}), 400
    controller = result["controller"]
    mod, internal, damage = controller["key"].rsplit(":", 2)
    # The damage as a number: OC reads a stack's damage only as one.
    req_id = commands.queue_kit(g.user["id"], mctext.plain(controller["name"]), mod, internal, int(damage), inputs)
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
        r.pop("kit_inputs", None)
        r.pop("kit_names", None)
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


def kit_label_used(req_id, label):
    """The kit name the game says it used for a kit request, if it's one
    of the names the request offered (commands.queue_kit); else None."""
    if not isinstance(label, str):
        return None
    found = commands.craft_requests.select(lambda r: r["id"] == req_id)
    names = found[0].get("kit_names") if found else None
    return label if names and any(n["label"] == label for n in names) else None


@bp.route("/api/craft/requests/<int:req_id>/result", methods=["POST"])
@auth.api_key_required
def craft_request_result(req_id):
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ("accepted", "failed"):
        return jsonify({"error": "status must be accepted or failed"}), 400

    cpu_name = payload.get("cpu_name")
    reason = None if status == "accepted" else payload.get("reason") or "request failed"
    label = kit_label_used(req_id, payload.get("label"))

    # The audit row first: if writing it fails, the request stays pending
    # in memory and its expiry still gets to close the row.
    store.requests.resolve_request(req_id, status, reason, cpu_name, label=label)

    named = {"label": label} if label else {}
    if status == "accepted":
        # Accepted requests leave the queue: the CPU pin takes over.
        req = commands.craft_requests.resolve(req_id, status, remove=True, cpu_name=cpu_name, **named)
    else:
        req = commands.craft_requests.resolve(req_id, status, reason=reason, **named)
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
    req_id, error = commands.request_cancel(user_id, payload.get("cpu_name"), payload.get("expected_output"))
    if error:
        message, status = error
        return jsonify({"error": message}), status
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
