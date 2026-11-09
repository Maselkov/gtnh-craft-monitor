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


@bp.route("/api/recipes/search", methods=["GET"])
@auth.viewer_required
def search():
    try:
        query = request.args.get("q", "").strip()
        kind = request.args.get("kind", "all")
        if kind not in ("all", "item", "fluid"):
            raise ValueError("Choose items, fluids, or both.")
        limit, offset = int(request.args.get("limit", "40")), int(request.args.get("offset", "0"))
        keys = request.args.getlist("key")
        if len(keys) > 12:
            raise ValueError("Too many recent items.")
        version, idx, assets = _dataset()
        if not idx.has_names():
            raise recipes.RecipeUnavailable(
                "Recipe search needs newer game data. Ask an administrator to update it in Settings → Game data.",
                "catalog_unavailable", 409)
        rows, total = idx.search_names(query, {"all": None, "item": 0, "fluid": 1}[kind], limit=limit, offset=offset)
        recent = []
        if not query and request.args.get("recent_version") == version:
            for key in dict.fromkeys(keys):
                try:
                    k, identity = recipe_viewer.parse_key(key)
                except ValueError:
                    continue
                name = idx.names(k, [identity]).get(identity)
                if name and (kind == "all" or (k == 1) == (kind == "fluid")):
                    recent.append((k, identity, name))
        return _response(version, idx, assets, items=recipe_viewer.catalog_items(rows),
                         recent=recipe_viewer.catalog_items(recent), total=total, offset=offset, limit=limit, query=query)
    except ValueError as e:
        if isinstance(e, recipes.RecipeUnavailable) and e.code in ("missing_recipes", "index_failed"):
            message = "Recipe search cannot read the selected game data." if e.code == "index_failed" else "This game data does not include recipes."
            e = recipes.RecipeUnavailable(message + " Ask an administrator to update it in Settings → Game data.", e.code, e.status)
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
        shown, total = recipe_viewer.listing(idx, kind, key, direction, map_name, limit=limit, offset=offset)
        return _response(version, idx, assets, total=total, offset=offset,
                       recipes=shown, machines=recipe_viewer.machine_summaries(shown))
    except ValueError as e:
        return _error(e)
