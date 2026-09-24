"""Power monitor: SQLite-backed time series of a GT machine's energy
level, fed by oc/power_monitor.lua."""

import time

from flask import Blueprint, jsonify, request, Response

from gcm import auth, charts, db


bp = Blueprint("power", __name__)


POWER_RANGE_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "lifetime": None,
}


# Cap on returned points regardless of range, so the chart stays fast
# and the payload stays small even after months of 1-minute-ish polling.
POWER_MAX_POINTS = 800


@bp.route("/api/power", methods=["POST"])
@auth.api_key_required
def power_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400

    stored = payload.get("stored")
    capacity = payload.get("capacity")
    ts = payload.get("timestamp")
    if stored is None or capacity is None:
        return jsonify({"error": "missing stored/capacity"}), 400
    if ts is None:
        ts = time.time()

    def _f(key):
        # All optional - older power_monitor.lua deployments (or a
        # component that doesn't report these at all) simply won't send
        # them, and that's fine, not an error.
        v = payload.get(key)
        return float(v) if v is not None else None

    conn = db.power_db()
    try:
        conn.execute(
            "INSERT INTO power_readings "
            "(ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s, avg_eu_in_5m, avg_eu_out_5m, "
            "avg_eu_in_1h, avg_eu_out_1h, time_to_empty_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                float(ts),
                float(stored),
                float(capacity),
                _f("avg_eu_in_5s"),
                _f("avg_eu_out_5s"),
                _f("avg_eu_in_5m"),
                _f("avg_eu_out_5m"),
                _f("avg_eu_in_1h"),
                _f("avg_eu_out_1h"),
                _f("time_to_empty_minutes"),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


def fetch_power_rows(range_key, max_points=POWER_MAX_POINTS):
    """(range_key, rows) with rows as (ts, stored, capacity), ascending.
    Over max_points readings, they're averaged into max_points equal
    time buckets inside SQLite, so a long range never pulls every raw
    reading into Python."""
    if range_key not in POWER_RANGE_SECONDS:
        range_key = "day"
    seconds = POWER_RANGE_SECONDS[range_key]
    if seconds is None:
        where, params = "", ()
    else:
        where, params = "WHERE ts >= ?", (time.time() - seconds,)

    conn = db.power_db()
    try:
        count, first, last = conn.execute(
            f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM power_readings {where}", params
        ).fetchone()
        if count <= max_points:
            rows = conn.execute(
                f"SELECT ts, stored, capacity FROM power_readings {where} ORDER BY ts ASC",
                params,
            ).fetchall()
        else:
            # The newest reading lands exactly on bucket max_points, so
            # it's folded into the last one.
            width = (last - first) / max_points
            rows = conn.execute(
                f"SELECT AVG(ts), AVG(stored), AVG(capacity) FROM power_readings {where} "
                "GROUP BY MIN(CAST((ts - ?) / ? AS INTEGER), ?) ORDER BY 1",
                params + (first, width, max_points - 1),
            ).fetchall()
    finally:
        conn.close()

    return range_key, rows


def fetch_latest_power_reading():
    """The single most recent RAW reading, in full - deliberately a
    separate, unfiltered query rather than reusing whatever
    fetch_power_rows(range_key) happened to return.

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
    conn = db.power_db()
    try:
        row = conn.execute(
            "SELECT ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s FROM power_readings ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row


@bp.route("/api/power", methods=["GET"])
@auth.public
def power_get():
    range_key, rows = fetch_power_rows(request.args.get("range", "day"))
    latest = fetch_latest_power_reading()

    return jsonify(
        {
            "range": range_key,
            "points": [{"t": r[0], "stored": r[1], "capacity": r[2]} for r in rows],
            "latest": (
                {
                    "t": latest[0],
                    "stored": latest[1],
                    "capacity": latest[2],
                    "avg_eu_in_5s": latest[3],
                    "avg_eu_out_5s": latest[4],
                }
                if latest
                else None
            ),
        }
    )


@bp.route("/api/power/chart.png", methods=["GET"])
@auth.public
def power_chart_png():
    rate_limited = charts.rate_limit_response()
    if rate_limited:
        return rate_limited
    range_key, rows = fetch_power_rows(request.args.get("range", "day"))
    png_bytes = charts.cached_png(
        ("power", range_key),
        lambda: charts.render_png(
            [(r[0], r[1]) for r in rows], stepped=False
        ).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp
