"""OAuth clients, codes, grants and tokens (app.db), for AI assistants
connecting to /mcp (gcm/oauth.py has the protocol). Codes and tokens are
stored only as SHA-256 hashes.

A grant is one app a user connected: the tokens issued to it hang off
it, and revoking it (or the login token the user consented with, or
the user) ends them all."""

import json
import secrets
import time

from gcm import db
from gcm.store.users import hash_secret

CODE_SECONDS = 60
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400

# Clients are made by anyone who asks (RFC 7591: Claude registers on every
# new connection), so ones never used for a grant are dropped after a day,
# and only so many may be made in an hour.
UNUSED_CLIENT_SECONDS = 86400
CLIENTS_PER_HOUR = 200

ACCESS_PREFIX = "gcmat_"
REFRESH_PREFIX = "gcmrt_"


def create_client(name, redirect_uris):
    """Registers a client; returns its id, or None when too many were
    registered in the last hour."""
    now = time.time()
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "DELETE FROM oauth_clients WHERE created_at < ? "
            "AND id NOT IN (SELECT client_id FROM oauth_grants)",
            (now - UNUSED_CLIENT_SECONDS,),
        )
        recent = conn.execute(
            "SELECT COUNT(*) FROM oauth_clients WHERE created_at > ?", (now - 3600,)
        ).fetchone()[0]
        if recent >= CLIENTS_PER_HOUR:
            return None
        client_id = f"gcmc_{secrets.token_hex(16)}"
        conn.execute(
            "INSERT INTO oauth_clients (id, name, redirect_uris, created_at) VALUES (?, ?, ?, ?)",
            (client_id, name, json.dumps(redirect_uris), now),
        )
    return client_id


def client(client_id):
    """{id, name, redirect_uris} or None."""
    if not isinstance(client_id, str):
        return None
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT id, name, redirect_uris FROM oauth_clients WHERE id = ?", (client_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "redirect_uris": json.loads(row[2])}


def create_code(client_id, user_id, access_token_id, redirect_uri, code_challenge):
    code = secrets.token_urlsafe(32)
    now = time.time()
    with db.transaction(db.app_db) as conn:
        conn.execute("DELETE FROM oauth_codes WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO oauth_codes (code_hash, client_id, user_id, access_token_id, redirect_uri, "
            "code_challenge, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (hash_secret(code), client_id, user_id, access_token_id, redirect_uri, code_challenge,
             now + CODE_SECONDS),
        )
    return code


def take_code(code):
    """The code's details, removing it (a code is used once), or None
    when there's no such live code."""
    if not isinstance(code, str):
        return None
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT client_id, user_id, access_token_id, redirect_uri, code_challenge, expires_at "
            "FROM oauth_codes WHERE code_hash = ?",
            (hash_secret(code),),
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM oauth_codes WHERE code_hash = ?", (hash_secret(code),))
    if row[5] <= time.time():
        return None
    return {"client_id": row[0], "user_id": row[1], "access_token_id": row[2], "redirect_uri": row[3],
            "code_challenge": row[4]}


def _issue(conn, grant_id, now):
    access = ACCESS_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    conn.executemany(
        "INSERT INTO oauth_tokens (token_hash, grant_id, kind, expires_at) VALUES (?, ?, ?, ?)",
        [(hash_secret(access), grant_id, "access", now + ACCESS_SECONDS),
         (hash_secret(refresh), grant_id, "refresh", now + REFRESH_SECONDS)],
    )
    # Tokens past their time can never be used again.
    conn.execute("DELETE FROM oauth_tokens WHERE grant_id = ? AND expires_at <= ?", (grant_id, now))
    return {"access_token": access, "refresh_token": refresh, "expires_in": ACCESS_SECONDS}


def create_grant(code_details):
    """Starts a grant from a redeemed code; returns its first tokens, or
    None when the user or the login token they consented with has gone
    since."""
    now = time.time()
    with db.transaction(db.app_db) as conn:
        live = conn.execute(
            "SELECT 1 FROM users WHERE id = ? AND disabled_at IS NULL", (code_details["user_id"],)
        ).fetchone()
        token_id = code_details["access_token_id"]
        if token_id and not conn.execute(
            "SELECT 1 FROM access_tokens WHERE id = ? AND revoked_at IS NULL", (token_id,)
        ).fetchone():
            live = None
        if not live:
            return None
        grant_id = f"grant_{secrets.token_hex(12)}"
        conn.execute(
            "INSERT INTO oauth_grants (id, client_id, user_id, access_token_id, created_at, last_used_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (grant_id, code_details["client_id"], code_details["user_id"], token_id, now, now),
        )
        return _issue(conn, grant_id, now)


def refresh(refresh_token, client_id):
    """New tokens for a refresh token, which is used up. A refresh token
    used a second time means it was copied: the whole grant is revoked.
    None when it can't be used."""
    if not isinstance(refresh_token, str) or not refresh_token.startswith(REFRESH_PREFIX):
        return None
    now = time.time()
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT oauth_tokens.grant_id, oauth_tokens.expires_at, oauth_tokens.used_at, "
            "oauth_grants.client_id, oauth_grants.revoked_at, users.disabled_at "
            "FROM oauth_tokens JOIN oauth_grants ON oauth_grants.id = oauth_tokens.grant_id "
            "JOIN users ON users.id = oauth_grants.user_id "
            "WHERE oauth_tokens.token_hash = ? AND oauth_tokens.kind = 'refresh'",
            (hash_secret(refresh_token),),
        ).fetchone()
        if not row or row[3] != client_id:
            return None
        grant_id, expires_at, used_at, _, revoked_at, disabled_at = row
        if used_at is not None:
            conn.execute(
                "UPDATE oauth_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (now, grant_id)
            )
            return None
        if expires_at <= now or revoked_at is not None or disabled_at is not None:
            return None
        conn.execute("UPDATE oauth_tokens SET used_at = ? WHERE token_hash = ?", (now, hash_secret(refresh_token)))
        return _issue(conn, grant_id, now)


def access_user(access_token):
    """The user a live access token acts for, or None."""
    if not access_token.startswith(ACCESS_PREFIX):
        return None
    now = time.time()
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT users.id, users.display_name, users.role, oauth_grants.id "
            "FROM oauth_tokens JOIN oauth_grants ON oauth_grants.id = oauth_tokens.grant_id "
            "JOIN users ON users.id = oauth_grants.user_id "
            "WHERE oauth_tokens.token_hash = ? AND oauth_tokens.kind = 'access' "
            "AND oauth_tokens.expires_at > ? AND oauth_grants.revoked_at IS NULL "
            "AND users.disabled_at IS NULL",
            (hash_secret(access_token), now),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE oauth_grants SET last_used_at = ? WHERE id = ?", (now, row[3]))
    return {"id": row[0], "display_name": row[1], "role": row[2], "grant_id": row[3]}


def grants_by_user():
    """Every live grant, by user id: [{id, client_name, created_at,
    last_used_at}], oldest first."""
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT oauth_grants.user_id, oauth_grants.id, oauth_clients.name, oauth_grants.created_at, "
            "oauth_grants.last_used_at FROM oauth_grants "
            "JOIN oauth_clients ON oauth_clients.id = oauth_grants.client_id "
            "WHERE oauth_grants.revoked_at IS NULL ORDER BY oauth_grants.created_at"
        ).fetchall()
    out = {}
    for user_id, grant_id, name, created_at, last_used_at in rows:
        out.setdefault(user_id, []).append(
            {"id": grant_id, "client_name": name, "created_at": created_at, "last_used_at": last_used_at}
        )
    return out


def revoke_grant(grant_id):
    """False if there's no live grant with that id."""
    with db.transaction(db.app_db) as conn:
        cursor = conn.execute(
            "UPDATE oauth_grants SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (time.time(), grant_id)
        )
    return cursor.rowcount > 0

