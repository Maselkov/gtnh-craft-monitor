"""Power monitor: SQLite-backed time series of a GT machine's energy
level, fed by oc/power_monitor.lua."""

import time

from flask import Blueprint, jsonify, request, Response

from gcm import auth, charts, store


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

# Optional fields power_monitor.lua may send besides stored/capacity:
# the machine's own averaged EU in/out and time-to-empty estimate.
TREND_FIELDS = (
    "avg_eu_in_5s",
    "avg_eu_out_5s",
    "avg_eu_in_5m",
    "avg_eu_out_5m",
    "avg_eu_in_1h",
    "avg_eu_out_1h",
    "time_to_empty_minutes",
)


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

    store.power.add_reading(
        float(ts),
        float(stored),
        float(capacity),
        {key: _f(key) for key in TREND_FIELDS},
    )

    return jsonify({"ok": True})


def range_rows(range_key):
    """(range_key, rows) for a chart range name; unknown names mean
    "day"."""
    if range_key not in POWER_RANGE_SECONDS:
        range_key = "day"
    seconds = POWER_RANGE_SECONDS[range_key]
    since = None if seconds is None else time.time() - seconds
    return range_key, store.power.readings(since, POWER_MAX_POINTS)


@bp.route("/api/power", methods=["GET"])
@auth.public
def power_get():
    range_key, rows = range_rows(request.args.get("range", "day"))
    latest = store.power.latest()

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
    range_key, rows = range_rows(request.args.get("range", "day"))
    png_bytes = charts.cached_png(
        ("power", range_key),
        lambda: charts.render_png(
            [(r[0], r[1]) for r in rows], stepped=False
        ).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp
