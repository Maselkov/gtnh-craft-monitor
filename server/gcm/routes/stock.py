"""Stock rules (gcm/stock.py): a signed-in user's own low-stock alerts,
and the base's keep-in-stock targets, which only operators change since
they queue real crafts."""

from flask import Blueprint, g, jsonify, request

from gcm import auth, icons, state, stock, store


bp = Blueprint("stock", __name__)


def _item_from(payload):
    """The item a rule is about, from a request body, or an error
    message."""
    item = {
        "label": payload.get("label"),
        "mod": payload.get("mod") or None,
        "internal": payload.get("internal"),
        "damage": payload.get("damage"),
        "kind": payload.get("kind") or "item",
        "variant": payload.get("variant") or None,
    }
    if not item["label"] or not item["internal"]:
        return None, "missing label/internal"
    if item["kind"] not in ("item", "fluid"):
        return None, "kind must be item or fluid"
    if item["damage"] is not None and (isinstance(item["damage"], bool) or not isinstance(item["damage"], int)):
        return None, "damage must be a whole number"
    if item["variant"] is not None and not isinstance(item["variant"], str):
        return None, "variant must be a string"
    return item, None


def _key(item):
    return store.items.item_key(item["mod"], item["internal"], item["damage"], item["kind"], item["variant"])


def _positive(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0


def _network_item(key):
    with state.network_lock:
        return next((it for it in state.network["items"] if store.items.key_of(it) == key), None)


@bp.route("/api/stock/rules", methods=["GET"])
@auth.public
def stock_rules_get():
    """Every keep-in-stock target, and the signed-in user's own alerts,
    each with the item's amount in the latest scan (null before any)."""
    with state.network_lock:
        items = state.network["items"]
        scanned = state.network["updated_at"] is not None or bool(items)
    sizes = stock.current_sizes(items)
    with state.crafts_lock:
        jobs = list(state.crafts["jobs"])
    pending = stock.pending_auto_requests()

    def with_item(rule):
        current = sizes.get(rule["key"], 0) if scanned else None
        return {
            "key": rule["key"],
            "label": rule["label"],
            "mod": rule["mod"],
            "internal": rule["internal"],
            "damage": rule["damage"],
            "kind": rule["kind"],
            "variant": rule["variant"],
            "icon": icons.resolve_icon(rule["mod"], rule["internal"], rule["damage"], rule["label"], rule["variant"]),
            "current": current,
        }

    targets = []
    for t in store.stock.targets():
        row = with_item(t)
        low = row["current"] is not None and row["current"] < t["keep_at_least"]
        # Only while low: a CPU making the item for some other reason
        # isn't this target's doing.
        cpu = stock.busy_cpu_making(t, jobs) if low else None
        if t["key"] in pending:
            status = "requested"
        elif cpu:
            status = "crafting"
        elif not t["enabled"]:
            status = "off"
        elif low and t["last_status"] in ("failed", "waiting"):
            status = t["last_status"]
        else:
            status = "low" if low else "ok"
        targets.append({
            **row,
            "keep_at_least": t["keep_at_least"],
            "refill_to": t["refill_to"],
            "enabled": t["enabled"],
            "status": status,
            "reason": t["last_reason"] if status in ("failed", "waiting") else None,
            "cpu": cpu,
            "last_requested_at": t["last_requested_at"],
            "last_status": t["last_status"],
            "last_reason": t["last_reason"],
        })

    alerts = []
    user = auth.session_user()
    if user:
        for a in store.stock.alerts_for_user(user["id"]):
            row = with_item(a)
            alerts.append({**row, "below": a["below"], "low": row["current"] is not None and row["current"] < a["below"]})
    return jsonify({"targets": targets, "alerts": alerts})


@bp.route("/api/stock/alert", methods=["POST"])
@auth.login_required
def stock_alert_set():
    payload = request.get_json(silent=True) or {}
    item, error = _item_from(payload)
    if error:
        return jsonify({"error": error}), 400
    below = payload.get("below")
    if not _positive(below):
        return jsonify({"error": "below must be a positive number"}), 400
    store.stock.set_alert(g.user["id"], item, below)
    return jsonify({"ok": True})


@bp.route("/api/stock/alert/delete", methods=["POST"])
@auth.login_required
def stock_alert_delete():
    item, error = _item_from(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    store.stock.delete_alert(g.user["id"], _key(item))
    return jsonify({"ok": True})


@bp.route("/api/stock/target", methods=["POST"])
@auth.login_required
@auth.operator_required
def stock_target_set():
    payload = request.get_json(silent=True) or {}
    item, error = _item_from(payload)
    if error:
        return jsonify({"error": error}), 400
    keep, refill = payload.get("keep_at_least"), payload.get("refill_to")
    if not _positive(keep) or not _positive(refill):
        return jsonify({"error": "keep_at_least and refill_to must be positive numbers"}), 400
    if refill <= keep:
        return jsonify({"error": "refill_to must be more than keep_at_least"}), 400
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be true or false"}), 400
    # The game can only make what it has a pattern for, and the last
    # scan is the only place that says.
    network_item = _network_item(_key(item))
    if not (network_item and network_item.get("isCraftable")):
        return jsonify({"error": "only items the network can craft can be kept in stock"}), 400
    store.stock.set_target(g.user["id"], item, keep, refill, enabled)
    return jsonify({"ok": True})


@bp.route("/api/stock/target/delete", methods=["POST"])
@auth.login_required
@auth.operator_required
def stock_target_delete():
    item, error = _item_from(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    store.stock.delete_target(_key(item))
    return jsonify({"ok": True})
