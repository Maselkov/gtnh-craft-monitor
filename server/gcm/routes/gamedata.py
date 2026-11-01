"""The Game data admin page: which GTNH version's icons and item catalog
are live (and with which textures), what's published, and installing one;
checking the network's patterns against another version's recipes; and
the notice that the game data looks older than the game. The logic is in
gcm/gamedata.py, gcm/recipes.py and gcm/gamedata_check.py."""

from flask import Blueprint, g, jsonify, request

from gcm import auth, gamedata, gamedata_check, patterns, recipes


bp = Blueprint("gamedata", __name__)

NO_UPGRADE = ("There's no upgrade to check yet: the pattern check compares the game data in use with the "
              "version used before it, and the game data hasn't been switched from one version to another.")


@bp.route("/api/admin/gamedata", methods=["GET"])
@auth.admin_required
def admin_gamedata_get():
    releases, error = gamedata.available(refresh=request.args.get("refresh") == "1")
    version, textures = gamedata.selected()
    missing = patterns.missing()
    return jsonify(
        {
            "selected": version,
            "selected_textures": textures,
            # The pattern check after an upgrade compares against it.
            "previous": gamedata.previous_version(),
            # Why the pattern check can't run yet (patterns.missing()), and
            # how to say so: its button waits for a pattern scan.
            "patterns_missing": missing,
            "patterns_missing_reason": missing and patterns.missing_reason("pattern check"),
            "textures": gamedata.texture_sets(),
            "installed": gamedata.installed(),
            "available": [
                {
                    **{k: r[k] for k in ("version", "published_at", "base_size", "textures")},
                    "update_available": gamedata.update_available(r),
                }
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


@bp.route("/api/admin/recipe-check", methods=["GET"])
@auth.admin_required
def admin_recipe_check_get():
    """?from=&to=: the patterns checked from one GTNH version's recipes to
    another's (gcm/recipes.py). Both left out: the last upgrade's
    (previous -> selected). {"state": "preparing"} while recipes download
    or index; poll again."""
    default_from, default_to = recipes.default_versions()
    return _recipe_check(request.args.get("from") or default_from, request.args.get("to") or default_to)


@bp.route("/api/recipe-check", methods=["GET"])
@auth.login_required
@auth.operator_required
def recipe_check_get():
    """The last upgrade's check (previous -> selected), for operators too:
    they fix patterns in game. Only that pair, whose result the notice
    already asks for: another version means downloading its recipes,
    which is an admin's call (/api/admin/recipe-check)."""
    from_version, to_version = recipes.default_versions()
    if not to_version:
        return jsonify({"state": "error", "error": NO_UPGRADE}), 409
    return _recipe_check(from_version, to_version)


def _recipe_check(from_version, to_version):
    state, body = recipes.result(from_version, to_version)
    if state == "no_patterns":
        # "reading" is a wait, not a fault; "no_terminal" gets a how-to.
        return jsonify({"state": "error", "error": body, "patterns": patterns.missing()}), 409
    if state == "error":
        return jsonify({"state": "error", "error": body}), 409
    if state == "preparing":
        return jsonify({"state": "preparing", "from": from_version, "to": to_version}), 202
    return jsonify({"state": "ready", **body})


@bp.route("/api/recipe-notice", methods=["GET"])
@auth.login_required
@auth.operator_required
def recipe_notice_get():
    """For the page's notice that the last upgrade broke or changed
    patterns (recipes.notice()): {from, to, counts}, {from, to, error} for
    admins when the check couldn't run, or {}."""
    notice = recipes.notice()
    # Only an admin can do anything about it.
    if "error" in notice and not auth.is_admin(g.user):
        notice = {}
    return jsonify(notice)


@bp.route("/api/admin/gamedata-notice", methods=["GET"])
@auth.admin_required
def admin_gamedata_notice_get():
    """For the page's notice that the game data looks older than the game
    (gamedata_check.notice())."""
    return jsonify(gamedata_check.notice())
