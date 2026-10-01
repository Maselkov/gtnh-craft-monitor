"""Stock rules (app.db): per-user low-stock alerts and the base's
keep-in-stock targets, checked after every network scan by
gcm/stock.py."""

import time

from gcm import db
from gcm.store.items import item_key

_ITEM_COLUMNS = "item_key, label, mod, internal, damage, kind, variant"


def _item_dict(row):
    return {
        "key": row[0],
        "label": row[1],
        "mod": row[2],
        "internal": row[3],
        "damage": row[4],
        "kind": row[5],
        "variant": row[6],
    }


def _item_values(item):
    return (
        item_key(item["mod"], item["internal"], item["damage"], item["kind"], item["variant"]),
        item["label"],
        item["mod"],
        item["internal"],
        item["damage"],
        item["kind"],
        item["variant"],
    )


# ---------------------------------------------------------------- alerts

def set_alert(user_id, item, below):
    """Adds or changes user_id's alert on item ({label, mod, internal,
    damage, kind, variant}). A changed threshold is armed again."""
    with db.transaction(db.app_db) as conn:
        conn.execute(
            f"INSERT INTO stock_alerts (user_id, {_ITEM_COLUMNS}, below, armed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) "
            "ON CONFLICT (user_id, item_key) DO UPDATE SET "
            "label = excluded.label, below = excluded.below, armed = 1",
            (user_id, *_item_values(item), below, time.time()),
        )


def delete_alert(user_id, key):
    with db.transaction(db.app_db) as conn:
        conn.execute("DELETE FROM stock_alerts WHERE user_id = ? AND item_key = ?", (user_id, key))


def _alerts(where, params):
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            f"SELECT {_ITEM_COLUMNS}, user_id, below, armed FROM stock_alerts {where} ORDER BY label",
            params,
        ).fetchall()
    return [{**_item_dict(r), "user_id": r[7], "below": r[8], "armed": bool(r[9])} for r in rows]


def alerts_for_user(user_id):
    return _alerts("WHERE user_id = ?", (user_id,))


def all_alerts():
    return _alerts("", ())


def alert_holders(key):
    """Users with an alert on the item with this key."""
    with db.transaction(db.app_db) as conn:
        rows = conn.execute("SELECT user_id FROM stock_alerts WHERE item_key = ?", (key,)).fetchall()
    return [r[0] for r in rows]


def set_armed(changes):
    """changes: (user_id, item_key, armed) for each alert to update."""
    with db.transaction(db.app_db) as conn:
        conn.executemany(
            "UPDATE stock_alerts SET armed = ? WHERE user_id = ? AND item_key = ?",
            [(int(armed), user_id, key) for user_id, key, armed in changes],
        )


# ---------------------------------------------------------------- targets

def set_target(user_id, item, keep_at_least, refill_to, enabled):
    """Adds or changes the base's target for item. Keeps the record of
    its latest auto request."""
    with db.transaction(db.app_db) as conn:
        conn.execute(
            f"INSERT INTO stock_targets ({_ITEM_COLUMNS}, keep_at_least, refill_to, enabled, "
            "updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (item_key) DO UPDATE SET label = excluded.label, "
            "keep_at_least = excluded.keep_at_least, refill_to = excluded.refill_to, "
            "enabled = excluded.enabled, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (*_item_values(item), keep_at_least, refill_to, int(enabled), user_id, time.time()),
        )


def delete_target(key):
    with db.transaction(db.app_db) as conn:
        conn.execute("DELETE FROM stock_targets WHERE item_key = ?", (key,))


def targets():
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            f"SELECT {_ITEM_COLUMNS}, keep_at_least, refill_to, enabled, updated_by, "
            "last_request_id, last_requested_at, last_status, last_reason "
            "FROM stock_targets ORDER BY label"
        ).fetchall()
    return [
        {
            **_item_dict(r),
            "keep_at_least": r[7],
            "refill_to": r[8],
            "enabled": bool(r[9]),
            "updated_by": r[10],
            "last_request_id": r[11],
            "last_requested_at": r[12],
            "last_status": r[13],
            "last_reason": r[14],
        }
        for r in rows
    ]


def record_auto_request(key, request_id, requested_at):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "UPDATE stock_targets SET last_request_id = ?, last_requested_at = ?, "
            "last_status = 'requested', last_reason = NULL WHERE item_key = ?",
            (request_id, requested_at, key),
        )


def record_waiting(key, reason):
    """A low target that couldn't be requested this scan."""
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "UPDATE stock_targets SET last_status = 'waiting', last_reason = ? WHERE item_key = ?",
            (reason, key),
        )


def record_auto_result(request_id, status, reason):
    """How the auto request with this id went ("accepted" or "failed").
    Returns the target's item key, or None if no target's latest request
    is this one any more."""
    with db.transaction(db.app_db) as conn:
        row = conn.execute(
            "SELECT item_key FROM stock_targets WHERE last_request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE stock_targets SET last_status = ?, last_reason = ? WHERE item_key = ?",
            (status, reason, row[0]),
        )
    return row[0]
