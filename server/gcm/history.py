"""craft_request_history / craft_cancel_history rows (craft_history.db):
an audit trail of every browser-submitted craft request and
cancellation, shown in the admin user-history view."""

import time

from gcm import auth, db


def record_craft_request(
    request_id, user_id, label, mod, internal, damage, amount, kind, created_at
):
    conn = db.craft_db()
    try:
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
        conn.commit()
    finally:
        conn.close()


def update_craft_request(request_id, status, reason, cpu_name):
    conn = db.craft_db()
    try:
        conn.execute(
            "UPDATE craft_request_history "
            "SET status = ?, reason = ?, cpu_name = ?, resolved_at = ? "
            "WHERE request_id = ? AND resolved_at IS NULL",
            (status, reason, cpu_name, time.time(), request_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_craft_cancel(request_id, user_id, cpu_name, created_at):
    conn = db.craft_db()
    try:
        conn.execute(
            "INSERT INTO craft_cancel_history "
            "(request_id, user_id, cpu_name, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (request_id, user_id, cpu_name, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def update_craft_cancel(request_id, success, reason):
    conn = db.craft_db()
    try:
        conn.execute(
            "UPDATE craft_cancel_history "
            "SET status = 'resolved', success = ?, reason = ?, resolved_at = ? "
            "WHERE request_id = ? AND resolved_at IS NULL",
            (int(success), reason, time.time(), request_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_orphaned_requests():
    # Craft and cancel requests live in memory, so any history row still
    # open at startup belongs to a request the previous process lost.
    now = time.time()
    conn = db.craft_db()
    try:
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
        auth.prune_sessions(conn)
        conn.commit()
    finally:
        conn.close()
