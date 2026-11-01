"""Sign-in/sign-out, first-run setup, admin user/token management, and
the page's notices someone dismissed."""

from flask import Blueprint, g, jsonify, request

from gcm import auth, config, notices, store


bp = Blueprint("users", __name__)


@bp.route("/api/auth/session", methods=["GET"])
@auth.public
def auth_session_get():
    user = auth.session_user()
    if not user:
        return jsonify(
            {"authenticated": False, "setup_required": auth.setup_code() is not None, "private": auth.is_private()}
        )
    return jsonify(
        {
            "authenticated": True,
            "user": {key: user[key] for key in ("id", "display_name", "role")},
            "private": auth.is_private(),
        }
    )


def _signed_in_response(body, session_token):
    response = jsonify(body)
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
    user, session_token = signed_in
    return _signed_in_response({"authenticated": True, "user": user}, session_token)


@bp.route("/api/auth/setup", methods=["POST"])
@auth.custom_check
def auth_setup_post():
    # Checked by the setup code (gcm.auth.finish_setup), which only
    # exists while there are no users.
    payload = request.get_json(silent=True) or {}
    code = payload.get("code")
    display_name = payload.get("display_name")
    display_name = display_name.strip() if isinstance(display_name, str) else ""
    if auth.setup_code() is None:
        return jsonify({"error": "setup is already done; sign in instead"}), 409
    if not isinstance(code, str) or not display_name:
        return jsonify({"error": "the setup code and a display name are required"}), 400
    created = auth.finish_setup(code.strip(), display_name)
    if not created:
        return jsonify({"error": "wrong setup code"}), 403
    user, token, session_token = created
    return _signed_in_response({"authenticated": True, "user": user, "token": token}, session_token)


@bp.route("/api/auth/logout", methods=["POST"])
@auth.public
def auth_logout_post():
    session_token = request.cookies.get("gcm_session")
    if session_token:
        store.users.sign_out(session_token)
    response = jsonify({"ok": True})
    response.delete_cookie("gcm_session", path="/")
    return response


@bp.route("/api/admin/visibility", methods=["PUT"])
@auth.admin_required
def admin_visibility_put():
    """Body: {"private": bool} - whether only signed-in users may see the
    crafts, network and power (gcm.auth.is_private)."""
    payload = request.get_json(silent=True)
    private = payload.get("private") if isinstance(payload, dict) else None
    if not isinstance(private, bool):
        return jsonify({"error": "private must be true or false"}), 400
    auth.set_private(private)
    return jsonify({"private": private})


@bp.route("/api/notices", methods=["GET"])
@auth.login_required
@auth.operator_required
def notices_get():
    """{"dismissed": {name: value}}: the notices this user sees that were
    dismissed, and for which value (gcm/notices.py)."""
    return jsonify({"dismissed": notices.dismissed(g.user)})


@bp.route("/api/notices/<name>", methods=["PUT"])
@auth.login_required
@auth.operator_required
def notice_put(name):
    """Body: {"value": str} - dismisses the notice for that value, for
    everyone who sees it."""
    payload = request.get_json(silent=True)
    error = notices.dismiss(g.user, name, payload.get("value") if isinstance(payload, dict) else None)
    if error:
        return jsonify({"error": error[0]}), error[1]
    return notices_get()


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
