"""Web Push subscriptions: the browser side of gcm/push.py."""

from flask import Blueprint, g, jsonify, request

from gcm import auth, push, store


bp = Blueprint("push", __name__)

# Real subscriptions are far shorter; this only bounds what gets stored.
_MAX_FIELD_LENGTH = 2048


@bp.route("/api/push/key", methods=["GET"])
@auth.login_required
def push_key_get():
    return jsonify({"publicKey": push.public_key()})


@bp.route("/api/push/subscribe", methods=["POST"])
@auth.login_required
def push_subscribe():
    """Body: the browser's PushSubscription.toJSON()."""
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    fields = (endpoint, keys.get("p256dh"), keys.get("auth"))
    if not all(isinstance(f, str) and 0 < len(f) <= _MAX_FIELD_LENGTH for f in fields):
        return jsonify({"error": "invalid subscription"}), 400
    if not push.endpoint_allowed(endpoint):
        return jsonify({"error": "unsupported push service"}), 400
    store.push.save_subscription(
        g.user["id"], endpoint, keys["p256dh"], keys["auth"], request.host_url.rstrip("/")
    )
    return jsonify({"ok": True})


@bp.route("/api/push/unsubscribe", methods=["POST"])
@auth.login_required
def push_unsubscribe():
    payload = request.get_json(silent=True) or {}
    endpoint = payload.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return jsonify({"error": "missing endpoint"}), 400
    store.push.delete_subscription(g.user["id"], endpoint)
    return jsonify({"ok": True})
