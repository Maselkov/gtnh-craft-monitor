"""The Game data admin page: which GTNH version's icons and item catalog
are live (and with which textures), what's published, and installing one.
The logic is in gcm/gamedata.py."""

from flask import Blueprint, jsonify, request

from gcm import auth, gamedata


bp = Blueprint("gamedata", __name__)


@bp.route("/api/admin/gamedata", methods=["GET"])
@auth.admin_required
def admin_gamedata_get():
    releases, error = gamedata.available(refresh=request.args.get("refresh") == "1")
    version, textures = gamedata.selected()
    return jsonify(
        {
            "selected": version,
            "selected_textures": textures,
            "textures": gamedata.texture_sets(),
            "installed": gamedata.installed(),
            "available": [
                {k: r[k] for k in ("version", "published_at", "base_size", "textures")}
                for r in releases
            ],
            "available_error": error,
            "status": gamedata.status(),
        }
    )


@bp.route("/api/admin/gamedata/status", methods=["GET"])
@auth.admin_required
def admin_gamedata_status_get():
    version, textures = gamedata.selected()
    return jsonify(
        {"selected": version, "selected_textures": textures, "status": gamedata.status()}
    )


@bp.route("/api/admin/gamedata/install", methods=["POST"])
@auth.admin_required
def admin_gamedata_install_post():
    payload = request.get_json(silent=True) or {}
    error = gamedata.start_install(payload.get("version"), payload.get("textures") or "default")
    if error:
        return jsonify({"error": error}), 409
    version, textures = gamedata.selected()
    return (
        jsonify(
            {"selected": version, "selected_textures": textures, "status": gamedata.status()}
        ),
        202,
    )
