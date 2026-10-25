"""Backups for admins (gcm/backup.py): POST makes one, GET fetches it
once; POST /api/admin/restore takes one back."""

import os

from flask import Blueprint, Response, jsonify, request

import gcm
from gcm import auth, backup, commands, store

bp = Blueprint("backup", __name__)


@bp.route("/api/admin/backup", methods=["POST"])
@auth.admin_required
def admin_backup_post():
    try:
        made = backup.prepare()
    except backup.BackupBusy:
        return jsonify({"error": "A backup is already being made."}), 409
    except backup.NoSpace:
        return jsonify({"error": "Not enough free disk space beside the databases for a backup."}), 507
    return jsonify(made)


@bp.route("/api/admin/backup/<backup_id>", methods=["GET"])
@auth.admin_required
def admin_backup_get(backup_id):
    taken = backup.take(backup_id)
    if taken is None:
        return jsonify({"error": "No such backup - make a new one."}), 404
    path, name, cleanup = taken

    # A generator rather than send_file(): its finally runs when the
    # server closes the response, sent in full or not, which
    # call_on_close() doesn't for a file response.
    def stream():
        try:
            with open(path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            cleanup()

    return Response(stream(), mimetype="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Content-Length": str(os.path.getsize(path)),
        "Cache-Control": "no-store",
    })


@bp.route("/api/admin/restore", methods=["POST"])
@auth.admin_required
def admin_restore_post():
    # Past the app-wide 1 MB cap, for this request only.
    request.max_content_length = backup.RESTORE_MAX_BYTES
    try:
        restored = backup.restore(request.stream, request.content_length)
    except backup.BackupBusy:
        return jsonify({"error": "A backup is being made or restored. Try again in a moment."}), 409
    except backup.NoSpace:
        return jsonify({"error": "Not enough free disk space beside the databases to restore that."}), 507
    except backup.TooBig:
        return jsonify({"error": "That file is too big to be a backup."}), 413
    except backup.BadBackup as e:
        return jsonify({"error": str(e)}), 400
    if "app.db" in restored:
        # Their history rows went with the old app.db.
        commands.craft_requests.drop_pending()
        commands.cancel_requests.drop_pending()
    gcm.reload_data()
    session_token = request.cookies.get("gcm_session")
    signed_in = bool(session_token) and store.users.session_user(session_token) is not None
    return jsonify({"restored": restored, "signed_in": signed_in})
