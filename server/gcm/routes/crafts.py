"""Crafting-CPU status from craft_monitor.lua, per-user CPU pins and
completion notifications, and the Lua debug-dump channel."""

import time

from flask import Blueprint, g, jsonify, request, Response

from gcm import auth, config, icons, state, store, tracking


bp = Blueprint("crafts", __name__)


# user_completions rows older than this get pruned.
COMPLETIONS_MAX_AGE_SECONDS = 30 * 86400


_DEBUG_DUMPS_KEEP = 10


@bp.route("/api/debug", methods=["POST"])
@auth.api_key_required
def debug_post():
    payload = request.get_json(silent=True) or {}
    with state.crafts_lock:
        state.debug_dumps.append(
            {
                "tag": payload.get("tag", "?"),
                "dump": payload.get("dump", ""),
                "received_at": time.time(),
            }
        )
        del state.debug_dumps[:-_DEBUG_DUMPS_KEEP]
    return jsonify({"ok": True})


@bp.route("/api/debug", methods=["GET"])
@auth.operator_required
def debug_get():
    with state.crafts_lock:
        if not state.debug_dumps:
            return Response("(no debug dumps received yet)", mimetype="text/plain")
        parts = []
        for d in state.debug_dumps:
            when = time.strftime("%H:%M:%S", time.localtime(d["received_at"]))
            parts.append(f"===== [{when}] {d['tag']} =====\n{d['dump']}")
        return Response("\n\n".join(parts), mimetype="text/plain")


@bp.route("/api/crafts", methods=["POST"])
@auth.api_key_required
def crafts_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400

    jobs = icons.attach_job_icons(payload.get("jobs", []))

    with state.crafts_lock:
        state.crafts["jobs"] = jobs
        state.crafts["source"] = payload.get("source")
        state.crafts["received_at"] = time.time()

    # Outside crafts_lock: tracking has its own lock, and only needs the
    # jobs list we already hold.
    tracking.process_jobs(jobs)

    return jsonify({"ok": True})


@bp.route("/api/crafts", methods=["GET"])
@auth.public
def crafts_get():
    with state.crafts_lock:
        received_at = state.crafts["received_at"]
        age = (time.time() - received_at) if received_at else None
        return jsonify(
            {
                "jobs": state.crafts["jobs"],
                "source": state.crafts["source"],
                "age_seconds": age,
                "stale": age is None or age > config.STALE_AFTER_SECONDS,
            }
        )


@bp.route("/api/pins", methods=["GET"])
@auth.login_required
def pins_get():
    return jsonify({"pins": store.crafts.pinned_cpus(g.user["id"])})


@bp.route("/api/pins", methods=["POST"])
@auth.login_required
def pins_post():
    user_id = g.user["id"]

    payload = request.get_json(silent=True) or {}
    cpu_name = payload.get("cpu_name")
    if not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400

    # Pinning only ever means "watch the job currently running on this
    # CPU" - an idle CPU has no job to watch, so this is rejected here
    # (not just discouraged client-side, since anyone could otherwise hit
    # this endpoint directly regardless of what the UI allows).
    with state.crafts_lock:
        jobs = state.crafts.get("jobs", [])
    job = next((j for j in jobs if j.get("name") == cpu_name), None)
    if job is None:
        return jsonify({"error": "unknown cpu_name"}), 404
    if not job.get("busy"):
        return jsonify({"error": "CPU is not currently busy - nothing to pin"}), 400

    store.crafts.pin_cpu(user_id, cpu_name)
    return jsonify({"ok": True})


@bp.route("/api/pins/unpin", methods=["POST"])
@auth.login_required
def pins_unpin():
    user_id = g.user["id"]

    payload = request.get_json(silent=True) or {}
    cpu_name = payload.get("cpu_name")
    if not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400

    store.crafts.unpin_cpu(user_id, cpu_name)
    return jsonify({"ok": True})


@bp.route("/api/completions", methods=["GET"])
@auth.login_required
def completions_get():
    return jsonify(
        {
            "completions": store.crafts.completions(
                g.user["id"], COMPLETIONS_MAX_AGE_SECONDS
            )
        }
    )


@bp.route("/api/completions/<int:completion_id>/ack", methods=["POST"])
@auth.login_required
def completions_ack(completion_id):
    store.crafts.acknowledge_completion(g.user["id"], completion_id)
    return jsonify({"ok": True})


@bp.route("/api/completions/ack-all", methods=["POST"])
@auth.login_required
def completions_ack_all():
    store.crafts.acknowledge_all_completions(g.user["id"])
    return jsonify({"ok": True})
