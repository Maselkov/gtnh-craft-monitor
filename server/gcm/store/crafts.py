"""Crafting-CPU jobs (app.db): the permanent craft_events log
of every busy->idle transition, per-user CPU pins, and the completion
notifications fanned out to whoever had a finished job's CPU pinned."""

import time

from gcm import db


def pin_cpu(user_id, cpu_name):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_pins (user_id, cpu_name, pinned_at) VALUES (?, ?, ?)",
            (user_id, cpu_name, time.time()),
        )


def unpin_cpu(user_id, cpu_name):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "DELETE FROM user_pins WHERE user_id = ? AND cpu_name = ?",
            (user_id, cpu_name),
        )


def pinned_cpus(user_id):
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT cpu_name FROM user_pins WHERE user_id = ?", (user_id,)
        ).fetchall()
    return [r[0] for r in rows]


def drop_cpu_pins(cpu_name):
    with db.transaction(db.app_db) as conn:
        conn.execute("DELETE FROM user_pins WHERE cpu_name = ?", (cpu_name,))


def record_job_end(cpu_name, label, icon, status, progress):
    """Logs a craft_events row for a job that just left cpu_name, gives
    every user who had that CPU pinned a completion for it, and clears
    those pins - all in one transaction."""
    occurred_at = time.time()
    with db.transaction(db.app_db) as conn:
        event_id = conn.execute(
            "INSERT INTO craft_events (cpu_name, item_label, item_icon, status, progress_at_end, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cpu_name, label, icon, status, progress, occurred_at),
        ).lastrowid
        conn.execute(
            "INSERT INTO user_completions (user_id, craft_event_id, created_at) "
            "SELECT user_id, ?, ? FROM user_pins WHERE cpu_name = ?",
            (event_id, occurred_at, cpu_name),
        )
        conn.execute("DELETE FROM user_pins WHERE cpu_name = ?", (cpu_name,))


def completions(user_id, max_age_seconds):
    """The user's unacknowledged completions, newest first. Ones older
    than max_age_seconds (anyone's) are deleted first."""
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "DELETE FROM user_completions WHERE created_at < ?",
            (time.time() - max_age_seconds,),
        )
        rows = conn.execute(
            """
            SELECT uc.id, ce.item_label, ce.item_icon, ce.status, ce.occurred_at
            FROM user_completions uc
            JOIN craft_events ce ON ce.id = uc.craft_event_id
            WHERE uc.user_id = ?
            ORDER BY ce.occurred_at DESC
        """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r[0],
            "itemName": r[1],
            "icon": r[2],
            "status": r[3],
            "finishedAt": r[4],
        }
        for r in rows
    ]


def acknowledge_completion(user_id, completion_id):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "DELETE FROM user_completions WHERE id = ? AND user_id = ?",
            (completion_id, user_id),
        )


def acknowledge_all_completions(user_id):
    with db.transaction(db.app_db) as conn:
        conn.execute("DELETE FROM user_completions WHERE user_id = ?", (user_id,))
