"""power.db: stored-energy readings from power_monitor.lua."""

import math
import time

from gcm import db


RANGE_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "lifetime": None,
}


# Cap on returned points regardless of range, so the chart stays fast
# and the payload stays small even after months of 1-minute-ish polling.
MAX_POINTS = 800


# Each wireless capacitor tier's share of an LSC's wireless threshold
# (wiki: Lapotronic Supercapacitor; ×100 a tier from UHV's 60T).
WIRELESS_THRESHOLD_PER_CAPACITOR = {"UHV": 6e13, "UEV": 6e15, "UIV": 6e17, "UMV": 6e19}


def wireless_threshold(wireless_mode, capacitors):
    """The EU an LSC in wireless mode keeps: every 5 minutes it uploads
    what's above it to the wireless network and downloads up to it.
    None when wireless mode is off or unknown, or it has no wireless
    capacitors. capacitors: {tier: count}, as power_monitor.lua posts."""
    if wireless_mode is not True or not isinstance(capacitors, dict):
        return None
    total = sum(
        WIRELESS_THRESHOLD_PER_CAPACITOR[tier] * count
        for tier, count in capacitors.items()
        if tier in WIRELESS_THRESHOLD_PER_CAPACITOR
        and isinstance(count, (int, float)) and not isinstance(count, bool)
        and math.isfinite(count) and count > 0
    )
    return total or None


def add_reading(ts, stored, capacity, trends, machine="", threshold=None, wireless_mode=None):
    """trends: the optional trend fields power_monitor.lua may send
    (avg_eu_in_5s ... time_to_empty_minutes), each a float or None.
    machine: its name, '' for one reported without.
    threshold: see wireless_threshold().
    wireless_mode: an LSC's, True/False; None when not reported."""
    with db.transaction(db.power_db) as conn:
        conn.execute(
            "INSERT INTO power_readings "
            "(machine, ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s, avg_eu_in_5m, avg_eu_out_5m, "
            "avg_eu_in_1h, avg_eu_out_1h, time_to_empty_minutes, wireless_threshold, wireless_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                machine,
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
                threshold,
                None if wireless_mode is None else int(wireless_mode),
            ),
        )


def readings(since, max_points, machine=""):
    """(ts, stored, capacity, wireless_threshold) rows of one machine
    from `since` on (None: all), ascending.
    Over max_points readings, they're averaged into max_points equal
    time buckets inside SQLite, so a long range never pulls every raw
    reading into Python."""
    if since is None:
        where, params = "WHERE machine = ?", (machine,)
    else:
        where, params = "WHERE machine = ? AND ts >= ?", (machine, since)

    with db.transaction(db.power_db) as conn:
        count, first, last = conn.execute(
            f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM power_readings {where}", params
        ).fetchone()
        if count <= max_points:
            return conn.execute(
                f"SELECT ts, stored, capacity, wireless_threshold FROM power_readings {where} ORDER BY ts ASC",
                params,
            ).fetchall()
        # The newest reading lands exactly on bucket max_points, so it's
        # folded into the last one.
        width = (last - first) / max_points
        return conn.execute(
            f"SELECT AVG(ts), AVG(stored), AVG(capacity), AVG(wireless_threshold) FROM power_readings {where} "
            "GROUP BY MIN(CAST((ts - ?) / ? AS INTEGER), ?) ORDER BY 1",
            params + (first, width, max_points - 1),
        ).fetchall()


def range_readings(range_key, machine=""):
    """(range_key, rows) of readings() for a chart range name; unknown
    names mean "day"."""
    if range_key not in RANGE_SECONDS:
        range_key = "day"
    seconds = RANGE_SECONDS[range_key]
    since = None if seconds is None else time.time() - seconds
    return range_key, readings(since, MAX_POINTS, machine)


def latest(machine=""):
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
            "SELECT ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s, wireless_threshold, avg_eu_in_1h, avg_eu_out_1h, "
            "wireless_mode "
            "FROM power_readings WHERE machine = ? ORDER BY ts DESC LIMIT 1",
            (machine,),
        ).fetchone()


def machines():
    """Every machine that has readings, as (name, last reading time),
    by name ('' first)."""
    with db.transaction(db.power_db) as conn:
        # Walks the (machine, ts) index one name at a time, rather than
        # a GROUP BY reading every row of a long history.
        names = [row[0] for row in conn.execute(
            "WITH RECURSIVE m(name) AS ("
            " SELECT MIN(machine) FROM power_readings"
            " UNION ALL"
            " SELECT (SELECT MIN(machine) FROM power_readings WHERE machine > m.name) FROM m"
            " WHERE m.name IS NOT NULL"
            ") SELECT name FROM m WHERE name IS NOT NULL"
        )]
        return [
            (name, conn.execute("SELECT MAX(ts) FROM power_readings WHERE machine = ?", (name,)).fetchone()[0])
            for name in names
        ]


# How recent the wireless network's last reading must be for it to be
# the default machine (health.POWER_STALE_SECONDS).
WIRELESS_FRESH_SECONDS = 300


def pick(wanted, now=None):
    """(machine, machines): `wanted` when it has readings, else the
    wireless network if it's reporting, else the first one ('' when
    there is one, as before machines had names), plus machines() for
    the caller to reuse.

    The wireless network is the one posted without a capacity. Once it
    is in use, an LSC in wireless mode keeps only its threshold and
    moves the rest there every 5 minutes, so the network is where the
    base's power is."""
    found = machines()
    names = [name for name, _ in found]
    if wanted in names:
        return wanted, found
    now = time.time() if now is None else now
    for name, last in found:
        if last is not None and now - last <= WIRELESS_FRESH_SECONDS:
            row = latest(name)
            if row and not row[2]:
                return name, found
    return (names[0] if names else ""), found
