"""Sign-in/sign-out, bootstrap token rotation, and admin user/token
management."""

import time
import uuid
import sqlite3

from flask import Blueprint, g, jsonify, request

from gcm import auth, config, db, store


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

    conn = db.craft_db()
    try:
        access_token = auth.find_access_token(conn, token.strip())
        if not access_token:
            return jsonify({"error": "invalid credentials"}), 401
        user = conn.execute(
            "SELECT id, display_name, role, disabled_at FROM users WHERE id = ?",
            (access_token["user_id"],),
        ).fetchone()
        if not user or user[3] is not None:
            return jsonify({"error": "invalid credentials"}), 401
        conn.execute(
            "UPDATE access_tokens SET last_used_at = ? WHERE id = ?",
            (time.time(), access_token["id"]),
        )
        auth.prune_sessions(conn)
        session_token = auth.create_session(conn, access_token)
        conn.commit()
    finally:
        conn.close()

    response = jsonify(
        {
            "authenticated": True,
            "user": {"id": user[0], "display_name": user[1], "role": user[2]},
            "must_rotate_bootstrap": access_token["is_bootstrap"],
        }
    )
    response.set_cookie(
        "gcm_session",
        session_token,
        max_age=auth.SESSION_LIFETIME_SECONDS,
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
        conn = db.craft_db()
        try:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (time.time(), auth.hash_secret(session_token)),
            )
            conn.commit()
        finally:
            conn.close()
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

    conn = db.craft_db()
    try:
        replacement_token = auth.create_access_token(conn, user["id"])
        replacement_token_id, _ = auth.parse_access_token(replacement_token)
        now = time.time()
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? WHERE id = ? AND is_bootstrap = 1",
            (now, user["access_token_id"]),
        )
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE access_token_id = ? AND id != ?",
            (now, user["access_token_id"], user["session_id"]),
        )
        conn.execute(
            "UPDATE sessions SET access_token_id = ?, must_rotate_bootstrap = 0 WHERE id = ?",
            (replacement_token_id, user["session_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"token": replacement_token})


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

    conn = db.craft_db()
    try:
        user_id = f"usr_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) VALUES (?, ?, ?, ?)",
            (user_id, display_name, role, time.time()),
        )
        token = auth.create_access_token(conn, user_id)
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "display name already exists"}), 409
    finally:
        conn.close()
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
    conn = db.craft_db()
    try:
        rows = conn.execute(
            "SELECT users.id, users.display_name, users.role, users.created_at, "
            "users.disabled_at, access_tokens.id, access_tokens.created_at, "
            "access_tokens.last_used_at "
            "FROM users LEFT JOIN access_tokens "
            "ON access_tokens.user_id = users.id AND access_tokens.revoked_at IS NULL "
            "ORDER BY users.display_name COLLATE NOCASE, access_tokens.created_at"
        ).fetchall()
    finally:
        conn.close()
    users = {}
    for row in rows:
        user = users.setdefault(
            row[0],
            {
                "id": row[0],
                "display_name": row[1],
                "role": row[2],
                "created_at": row[3],
                "disabled_at": row[4],
                "tokens": [],
            },
        )
        if row[5]:
            user["tokens"].append(
                {
                    "id": row[5],
                    "created_at": row[6],
                    "last_used_at": row[7],
                }
            )
    return jsonify({"users": list(users.values())})


@bp.route("/api/admin/users/<user_id>", methods=["DELETE"])
@auth.admin_required
def admin_user_delete(user_id):
    admin = g.user
    if user_id == admin["id"]:
        return jsonify({"error": "cannot delete your own account"}), 400

    conn = db.craft_db()
    try:
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if not cursor.rowcount:
            return jsonify({"error": "unknown user"}), 404
        # access_tokens/sessions aren't ON DELETE CASCADE (SQLite foreign
        # keys are declared but not enforced unless PRAGMA foreign_keys is
        # on for this connection) - removed explicitly so a deleted user's
        # credentials can never authenticate again.
        conn.execute("DELETE FROM access_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/api/admin/users/<user_id>/tokens", methods=["POST"])
@auth.admin_required
def admin_user_token_regenerate(user_id):
    conn = db.craft_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "unknown user"}), 404
        now = time.time()
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        conn.execute(
            "UPDATE sessions SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        token = auth.create_access_token(conn, user_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"token": token})


@bp.route("/api/admin/users/<user_id>/history", methods=["GET"])
@auth.admin_required
def admin_user_history_get(user_id):
    conn = db.craft_db()
    try:
        user = conn.execute(
            "SELECT id, display_name FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user:
            return jsonify({"error": "unknown user"}), 404
    finally:
        conn.close()
    return jsonify(
        {
            "user": {"id": user[0], "display_name": user[1]},
            "events": store.requests.user_activity(user_id),
        }
    )


@bp.route("/api/admin/tokens/<token_id>/revoke", methods=["POST"])
@auth.admin_required
def admin_token_revoke_post(token_id):
    conn = db.craft_db()
    try:
        now = time.time()
        cursor = conn.execute(
            "UPDATE access_tokens SET revoked_at = ? "
            "WHERE id = ? AND revoked_at IS NULL",
            (now, token_id),
        )
        if not cursor.rowcount:
            return jsonify({"error": "active token not found"}), 404
        conn.execute(
            "UPDATE sessions SET revoked_at = ? "
            "WHERE access_token_id = ? AND revoked_at IS NULL",
            (now, token_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})
