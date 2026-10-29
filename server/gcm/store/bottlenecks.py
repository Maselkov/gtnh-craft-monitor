"""Time running jobs were held up, per provider and hour (app.db) - see
gcm/bottlenecks.py. A row per provider that held something up in that
hour, so it stays small: unpruned, like craft_events."""

from gcm import db


def add(hour, rows):
    """Adds to hour's totals. rows: (provider key, {name, x, y, z, dim},
    {blocked, busy, stalled, critical, jobs})."""
    with db.transaction(db.app_db) as conn:
        conn.executemany(
            "INSERT INTO provider_stalls (hour, provider, name, x, y, z, dim, "
            "blocked_s, busy_s, stalled_s, critical_s, jobs) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (hour, provider) DO UPDATE SET "
            "blocked_s = blocked_s + excluded.blocked_s, busy_s = busy_s + excluded.busy_s, "
            "stalled_s = stalled_s + excluded.stalled_s, critical_s = critical_s + excluded.critical_s, "
            "jobs = jobs + excluded.jobs",
            [
                (hour, key, where.get("name"), where.get("x"), where.get("y"), where.get("z"), where.get("dim"),
                 t["blocked"], t["busy"], t["stalled"], t["critical"], t["jobs"])
                for key, where, t in rows
            ],
        )


def totals(since, limit):
    """Each provider's totals from the hour `since` on (None: all), the
    most critical time first, then the most held up: up to `limit` of
    [{name, x, y, z, dim, blocked, busy, stalled, critical, jobs}]. Where
    and name are as the latest hour has them."""
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT provider, MAX(hour), SUM(blocked_s), SUM(busy_s), SUM(stalled_s), SUM(critical_s), SUM(jobs) "
            "FROM provider_stalls" + ("" if since is None else " WHERE hour >= ?")
            + " GROUP BY provider"
            " ORDER BY SUM(critical_s) DESC, SUM(blocked_s) + SUM(busy_s) + SUM(stalled_s) DESC, provider"
            " LIMIT ?",
            ((limit,) if since is None else (since, limit)),
        ).fetchall()
        out = []
        for provider, hour, blocked, busy, stalled, critical, jobs in rows:
            name, x, y, z, dim = conn.execute(
                "SELECT name, x, y, z, dim FROM provider_stalls WHERE provider = ? AND hour = ?", (provider, hour)
            ).fetchone()
            out.append({"name": name, "x": x, "y": y, "z": z, "dim": dim, "blocked": blocked, "busy": busy,
                        "stalled": stalled, "critical": critical, "jobs": jobs})
    return out
