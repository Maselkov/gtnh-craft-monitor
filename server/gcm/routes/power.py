"""Power monitor: SQLite-backed time series of a GT machine's energy
level, fed by oc/power_monitor.lua."""

import math
import time

from flask import Blueprint, jsonify, request, Response

from gcm import auth, charts, rates, state, store


bp = Blueprint("power", __name__)


MACHINE_NAME_MAX = 64
ADDRESS_MAX = 64
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


def _finite(v):
    """v as a float when it's a finite number, else None: one Infinity
    stored would make every later /api/power reply invalid JSON."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


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
    stored, capacity = _finite(stored), _finite(capacity)
    if stored is None or capacity is None:
        return jsonify({"error": "stored/capacity must be finite numbers"}), 400
    now = time.time()
    if ts is None:
        ts = now
    elif (not isinstance(ts, (int, float)) or isinstance(ts, bool)
          or not now - TIMESTAMP_MAX_AGE_SECONDS <= ts <= now + TIMESTAMP_MAX_AHEAD_SECONDS):
        return jsonify({"error": "timestamp must be a Unix time within the last day"}), 400
    machine = payload.get("machine", "")
    if not isinstance(machine, str) or len(machine.strip()) > MACHINE_NAME_MAX:
        return jsonify({"error": "invalid machine name"}), 400
    # The gt_machine's component address, from power_monitor.lua since
    # 2026-11: tells apart unnamed machines posted by several computers.
    address = payload.get("address")
    if address is not None and (not isinstance(address, str) or not address or len(address) > ADDRESS_MAX):
        return jsonify({"error": "invalid address"}), 400

    def _f(key):
        # All optional - older power_monitor.lua deployments (or a
        # component that doesn't report these at all) simply won't send
        # them, and that's fine, not an error.
        # Anything but a finite number is left out like a missing one.
        return _finite(payload.get(key))

    wireless_mode = payload.get("wireless_mode")
    if not isinstance(wireless_mode, bool):
        wireless_mode = None
    # Held across both, so a rename can't land between them and leave this
    # reading under the old name.
    with store.power.machines_lock:
        # Only an LSC's sensor says its wireless mode: numbered "LSC 2", not "Storage 2".
        name, renamed = store.power.resolve(machine.strip(), address, is_lsc=wireless_mode is not None)
        store.power.add_reading(
            float(ts),
            stored,
            capacity,
            {key: _f(key) for key in TREND_FIELDS},
            name,
            threshold=store.power.wireless_threshold(wireless_mode, payload.get("capacitors")),
            wireless_mode=wireless_mode,
        )
    _moved_poll_seconds(renamed)
    with state.power_lock:
        state.power_poll_seconds[name] = state.reported_interval(payload.get("poll_seconds"))

    return jsonify({"ok": True})


def _moved_poll_seconds(renamed):
    """Each machine's reported poll interval (for health) follows a rename."""
    with state.power_lock:
        for old, new in renamed:
            if old in state.power_poll_seconds:
                state.power_poll_seconds[new] = state.power_poll_seconds.pop(old)


@bp.route("/api/admin/power/machines", methods=["PUT"])
@auth.admin_required
def power_machine_rename():
    """Body: {"from": name, "to": new name} - renames a machine, keeping
    its history; its script's posts keep landing under the new name."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400
    old, new = payload.get("from"), payload.get("to")
    if not isinstance(old, str) or not isinstance(new, str):
        return jsonify({"error": "from and to must be names"}), 400
    new = new.strip()
    if not new or len(new) > MACHINE_NAME_MAX:
        return jsonify({"error": f"A name needs 1 to {MACHINE_NAME_MAX} characters."}), 400
    if new == old:
        return jsonify({"name": new})
    try:
        store.power.rename(old, new)
    except store.power.UnknownMachine:
        return jsonify({"error": "There's no such machine."}), 404
    except store.power.NameInUse:
        return jsonify({"error": f"There's already a machine named {new}."}), 409
    _moved_poll_seconds([(old, new)])
    return jsonify({"name": new})


def _machine_entry(name, t, latest=None):
    """A machine for the picker and the tab's fill: its name, last
    reading time and that reading's stored/capacity/wireless threshold."""
    row = latest or store.power.latest(name)
    return {
        "name": name,
        "t": t,
        "stored": row[1] if row else None,
        "capacity": row[2] if row else None,
        "wireless_threshold": row[5] if row else None,
        # An LSC's wireless mode, as its sensor says; None for any other
        # machine, or an LSC on a script from before it was read.
        "wireless_mode": None if not row or row[8] is None else bool(row[8]),
    }


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
            "points": [{"t": r[0], "stored": r[1], "capacity": r[2], "wireless_threshold": r[3]} for r in rows],
            "latest": (
                {
                    "t": latest[0],
                    "stored": latest[1],
                    "capacity": latest[2],
                    "avg_eu_in_5s": latest[3],
                    "avg_eu_out_5s": latest[4],
                    "wireless_threshold": latest[5],
                    "avg_eu_in_1h": latest[6],
                    "avg_eu_out_1h": latest[7],
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
