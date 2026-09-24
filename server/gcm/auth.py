"""Authentication: the Lua-side API key, user access tokens, browser
sessions and roles, and first-run admin bootstrap."""

import hashlib
import hmac
import os
import secrets
import time
import uuid

from flask import request

from gcm import config, db


def require_api_key():
    supplied_key = request.headers.get("X-API-Key", "")
    return bool(config.API_KEY) and hmac.compare_digest(supplied_key, config.API_KEY)


SESSION_LIFETIME_SECONDS = int(
    os.environ.get("SESSION_LIFETIME_SECONDS", str(7 * 86400))
)


def hash_secret(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_access_token(token):
    try:
        prefix, token_id, secret = token.split("_", 2)
    except ValueError:
        return None
    if prefix != "gcm" or not token_id or not secret:
        return None
    return token_id, secret


def create_access_token(conn, user_id, is_bootstrap=False):
    token_id = f"tok-{secrets.token_hex(12)}"
    secret = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO access_tokens "
        "(id, user_id, secret_hash, created_at, is_bootstrap) VALUES (?, ?, ?, ?, ?)",
        (token_id, user_id, hash_secret(secret), time.time(), int(is_bootstrap)),
    )
    return f"gcm_{token_id}_{secret}"


def find_access_token(conn, token):
    parsed = parse_access_token(token)
    if not parsed:
        return None
    token_id, secret = parsed
    row = conn.execute(
        "SELECT id, user_id, secret_hash, revoked_at, is_bootstrap "
        "FROM access_tokens WHERE id = ?",
        (token_id,),
    ).fetchone()
    if (
        not row
        or row[3] is not None
        or not hmac.compare_digest(row[2], hash_secret(secret))
    ):
        return None
    return {"id": row[0], "user_id": row[1], "is_bootstrap": bool(row[4])}


def create_session(conn, access_token):
    session_token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions "
        "(id, user_id, token_hash, access_token_id, must_rotate_bootstrap, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"ses_{uuid.uuid4().hex}",
            access_token["user_id"],
            hash_secret(session_token),
            access_token["id"],
            int(access_token["is_bootstrap"]),
            now,
            now + SESSION_LIFETIME_SECONDS,
        ),
    )
    return session_token


def session_user():
    session_token = request.cookies.get("gcm_session")
    if not session_token:
        return None
    conn = db.craft_db()
    try:
        row = conn.execute(
            "SELECT users.id, users.display_name, users.role, sessions.id, "
            "sessions.access_token_id, sessions.must_rotate_bootstrap FROM sessions "
            "JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL "
            "AND sessions.expires_at > ? AND users.disabled_at IS NULL",
            (hash_secret(session_token), time.time()),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "display_name": row[1],
        "role": row[2],
        "session_id": row[3],
        "access_token_id": row[4],
        "must_rotate_bootstrap": bool(row[5]),
    }


def is_admin(user):
    return user is not None and user["role"] == "admin"


def is_operator(user):
    return user is not None and user["role"] in ("operator", "admin")


def prune_sessions(conn):
    # Expired and revoked sessions can never authenticate again.
    conn.execute(
        "DELETE FROM sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
        (time.time(),),
    )


def bootstrap_admin():
    token = os.environ.get("GCM_BOOTSTRAP_ADMIN_TOKEN", "").strip()
    if not token:
        return
    parsed = parse_access_token(token)
    if not parsed:
        raise RuntimeError(
            "GCM_BOOTSTRAP_ADMIN_TOKEN must use the gcm_<token-id>_<secret> format"
        )

    conn = db.craft_db()
    try:
        token_id, secret = parsed
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count:
            row = conn.execute(
                "SELECT user_id FROM access_tokens "
                "WHERE id = ? AND secret_hash = ? AND revoked_at IS NULL",
                (token_id, hash_secret(secret)),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE access_tokens SET is_bootstrap = 1 WHERE id = ?",
                    (token_id,),
                )
                conn.execute(
                    "UPDATE sessions SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (time.time(), row[0]),
                )
            conn.commit()
            return
        user_id = f"usr_{uuid.uuid4().hex}"
        now = time.time()
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) VALUES (?, ?, 'admin', ?)",
            (user_id, os.environ.get("GCM_BOOTSTRAP_ADMIN_NAME", "Administrator"), now),
        )
        conn.execute(
            "INSERT INTO access_tokens "
            "(id, user_id, secret_hash, created_at, is_bootstrap) VALUES (?, ?, ?, ?, 1)",
            (token_id, user_id, hash_secret(secret), now),
        )
        conn.commit()
    finally:
        conn.close()


def require_initial_admin():
    conn = db.craft_db()
    try:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    if user_count:
        return
    raise RuntimeError(
        "No users exist. Create .env from .env.example, set "
        "GCM_BOOTSTRAP_ADMIN_TOKEN, then restart the server."
    )


def require_user_id():
    user = session_user()
    return user["id"] if user else None
