"""power.db: stored-energy readings from power_monitor.lua."""

from gcm import db


def add_reading(ts, stored, capacity, trends):
    """trends: the optional trend fields power_monitor.lua may send
    (avg_eu_in_5s ... time_to_empty_minutes), each a float or None."""
    with db.transaction(db.power_db) as conn:
        conn.execute(
            "INSERT INTO power_readings "
            "(ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s, avg_eu_in_5m, avg_eu_out_5m, "
            "avg_eu_in_1h, avg_eu_out_1h, time_to_empty_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts,
                stored,
                capacity,
                trends.get("avg_eu_in_5s"),
                trends.get("avg_eu_out_5s"),
                trends.get("avg_eu_in_5m"),
                trends.get("avg_eu_out_5m"),
                trends.get("avg_eu_in_1h"),
                trends.get("avg_eu_out_1h"),
                trends.get("time_to_empty_minutes"),
            ),
        )


def readings(since, max_points):
    """(ts, stored, capacity) rows from `since` on (None: all), ascending.
    Over max_points readings, they're averaged into max_points equal
    time buckets inside SQLite, so a long range never pulls every raw
    reading into Python."""
    if since is None:
        where, params = "", ()
    else:
        where, params = "WHERE ts >= ?", (since,)

    with db.transaction(db.power_db) as conn:
        count, first, last = conn.execute(
            f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM power_readings {where}", params
        ).fetchone()
        if count <= max_points:
            return conn.execute(
                f"SELECT ts, stored, capacity FROM power_readings {where} ORDER BY ts ASC",
                params,
            ).fetchall()
        # The newest reading lands exactly on bucket max_points, so it's
        # folded into the last one.
        width = (last - first) / max_points
        return conn.execute(
            f"SELECT AVG(ts), AVG(stored), AVG(capacity) FROM power_readings {where} "
            "GROUP BY MIN(CAST((ts - ?) / ? AS INTEGER), ?) ORDER BY 1",
            params + (first, width, max_points - 1),
        ).fetchall()


def latest():
    """The single most recent RAW reading, in full - deliberately a
    separate, unfiltered query rather than reusing whatever readings()
    returned for the chart.

    CONFIRMED as a real, reported bug otherwise: the live "currently
    stored" readout used to come from rows[-1] of the RANGE-FILTERED,
    DOWNSAMPLED points array - the last bucket's AVERAGED value, not a
    true single reading. Different ranges downsample into different-
    sized buckets, so that last bucket's average genuinely differs
    between e.g. "day" and "week" - which is exactly why switching the
    chart range visibly changed the live percentage readout. Bucket
    timestamps are midpoints too, not real reading times, which is the
    same root cause behind "updated Xs ago" also drifting when
    switching ranges, just less consistently noticeable.

    Merged into one query covering stored/capacity AND the trend fields
    together (not two separate latest-row queries) - both correctness
    (no risk of the two disagreeing if a new reading lands between two
    separate queries) and efficiency."""
    with db.transaction(db.power_db) as conn:
        return conn.execute(
            "SELECT ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s "
            "FROM power_readings ORDER BY ts DESC LIMIT 1"
        ).fetchone()
