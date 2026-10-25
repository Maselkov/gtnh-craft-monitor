"""Power monitor: SQLite-backed time series of a GT machine's energy
level, fed by oc/power_monitor.lua."""

import time

from flask import Blueprint, jsonify, request, Response

from gcm import auth, charts, rates, store


bp = Blueprint("power", __name__)


MACHINE_NAME_MAX = 64
# A posted timestamp (power_monitor.lua sends none; tests and backfills
# do) must be about now: one from a game clock or a typo would put a
# point years off and stretch the Lifetime chart to reach it.
TIMESTAMP_MAX_AGE_SECONDS = 86400
TIMESTAMP_MAX_AHEAD_SECONDS = 300

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
    now = time.time()
    if ts is None:
        ts = now
    elif (not isinstance(ts, (int, float)) or isinstance(ts, bool)
          or not now - TIMESTAMP_MAX_AGE_SECONDS <= ts <= now + TIMESTAMP_MAX_AHEAD_SECONDS):
        return jsonify({"error": "timestamp must be a Unix time within the last day"}), 400
    machine = payload.get("machine", "")
    if not isinstance(machine, str) or len(machine.strip()) > MACHINE_NAME_MAX:
        return jsonify({"error": "invalid machine name"}), 400

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
        machine.strip(),
    )

    return jsonify({"ok": True})


def _machine_entry(name, t, latest=None):
    """A machine for the picker and the tab's fill: its name, last
    reading time and that reading's stored/capacity."""
    row = latest or store.power.latest(name)
    return {"name": name, "t": t, "stored": row[1] if row else None, "capacity": row[2] if row else None}


@bp.route("/api/power", methods=["GET"])
@auth.viewer_required
def power_get():
    """One machine's readings (?machine=, see store.power.pick) over
    ?range=, and the list of machines."""
    machine, machines = store.power.pick(request.args.get("machine"))
    range_key, rows = store.power.range_readings(request.args.get("range", "day"), machine)
    latest = store.power.latest(machine)
    now = time.time()
    trend = rates.power_trend(
        store.power.readings(now - rates.POWER_WINDOW_SECONDS, store.power.MAX_POINTS, machine), now
    )

    return jsonify(
        {
            "machine": machine,
            "machines": [_machine_entry(name, t, latest if name == machine else None) for name, t in machines],
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
            "trend": trend,
        }
    )


@bp.route("/api/power/chart.png", methods=["GET"])
@auth.viewer_required
def power_chart_png():
    rate_limited = charts.rate_limit_response()
    if rate_limited:
        return rate_limited
    machine, _ = store.power.pick(request.args.get("machine"))
    range_key, rows = store.power.range_readings(request.args.get("range", "day"), machine)
    png_bytes = charts.cached_png(
        ("power", machine, range_key),
        lambda: charts.render_png(
            [(r[0], r[1]) for r in rows],
            stepped=False,
            span_seconds=store.power.RANGE_SECONDS[range_key],
        ).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp
