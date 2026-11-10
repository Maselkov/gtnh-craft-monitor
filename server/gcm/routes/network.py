"""ME network browser endpoints: scan ingestion from
oc/network_browser.lua, the live item list, per-item quantity history
and item pins. The logic behind them is in gcm/inventory.py."""

import json
import logging
import secrets
import time

from flask import abort, Blueprint, g, jsonify, request, Response, send_file

from gcm import auth, charts, gamedata, icons, inventory, item_info, memory, multiblocks, patterns, planner, state, stock, store

log = logging.getLogger(__name__)


bp = Blueprint("network", __name__)


@bp.route("/api/network/scan/start", methods=["POST"])
@auth.api_key_required
def network_scan_start():
    payload = request.get_json(silent=True)
    interval = payload.get("scan_interval_seconds") if isinstance(payload, dict) else None
    # catalog_version tells network_browser.lua to fetch
    # /api/network/catalog when it doesn't have that version's catalog yet.
    # heavy_ids: see inventory.HEAVY_ID_STACKS. Read before start_scan(),
    # which leaves the live snapshot alone anyway.
    return jsonify(
        {
            "ok": True,
            "heavy_ids": inventory.heavy_ids(),
            "scan_token": inventory.start_scan(interval),
            "catalog_version": gamedata.catalog_version(),
        }
    )


@bp.route("/api/network/catalog", methods=["GET"])
@auth.api_key_required
def network_catalog_get():
    text = gamedata.catalog_text()
    if text is None:
        abort(404)
    response = Response(text, mimetype="text/plain")
    response.headers["X-Catalog-Version"] = gamedata.catalog_version()
    return response


@bp.route("/api/network/scan/batch", methods=["POST"])
@auth.api_key_required
def network_scan_batch():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400
    accepted = inventory.add_batch(payload.get("scan_token"), payload.get("items", []))
    # A 200 status either way, not 409/etc - request_with_timeout on the
    # Lua side never inspects the HTTP status code at all (confirmed
    # directly from its source: it only reads the response body via OC's
    # request iterator, which doesn't expose status), so the rejection
    # signal has to live in the JSON body itself for Lua to actually see
    # it, not in a status code nothing on that side ever checks.
    if accepted is None:
        return jsonify(
            {
                "ok": False,
                "error": "stale_scan_token",
                # The code network_browser.lua stops a scan on, so it's
                # also what a scan past MAX_SCAN_ITEMS gets.
                "detail": "This batch doesn't belong to the currently active scan "
                "(the server may have restarted, a newer scan already started, "
                "or this one went over the item limit) - discarding it rather "
                "than accumulating a partial result.",
            }
        )
    buffered, chunks_received = accepted
    return jsonify(
        {"ok": True, "buffered": buffered, "chunks_received": chunks_received}
    )


@bp.route("/api/network/scan/finish", methods=["POST"])
@auth.api_key_required
def network_scan_finish():
    payload = request.get_json(silent=True) or {}
    result = inventory.finish_scan(
        payload.get("scan_token"), payload.get("chunks_sent"), payload.get("total_errors"),
        payload.get("last_error"),
    )
    if result.get("ok") and not result.get("rejected"):
        patterns.note_terminal(payload.get("interface_terminal"))
        # Like finish_scan()'s own extras: a failure here is logged, and
        # never makes network_browser.lua think the scan failed.
        try:
            stock.evaluate_scan()
        except Exception:
            log.exception("checking stock rules failed (the scan itself succeeded)")
    # Accepted or not, the replaced snapshot or the discarded buffer was
    # just freed.
    memory.trim()
    return jsonify(result)


@bp.route("/api/network/crashed", methods=["POST"])
@auth.api_key_required
def network_crashed_post():
    """network_browser.lua caught an error that killed its scan loop:
    {error, phase, free_memory}."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400
    inventory.record_crash(payload.get("error"), payload.get("phase"), payload.get("free_memory"))
    return jsonify({"ok": True})


@bp.route("/api/network/watch", methods=["GET"])
@auth.api_key_required
def network_watch_get():
    """The items network_browser.lua checks between full scans."""
    return jsonify(stock.watch_list())


@bp.route("/api/network/levels", methods=["POST"])
@auth.api_key_required
def network_levels_post():
    """A check of the watched items: {checked: what was asked for, as
    /watch gave it; items: every stack found, as in a scan batch;
    elapsed: seconds since the check began}. Like scan/batch, a refusal
    is in the body, not the status - http.lua never reads the status."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return jsonify({"error": "invalid payload"}), 400
    found = inventory.apply_levels(payload.get("checked"), payload["items"])
    if found is None:
        return jsonify({"ok": False, "error": "scan_in_progress"})
    elapsed = payload.get("elapsed")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        elapsed = 0
    try:
        stock.evaluate_scan(started_at=time.time() - elapsed)
    except Exception:
        log.exception("checking stock rules failed (the levels were still recorded)")
    return jsonify({"ok": True, "found": found})


# Changes whenever the served snapshot could: a new process (icons are
# re-resolved on reload), new game data (icons re-resolved again) or any
# of the fields below.
_PROCESS_TAG = secrets.token_hex(4)


def _network_etag():
    """Call with state.network_lock held."""
    return "-".join(
        str(v)
        for v in (
            _PROCESS_TAG,
            icons.data_version(),
            gamedata.selected_version(),
            state.network["updated_at"],
            state.network["in_progress"],
            state.network["scan_started_at"],
            state.network["is_reconstructed"],
            state.network["levels_at"],
            (state.network["crash"] or {}).get("at"),
        )
    )


@bp.route("/api/network", methods=["GET"])
@auth.viewer_required
def network_get():
    # Every open tab re-polls the whole item list (thousands of items),
    # but it only changes once per scan - so answer with an ETag and let
    # the browser cache turn most polls into an empty 304.
    # Read first: patterns has its own lock, and the banner it drives
    # can change between item scans, when a pattern scan lands.
    needs_terminal = patterns.terminal_missing()
    with state.network_lock:
        etag = f"{_network_etag()}-{needs_terminal}"
        if request.if_none_match.contains(etag):
            response = Response(status=304)
        else:
            response = jsonify(
                {
                    "items": state.network["items"],
                    "item_count": state.network["item_count"],
                    "updated_at": state.network["updated_at"],
                    "in_progress": state.network["in_progress"],
                    "scan_started_at": state.network["scan_started_at"],
                    "is_reconstructed": state.network["is_reconstructed"],
                    "crash": state.network["crash"],
                    # None until an admin picks a GTNH version: the
                    # page then says so, as nothing else would.
                    "game_data": gamedata.selected_version(),
                    # No patterns, and no ME Interface Terminal to read
                    # them with: the tab suggests connecting one.
                    "needs_terminal": needs_terminal,
                }
            )
    response.set_etag(etag)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/api/network/info", methods=["GET"])
@auth.viewer_required
def network_info_get():
    """What the game data says about the items in the network snapshot
    (gcm/item_info.py): {items: {item key: {description,
    shift_description, rarity, ore}}, only items with any of it, and
    mods: {mod id: name}}. Apart from /api/network: on a big network
    the descriptions are megabytes, and the page only fetches these
    once it shows a tooltip or searches with #, $ or @."""
    with state.network_lock:
        etag = _network_etag()
        if request.if_none_match.contains(etag):
            response = Response(status=304)
        else:
            items = {}
            for item in state.network["items"]:
                info = item_info.describe(item)
                if info:
                    items[store.items.key_of(item)] = info
            response = jsonify({"items": items, "mods": item_info.mod_names()})
    response.set_etag(etag)
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/api/network/item-info", methods=["GET"])
@auth.viewer_required
def network_item_info_get():
    """One item's game data, by its item key (mod|internal|damage|kind,
    and |variant for an NBT variant), for the tooltips of items that
    needn't be in the network (a craft's ingredients, a plan's rows):
    {info: {description, shift_description, rarity, formula, ore,
    mod_name}}, as the item dialog gets it with its history."""
    parts = (request.args.get("key") or "").split("|")
    if len(parts) not in (4, 5) or not parts[1]:
        return jsonify({"error": "bad key"}), 400
    mod, internal, damage, kind = parts[0] or None, parts[1], parts[2], parts[3] or "item"
    variant = parts[4] if len(parts) == 5 and parts[4] else None
    try:
        damage = int(damage) if damage != "" else None
    except ValueError:
        return jsonify({"error": "bad key"}), 400
    item = inventory.describe(store.items.item_key(mod, internal, damage, kind, variant)) or {
        "mod": mod, "internal": internal, "damage": damage, "kind": kind,
    }
    return jsonify({"info": {**item_info.describe(item), "mod_name": item_info.mod_name(mod)}})


@bp.route("/api/network/history/chart.png", methods=["GET"])
@auth.viewer_required
def network_history_chart_png():
    rate_limited = charts.rate_limit_response()
    if rate_limited:
        return rate_limited
    mod = request.args.get("mod") or None
    internal = request.args.get("internal") or None
    damage_raw = request.args.get("damage")
    damage = None
    if damage_raw not in (None, ""):
        try:
            damage = int(damage_raw)
        except ValueError:
            damage = damage_raw
    kind = request.args.get("kind") or "item"
    variant = request.args.get("variant") or None
    if not internal:
        abort(400)

    range_key, rows, _ = inventory.history(
        mod, internal, damage, kind, request.args.get("range", "day"), variant
    )
    png_bytes = charts.cached_png(
        ("network", mod, internal, damage, kind, variant, range_key),
        lambda: charts.render_png(
            rows, stepped=True, span_seconds=inventory.HISTORY_RANGE_SECONDS[range_key]
        ).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@bp.route("/api/network/history", methods=["GET"])
@auth.viewer_required
def network_history_get():
    mod = request.args.get("mod") or None
    internal = request.args.get("internal") or None
    damage_raw = request.args.get("damage")
    damage = None
    if damage_raw not in (None, ""):
        try:
            damage = int(damage_raw)
        except ValueError:
            damage = damage_raw
    kind = request.args.get("kind") or "item"
    variant = request.args.get("variant") or None

    if not internal:
        return jsonify({"error": "missing internal"}), 400

    range_key, rows, trend = inventory.history(
        mod, internal, damage, kind, request.args.get("range", "day"), variant
    )
    latest = rows[-1] if rows else None
    # An NBT variant's own description is found by its name, which only
    # the snapshot has; one that's left the network gets its item id's.
    item = inventory.describe(store.items.item_key(mod, internal, damage, kind, variant)) or {
        "mod": mod, "internal": internal, "damage": damage, "kind": kind,
    }

    return jsonify(
        {
            "range": range_key,
            "points": [{"t": r[0], "size": r[1]} for r in rows],
            "latest": {"t": latest[0], "size": latest[1]} if latest else None,
            "trend": trend,
            "info": {**item_info.describe(item), "mod_name": item_info.mod_name(mod)},
            # Its multiblock kit (gcm/multiblocks.py), for a controller.
            "multiblock": kind == "item" and not variant and multiblocks.available(mod, internal, damage),
        }
    )


@bp.route("/api/multiblocks", methods=["GET"])
@auth.viewer_required
def multiblocks_get():
    """Kits by name; ?network=1 limits them to stored or craftable controllers."""
    found = multiblocks.controllers(network_only=request.args.get("network") == "1")
    return jsonify({"multiblocks": [{"key": k, "name": n, "icon": icons.icon_for_key(k)} for k, n in found]})


@bp.route("/api/multiblocks/kit", methods=["GET"])
@auth.viewer_required
def multiblock_kit_get():
    """A multiblock's kit: ?key=mod:internal:damage&count=n, and a
    channel's tier as ?tier.<channel>=n (gcm/multiblocks.kit())."""
    key = request.args.get("key") or ""
    preferences = store.kit_preferences.get(g.user["id"], key) if g.user else None
    tiers = multiblocks.preferred_tiers(key, preferences)
    tiers.update({name[len("tier."):]: value for name, value in request.args.items() if name.startswith("tier.")})
    try:
        count = int(request.args.get("count") or 1)
    except ValueError:
        return jsonify({"error": "count must be a number"}), 400
    result = multiblocks.kit(key, tiers, count) if key.count(":") >= 2 else None
    if result is None:
        return jsonify({"error": "no kit for that item"}), 404
    # Whether it can be requested as one job (/api/craft/request-kit).
    result["kit_slots"] = state.kit_slots()
    result["plan_available"] = patterns.missing() is None
    result["plan_unavailable_reason"] = None if result["plan_available"] else patterns.missing_reason("crafting plan")
    result["needs_terminal"] = patterns.terminal_missing()
    result["preferred_separate"] = bool(preferences and preferences.get("separate"))
    return jsonify(result)


@bp.route("/api/multiblocks/preferences", methods=["PUT"])
@auth.login_required
@auth.operator_required
def multiblock_preferences_put():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "a kit configuration is required"}), 400
    key, tiers, separate = (payload.get(k) for k in ("key", "tiers", "separate"))
    if (not isinstance(key, str) or key.count(":") < 2 or len(key) > 512
            or not isinstance(tiers, dict) or not isinstance(separate, bool)
            or any(not isinstance(k, str) or type(v) is not int for k, v in tiers.items())):
        return jsonify({"error": "invalid kit configuration"}), 400
    result = multiblocks.kit(key, tiers)
    if result is None:
        return jsonify({"error": "no kit for that item"}), 404
    value = {
        "separate": separate,
        "tiers": {ch["name"]: ch["tier"] for ch in result["channels"]},
        "blocks": {ch["name"]: o["key"] for ch in result["channels"]
                   for o in ch["options"] if o["tier"] == ch["tier"] and o.get("key")},
    }
    store.kit_preferences.save(g.user["id"], key, value)
    return jsonify({"ok": True})


@bp.route("/api/multiblocks/layout", methods=["GET"])
@auth.viewer_required
def multiblock_layout_get():
    """Where each block of a multiblock goes, for the kit's 3D view:
    ?key= and ?tier.<channel>= as the kit takes them
    (gcm/multiblocks.layout()), and the URL of the textures it uses."""
    key = request.args.get("key") or ""
    tiers = {name[len("tier."):]: value for name, value in request.args.items() if name.startswith("tier.")}
    result = multiblocks.layout(key, tiers) if key.count(":") >= 2 else None
    if result is None:
        return jsonify({"error": "no layout for that item"}), 404
    version = gamedata.selected_version()
    result["atlas"] = f"/api/multiblocks/atlas?v={gamedata.build_id(version)}" if multiblocks.atlas_path() else None
    return jsonify(result)


@bp.route("/api/multiblocks/atlas", methods=["GET"])
@auth.viewer_required
def multiblock_atlas_get():
    """The block textures the 3D view draws with (layouts.png). Its URL
    names the game data's build, so it's cached for long."""
    path = multiblocks.atlas_path()
    if not path:
        abort(404)
    return send_file(path, mimetype="image/png", max_age=7 * 24 * 3600)


@bp.route("/api/network/changes", methods=["GET"])
@auth.viewer_required
def network_changes_get():
    """How each item's quantity changed over ?range=day|week, for the
    Network tab's Change sort: only items that did."""
    range_key, changes = inventory.changes(request.args.get("range", "day"))
    return jsonify({"range": range_key, "changes": changes})


# ---------------------------------------------------------------------
# Item pins (Network tab "favorite this item" feature) - purely a
# personal browse preference, no in-game consequence, so unlike craft
# requests/cancellation this doesn't require the operator role - any
# signed-in user can pin items.
@bp.route("/api/network/pins", methods=["GET"])
@auth.login_required
def network_item_pins_get():
    return jsonify({"pins": store.items.pins(g.user["id"])})


@bp.route("/api/network/pins", methods=["POST"])
@auth.login_required
def network_item_pins_post():
    user_id = g.user["id"]
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    variant = payload.get("variant") or None
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    store.items.pin(user_id, mod, internal, damage, kind, variant)
    return jsonify({"ok": True})


@bp.route("/api/network/pins/unpin", methods=["POST"])
@auth.login_required
def network_item_pins_unpin():
    user_id = g.user["id"]
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    variant = payload.get("variant") or None
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    store.items.unpin(user_id, mod, internal, damage, kind, variant)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------
# Pattern scans: network_browser.lua reading every pattern through an
# ME Interface Terminal (gcm/patterns.py). Same protocol and refusals
# as the item scan above.
@bp.route("/api/network/patterns/start", methods=["POST"])
@auth.api_key_required
def network_patterns_start():
    return jsonify({"ok": True, "scan_token": patterns.start_scan()})


@bp.route("/api/network/patterns/batch", methods=["POST"])
@auth.api_key_required
def network_patterns_batch():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("patterns"), list):
        return jsonify({"error": "invalid payload"}), 400
    accepted = patterns.add_batch(payload.get("scan_token"), payload["patterns"])
    if accepted is None:
        return jsonify({"ok": False, "error": "stale_scan_token"})
    buffered, chunks_received = accepted
    return jsonify({"ok": True, "buffered": buffered, "chunks_received": chunks_received})


@bp.route("/api/network/patterns/finish", methods=["POST"])
@auth.api_key_required
def network_patterns_finish():
    payload = request.get_json(silent=True) or {}
    result = patterns.finish_scan(
        payload.get("scan_token"), payload.get("chunks_sent"), payload.get("total_errors"),
        payload.get("last_error"),
    )
    memory.trim()  # as scan/finish
    return jsonify(result)


# Signed-in only, unlike the item list: each pattern says where in the
# world its machine is.
@bp.route("/api/network/patterns", methods=["GET"])
@auth.login_required
def network_patterns_get():
    etag = f"{_PROCESS_TAG}-{icons.data_version()}-{patterns.version()}"
    if request.if_none_match.contains(etag):
        response = Response(status=304)
    else:
        response = jsonify(patterns.snapshot())
    response.set_etag(etag)
    response.headers["Cache-Control"] = "private, no-cache"
    return response


# The most a plan can be asked for: AE2 takes a long, but a plan for
# more than this is no use to anyone.
MAX_PLAN_AMOUNT = 10**12
# The most items one plan can be for (a multiblock kit's blocks).
MAX_PLAN_TARGETS = 100


def _plan_targets(raw):
    """?targets= as [(item key, amount)], and an error or None."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, "targets must be JSON"
    if not isinstance(parsed, list) or len(parsed) > MAX_PLAN_TARGETS:
        return None, f"targets must be a list of at most {MAX_PLAN_TARGETS}"
    targets = []
    for t in parsed:
        if not (isinstance(t, list) and len(t) == 2 and isinstance(t[0], str) and t[0].count("|") >= 3
                and isinstance(t[1], int) and not isinstance(t[1], bool) and 1 <= t[1] <= MAX_PLAN_AMOUNT):
            return None, "each target must be [item key, amount]"
        targets.append((t[0], t[1]))
    return targets, None


@bp.route("/api/network/plan", methods=["GET"])
@auth.login_required
def network_plan_get():
    """What crafting `amount` of an item would take (gcm/planner.py), for
    the craft-request dialog: ?mod&internal&damage&kind&variant&amount,
    and optionally choose={item key: pattern id} for steps made with a
    pattern other than the default. {plan: null, reason} when there's
    nothing to plan from.

    The tree is sent a few levels at a time - a whole endgame plan can be
    tens of MB. The first answer has two levels below the requested item;
    a step with more below says how many (`more`), and asking again with
    path (its child positions from the top, "0.3.1") and the first
    answer's version gets the next level under it, or {stale: true} if
    the patterns or stock have changed since. reveal=<item key>... with
    the version gets the way to each place those items are at
    (planner.reveal), for the tree's filter box.

    For a multiblock kit, ?targets=[[item key, amount], ...] in place of
    the item plans them all together (planner.plan_many), under a step
    for the kit: ?kit=<its controller's mod:internal:damage>&count=n."""
    targets = None
    if request.args.get("targets") is not None:
        targets, error = _plan_targets(request.args.get("targets"))
        if error:
            return jsonify({"error": error}), 400
    internal = request.args.get("internal")
    if not internal and targets is None:
        return jsonify({"error": "missing internal"}), 400
    try:
        amount = int(request.args.get("amount", "1"))
    except ValueError:
        return jsonify({"error": "amount must be a whole number"}), 400
    if not 1 <= amount <= MAX_PLAN_AMOUNT:
        return jsonify({"error": "amount out of range"}), 400
    damage_raw = request.args.get("damage")
    try:
        damage = int(damage_raw) if damage_raw not in (None, "") else None
    except ValueError:
        return jsonify({"error": "bad damage"}), 400
    try:
        choose = json.loads(request.args.get("choose") or "{}")
    except ValueError:
        choose = {}
    if not isinstance(choose, dict):
        choose = {}
    choices = {k: v for k, v in choose.items() if isinstance(k, str) and isinstance(v, str)}

    key = store.items.item_key(
        request.args.get("mod") or None, internal, damage,
        request.args.get("kind") or "item", request.args.get("variant") or None,
    ) if targets is None else None
    pattern_list, patterns_at = patterns.current()
    if patterns_at is None:
        return jsonify({"plan": None, "reason": patterns.missing_reason("crafting plan"),
                        "needs_terminal": patterns.terminal_missing()})
    path_raw = request.args.get("path")
    path = None
    if path_raw is not None:
        try:
            path = [int(p) for p in path_raw.split(".")] if path_raw else []
        except ValueError:
            return jsonify({"error": "bad path"}), 400
    reveal = request.args.getlist("reveal")
    stock_levels, stock_at, stock_version = inventory.stock_levels()
    version = f"{patterns_at}|{stock_version}"
    if (path is not None or reveal) and request.args.get("version") != version:
        return jsonify({"stale": True})

    rules = planner.rules_for_user(g.user["id"])
    if targets is not None:
        root = multiblocks.plan_root(request.args.get("kit") or "", request.args.get("count"))
        result = planner.cached_plan_many(pattern_list, stock_levels, stock_version, targets, root, choices, rules,
                                          describe=inventory.describe)
        if result is None:
            return jsonify({"plan": None, "reason": "Nothing to request, so nothing to plan."})
    else:
        result = planner.cached_plan(pattern_list, stock_levels, stock_version, key, amount, choices, rules,
                                     describe=inventory.describe)
    if result is None:
        return jsonify({"plan": None, "reason": "None of the network's patterns makes this item."})
    if path is not None:
        node = planner.subtree(result["root"], path)
        if node is None:
            return jsonify({"stale": True})
        return jsonify({"node": planner.trim(node, 1)})
    if reveal:
        return jsonify(planner.reveal_answer(result, reveal))
    return jsonify({
        "plan": {
            **result,
            "root": planner.trim(result["root"], 2),
            # A missing item's places (`at`) only once, in `missing`: the
            # same item is in both lists.
            "items": [{k: v for k, v in i.items() if k != "at"} for i in result["items"]],
            "index": planner.index_listing(result["index"]),
        },
        "version": version,
        "patterns_updated_at": patterns_at,
        "stock_updated_at": stock_at,
    })
