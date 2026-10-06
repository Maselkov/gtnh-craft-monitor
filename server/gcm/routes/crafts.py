"""Crafting-CPU status from craft_monitor.lua, per-user CPU pins and
completion notifications, and the Lua debug-dump channel."""

import time

from flask import Blueprint, g, jsonify, request, Response

from gcm import auth, config, icons, job_tree, planner, state, store, tracking


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

    # Before storing: it also fills in each job's progress. Outside
    # crafts_lock - tracking has its own lock.
    tracking.process_jobs(jobs)

    with state.crafts_lock:
        state.crafts["jobs"] = jobs
        state.crafts["source"] = payload.get("source")
        state.crafts["received_at"] = time.time()

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


@bp.route("/api/crafts/<cpu>/tree", methods=["GET"])
@auth.public
def crafts_tree_get(cpu):
    """A busy CPU's job as a tree (gcm/job_tree.py), the first levels of
    it; ?path=0.3.1&version=... sends one step's steps, as the craft
    plan's tree does. {tree: null, reason} when there's none to show,
    {stale: true} when the job or patterns changed since `version`."""
    path_raw = request.args.get("path")
    path = None
    if path_raw is not None:
        try:
            path = [int(p) for p in path_raw.split(".")] if path_raw else []
        except ValueError:
            return jsonify({"error": "bad path"}), 400
    version = job_tree.version(cpu)
    if path is not None and (version is None or request.args.get("version") != version):
        return jsonify({"stale": True})
    result, reason = job_tree.tree(cpu)
    if result is None:
        return jsonify({"tree": None, "reason": reason})
    if path is not None:
        node = planner.subtree(result["root"], path)
        if node is None:
            return jsonify({"stale": True})
        return jsonify({"node": planner.trim(node, 1)})
    return jsonify({
        "tree": {**result, "root": planner.trim(result["root"], 2)},
        "version": version,
    })


@bp.route("/api/crafts/<cpu>/steps", methods=["GET"])
@auth.public
def crafts_steps_get(cpu):
    """How far each step of a busy CPU's job has got, for the live tree:
    {steps: {item key: {total, left, crafting, moved_at, state}},
    version, stall_seconds}. {steps: null} once the CPU is idle."""
    return jsonify({
        "steps": job_tree.steps(cpu),
        "version": job_tree.version(cpu),
        "stall_seconds": config.CRAFT_STALL_SECONDS,
        "now": time.time(),
    })


# Most rows /api/crafts/history returns at once.
HISTORY_PAGE_MAX = 100


def _int_arg(name):
    value = request.args.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@bp.route("/api/crafts/history", methods=["GET"])
@auth.public
def crafts_history_get():
    """Finished and stopped jobs, newest first. ?before=<id> pages back
    through older ones, ?after=<id> fetches only newer ones."""
    limit = _int_arg("limit") or 50
    events, more = store.crafts.history(
        before=_int_arg("before"),
        after=_int_arg("after"),
        limit=max(1, min(limit, HISTORY_PAGE_MAX)),
    )
    return jsonify({"events": events, "more": more})


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
