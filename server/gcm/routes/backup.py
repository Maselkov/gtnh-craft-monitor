"""Backup download for admins (gcm/backup.py): POST makes one, GET
fetches it once."""

import os

from flask import Blueprint, Response, jsonify

from gcm import auth, backup

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
