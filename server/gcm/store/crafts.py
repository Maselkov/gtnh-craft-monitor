"""Crafting-CPU jobs (app.db): the permanent craft_events log
of every busy->idle transition (the Crafts tab's history), per-user CPU pins, and the completion
notifications fanned out to whoever had a finished job's CPU pinned."""

import time

from gcm import db, icons


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


def record_job_end(cpu_name, label, icon, status, progress, started_at=None, item=None):
    """Logs a craft_events row for a job that just left cpu_name, gives
    every user who had that CPU pinned a completion for it, and clears
    those pins - all in one transaction. Returns the new completions as
    (user_id, completion_id) pairs.

    started_at is None when the job's start wasn't seen; item is the
    output's {mod, internal, damage}, or None when it wasn't reported."""
    occurred_at = time.time()
    item = item or {}
    with db.transaction(db.app_db) as conn:
        event_id = conn.execute(
            "INSERT INTO craft_events (cpu_name, item_label, item_icon, status, progress_at_end, occurred_at, "
            "started_at, item_mod, item_internal, item_damage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cpu_name, label, icon, status, progress, occurred_at,
             started_at, item.get("mod"), item.get("internal"), item.get("damage")),
        ).lastrowid
        conn.execute(
            "INSERT INTO user_completions (user_id, craft_event_id, created_at) "
            "SELECT user_id, ?, ? FROM user_pins WHERE cpu_name = ?",
            (event_id, occurred_at, cpu_name),
        )
        conn.execute("DELETE FROM user_pins WHERE cpu_name = ?", (cpu_name,))
        return conn.execute(
            "SELECT user_id, id FROM user_completions WHERE craft_event_id = ?",
            (event_id,),
        ).fetchall()


def history(before=None, after=None, limit=50):
    """Job ends on every CPU, newest first: up to `limit` of them, only
    ones older than event id `before` or newer than `after` if given.
    Returns (events, more), more saying whether rows past the limit
    were left out."""
    where, params = [], []
    if before is not None:
        where.append("id < ?")
        params.append(before)
    if after is not None:
        where.append("id > ?")
        params.append(after)
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT id, cpu_name, item_label, item_icon, status, progress_at_end, occurred_at, "
            "started_at, item_mod, item_internal, item_damage FROM craft_events"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
    events = [
        {
            "id": r[0],
            "cpu": r[1],
            "itemName": r[2],
            "icon": icons.current_path(r[3]),
            "status": r[4],
            "progress": r[5],
            "finishedAt": r[6],
            "startedAt": r[7],
            "mod": r[8],
            "internal": r[9],
            "damage": r[10],
        }
        for r in rows[:limit]
    ]
    return events, len(rows) > limit


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
            "icon": icons.current_path(r[2]),
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


def items_ended_since(since):
    """(mod, internal, damage) of every item a job finished making since
    the time `since`."""
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT DISTINCT item_mod, item_internal, item_damage FROM craft_events "
            "WHERE occurred_at >= ? AND status = 'finished' AND item_internal IS NOT NULL",
            (since,),
        ).fetchall()
    return {tuple(r) for r in rows}
