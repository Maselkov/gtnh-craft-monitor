"""Stock rules (gcm/stock.py): a signed-in user's own low-stock alerts,
and the base's keep-in-stock targets, which only operators change since
they queue real crafts."""

from flask import Blueprint, g, jsonify, request

from gcm import auth, stock, store


bp = Blueprint("stock", __name__)


@bp.route("/api/stock/rules", methods=["GET"])
@auth.viewer_required
def stock_rules_get():
    """Every keep-in-stock target, and the signed-in user's own alerts,
    each with the item's amount in the latest scan (null before any)."""
    return jsonify(stock.rules(auth.session_user()))


@bp.route("/api/stock/alert", methods=["POST"])
@auth.login_required
def stock_alert_set():
    payload = request.get_json(silent=True) or {}
    item, error = stock.item_from(payload)
    error = error or stock.set_alert(g.user["id"], item, payload.get("below"))
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True})


@bp.route("/api/stock/alert/delete", methods=["POST"])
@auth.login_required
def stock_alert_delete():
    item, error = stock.item_from(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    store.stock.delete_alert(g.user["id"], stock.rule_key(item))
    return jsonify({"ok": True})


@bp.route("/api/stock/target", methods=["POST"])
@auth.login_required
@auth.operator_required
def stock_target_set():
    payload = request.get_json(silent=True) or {}
    item, error = stock.item_from(payload)
    error = error or stock.set_target(
        g.user["id"], item, payload.get("keep_at_least"), payload.get("refill_to"), payload.get("enabled", True)
    )
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"ok": True})


@bp.route("/api/stock/target/delete", methods=["POST"])
@auth.login_required
@auth.operator_required
def stock_target_delete():
    item, error = stock.item_from(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    store.stock.delete_target(stock.rule_key(item))
    return jsonify({"ok": True})
