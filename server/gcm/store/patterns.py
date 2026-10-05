"""The last complete pattern scan (item_history.db), kept so a restart
doesn't lose it - see gcm/patterns.py."""

import json

from gcm import db


def save_snapshot(patterns, updated_at):
    with db.transaction(db.item_history_db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pattern_snapshot (id, patterns, updated_at) VALUES (1, ?, ?)",
            (json.dumps(patterns, separators=(",", ":")), updated_at),
        )


def load_snapshot():
    """(patterns, updated_at) as last saved, or ([], None)."""
    with db.transaction(db.item_history_db) as conn:
        row = conn.execute("SELECT patterns, updated_at FROM pattern_snapshot WHERE id = 1").fetchone()
    if not row:
        return [], None
    return json.loads(row[0]), row[1]
