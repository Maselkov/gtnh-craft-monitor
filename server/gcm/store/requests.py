"""craft_request_history / craft_cancel_history rows (app.db):
an audit trail of every browser-submitted craft request and
cancellation, shown in the admin user-history view. The live requests
themselves are in memory (state.py); these rows share their ids."""

import time

from gcm import db


def record_request(
    request_id, user_id, label, mod, internal, damage, amount, kind, created_at, source="manual"
):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "INSERT INTO craft_request_history "
            "(request_id, user_id, label, mod, internal, damage, amount, kind, status, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (
                request_id,
                user_id,
                label,
                mod,
                internal,
                damage,
                amount,
                kind,
                created_at,
                source,
            ),
        )


# only_if_open: an expiry only closes a row nothing has resolved yet,
# while the game's own result always wins - it can arrive after the
# request expired, and it's what actually happened in-game.
def resolve_request(request_id, status, reason, cpu_name, only_if_open=False):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "UPDATE craft_request_history "
            "SET status = ?, reason = ?, cpu_name = ?, resolved_at = ? "
            "WHERE request_id = ?" + (" AND resolved_at IS NULL" if only_if_open else ""),
            (status, reason, cpu_name, time.time(), request_id),
        )


def record_cancel(request_id, user_id, cpu_name, created_at, item_label=None, item_icon=None):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "INSERT INTO craft_cancel_history "
            "(request_id, user_id, cpu_name, status, created_at, item_label, item_icon) "
            "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (request_id, user_id, cpu_name, created_at, item_label, item_icon),
        )


def resolve_cancel(request_id, success, reason, only_if_open=False):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "UPDATE craft_cancel_history "
            "SET status = 'resolved', success = ?, reason = ?, resolved_at = ? "
            "WHERE request_id = ?" + (" AND resolved_at IS NULL" if only_if_open else ""),
            (int(success), reason, time.time(), request_id),
        )


def last_ids():
    """(highest craft request id, highest cancel id) ever recorded, so a
    new process can carry on numbering above them."""
    with db.transaction(db.app_db) as conn:
        craft = conn.execute(
            "SELECT COALESCE(MAX(request_id), 0) FROM craft_request_history"
        ).fetchone()[0]
        cancel = conn.execute(
            "SELECT COALESCE(MAX(request_id), 0) FROM craft_cancel_history"
        ).fetchone()[0]
    return craft, cancel


def close_orphaned():
    # Craft and cancel requests live in memory, so any history row still
    # open at startup belongs to a request the previous process lost.
    now = time.time()
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "UPDATE craft_request_history SET status = 'failed', "
            "reason = 'server restarted', resolved_at = ? WHERE resolved_at IS NULL",
            (now,),
        )
        conn.execute(
            "UPDATE craft_cancel_history SET status = 'resolved', success = 0, "
            "reason = 'server restarted', resolved_at = ? WHERE resolved_at IS NULL",
            (now,),
        )


def user_activity(user_id, limit=100, include_auto=False):
    """A user's latest craft requests and cancellations, newest first.
    Requests a keep-in-stock target made (source 'auto', filed under who
    last set the target) only with include_auto: the server made them,
    and a busy target would otherwise push everything else past limit.
    target: the item (None for a cancel recorded without one); success:
    whether a resolved cancel worked (None for requests). A request's
    mod/internal/damage name its item; a cancel's item_icon is the icon
    path as it was then."""
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT 'request', label, status, reason, cpu_name, created_at, resolved_at, source, "
            "amount, kind, NULL, mod, internal, damage, NULL "
            "FROM craft_request_history WHERE user_id = ? AND (? OR source != 'auto') "
            "UNION ALL "
            "SELECT 'cancel', item_label, status, reason, cpu_name, created_at, resolved_at, 'manual', "
            "NULL, NULL, success, NULL, NULL, NULL, item_icon "
            "FROM craft_cancel_history WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, include_auto, user_id, limit),
        ).fetchall()
    return [
        {
            "type": row[0],
            "target": row[1],
            "status": row[2],
            "reason": row[3],
            "cpu_name": row[4],
            "created_at": row[5],
            "resolved_at": row[6],
            "source": row[7],
            "amount": row[8],
            "kind": row[9],
            "success": None if row[10] is None else bool(row[10]),
            "mod": row[11],
            "internal": row[12],
            "damage": row[13],
            "item_icon": row[14],
        }
        for row in rows
    ]


def user_auto_request_count(user_id):
    """How many requests a user's keep-in-stock targets have made."""
    with db.transaction(db.app_db) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM craft_request_history WHERE user_id = ? AND source = 'auto'",
            (user_id,),
        ).fetchone()[0]
