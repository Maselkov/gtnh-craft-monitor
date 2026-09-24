"""craft_request_history / craft_cancel_history rows (craft_history.db):
an audit trail of every browser-submitted craft request and
cancellation, shown in the admin user-history view. The live requests
themselves are in memory (state.py); these rows share their ids."""

import time

from gcm import db


def record_request(
    request_id, user_id, label, mod, internal, damage, amount, kind, created_at
):
    with db.transaction(db.craft_db) as conn:
        conn.execute(
            "INSERT INTO craft_request_history "
            "(request_id, user_id, label, mod, internal, damage, amount, kind, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
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
            ),
        )


def resolve_request(request_id, status, reason, cpu_name):
    with db.transaction(db.craft_db) as conn:
        conn.execute(
            "UPDATE craft_request_history "
            "SET status = ?, reason = ?, cpu_name = ?, resolved_at = ? "
            "WHERE request_id = ? AND resolved_at IS NULL",
            (status, reason, cpu_name, time.time(), request_id),
        )


def record_cancel(request_id, user_id, cpu_name, created_at):
    with db.transaction(db.craft_db) as conn:
        conn.execute(
            "INSERT INTO craft_cancel_history "
            "(request_id, user_id, cpu_name, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (request_id, user_id, cpu_name, created_at),
        )


def resolve_cancel(request_id, success, reason):
    with db.transaction(db.craft_db) as conn:
        conn.execute(
            "UPDATE craft_cancel_history "
            "SET status = 'resolved', success = ?, reason = ?, resolved_at = ? "
            "WHERE request_id = ? AND resolved_at IS NULL",
            (int(success), reason, time.time(), request_id),
        )


def last_ids():
    """(highest craft request id, highest cancel id) ever recorded, so a
    new process can carry on numbering above them."""
    with db.transaction(db.craft_db) as conn:
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
    with db.transaction(db.craft_db) as conn:
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


def user_activity(user_id, limit=100):
    """A user's latest craft requests and cancellations, newest first."""
    with db.transaction(db.craft_db) as conn:
        rows = conn.execute(
            "SELECT 'request', label, status, reason, cpu_name, created_at, resolved_at "
            "FROM craft_request_history WHERE user_id = ? "
            "UNION ALL "
            "SELECT 'cancel', cpu_name, status, reason, cpu_name, created_at, resolved_at "
            "FROM craft_cancel_history WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, user_id, limit),
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
        }
        for row in rows
    ]
