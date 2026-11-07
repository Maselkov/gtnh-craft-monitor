"""Last requested configuration of each multiblock, per account."""

import json

from gcm import db


def get(user_id, controller):
    with db.transaction(db.app_db) as conn:
        row = conn.execute("SELECT value FROM kit_preferences WHERE user_id = ? AND controller = ?",
                           (user_id, controller)).fetchone()
        return json.loads(row[0]) if row else None


def save(user_id, controller, value):
    with db.transaction(db.app_db) as conn:
        conn.execute("""
            INSERT INTO kit_preferences (user_id, controller, value) VALUES (?, ?, ?)
            ON CONFLICT (user_id, controller) DO UPDATE SET value = excluded.value
        """, (user_id, controller, json.dumps(value)))
