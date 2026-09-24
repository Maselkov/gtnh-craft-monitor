"""Crafting-CPU status from craft_monitor.lua, per-user CPU pins and
completion notifications, and the Lua debug-dump channel."""

import time

from flask import Blueprint, g, jsonify, request, Response

from gcm import auth, config, db, icons, state


bp = Blueprint("crafts", __name__)


# Craft completion tracking is done here, server-side, rather than
# client-side (as it originally was, by watching
# busy-state transitions in the browser) for two reasons that design
# couldn't fix:
#   1. A closed browser tab can't observe anything - it can only compare
#      "last known state before closing" vs "state on reopening", which
#      misses any completion-then-new-job cycle that happens entirely
#      while no tab is open. The server is always running (as long as
#      craft_monitor.lua is), so it never has that gap.
#   2. The client had no way to distinguish a finished craft from a
#      cancelled/interrupted one - "was busy, now isn't" looked identical
#      either way. The server classifies this from the last known
#      progress_percent at the moment of the transition instead.
#
# craft_events is a permanent, unpruned log of every busy->idle
# transition for every CPU, regardless of whether anyone has it pinned -
# cheap (a few dozen writes a day at most) and doubles as raw material
# for any future historical/analytics view.
#
# user_pins / user_completions are per-user, keyed by the signed-in
# user's ID from their session.
CRAFT_EVENT_FINISHED_THRESHOLD = 99  # progress_percent >= this counts as "finished"
COMPLETIONS_MAX_AGE_SECONDS = (
    30 * 86400
)  # user_completions rows older than this get pruned


def _classify_status(progress):
    # progress is None specifically means the main 5s status poll never
    # captured even ONE snapshot of this CPU while it was busy, before it
    # went idle again - confirmed (via the FusionTech Mk-IV test earlier)
    # that a REJECTED craft request never flips a CPU busy at all, so an
    # entirely-invisible-but-real busy period is strong evidence of a
    # fast, genuine SUCCESS that simply outran the polling interval - not
    # evidence of a failure we just failed to observe. Treating it as
    # "incomplete" was actively wrong in exactly that case.
    if progress is None:
        return "finished"
    return "finished" if progress >= CRAFT_EVENT_FINISHED_THRESHOLD else "incomplete"


def _process_craft_transitions(jobs):
    """Detects busy->idle transitions against our own server-side memory
    of each CPU's last state, logs a permanent craft_events row for each,
    and fans out + auto-unpins any users who had that CPU pinned."""
    conn = db.craft_db()
    try:
        with state.tracking_lock:
            for job in jobs:
                name = job.get("name")
                if not name:
                    continue
                busy = bool(job.get("busy"))
                was_busy = state.cpu_last_busy.get(name)

                if was_busy is None and not busy:
                    conn.execute("DELETE FROM user_pins WHERE cpu_name = ?", (name,))

                if was_busy is True and busy is False:
                    last_known = state.cpu_last_known.get(name, {})
                    label = job.get("final_output") or last_known.get("label")
                    icon = job.get("final_output_icon") or last_known.get("icon")
                    progress = last_known.get("progress")
                    status = _classify_status(progress)
                    occurred_at = time.time()

                    cur = conn.execute(
                        "INSERT INTO craft_events (cpu_name, item_label, item_icon, status, progress_at_end, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (name, label, icon, status, progress, occurred_at),
                    )
                    event_id = cur.lastrowid

                    pinned_users = [
                        r[0]
                        for r in conn.execute(
                            "SELECT user_id FROM user_pins WHERE cpu_name = ?", (name,)
                        ).fetchall()
                    ]
                    for user_id in pinned_users:
                        conn.execute(
                            "INSERT INTO user_completions (user_id, craft_event_id, created_at) VALUES (?, ?, ?)",
                            (user_id, event_id, occurred_at),
                        )
                    conn.execute("DELETE FROM user_pins WHERE cpu_name = ?", (name,))

                    state.cpu_last_known.pop(name, None)

                if busy:
                    entry = state.cpu_last_known.setdefault(
                        name, {"label": None, "icon": None, "progress": None}
                    )
                    if job.get("final_output"):
                        entry["label"] = job.get("final_output")
                        entry["icon"] = job.get("final_output_icon")
                    if job.get("progress_percent") is not None:
                        entry["progress"] = job.get("progress_percent")

                state.cpu_last_busy[name] = busy

            conn.commit()
    finally:
        conn.close()


def _prune_old_completions(conn):
    cutoff = time.time() - COMPLETIONS_MAX_AGE_SECONDS
    conn.execute("DELETE FROM user_completions WHERE created_at < ?", (cutoff,))


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

    # Outside _lock (a separate DB, no shared state with _state beyond
    # the jobs list we already have a local reference to).
    _process_craft_transitions(jobs)

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
    user_id = g.user["id"]
    conn = db.craft_db()
    try:
        rows = conn.execute(
            "SELECT cpu_name FROM user_pins WHERE user_id = ?", (user_id,)
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"pins": [r[0] for r in rows]})


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

    conn = db.craft_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_pins (user_id, cpu_name, pinned_at) VALUES (?, ?, ?)",
            (user_id, cpu_name, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/api/pins/unpin", methods=["POST"])
@auth.login_required
def pins_unpin():
    user_id = g.user["id"]

    payload = request.get_json(silent=True) or {}
    cpu_name = payload.get("cpu_name")
    if not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400

    conn = db.craft_db()
    try:
        conn.execute(
            "DELETE FROM user_pins WHERE user_id = ? AND cpu_name = ?",
            (user_id, cpu_name),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/api/completions", methods=["GET"])
@auth.login_required
def completions_get():
    user_id = g.user["id"]

    conn = db.craft_db()
    try:
        _prune_old_completions(conn)
        conn.commit()
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
    finally:
        conn.close()

    return jsonify(
        {
            "completions": [
                {
                    "id": r[0],
                    "itemName": r[1],
                    "icon": r[2],
                    "status": r[3],
                    "finishedAt": r[4],
                }
                for r in rows
            ]
        }
    )


@bp.route("/api/completions/<int:completion_id>/ack", methods=["POST"])
@auth.login_required
def completions_ack(completion_id):
    user_id = g.user["id"]
    conn = db.craft_db()
    try:
        conn.execute(
            "DELETE FROM user_completions WHERE id = ? AND user_id = ?",
            (completion_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/api/completions/ack-all", methods=["POST"])
@auth.login_required
def completions_ack_all():
    user_id = g.user["id"]
    conn = db.craft_db()
    try:
        conn.execute("DELETE FROM user_completions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})
