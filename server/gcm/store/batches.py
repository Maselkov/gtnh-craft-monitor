"""Each account's batch being put together (gcm/batches.py): its rows,
as JSON."""

import json

from gcm import db


def get(user_id):
    with db.transaction(db.app_db) as conn:
        row = conn.execute("SELECT value FROM batches WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(row[0]) if row else []


def save(user_id, rows):
    with db.transaction(db.app_db) as conn:
        if rows:
            conn.execute("""
                INSERT INTO batches (user_id, value) VALUES (?, ?)
                ON CONFLICT (user_id) DO UPDATE SET value = excluded.value
            """, (user_id, json.dumps(rows)))
        else:
            conn.execute("DELETE FROM batches WHERE user_id = ?", (user_id,))
