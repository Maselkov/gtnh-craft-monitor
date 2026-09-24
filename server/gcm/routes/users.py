"""Sign-in/sign-out, bootstrap token rotation, and admin user/token
management."""

from flask import Blueprint, g, jsonify, request

from gcm import auth, config, store


bp = Blueprint("users", __name__)


@bp.route("/api/auth/session", methods=["GET"])
@auth.public
def auth_session_get():
    user = auth.session_user()
    if not user:
        return jsonify({"authenticated": False})
    return jsonify(
        {
            "authenticated": True,
            "user": {key: user[key] for key in ("id", "display_name", "role")},
            "must_rotate_bootstrap": user["must_rotate_bootstrap"],
        }
    )


@bp.route("/api/auth/login", methods=["POST"])
@auth.public
def auth_login_post():
    payload = request.get_json(silent=True) or {}
    token = payload.get("token")
    if not isinstance(token, str):
        return jsonify({"error": "invalid credentials"}), 401

    signed_in = store.users.sign_in(token.strip(), config.SESSION_LIFETIME_SECONDS)
    if not signed_in:
        return jsonify({"error": "invalid credentials"}), 401
    user, session_token, must_rotate_bootstrap = signed_in

    response = jsonify(
        {
            "authenticated": True,
            "user": user,
            "must_rotate_bootstrap": must_rotate_bootstrap,
        }
    )
    response.set_cookie(
        "gcm_session",
        session_token,
        max_age=config.SESSION_LIFETIME_SECONDS,
        secure=config.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


@bp.route("/api/auth/logout", methods=["POST"])
@auth.public
def auth_logout_post():
    session_token = request.cookies.get("gcm_session")
    if session_token:
        store.users.sign_out(session_token)
    response = jsonify({"ok": True})
    response.delete_cookie("gcm_session", path="/")
    return response


@bp.route("/api/auth/rotate-bootstrap", methods=["POST"])
@auth.custom_check
def auth_rotate_bootstrap_post():
    # Only the admin session that signed in with the bootstrap token may
    # rotate it - one combined check with one deliberately uninformative
    # message, rather than admin_required's.
    user = auth.session_user()
    if not user or not user["must_rotate_bootstrap"] or not auth.is_admin(user):
        return jsonify({"error": "bootstrap rotation is not available"}), 403
    return jsonify({"token": store.users.rotate_bootstrap_token(user)})


@bp.route("/api/admin/users", methods=["POST"])
@auth.admin_required
def admin_user_post():
    payload = request.get_json(silent=True) or {}
    display_name = (payload.get("display_name") or "").strip()
    role = payload.get("role")
    if not display_name or role not in ("viewer", "operator", "admin"):
        return (
            jsonify(
                {"error": "display_name and a viewer, operator, or admin role are required"}
            ),
            400,
        )

    created = store.users.create(display_name, role)
    if not created:
        return jsonify({"error": "display name already exists"}), 409
    user_id, token = created
    return (
        jsonify(
            {
                "user": {"id": user_id, "display_name": display_name, "role": role},
                "token": token,
            }
        ),
        201,
    )


@bp.route("/api/admin/users", methods=["GET"])
@auth.admin_required
def admin_users_get():
    return jsonify({"users": store.users.all_with_tokens()})


@bp.route("/api/admin/users/<user_id>", methods=["DELETE"])
@auth.admin_required
def admin_user_delete(user_id):
    if user_id == g.user["id"]:
        return jsonify({"error": "cannot delete your own account"}), 400
    if not store.users.delete(user_id):
        return jsonify({"error": "unknown user"}), 404
    return jsonify({"ok": True})


@bp.route("/api/admin/users/<user_id>/tokens", methods=["POST"])
@auth.admin_required
def admin_user_token_regenerate(user_id):
    token = store.users.replace_credentials(user_id)
    if not token:
        return jsonify({"error": "unknown user"}), 404
    return jsonify({"token": token})


@bp.route("/api/admin/users/<user_id>/history", methods=["GET"])
@auth.admin_required
def admin_user_history_get(user_id):
    display_name = store.users.display_name(user_id)
    if display_name is None:
        return jsonify({"error": "unknown user"}), 404
    return jsonify(
        {
            "user": {"id": user_id, "display_name": display_name},
            "events": store.requests.user_activity(user_id),
        }
    )


@bp.route("/api/admin/tokens/<token_id>/revoke", methods=["POST"])
@auth.admin_required
def admin_token_revoke_post(token_id):
    if not store.users.revoke_token(token_id):
        return jsonify({"error": "active token not found"}), 404
    return jsonify({"ok": True})
