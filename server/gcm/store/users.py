"""Users, their access tokens and their browser sessions
(app.db). Token secrets and session cookies are stored only
as SHA-256 hashes.

create_access_token(), find_access_token() and create_session() take a
`conn` so they can be part of a caller's transaction; everything else
opens its own."""

import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid

from gcm import db


def hash_secret(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_access_token(token):
    """(token_id, secret) from a gcm_<token-id>_<secret> token, or None."""
    try:
        prefix, token_id, secret = token.split("_", 2)
    except ValueError:
        return None
    if prefix != "gcm" or not token_id or not secret:
        return None
    return token_id, secret


def create_access_token(conn, user_id):
    token_id = f"tok-{secrets.token_hex(12)}"
    secret = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO access_tokens (id, user_id, secret_hash, created_at) VALUES (?, ?, ?, ?)",
        (token_id, user_id, hash_secret(secret), time.time()),
    )
    return f"gcm_{token_id}_{secret}"


def find_access_token(conn, token):
    parsed = parse_access_token(token)
    if not parsed:
        return None
    token_id, secret = parsed
    row = conn.execute(
        "SELECT id, user_id, secret_hash, revoked_at FROM access_tokens WHERE id = ?",
        (token_id,),
    ).fetchone()
    if (
        not row
        or row[3] is not None
        or not hmac.compare_digest(row[2], hash_secret(secret))
    ):
        return None
    return {"id": row[0], "user_id": row[1]}


def create_session(conn, access_token, lifetime_seconds):
    session_token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions "
        "(id, user_id, token_hash, access_token_id, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            f"ses_{uuid.uuid4().hex}",
            access_token["user_id"],
            hash_secret(session_token),
            access_token["id"],
            now,
            now + lifetime_seconds,
        ),
    )
    return session_token


def _prune_sessions(conn):
    # Expired and revoked sessions can never authenticate again.
    conn.execute(
        "DELETE FROM sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
        (time.time(),),
    )


def prune_sessions():
    with db.transaction(db.app_db) as conn:
        _prune_sessions(conn)


def sign_in(token, session_lifetime_seconds):
    """Checks an access token and starts a session for its user.
    Returns (user, session_token), or None for a bad token or a disabled
    user."""
    with db.transaction(db.app_db) as conn:
        access_token = find_access_token(conn, token)
        if not access_token:
            return None
        row = conn.execute(
            "SELECT id, display_name, role, disabled_at FROM users WHERE id = ?",
            (access_token["user_id"],),
        ).fetchone()
        if not row or row[3] is not None:
            return None
        conn.execute(
            "UPDATE access_tokens SET last_used_at = ? WHERE id = ?",
            (time.time(), access_token["id"]),
        )
        _prune_sessions(conn)
        session_token = create_session(conn, access_token, session_lifetime_seconds)
    user = {"id": row[0], "display_name": row[1], "role": row[2]}
    return user, session_token


def session_user(session_token):
    """The user behind a live session cookie, with that session's
    details, or None."""
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT users.id, users.display_name, users.role, sessions.id, "
            "sessions.access_token_id FROM sessions "
            "JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL "
            "AND sessions.expires_at > ? AND users.disabled_at IS NULL",
            (hash_secret(session_token), time.time()),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "display_name": row[1],
        "role": row[2],
        "session_id": row[3],
        "access_token_id": row[4],
    }


def sign_out(session_token):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
            (time.time(), hash_secret(session_token)),
        )


def count():
    with db.transaction(db.app_db) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def display_name(user_id):
    """The user's display name, or None if there's no such user."""
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT display_name FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else None


def id_for_display_name(name):
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE display_name = ?", (name,)
        ).fetchone()
    return row[0] if row else None


def create(display_name, role):
    """Adds a user with a first access token. Returns (user_id, token),
    or None if the display name is taken."""
    user_id = f"usr_{uuid.uuid4().hex}"
    try:
        with db.transaction(db.app_db) as conn:
            conn.execute(
                "INSERT INTO users (id, display_name, role, created_at) VALUES (?, ?, ?, ?)",
                (user_id, display_name, role, time.time()),
            )
            token = create_access_token(conn, user_id)
    except sqlite3.IntegrityError:
        return None
    return user_id, token


def all_with_tokens():
    """Every user, by display name, each with their unrevoked tokens."""
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT users.id, users.display_name, users.role, users.created_at, "
            "users.disabled_at, access_tokens.id, access_tokens.created_at, "
            "access_tokens.last_used_at "
            "FROM users LEFT JOIN access_tokens "
            "ON access_tokens.user_id = users.id AND access_tokens.revoked_at IS NULL "
            "ORDER BY users.display_name COLLATE NOCASE, access_tokens.created_at"
        ).fetchall()
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
    return list(users.values())


def delete(user_id):
    """Removes the user with their credentials, pins and pending
    completions. False if there was no such user. Their request/cancel
    history stays as the audit trail."""
    with db.transaction(db.app_db) as conn:
        # Every table that exists only for its user has user_id ... ON
        # DELETE CASCADE (see _USER_OWNED_SCHEMAS in db.py); the audit
        # tables have no key, so their rows stay.
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cursor.rowcount > 0


def replace_credentials(user_id):
    """Revokes every token and session the user has and issues one new
    token, which it returns - or None if there's no such user."""
    with db.transaction(db.app_db) as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
            return None
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
        return create_access_token(conn, user_id)


def revoke_token(token_id):
    """Revokes one token and the sessions made from it. False if no
    unrevoked token has that id."""
    with db.transaction(db.app_db) as conn:
        now = time.time()
        cursor = conn.execute(
            "UPDATE access_tokens SET revoked_at = ? "
            "WHERE id = ? AND revoked_at IS NULL",
            (now, token_id),
        )
        if not cursor.rowcount:
            return False
        conn.execute(
            "UPDATE sessions SET revoked_at = ? "
            "WHERE access_token_id = ? AND revoked_at IS NULL",
            (now, token_id),
        )
    return True


def create_first_admin(display_name, session_lifetime_seconds):
    """First-run setup: creates an admin with a first access token and
    signs them in, all in one transaction. Returns (user, token,
    session_token), or None once any user exists."""
    with db.transaction(db.app_db) as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return None
        user_id = f"usr_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) VALUES (?, ?, 'admin', ?)",
            (user_id, display_name, time.time()),
        )
        token = create_access_token(conn, user_id)
        session_token = create_session(conn, find_access_token(conn, token), session_lifetime_seconds)
    return {"id": user_id, "display_name": display_name, "role": "admin"}, token, session_token


def live_bootstrap_tokens():
    """How many unrevoked tokens are still the first admin token that an
    older version took from GCM_BOOTSTRAP_ADMIN_TOKEN and never rotated."""
    with db.transaction(db.app_db) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM access_tokens WHERE is_bootstrap = 1 AND revoked_at IS NULL"
        ).fetchone()[0]
