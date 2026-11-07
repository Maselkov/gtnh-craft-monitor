"""Read-only recipe browsing, using the selected game data bundle."""
from flask import Blueprint, jsonify, request

from gcm import auth, gamedata, icons, recipe_viewer, recipes

bp = Blueprint("recipe_viewer", __name__)


def _dataset():
    version, idx = recipes.selected_index()
    assets = icons.data_version()
    if ((request.args.get("version") and request.args["version"] != version)
            or (request.args.get("fingerprint") and request.args["fingerprint"] != idx.fingerprint)
            or ("assets" in request.args and request.args["assets"] != (assets or ""))):
        raise recipes.RecipeUnavailable("Game data changed. Reload recipes to continue.", "stale_recipes", 409)
    return version, idx, assets


def _response(version, idx, assets, **body):
    if gamedata.selected_version() != version or icons.data_version() != assets:
        raise recipes.RecipeUnavailable("Game data changed. Reload recipes to continue.", "stale_recipes", 409)
    return jsonify(version=version, fingerprint=idx.fingerprint, assets=assets, **body)


def _error(e):
    if isinstance(e, recipes.RecipeUnavailable):
        return jsonify(error=str(e), code=e.code), e.status
    return jsonify(error=str(e), code="invalid_query"), 400


@bp.route("/api/recipes/item", methods=["GET"])
@auth.viewer_required
def item():
    try:
        kind, key = recipe_viewer.parse_key(request.args.get("key"))
        version, idx, assets = _dataset()
        return _response(version, idx, assets, **recipe_viewer.item_summary(idx, kind, key))
    except ValueError as e:
        return _error(e)


@bp.route("/api/recipes", methods=["GET"])
@auth.viewer_required
def listing():
    try:
        kind, key = recipe_viewer.parse_key(request.args.get("key"))
        direction = request.args.get("dir", "making")
        if direction not in ("making", "using"):
            raise ValueError("Direction must be making or using.")
        limit, offset = int(request.args.get("limit", "20")), int(request.args.get("offset", "0"))
        if not 1 <= limit <= 20 or not 0 <= offset <= 2147483647:
            raise ValueError("Choose a limit from 1 to 20 and a nonnegative offset.")
        version, idx, assets = _dataset()
        map_name = request.args.get("map") or None
        if map_name is not None and map_name not in idx.maps():
            raise ValueError("Unknown recipe category.")
        query = idx.made_by if direction == "making" else idx.used_by
        rows, total = query(kind, key, map_name, limit=limit, offset=offset)
        return _response(version, idx, assets, total=total, offset=offset,
                       recipes=recipe_viewer.present_recipes(rows, idx))
    except ValueError as e:
        return _error(e)
