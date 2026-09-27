"""Web Push subscriptions (app.db): which browsers to push a finished
craft to, per user. Sending is gcm/push.py."""

import time

from gcm import db


def save_subscription(user_id, endpoint, p256dh, auth, origin):
    """Adds the browser's subscription, or moves it to this user if it
    was saved while someone else was signed in there."""
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "INSERT INTO push_subscriptions (endpoint, user_id, p256dh, auth, origin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (endpoint) DO UPDATE SET user_id = excluded.user_id, "
            "p256dh = excluded.p256dh, auth = excluded.auth, origin = excluded.origin",
            (endpoint, user_id, p256dh, auth, origin, time.time()),
        )


def delete_subscription(user_id, endpoint):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
            (endpoint, user_id),
        )


def delete_endpoint(endpoint):
    """For a subscription the push service says no longer exists."""
    with db.transaction(db.app_db) as conn:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def subscriptions_for(user_ids):
    if not user_ids:
        return []
    placeholders = ", ".join("?" for _ in user_ids)
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            f"SELECT endpoint, user_id, p256dh, auth, origin FROM push_subscriptions "
            f"WHERE user_id IN ({placeholders})",
            list(user_ids),
        ).fetchall()
    return [
        {"endpoint": r[0], "user_id": r[1], "p256dh": r[2], "auth": r[3], "origin": r[4]}
        for r in rows
    ]
