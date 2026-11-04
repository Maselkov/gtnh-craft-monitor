"""Crafting-CPU jobs (app.db): the permanent craft_events log
of every busy->idle transition (the Crafts tab's history), per-user CPU pins, and the completion
notifications fanned out to whoever had a finished job's CPU pinned."""

import time

from gcm import db, icons, search


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


def record_job_end(cpu_name, label, icon, status, progress, started_at=None, item=None, auto=False):
    """Logs a craft_events row for a job that just left cpu_name, gives
    every user who had that CPU pinned a completion for it, and clears
    those pins - all in one transaction. Returns the new completions as
    (user_id, completion_id) pairs.

    started_at is None when the job's start wasn't seen; item is the
    output's {mod, internal, damage}, or None when it wasn't reported;
    auto, whether a keep-in-stock target started the job."""
    occurred_at = time.time()
    item = item or {}
    with db.transaction(db.app_db) as conn:
        event_id = conn.execute(
            "INSERT INTO craft_events (cpu_name, item_label, item_icon, status, progress_at_end, occurred_at, "
            "started_at, item_mod, item_internal, item_damage, auto) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cpu_name, label, icon, status, progress, occurred_at,
             started_at, item.get("mod"), item.get("internal"), item.get("damage"), int(auto)),
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


def history(before=None, after=None, limit=50, query="", status=None):
    """Job ends on every CPU, newest first: up to `limit` of them, only
    ones older than event id `before` or newer than `after` if given.
    `query` is an NEI-style search on the item (gcm.search); `status`
    "auto" keeps auto-crafts, "incomplete" jobs that stopped early.
    Returns (events, more), more saying whether rows past the limit
    were left out."""
    where, params = [], []
    if before is not None:
        where.append("id < ?")
        params.append(before)
    if after is not None:
        where.append("id > ?")
        params.append(after)
    if status == "auto":
        where.append("auto = 1")
    elif status == "incomplete":
        where.append("status = 'incomplete'")
    terms = search.parse(query)
    sql = (
        "SELECT id, cpu_name, item_label, item_icon, status, progress_at_end, occurred_at, "
        "started_at, item_mod, item_internal, item_damage, auto FROM craft_events"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY id DESC"
    )
    with db.transaction(db.app_db) as conn:
        if terms:
            # Names are matched here rather than in SQL, the same way
            # the page matches them; rows stream until a page is full.
            rows = []
            for r in conn.execute(sql, params):
                if search.matches(r[2], r[8], terms):
                    rows.append(r)
                    if len(rows) > limit:
                        break
        else:
            rows = conn.execute(sql + " LIMIT ?", (*params, limit + 1)).fetchall()
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
            "auto": bool(r[11]),
        }
        for r in rows[:limit]
    ]
    return events, len(rows) > limit


# craft_stats.py's item: the item, or its name for rows from before
# items were recorded.
_ITEM_GROUP = """
    CASE WHEN COALESCE(item_internal, '') <> '' THEN item_mod ELSE '' END AS gm,
    CASE WHEN COALESCE(item_internal, '') <> '' THEN item_internal ELSE item_label END AS gi,
    CASE WHEN COALESCE(item_internal, '') <> '' THEN item_damage END AS gd"""


def ended_stats(since, top):
    """What the Crafts tab's statistics count, over every job that ended
    at or after `since` (None: all of them), worked out by SQLite: a
    base's 160,000 jobs read into Python took 70 MB for one request.
    {first_start, total, incomplete, auto, distinct_items,
    cpus: [(cpu, count, busy seconds)], items: [{label, mod, internal,
    damage, icon, count, incomplete, durations}]}: the `top` most made
    items, most first, then by name. An item's label and fields are its
    first job's, its icon the latest. Busy time counts from `since`, or
    from the first job's start."""
    where, args = ("", ()) if since is None else (" WHERE occurred_at >= ?", (since,))
    jobs = f"SELECT *, {_ITEM_GROUP} FROM craft_events{where}"
    with db.transaction(db.app_db) as conn:
        total, incomplete, auto, first_start = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(status = 'incomplete'), 0), COALESCE(SUM(auto <> 0), 0), "
            f"MIN(COALESCE(started_at, occurred_at)) FROM craft_events{where}",
            args,
        ).fetchone()
        start = first_start if since is None else since
        cpus = conn.execute(
            "SELECT cpu_name, COUNT(*), "
            "TOTAL(CASE WHEN started_at IS NOT NULL THEN MAX(0.0, occurred_at - MAX(started_at, ?)) END) "
            f"FROM craft_events{where} GROUP BY cpu_name",
            (start, *args),
        ).fetchall()
        groups = (
            f"WITH j AS ({jobs}), g AS (SELECT gm, gi, gd, COUNT(*) AS n, SUM(status = 'incomplete') AS inc, "
            "MIN(id) AS first_id, MAX(id) AS last_id FROM j GROUP BY gm, gi, gd), "
            "t AS (SELECT g.*, f.item_label AS label FROM g JOIN craft_events f ON f.id = g.first_id "
            "ORDER BY g.n DESC, COALESCE(f.item_label, ''), g.first_id LIMIT ?) "
        )
        distinct = conn.execute(f"WITH j AS ({jobs}) SELECT COUNT(*) FROM (SELECT 1 FROM j GROUP BY gm, gi, gd)",
                                args).fetchone()[0]
        rows = conn.execute(
            groups + "SELECT t.first_id, t.n, t.inc, t.label, f.item_mod, f.item_internal, f.item_damage, "
            "l.item_icon FROM t JOIN craft_events f ON f.id = t.first_id JOIN craft_events l ON l.id = t.last_id "
            "ORDER BY t.n DESC, COALESCE(t.label, ''), t.first_id",
            (*args, top),
        ).fetchall()
        durations = {}
        for first_id, seconds in conn.execute(
            groups + "SELECT t.first_id, j.occurred_at - j.started_at FROM j "
            "JOIN t ON j.gm IS t.gm AND j.gi IS t.gi AND j.gd IS t.gd "
            "WHERE j.started_at IS NOT NULL ORDER BY j.id",
            (*args, top),
        ):
            durations.setdefault(first_id, []).append(seconds)
        items = [{"label": label, "mod": mod, "internal": internal, "damage": damage, "icon": icon,
                  "count": n, "incomplete": inc, "durations": durations.get(first_id, [])}
                 for first_id, n, inc, label, mod, internal, damage, icon in rows]
    return {"first_start": first_start, "total": total, "incomplete": incomplete, "auto": auto,
            "distinct_items": distinct, "cpus": cpus, "items": items}


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


def item_crafts_since(mod, internal, damage, since):
    """How many jobs making exactly this item (its mod, internal name
    and damage, as craft_events keep them) ended at or after `since`, how
    many of those were keep-in-stock refills, and when the last ended:
    (count, auto, last_at or None)."""
    with db.transaction(db.app_db) as conn:
        count, auto, last_at = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(auto <> 0), 0), MAX(occurred_at) FROM craft_events "
            "WHERE item_internal = ? AND item_mod IS ? AND item_damage IS ? AND occurred_at >= ?",
            (internal, mod, damage, since),
        ).fetchone()
    return count, auto, last_at
