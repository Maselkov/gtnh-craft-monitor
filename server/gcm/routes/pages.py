"""The single-page frontend (with per-path OpenGraph tags for link
unfurling) and icon images."""

import hashlib
import os
import re
from html import escape as html_escape
from urllib.parse import unquote

from flask import abort, Blueprint, request, Response, send_from_directory

from gcm import auth, charts, config, icons, inventory, security, state, store


bp = Blueprint("pages", __name__)


@bp.route("/icons")
@auth.public
def icon():
    # Deliberately a query param, not /icons/<path:...> - an encoded
    # slash (%2F) inside a URL *path segment* gets mangled or rejected by
    # a lot of reverse proxies (security measure against path-traversal
    # tricks), even though the icon paths from NESQL are inherently
    # slash-containing (item/gregtech/whatever.png). Query string values
    # don't have that problem - %2F there just decodes normally.
    img_path = request.args.get("path", "")
    if not img_path:
        abort(404)
    # Animated icons are APNGs; ?still=1 asks for just the first frame.
    if request.args.get("still") == "1":
        data = icons.read_still_image(img_path)
    else:
        data = icons.read_image(img_path)
    if data is None:
        abort(404)
    return Response(
        data,
        mimetype="image/png",
        headers={
            # Icons for a given item never change, safe to cache hard.
            "Cache-Control": "public, max-age=604800, immutable",
        },
    )


NETWORK_ITEM_PATH_PREFIX = "/network/item/"


def parse_item_url_path(identifier):
    """Mirrors the frontend's own parseItemUrlPath() exactly: mod:internal
    or mod:internal:damage for an item, bare internal for a fluid (kind
    inferred from whether a colon is present at all - a fluid's internal
    name never contains one, an item's mod:internal always does - same
    heuristic already trusted elsewhere in this codebase for exactly this
    distinction, see the Cryotheum fix). An NBT variant's id follows a
    "~" (mod:internal:damage~variant)."""
    identifier, _, variant = identifier.partition("~")
    parts = [unquote(p) for p in identifier.split(":")]
    if len(parts) == 1:
        return {"mod": None, "internal": parts[0], "damage": None, "kind": "fluid", "variant": None}
    mod = parts[0] or None
    internal = parts[1]
    damage = int(parts[2]) if len(parts) >= 3 and parts[2] else 0
    return {
        "mod": mod,
        "internal": internal,
        "damage": damage,
        "kind": "item",
        # Only ever hex with an optional "L" prefix (gcm/inventory.py).
        "variant": variant if re.fullmatch(r"[0-9A-Za-z]{1,16}", variant) else None,
    }


def _build_og_tags(path, args):
    base = request.url_root.rstrip("/")
    # request.full_path always appends a trailing "?" even with no query
    # string at all (a known Flask quirk) - only include it when there's
    # an actual query to preserve, or a plain /crafts link would render
    # as ".../crafts?" for no reason.
    full_path = path + (
        "?" + request.query_string.decode("utf-8") if request.query_string else ""
    )
    title = "GTNH Monitor"
    desc = "GTNH crafting, power, and network monitor"
    image = None

    if path in ("/", "/crafts"):
        with state.crafts_lock:
            jobs = state.crafts.get("jobs", [])
        total = len(jobs)
        busy = sum(1 for j in jobs if j.get("busy"))
        title = "Crafts"
        desc = (
            f"{busy} of {total} crafting CPUs busy" if total else "No crafting data yet"
        )

    elif path == "/power":
        # The latest raw reading, like the page's own readout - not the
        # last bucket of a downsampled range.
        latest = store.power.latest()
        title = "Power"
        if latest:
            stored, capacity = latest[1], latest[2]
            pct = round(stored / capacity * 100, 1) if capacity else 0
            desc = f"{charts.format_qty(stored)} EU stored of {charts.format_qty(capacity)} EU ({pct}%)"
            image = f"{base}/api/power/chart.png?range=day"
        else:
            desc = "No power data yet"

    elif path.startswith(NETWORK_ITEM_PATH_PREFIX):
        identifier = path[len(NETWORK_ITEM_PATH_PREFIX) :]
        parsed = parse_item_url_path(identifier) if identifier else {}
        mod = parsed.get("mod")
        internal = parsed.get("internal")
        damage = parsed.get("damage")
        kind = parsed.get("kind") or "item"
        variant = parsed.get("variant")
        if internal:
            label, size = inventory.item_display_info(mod, internal, damage, kind, variant)
            title = label if label else "Item"
            unit = " mB" if kind == "fluid" else ""
            desc = (
                f"Currently stored: {size:,.0f}{unit}"
                if size is not None
                else "No data yet"
            )
            q = f"mod={mod or ''}&internal={internal}&damage={damage if damage is not None else ''}&kind={kind}"
            if variant:
                q += f"&variant={variant}"
            image = f"{base}/api/network/history/chart.png?{q}&range=day"
        else:
            title = "Item"

    elif path == "/network":
        with state.network_lock:
            items = state.network["items"]
        item_count = sum(1 for it in items if (it.get("kind") or "item") == "item")
        fluid_count = sum(1 for it in items if it.get("kind") == "fluid")
        title = "Network"
        desc = (
            f"{item_count} items, {fluid_count} fluids tracked"
            if items
            else "No network scan yet"
        )

    tags = (
        f'<meta property="og:site_name" content="GTNH Monitor">\n'
        f'<meta property="og:title" content="{html_escape(title)}">\n'
        f'<meta property="og:description" content="{html_escape(desc)}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:url" content="{html_escape(base + full_path)}">\n'
        # Tints the mobile browser chrome (address bar) to match the
        # page instead of showing a default color - two variants, one
        # per scheme, matching the same light/dark split the site's own
        # CSS already does (:root vs the prefers-color-scheme override).
        f'<meta name="theme-color" content="#0f1115" media="(prefers-color-scheme: dark)">\n'
        f'<meta name="theme-color" content="#f4f5f7" media="(prefers-color-scheme: light)">\n'
    )
    if image:
        # Discord picks between a large (~400px, below the text) and a
        # small thumbnail (~80x80px, beside the text) embed layout based
        # on two signals - twitter:card is the more reliable one on its
        # own (confirmed even Discord's own developer portal uses this
        # exact tag for its own link previews, so this isn't a trick,
        # it's the documented mechanism), and explicit width/height are
        # a secondary signal that also lets Discord skip fetching the
        # image first just to check its aspect ratio. Without either,
        # it was evidently landing on the thumbnail layout by default.
        tags += (
            f'<meta property="og:image" content="{html_escape(image)}">\n'
            f'<meta property="og:image:width" content="{charts.WIDTH_PX}">\n'
            f'<meta property="og:image:height" content="{charts.HEIGHT_PX}">\n'
            f'<meta name="twitter:card" content="summary_large_image">\n'
        )
    return tags


@bp.route("/", methods=["GET"])
@bp.route("/crafts", methods=["GET"])
@bp.route("/power", methods=["GET"])
@bp.route("/network", methods=["GET"])
@bp.route("/network/item/<identifier>", methods=["GET"])
@auth.public
def index(identifier=None):
    html = INDEX_HTML.replace(
        "<!--OG_TAGS-->", _build_og_tags(request.path, request.args)
    )
    response = Response(html, mimetype="text/html")
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


@bp.route("/sw.js", methods=["GET"])
@auth.public
def service_worker():
    # From the site root rather than /static/, so its scope covers the
    # whole page - iOS only delivers pushes to a worker that does.
    response = send_from_directory(STATIC_DIR, "sw.js", mimetype="text/javascript")
    # Browsers check for a new worker on each load; never let a cache
    # keep an old one running.
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/static/js/<name>", methods=["GET"])
@auth.public
def js_module(name):
    source = JS_MODULES.get(name)
    if source is None:
        abort(404)
    response = Response(source, mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache"
    response.add_etag()
    return response.make_conditional(request)


INDEX_HTML_PATH = os.path.join(config.SERVER_DIR, "index.html")
STATIC_DIR = os.path.join(config.SERVER_DIR, "static")
JS_DIR = os.path.join(STATIC_DIR, "js")
INDEX_HTML = None
CONTENT_SECURITY_POLICY = None
# static/js/*.js by file name, with every import stamped like main.js.
JS_MODULES = {}

# Only the one form the modules use: `... from './name.js';`.
_MODULE_IMPORT = re.compile(r"(\bfrom\s+'\./[\w-]+\.js)'")


def _asset_version():
    """Short hash of everything under static/, appended to asset URLs as
    ?v=... so a deploy with changed CSS/JS is never served from a stale
    browser cache."""
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(STATIC_DIR)):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            digest.update(os.path.relpath(path, STATIC_DIR).encode("utf-8"))
            with open(path, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()[:12]


def load_index_html():
    global INDEX_HTML, CONTENT_SECURITY_POLICY
    version = _asset_version()
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        INDEX_HTML = f.read().replace("__ASSET_VERSION__", version)
    # Modules import each other with the same ?v= as main.js. Without
    # it a browser can pair a new main.js with an old module it still
    # has cached - a missing export then stops the whole page from
    # running (seen on Android Chrome despite no-cache). With it, each
    # release's modules are all new URLs.
    JS_MODULES.clear()
    for name in os.listdir(JS_DIR):
        if name.endswith(".js"):
            with open(os.path.join(JS_DIR, name), encoding="utf-8") as f:
                JS_MODULES[name] = _MODULE_IMPORT.sub(rf"\1?v={version}'", f.read())
    # Derived from the page itself so the policy can't drift from it.
    CONTENT_SECURITY_POLICY = security.content_security_policy(
        external_scripts=re.findall(r'<script\b[^>]*\bsrc="(https://[^"]+)"', INDEX_HTML),
        inline_style_values=re.findall(r'\sstyle="([^"]*)"', INDEX_HTML),
    )
