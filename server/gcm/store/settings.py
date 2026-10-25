"""Server-wide settings set on the page (app.db), as JSON values. The
defaults and what each one means live with whatever reads it."""

import json

from gcm import db


def load():
    with db.transaction(db.app_db) as conn:
        return {k: json.loads(v) for k, v in conn.execute("SELECT k, v FROM settings")}


def save(values):
    with db.transaction(db.app_db) as conn:
        conn.executemany(
            "INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v",
            [(k, json.dumps(v)) for k, v in values.items()],
        )
