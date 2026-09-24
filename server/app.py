"""
GTNH Craft Monitor - server side
---------------------------------
Receives crafting-CPU status POSTed by the in-game OpenComputers script
and serves a small webpage that polls it and displays current jobs.

Run directly (see README for the required environment variables):
    pip install -r requirements.txt
    python app.py

Or via Docker (see Dockerfile / docker-compose.yml in this project).
"""

import os
import time
import json
import secrets
import hashlib
import hmac
import ipaddress
import uuid
import zipfile
import sqlite3
import sys
import threading
from collections import OrderedDict, deque
from datetime import datetime
from html import escape as html_escape
from io import BytesIO
from urllib.parse import unquote

import matplotlib

matplotlib.use("Agg")  # no display in a container - must be set before
# importing pyplot, or it tries (and fails) to
# find a GUI backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

from flask import Flask, request, jsonify, Response, abort
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)


def _parse_trusted_proxies(value):
    networks = []
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as error:
            raise RuntimeError(f"Invalid TRUSTED_PROXIES entry: {entry}") from error
    return networks


TRUSTED_PROXY_NETWORKS = _parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES", ""))


def _is_trusted_proxy_address(address):
    try:
        ip_address = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip_address in network for network in TRUSTED_PROXY_NETWORKS)


_direct_wsgi_app = app.wsgi_app
_proxy_wsgi_app = ProxyFix(_direct_wsgi_app, x_for=1, x_proto=1, x_host=1)


def _trusted_proxy_wsgi_app(environ, start_response):
    if _is_trusted_proxy_address(environ.get("REMOTE_ADDR", "")):
        environ["gtnh.trusted_proxy"] = True
        return _proxy_wsgi_app(environ, start_response)
    return _direct_wsgi_app(environ, start_response)


app.wsgi_app = _trusted_proxy_wsgi_app

API_KEY = os.environ.get("API_KEY", "")
STALE_AFTER_SECONDS = int(os.environ.get("STALE_AFTER_SECONDS", "30"))
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") != "0"
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024


def _require_api_key():
    supplied_key = request.headers.get("X-API-Key", "")
    return bool(API_KEY) and hmac.compare_digest(supplied_key, API_KEY)


def _require_runtime_secrets():
    if len(API_KEY) < 32 or API_KEY == "change-me":
        raise RuntimeError(
            "API_KEY must be a unique secret of at least 32 characters. "
            "Set it in .env before starting the server."
        )


# Set by _configure(), called from create_app(). Nothing here touches
# the filesystem at import time.
DATA_DIR = None
ICONS_LOOKUP_PATH = None
IMAGES_ZIP_PATH = None
POWER_DB_PATH = None
ITEM_HISTORY_DB_PATH = None
CRAFT_HISTORY_DB_PATH = None


def _configure(data_dir=None, api_key=None):
    global API_KEY, DATA_DIR, ICONS_LOOKUP_PATH, IMAGES_ZIP_PATH
    global POWER_DB_PATH, ITEM_HISTORY_DB_PATH, CRAFT_HISTORY_DB_PATH
    global _images_zip, _images_zip_missing_logged
    if api_key is not None:
        API_KEY = api_key
    DATA_DIR = data_dir or os.environ.get("DATA_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data"
    )
    ICONS_LOOKUP_PATH = os.path.join(DATA_DIR, "icons_lookup.json")
    IMAGES_ZIP_PATH = os.path.join(DATA_DIR, "images.zip")
    POWER_DB_PATH = os.path.join(DATA_DIR, "power.db")
    ITEM_HISTORY_DB_PATH = os.path.join(DATA_DIR, "item_history.db")
    CRAFT_HISTORY_DB_PATH = os.path.join(DATA_DIR, "craft_history.db")
    _images_zip = None
    _images_zip_missing_logged = False

# Built from a NESQL export (see oc/README notes). Two separate keyspaces:
# - by_key: "modid:internalname:damage" -> image path, for ordinary items.
#   AE2 gives us `name` as "modid:internalname" for these (colon present).
# - fluids_by_key: raw Forge fluid registry name -> image path. Confirmed
#   empirically: GT/GTNH's Fluid Discretizer pseudo-items have a `name`
#   with NO colon at all - it's just the bare fluid registry name itself
#   (e.g. name="molten.mutatedlivingsolder", no mod prefix, no NBT tag
#   involved despite that being the original assumption). Forge fluid
#   names are globally unique, so no further disambiguation is needed.
# by_label is the weakest fallback for either case - plain item labels
# collide across mods ~8% of the time, so only used when nothing else matched.
_icons_by_key = {}
_icons_by_label = {}
_fluids_by_key = {}


def _load_icon_lookup():
    global _icons_by_key, _icons_by_label, _fluids_by_key
    icon_data = {}
    if os.path.exists(ICONS_LOOKUP_PATH):
        with open(ICONS_LOOKUP_PATH, "r", encoding="utf-8") as f:
            icon_data = json.load(f)
    _icons_by_key = icon_data.get("by_key", {})
    _icons_by_label = icon_data.get("by_label", {})
    _fluids_by_key = icon_data.get("fluids_by_key", {})

_images_zip = None
_images_zip_missing_logged = False
_zip_lock = threading.Lock()


def _get_images_zip():
    global _images_zip, _images_zip_missing_logged
    if _images_zip is not None:
        return _images_zip
    with _zip_lock:
        if _images_zip is None:
            if os.path.exists(IMAGES_ZIP_PATH):
                _images_zip = zipfile.ZipFile(IMAGES_ZIP_PATH, "r")
            elif not _images_zip_missing_logged:
                print(
                    f"[icons] {IMAGES_ZIP_PATH} not found - icons will be blank until it's added."
                )
                _images_zip_missing_logged = True
    return _images_zip


def _damage_str(damage):
    if damage is None:
        return None
    if isinstance(damage, float) and damage.is_integer():
        return str(int(damage))
    return str(damage)


def resolve_icon(mod, internal, damage, label):
    """Returns the image path inside images.zip for an item or fluid, or None."""
    if mod and internal:
        dmg = _damage_str(damage)
        if dmg is not None:
            path = _icons_by_key.get(f"{mod}:{internal}:{dmg}")
            if path:
                return path
    elif internal:
        # No mod prefix at all in `name` -> treat the whole thing as a
        # bare fluid registry name (see comment above the lookup tables).
        path = _fluids_by_key.get(internal)
        if path:
            return path
    if label:
        return _icons_by_label.get(label)
    return None


def _attach_item_icons(items):
    for item in items or []:
        icon = resolve_icon(
            item.get("mod"), item.get("internal"), item.get("damage"), item.get("name")
        )
        if icon:
            item["icon"] = icon
    return items


def _attach_job_icons(jobs):
    # Resolved once here, at ingestion, rather than on every /api/crafts
    # poll - the lookup table is static, no point redoing the work every
    # 3s for however many browser tabs happen to be open.
    for job in jobs or []:
        icon = resolve_icon(
            job.get("final_output_mod"),
            job.get("final_output_internal"),
            job.get("final_output_damage"),
            job.get("final_output"),
        )
        if icon:
            job["final_output_icon"] = icon
        _attach_item_icons(job.get("active"))
        _attach_item_icons(job.get("pending"))
        _attach_item_icons(job.get("stored"))
    return jobs


_lock = threading.Lock()
_state = {
    "jobs": [],
    "source": None,
    "received_at": None,  # server-side wall clock, for staleness checks
}

# ---------------------------------------------------------------------
# ME network item browser (fed by oc/network_browser.lua)
#
# In-memory as the live source of truth (unlike power/craft history,
# this is "what's in the network right now", not a time series) - but
# ALSO mirrored into the network_snapshot SQLite table on every real
# scan (see _persist_network_snapshot()/_load_network_snapshot() further
# down), specifically so a server restart doesn't leave the Network tab
# empty until the next scan completes.
#
# A scan is a start/batch*/finish sequence, not one big POST - the whole
# reason getItemsInNetworkById() is viable at all is that both the
# candidate catalog AND the results are streamed through in small
# batches on the Lua side (confirmed via repeated testing: holding the
# whole ~10,885-entry catalog in memory at once left only ~850KB free at
# the low point on a ~4MB computer; streaming both sides left ~1.9MB -
# real margin, not just "didn't crash this time"). Buffering into
# _network_buffer during the scan and only promoting it to
# _network_state on /finish means a browser reading GET /api/network
# mid-scan still sees the last COMPLETE snapshot, not a half-built one.
_network_lock = threading.Lock()
_network_buffer = []
_network_state = {
    "items": [],
    "item_count": 0,
    "updated_at": None,
    "in_progress": False,
    "scan_started_at": None,
    # True from server startup (if a persisted snapshot was found to
    # load) until the FIRST real scan completes afterward - lets the
    # frontend show "this is what we had before the restart" rather
    # than presenting reconstructed data as if it were a fresh scan.
    "is_reconstructed": False,
    # The active scan's identity - a fresh random token minted on every
    # scan/start, required back on every scan/batch and scan/finish.
    # NOT exposed to the frontend (network_get() below lists its own
    # response fields explicitly, so this simply isn't one of them) -
    # purely internal bookkeeping for the mechanism explained in full at
    # network_scan_finish() below.
    "current_scan_token": None,
    # How many scan/batch calls THIS process has actually accepted for
    # the current scan token - independently counted server-side, not
    # trusted from whatever Lua claims. Compared at scan/finish against
    # the chunks_sent count Lua reports, so the server verifies
    # completeness rather than assuming it.
    "chunks_received": 0,
}


@app.route("/api/network/scan/start", methods=["POST"])
def network_scan_start():
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    with _network_lock:
        _network_buffer.clear()
        _network_state["in_progress"] = True
        _network_state["scan_started_at"] = time.time()
        _network_state["chunks_received"] = 0
        token = secrets.token_hex(8)
        _network_state["current_scan_token"] = token
    return jsonify({"ok": True, "scan_token": token})


def _check_scan_token(payload):
    """True if payload's scan_token matches the currently active scan.
    Must be called with _network_lock already held."""
    token = payload.get("scan_token") if isinstance(payload, dict) else None
    current = _network_state.get("current_scan_token")
    return bool(current) and token == current


@app.route("/api/network/scan/batch", methods=["POST"])
def network_scan_batch():
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400
    items = _attach_item_icons(payload.get("items", []))
    with _network_lock:
        # See network_scan_finish() for the full reasoning - this batch
        # only gets accepted if it actually belongs to the scan this
        # process currently thinks is active. A 200 status either way,
        # not 409/etc - request_with_timeout on the Lua side never
        # inspects the HTTP status code at all (confirmed directly from
        # its source: it only reads the response body via OC's request
        # iterator, which doesn't expose status), so the rejection
        # signal has to live in the JSON body itself for Lua to actually
        # see it, not in a status code nothing on that side ever checks.
        if not _check_scan_token(payload):
            return jsonify(
                {
                    "ok": False,
                    "error": "stale_scan_token",
                    "detail": "This batch doesn't belong to the currently active scan "
                    "(the server may have restarted, or a newer scan already "
                    "started) - discarding it rather than accumulating a partial result.",
                }
            )
        _network_buffer.extend(items)
        _network_state["chunks_received"] += 1
        buffered = len(_network_buffer)
        chunks_received = _network_state["chunks_received"]
    return jsonify(
        {"ok": True, "buffered": buffered, "chunks_received": chunks_received}
    )


@app.route("/api/network/scan/finish", methods=["POST"])
def network_scan_finish():
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    with _network_lock:
        # Primary defense #1: does this scan/finish actually belong to
        # the scan this process currently thinks is active? A structural
        # check, not a heuristic - it directly answers "is this data
        # really from a complete, uninterrupted scan cycle" rather than
        # guessing from how much data showed up. Catches a server
        # restart mid-scan (a fresh process has no memory of any
        # previously-issued token, so nothing from the old scan can ever
        # match) AND a newer scan overlapping the tail of an older one on
        # the SAME process with no restart at all (scan/start always
        # mints a brand new token, immediately invalidating whatever the
        # previous scan was using).
        if not _check_scan_token(payload):
            _network_state["in_progress"] = False
            _network_state["scan_started_at"] = None
            app.logger.warning(
                "Rejected scan/finish: stale or missing scan_token - the server "
                "likely restarted mid-scan, or a newer scan already started. "
                "Keeping the previous snapshot."
            )
            return jsonify(
                {
                    "ok": False,
                    "error": "stale_scan_token",
                    "item_count": _network_state["item_count"],
                }
            )

        # Primary defense #2: did every chunk network_browser.lua
        # attempted to send actually arrive, and did nothing else go
        # wrong along the way? chunks_received is counted independently
        # HERE, server-side, from real accepted scan/batch calls - never
        # just trusted from whatever Lua claims - and compared against
        # chunks_sent, which Lua increments on every attempt (success OR
        # failure, before the outcome is even known - counting only
        # successes would let a failed POST silently vanish from both
        # sides' tallies, defeating the whole check). total_errors
        # (already tracked end-to-end in run_scan() for its own debug
        # logging, just never sent before now) catches the other place
        # data can go missing that a chunk-count comparison alone can't
        # see at all: a getItemsInNetworkById() call failing outright
        # for some candidate batch never produces a chunk to send in the
        # first place, so there's nothing for a transit-only check to
        # notice as lost.
        #
        # This replaces an earlier retention-fraction heuristic (reject
        # if the scan came back under some % of the last snapshot's
        # size) that was deliberately removed: arbitrary threshold,
        # genuinely no way to pick a number that's simultaneously loose
        # enough to accept a real, deliberate mass removal and tight
        # enough to reliably catch a partial scan - and it could get
        # permanently stuck rejecting a legitimate smaller network
        # forever, since a correctly-tokened, complete-but-smaller scan
        # would just keep getting compared against the same stale,
        # larger snapshot it could never update past. This mutual-
        # verification approach has no threshold to tune at all: a
        # scan is either provably complete or it isn't, and a genuine
        # mass removal - every query and POST succeeding, zero errors,
        # counts matching - is correctly accepted rather than guessed at.
        chunks_sent = payload.get("chunks_sent")
        total_errors = payload.get("total_errors")
        chunks_received = _network_state["chunks_received"]
        if (
            chunks_sent is None
            or total_errors is None
            or total_errors != 0
            or chunks_received != chunks_sent
        ):
            _network_state["in_progress"] = False
            _network_state["scan_started_at"] = None
            app.logger.warning(
                "Rejected a network scan as incomplete: chunks_received=%s chunks_sent=%s "
                "total_errors=%s - keeping the previous snapshot rather than recording "
                "every missing item as vanished.",
                chunks_received,
                chunks_sent,
                total_errors,
            )
            return jsonify(
                {
                    "ok": True,
                    "item_count": _network_state["item_count"],
                    "rejected": True,
                    "reason": f"incomplete scan (received {chunks_received}/{chunks_sent} chunks, "
                    f"{total_errors} errors) - discarded",
                }
            )

        old_items = _network_state["items"]
        new_items = list(_network_buffer)
        _network_state["items"] = new_items
        _network_state["item_count"] = len(new_items)
        _network_state["updated_at"] = time.time()
        _network_state["in_progress"] = False
        _network_state["is_reconstructed"] = False  # this is a real, live scan now
        item_count = _network_state["item_count"]

    # Outside the lock - a SQLite write shouldn't hold up anyone reading
    # the live snapshot at the same moment. Both wrapped defensively -
    # neither is the actual scan result the website depends on (that's
    # already committed to _network_state above), just best-effort
    # extras (history charts, restart recovery) - a failure in either
    # one is logged, not allowed to turn into a 500 that makes
    # network_browser.lua think the whole scan failed when it didn't.
    try:
        _record_item_history_changes(old_items, new_items)
    except Exception as e:
        app.logger.exception(
            "item history recording failed (scan itself still succeeded): %s", e
        )
    try:
        _persist_network_snapshot(new_items)
    except Exception as e:
        app.logger.exception(
            "network snapshot persistence failed (scan itself still succeeded): %s", e
        )

    return jsonify({"ok": True, "item_count": item_count})


@app.route("/api/network", methods=["GET"])
def network_get():
    with _network_lock:
        return jsonify(
            {
                "items": _network_state["items"],
                "item_count": _network_state["item_count"],
                "updated_at": _network_state["updated_at"],
                "in_progress": _network_state["in_progress"],
                "scan_started_at": _network_state["scan_started_at"],
                "is_reconstructed": _network_state["is_reconstructed"],
            }
        )


# ---------------------------------------------------------------------
# Craft requests (in-memory, ephemeral - not SQLite, same reasoning as
# the network browser: a request's lifetime is minutes at most, nothing
# here needs to survive a server restart)
#
# Two-sided auth:
#   - Lua-facing endpoints (poll pending, report results) use the same
#     API_KEY every other Lua<->server call already uses.
#   - Browser-facing endpoints use the signed-in user's session;
#     submitting or cancelling a craft additionally requires the
#     operator or admin role.
_craft_requests_lock = threading.Lock()
_craft_requests = {}  # id -> dict, see craft_request_post() for shape
_craft_request_next_id = 1

# Bounds on how long request records live in memory. A request is
# "picked up" once craft_monitor.lua has fetched it from /pending. One
# that's never picked up means the game isn't polling, and it's failed
# rather than left to run whenever the game comes back. A picked-up
# craft request stays pending while AE2 plans the craft (minutes for a
# big job), so it gets a much longer limit.
CRAFT_REQUEST_PICKUP_TIMEOUT_SECONDS = 120
CRAFT_REQUEST_RESULT_TIMEOUT_SECONDS = 3600
FAILED_CRAFT_REQUEST_RETENTION_SECONDS = 86400


def _expire_craft_requests():
    """Fails stale pending requests and forgets old failed ones. Returns
    [(req_id, reason)] for the caller to record in history outside the
    lock. Must be called with _craft_requests_lock held."""
    now = time.time()
    expired = []
    for req_id, req in list(_craft_requests.items()):
        if req["status"] == "pending":
            picked_up_at = req.get("picked_up_at")
            if picked_up_at is None:
                if now - req["created_at"] <= CRAFT_REQUEST_PICKUP_TIMEOUT_SECONDS:
                    continue
                reason = "the game didn't pick up this request - is craft_monitor running?"
            elif now - picked_up_at > CRAFT_REQUEST_RESULT_TIMEOUT_SECONDS:
                reason = "no result from the game - check in-game"
            else:
                continue
            req["status"] = "failed"
            req["reason"] = reason
            req["failed_at"] = now
            expired.append((req_id, reason))
        elif req["status"] == "failed":
            failed_at = req.get("failed_at", req["created_at"])
            if now - failed_at > FAILED_CRAFT_REQUEST_RETENTION_SECONDS:
                del _craft_requests[req_id]
    return expired


def _record_expired_craft_requests(expired):
    for req_id, reason in expired:
        _update_craft_request_history(req_id, "failed", reason, None)


@app.route("/api/craft/request", methods=["POST"])
def craft_request_post():
    global _craft_request_next_id
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    if not _is_operator(_session_user()):
        return jsonify({"error": "operator access required"}), 403

    payload = request.get_json(silent=True) or {}
    label = payload.get("label")
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    amount = payload.get("amount")
    kind = payload.get("kind") or "item"

    if not label or not internal:
        return jsonify({"error": "missing label/internal"}), 400
    if not isinstance(amount, (int, float)) or amount <= 0:
        return jsonify({"error": "amount must be a positive number"}), 400
    if kind not in ("item", "fluid"):
        return jsonify({"error": "kind must be item or fluid"}), 400

    icon = resolve_icon(mod, internal, damage, label)
    created_at = time.time()

    with _craft_requests_lock:
        req_id = _craft_request_next_id
        _craft_request_next_id += 1
        _craft_requests[req_id] = {
            "id": req_id,
            "user_id": user_id,
            "label": label,
            "mod": mod,
            "internal": internal,
            "damage": damage,
            "amount": amount,
            "kind": kind,  # "item" or "fluid" - craft_monitor.lua's request
            # loop needs this to pick the right getCraftables()
            # filter shape (confirmed via a real successful
            # fluid request: items filter by name+damage,
            # fluids only matched when filtered by label)
            "icon": icon,
            "status": "pending",  # pending -> accepted (removed from list, real
            # pin takes over) | failed (stays until dismissed)
            "reason": None,
            "cpu_name": None,
            "created_at": created_at,
        }
    _record_craft_request_history(
        req_id, user_id, label, mod, internal, damage, amount, kind, created_at
    )
    return jsonify({"ok": True, "id": req_id})


@app.route("/api/craft/requests", methods=["GET"])
def craft_requests_get():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    with _craft_requests_lock:
        expired = _expire_craft_requests()
        # "accepted" requests aren't returned here at all - the moment
        # one is accepted, a real pin is created and it's the pin
        # (existing infrastructure) that represents it from then on, not
        # this ephemeral request record.
        mine = [
            r
            for r in _craft_requests.values()
            if r["user_id"] == user_id and r["status"] in ("pending", "failed")
        ]
        mine.sort(key=lambda r: r["created_at"], reverse=True)
    _record_expired_craft_requests(expired)
    return jsonify({"requests": mine})


@app.route("/api/craft/requests/<int:req_id>/dismiss", methods=["POST"])
def craft_request_dismiss(req_id):
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    with _craft_requests_lock:
        req = _craft_requests.get(req_id)
        if req and req["user_id"] == user_id:
            del _craft_requests[req_id]
    return jsonify({"ok": True})


@app.route("/api/craft/requests/pending", methods=["GET"])
def craft_requests_pending():
    # Lua polling for work - every user's pending requests at once.
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    now = time.time()
    with _craft_requests_lock:
        expired = _expire_craft_requests()
        pending = [r for r in _craft_requests.values() if r["status"] == "pending"]
        for r in pending:
            r.setdefault("picked_up_at", now)
        pending = [dict(r) for r in pending]
    _record_expired_craft_requests(expired)
    return jsonify({"requests": pending})


def _create_pin_bypassing_busy_check(user_id, cpu_name):
    # Deliberately NOT going through the normal pin-creation path (which
    # requires the CPU to already show busy=true in _state["jobs"]) -
    # that state only updates on craft_monitor.lua's regular 5s poll,
    # and Lua's craft-request thread can report "accepted, CPU X" faster
    # than that next regular poll lands. The trust here comes directly
    # from Lua's own confirmation that it just watched this CPU get
    # assigned, not from re-deriving it against a possibly-stale cache.
    conn = _craft_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_pins (user_id, cpu_name, pinned_at) VALUES (?, ?, ?)",
            (user_id, cpu_name, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


@app.route("/api/craft/requests/<int:req_id>/result", methods=["POST"])
def craft_request_result(req_id):
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ("accepted", "failed"):
        return jsonify({"error": "status must be accepted or failed"}), 400

    with _craft_requests_lock:
        req = _craft_requests.get(req_id)
        if not req:
            return jsonify({"error": "unknown request id"}), 404
        if status == "accepted":
            req["cpu_name"] = payload.get("cpu_name")
            user_id = req["user_id"]
            cpu_name = req["cpu_name"]
            del _craft_requests[req_id]  # existing pin infra takes over now
        else:
            req["status"] = "failed"
            req["reason"] = payload.get("reason") or "request failed"
            req["failed_at"] = time.time()
            user_id = None
            cpu_name = None

    if status == "accepted" and user_id and cpu_name:
        _create_pin_bypassing_busy_check(user_id, cpu_name)
        # Also seed the transition-detection state the main status loop
        # relies on - without this, a craft that completes faster than
        # craft_monitor.lua's own 5s poll interval is invisible to
        # _process_craft_transitions entirely: it never observes a
        # busy=true sample to compare against the later busy=false one,
        # so the completion is never detected, the pin is never cleaned
        # up, and it just sits there forever pointing at an idle CPU.
        # Seeding "last known busy=true" here means the NEXT real poll -
        # even if it's the first one that ever samples this CPU - can
        # still correctly detect the transition retroactively. progress
        # is left unknown (None) rather than guessed, which _classify_status
        # now correctly reads as "finished" for exactly this reason.
        with _craft_tracking_lock:
            _cpu_last_busy[cpu_name] = True
            entry = _cpu_last_known.setdefault(
                cpu_name, {"label": None, "icon": None, "progress": None}
            )
            req_label = req.get("label")
            req_icon = req.get("icon")
            if req_label:
                entry["label"] = req_label
            if req_icon:
                entry["icon"] = req_icon

    _update_craft_request_history(
        req_id, status, payload.get("reason"), payload.get("cpu_name")
    )

    return jsonify({"ok": True})


# ---------------------------------------------------------------------
# Craft cancellation. Simpler than craft REQUESTS: AE2's cancel() call
# (confirmed from source) resolves synchronously on Lua's side - call
# it, get true/false back immediately, no multi-poll-cycle isComputing/
# CraftingStatus tracking needed the way .request() requires. So this is
# one round trip per cancellation, not a whole pending-state lifecycle -
# a lighter-weight mirror of the craft-request pattern, not a full copy
# of it.
_cancel_requests_lock = threading.Lock()
_cancel_requests = (
    {}
)  # id -> {id, user_id, cpu_name, status, success, reason, created_at}
_cancel_request_next_id = 1

# The browser stops waiting after ~15s and tells the user the game
# didn't respond, so an unclaimed cancel must not run later (it could
# hit a different job on that CPU by then).
CANCEL_PICKUP_TIMEOUT_SECONDS = 20
CANCEL_RESULT_TIMEOUT_SECONDS = 300
RESOLVED_CANCEL_RETENTION_SECONDS = 600


def _expire_cancel_requests():
    """Same contract as _expire_craft_requests(), for cancellations.
    Must be called with _cancel_requests_lock held."""
    now = time.time()
    expired = []
    for req_id, req in list(_cancel_requests.items()):
        if req["status"] == "pending":
            picked_up_at = req.get("picked_up_at")
            if picked_up_at is None:
                if now - req["created_at"] <= CANCEL_PICKUP_TIMEOUT_SECONDS:
                    continue
                reason = "the game didn't pick up this cancellation"
            elif now - picked_up_at > CANCEL_RESULT_TIMEOUT_SECONDS:
                reason = "no result from the game - check in-game"
            else:
                continue
            req["status"] = "resolved"
            req["success"] = False
            req["reason"] = reason
            req["resolved_at"] = now
            expired.append((req_id, reason))
        elif now - req.get("resolved_at", req["created_at"]) > RESOLVED_CANCEL_RETENTION_SECONDS:
            del _cancel_requests[req_id]
    return expired


def _record_expired_cancel_requests(expired):
    for req_id, reason in expired:
        _update_craft_cancel_history(req_id, False, reason)


@app.route("/api/craft/cancel", methods=["POST"])
def craft_cancel_post():
    global _cancel_request_next_id
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    if not _is_operator(_session_user()):
        return jsonify({"error": "operator access required"}), 403

    payload = request.get_json(silent=True) or {}
    cpu_name = payload.get("cpu_name")
    if not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400

    # Can only cancel a CPU that's actually busy right now - same
    # reasoning as craft-request's own "only a busy CPU can be pinned"
    # check, and it doesn't make sense to queue a cancel for something
    # with nothing running on it.
    with _lock:
        jobs = _state.get("jobs", [])
    job = next((j for j in jobs if j.get("name") == cpu_name), None)
    if job is None:
        return jsonify({"error": "unknown cpu_name"}), 404
    if not job.get("busy"):
        return jsonify({"error": "CPU is not currently busy - nothing to cancel"}), 400

    created_at = time.time()
    with _cancel_requests_lock:
        req_id = _cancel_request_next_id
        _cancel_request_next_id += 1
        _cancel_requests[req_id] = {
            "id": req_id,
            "user_id": user_id,
            "cpu_name": cpu_name,
            "status": "pending",  # pending -> resolved
            "success": None,
            "reason": None,
            "created_at": created_at,
        }
    _record_craft_cancel_history(req_id, user_id, cpu_name, created_at)
    return jsonify({"ok": True, "id": req_id})


@app.route("/api/craft/cancel/<int:req_id>", methods=["GET"])
def craft_cancel_get(req_id):
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    with _cancel_requests_lock:
        expired = _expire_cancel_requests()
        req = _cancel_requests.get(req_id)
        req = dict(req) if req and req["user_id"] == user_id else None
    _record_expired_cancel_requests(expired)
    if not req:
        return jsonify({"error": "unknown request id"}), 404
    return jsonify(req)


@app.route("/api/craft/cancel/pending", methods=["GET"])
def craft_cancel_pending():
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    now = time.time()
    with _cancel_requests_lock:
        expired = _expire_cancel_requests()
        pending = [r for r in _cancel_requests.values() if r["status"] == "pending"]
        for r in pending:
            r.setdefault("picked_up_at", now)
        pending = [dict(r) for r in pending]
    _record_expired_cancel_requests(expired)
    return jsonify({"requests": pending})


@app.route("/api/craft/cancel/<int:req_id>/result", methods=["POST"])
def craft_cancel_result(req_id):
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    success = bool(payload.get("success"))
    reason = payload.get("reason")

    with _cancel_requests_lock:
        req = _cancel_requests.get(req_id)
        if not req:
            return jsonify({"error": "unknown request id"}), 404
        req["status"] = "resolved"
        req["success"] = success
        req["reason"] = reason
        req["resolved_at"] = time.time()

    _update_craft_cancel_history(req_id, success, reason)

    return jsonify({"ok": True})


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
SESSION_LIFETIME_SECONDS = int(
    os.environ.get("SESSION_LIFETIME_SECONDS", str(7 * 86400))
)


def _craft_db():
    return sqlite3.connect(CRAFT_HISTORY_DB_PATH, timeout=10)


def _record_craft_request_history(
    request_id, user_id, label, mod, internal, damage, amount, kind, created_at
):
    conn = _craft_db()
    try:
        conn.execute(
            "INSERT INTO craft_request_history "
            "(request_id, user_id, label, mod, internal, damage, amount, kind, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                request_id,
                user_id,
                label,
                mod,
                internal,
                damage,
                amount,
                kind,
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _update_craft_request_history(request_id, status, reason, cpu_name):
    conn = _craft_db()
    try:
        conn.execute(
            "UPDATE craft_request_history "
            "SET status = ?, reason = ?, cpu_name = ?, resolved_at = ? "
            "WHERE request_id = ? AND resolved_at IS NULL",
            (status, reason, cpu_name, time.time(), request_id),
        )
        conn.commit()
    finally:
        conn.close()


def _record_craft_cancel_history(request_id, user_id, cpu_name, created_at):
    conn = _craft_db()
    try:
        conn.execute(
            "INSERT INTO craft_cancel_history "
            "(request_id, user_id, cpu_name, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (request_id, user_id, cpu_name, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def _update_craft_cancel_history(request_id, success, reason):
    conn = _craft_db()
    try:
        conn.execute(
            "UPDATE craft_cancel_history "
            "SET status = 'resolved', success = ?, reason = ?, resolved_at = ? "
            "WHERE request_id = ? AND resolved_at IS NULL",
            (int(success), reason, time.time(), request_id),
        )
        conn.commit()
    finally:
        conn.close()


def _hash_secret(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _parse_access_token(token):
    try:
        prefix, token_id, secret = token.split("_", 2)
    except ValueError:
        return None
    if prefix != "gcm" or not token_id or not secret:
        return None
    return token_id, secret


def _create_access_token(conn, user_id, is_bootstrap=False):
    token_id = f"tok-{secrets.token_hex(12)}"
    secret = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO access_tokens "
        "(id, user_id, secret_hash, created_at, is_bootstrap) VALUES (?, ?, ?, ?, ?)",
        (token_id, user_id, _hash_secret(secret), time.time(), int(is_bootstrap)),
    )
    return f"gcm_{token_id}_{secret}"


def _find_access_token(conn, token):
    parsed = _parse_access_token(token)
    if not parsed:
        return None
    token_id, secret = parsed
    row = conn.execute(
        "SELECT id, user_id, secret_hash, revoked_at, is_bootstrap "
        "FROM access_tokens WHERE id = ?",
        (token_id,),
    ).fetchone()
    if (
        not row
        or row[3] is not None
        or not hmac.compare_digest(row[2], _hash_secret(secret))
    ):
        return None
    return {"id": row[0], "user_id": row[1], "is_bootstrap": bool(row[4])}


def _create_session(conn, access_token):
    session_token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions "
        "(id, user_id, token_hash, access_token_id, must_rotate_bootstrap, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            f"ses_{uuid.uuid4().hex}",
            access_token["user_id"],
            _hash_secret(session_token),
            access_token["id"],
            int(access_token["is_bootstrap"]),
            now,
            now + SESSION_LIFETIME_SECONDS,
        ),
    )
    return session_token


def _session_user():
    session_token = request.cookies.get("gcm_session")
    if not session_token:
        return None
    conn = _craft_db()
    try:
        row = conn.execute(
            "SELECT users.id, users.display_name, users.role, sessions.id, "
            "sessions.access_token_id, sessions.must_rotate_bootstrap FROM sessions "
            "JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL "
            "AND sessions.expires_at > ? AND users.disabled_at IS NULL",
            (_hash_secret(session_token), time.time()),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "display_name": row[1],
        "role": row[2],
        "session_id": row[3],
        "access_token_id": row[4],
        "must_rotate_bootstrap": bool(row[5]),
    }


def _is_admin(user):
    return user is not None and user["role"] == "admin"


def _is_operator(user):
    return user is not None and user["role"] in ("operator", "admin")


def _init_craft_db():
    conn = _craft_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
                created_at REAL NOT NULL,
                disabled_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_tokens (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                secret_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_used_at REAL,
                revoked_at REAL,
                is_bootstrap INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_access_tokens_user ON access_tokens (user_id)"
        )
        token_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(access_tokens)")
        }
        if "is_bootstrap" not in token_columns:
            conn.execute(
                "ALTER TABLE access_tokens ADD COLUMN is_bootstrap INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id),
                token_hash TEXT NOT NULL UNIQUE,
                access_token_id TEXT,
                must_rotate_bootstrap INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked_at REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)"
        )
        session_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sessions)")
        }
        if "access_token_id" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN access_token_id TEXT")
        if "must_rotate_bootstrap" not in session_columns:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN must_rotate_bootstrap INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS craft_request_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                label TEXT NOT NULL,
                mod TEXT,
                internal TEXT NOT NULL,
                damage INTEGER,
                amount REAL NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                cpu_name TEXT,
                created_at REAL NOT NULL,
                resolved_at REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_craft_request_history_user "
            "ON craft_request_history (user_id, created_at DESC)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS craft_cancel_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                cpu_name TEXT NOT NULL,
                status TEXT NOT NULL,
                success INTEGER,
                reason TEXT,
                created_at REAL NOT NULL,
                resolved_at REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_craft_cancel_history_user "
            "ON craft_cancel_history (user_id, created_at DESC)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS craft_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cpu_name TEXT NOT NULL,
                item_label TEXT,
                item_icon TEXT,
                status TEXT NOT NULL,
                progress_at_end INTEGER,
                occurred_at REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_craft_events_cpu ON craft_events (cpu_name)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_pins (
                user_id TEXT NOT NULL,
                cpu_name TEXT NOT NULL,
                pinned_at REAL NOT NULL,
                PRIMARY KEY (user_id, cpu_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_completions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                craft_event_id INTEGER NOT NULL REFERENCES craft_events(id),
                created_at REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_completions_user ON user_completions (user_id)"
        )
        # Leftover from the retired craft_keys.txt sync; request access is
        # now decided by account roles.
        conn.execute("DROP TABLE IF EXISTS craft_keys")
        # Item pins (Network tab "favorite this item" feature) - a
        # genuinely different concept from user_pins above (which tracks
        # CPUs being watched for craft completion), so kept as its own
        # table rather than overloading that one. item_key reuses the
        # exact same mod|internal|damage|kind format _item_key() already
        # builds for item history, rather than a composite primary key
        # over individually-nullable columns (mod and damage can both be
        # NULL for a fluid - standard SQL NULL semantics treat NULL as
        # never equal to itself even in a primary key, so a plain
        # composite key over nullable columns wouldn't reliably prevent
        # duplicate rows for the same fluid).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_item_pins (
                user_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                mod TEXT,
                internal TEXT NOT NULL,
                damage INTEGER,
                kind TEXT NOT NULL,
                pinned_at REAL NOT NULL,
                PRIMARY KEY (user_id, item_key)
            )
        """)
        conn.commit()
    finally:
        conn.close()



def _prune_sessions(conn):
    # Expired and revoked sessions can never authenticate again.
    conn.execute(
        "DELETE FROM sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
        (time.time(),),
    )


def _close_orphaned_request_history():
    # Craft and cancel requests live in memory, so any history row still
    # open at startup belongs to a request the previous process lost.
    now = time.time()
    conn = _craft_db()
    try:
        conn.execute(
            "UPDATE craft_request_history SET status = 'failed', "
            "reason = 'server restarted', resolved_at = ? WHERE resolved_at IS NULL",
            (now,),
        )
        conn.execute(
            "UPDATE craft_cancel_history SET status = 'resolved', success = 0, "
            "reason = 'server restarted', resolved_at = ? WHERE resolved_at IS NULL",
            (now,),
        )
        _prune_sessions(conn)
        conn.commit()
    finally:
        conn.close()



def _bootstrap_admin():
    token = os.environ.get("GCM_BOOTSTRAP_ADMIN_TOKEN", "").strip()
    if not token:
        return
    parsed = _parse_access_token(token)
    if not parsed:
        raise RuntimeError(
            "GCM_BOOTSTRAP_ADMIN_TOKEN must use the gcm_<token-id>_<secret> format"
        )

    conn = _craft_db()
    try:
        token_id, secret = parsed
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count:
            row = conn.execute(
                "SELECT user_id FROM access_tokens "
                "WHERE id = ? AND secret_hash = ? AND revoked_at IS NULL",
                (token_id, _hash_secret(secret)),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE access_tokens SET is_bootstrap = 1 WHERE id = ?",
                    (token_id,),
                )
                conn.execute(
                    "UPDATE sessions SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (time.time(), row[0]),
                )
            conn.commit()
            return
        user_id = f"usr_{uuid.uuid4().hex}"
        now = time.time()
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) VALUES (?, ?, 'admin', ?)",
            (user_id, os.environ.get("GCM_BOOTSTRAP_ADMIN_NAME", "Administrator"), now),
        )
        conn.execute(
            "INSERT INTO access_tokens "
            "(id, user_id, secret_hash, created_at, is_bootstrap) VALUES (?, ?, ?, ?, 1)",
            (token_id, user_id, _hash_secret(secret), now),
        )
        conn.commit()
    finally:
        conn.close()



def _require_initial_admin():
    conn = _craft_db()
    try:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    if user_count:
        return
    raise RuntimeError(
        "No users exist. Create .env from .env.example, set "
        "GCM_BOOTSTRAP_ADMIN_TOKEN, then restart the server."
    )


# In-memory only, deliberately not persisted. After a restart, a CPU
# whose pinned job finished while the server was down is first seen
# idle with no prior state; _process_craft_transitions drops its pins
# then (no completion is recorded, since what happened is unknown),
# so they don't fire on the next unrelated job.
_craft_tracking_lock = threading.Lock()
_cpu_last_busy = {}  # cpu_name -> bool
_cpu_last_known = (
    {}
)  # cpu_name -> {"label":..., "icon":..., "progress":...}, only while busy


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
    conn = _craft_db()
    try:
        with _craft_tracking_lock:
            for job in jobs:
                name = job.get("name")
                if not name:
                    continue
                busy = bool(job.get("busy"))
                was_busy = _cpu_last_busy.get(name)

                if was_busy is None and not busy:
                    conn.execute("DELETE FROM user_pins WHERE cpu_name = ?", (name,))

                if was_busy is True and busy is False:
                    last_known = _cpu_last_known.get(name, {})
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

                    _cpu_last_known.pop(name, None)

                if busy:
                    entry = _cpu_last_known.setdefault(
                        name, {"label": None, "icon": None, "progress": None}
                    )
                    if job.get("final_output"):
                        entry["label"] = job.get("final_output")
                        entry["icon"] = job.get("final_output_icon")
                    if job.get("progress_percent") is not None:
                        entry["progress"] = job.get("progress_percent")

                _cpu_last_busy[name] = busy

            conn.commit()
    finally:
        conn.close()


def _prune_old_completions(conn):
    cutoff = time.time() - COMPLETIONS_MAX_AGE_SECONDS
    conn.execute("DELETE FROM user_completions WHERE created_at < ?", (cutoff,))


def _require_user_id():
    user = _session_user()
    return user["id"] if user else None


@app.route("/api/auth/session", methods=["GET"])
def auth_session_get():
    user = _session_user()
    if not user:
        return jsonify({"authenticated": False})
    return jsonify(
        {
            "authenticated": True,
            "user": {key: user[key] for key in ("id", "display_name", "role")},
            "must_rotate_bootstrap": user["must_rotate_bootstrap"],
        }
    )


@app.route("/api/auth/login", methods=["POST"])
def auth_login_post():
    payload = request.get_json(silent=True) or {}
    token = payload.get("token")
    if not isinstance(token, str):
        return jsonify({"error": "invalid credentials"}), 401

    conn = _craft_db()
    try:
        access_token = _find_access_token(conn, token.strip())
        if not access_token:
            return jsonify({"error": "invalid credentials"}), 401
        user = conn.execute(
            "SELECT id, display_name, role, disabled_at FROM users WHERE id = ?",
            (access_token["user_id"],),
        ).fetchone()
        if not user or user[3] is not None:
            return jsonify({"error": "invalid credentials"}), 401
        conn.execute(
            "UPDATE access_tokens SET last_used_at = ? WHERE id = ?",
            (time.time(), access_token["id"]),
        )
        _prune_sessions(conn)
        session_token = _create_session(conn, access_token)
        conn.commit()
    finally:
        conn.close()

    response = jsonify(
        {
            "authenticated": True,
            "user": {"id": user[0], "display_name": user[1], "role": user[2]},
            "must_rotate_bootstrap": access_token["is_bootstrap"],
        }
    )
    response.set_cookie(
        "gcm_session",
        session_token,
        max_age=SESSION_LIFETIME_SECONDS,
        secure=SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout_post():
    session_token = request.cookies.get("gcm_session")
    if session_token:
        conn = _craft_db()
        try:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (time.time(), _hash_secret(session_token)),
            )
            conn.commit()
        finally:
            conn.close()
    response = jsonify({"ok": True})
    response.delete_cookie("gcm_session", path="/")
    return response


@app.route("/api/auth/rotate-bootstrap", methods=["POST"])
def auth_rotate_bootstrap_post():
    user = _session_user()
    if not user or not user["must_rotate_bootstrap"] or not _is_admin(user):
        return jsonify({"error": "bootstrap rotation is not available"}), 403

    conn = _craft_db()
    try:
        replacement_token = _create_access_token(conn, user["id"])
        replacement_token_id, _ = _parse_access_token(replacement_token)
        now = time.time()
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? WHERE id = ? AND is_bootstrap = 1",
            (now, user["access_token_id"]),
        )
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE access_token_id = ? AND id != ?",
            (now, user["access_token_id"], user["session_id"]),
        )
        conn.execute(
            "UPDATE sessions SET access_token_id = ?, must_rotate_bootstrap = 0 WHERE id = ?",
            (replacement_token_id, user["session_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"token": replacement_token})


_BOOTSTRAP_BLOCKED_ENDPOINTS = {
    "craft_request_post",
    "craft_requests_get",
    "craft_request_dismiss",
    "craft_cancel_post",
    "craft_cancel_get",
    "pins_get",
    "pins_post",
    "pins_unpin",
    "completions_get",
    "completions_ack",
    "completions_ack_all",
    "network_item_pins_get",
    "network_item_pins_post",
    "network_item_pins_unpin",
    "admin_user_post",
    "admin_token_revoke_post",
    "admin_users_get",
    "admin_user_history_get",
    "admin_user_delete",
    "admin_user_token_regenerate",
}

_SESSION_WRITE_ENDPOINTS = {
    "auth_logout_post",
    "auth_rotate_bootstrap_post",
    "craft_request_post",
    "craft_request_dismiss",
    "craft_cancel_post",
    "pins_post",
    "pins_unpin",
    "completions_ack",
    "completions_ack_all",
    "network_item_pins_post",
    "network_item_pins_unpin",
    "admin_user_post",
    "admin_user_delete",
    "admin_user_token_regenerate",
    "admin_token_revoke_post",
}


@app.before_request
def block_unrotated_bootstrap_sessions():
    if request.endpoint not in _BOOTSTRAP_BLOCKED_ENDPOINTS:
        return None
    user = _session_user()
    if user and user["must_rotate_bootstrap"]:
        return jsonify({"error": "rotate the bootstrap token before continuing"}), 403
    return None


@app.before_request
def reject_cross_origin_session_writes():
    if request.endpoint not in _SESSION_WRITE_ENDPOINTS:
        return None
    if not _session_user():
        return None
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        return jsonify({"error": "cross-origin request rejected"}), 403
    return None


@app.after_request
def add_browser_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if request.endpoint in {
        "auth_session_get",
        "auth_login_post",
        "auth_logout_post",
        "auth_rotate_bootstrap_post",
        "admin_user_post",
        "admin_token_revoke_post",
        "admin_user_history_get",
        "admin_user_delete",
        "admin_user_token_regenerate",
    }:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/admin/users", methods=["POST"])
def admin_user_post():
    admin = _session_user()
    if not _is_admin(admin):
        return jsonify({"error": "administrator access required"}), 403
    payload = request.get_json(silent=True) or {}
    display_name = (payload.get("display_name") or "").strip()
    role = payload.get("role")
    if not display_name or role not in ("viewer", "operator", "admin"):
        return (
            jsonify(
                {"error": "display_name and a viewer, operator, or admin role are required"}
            ),
            400,
        )

    conn = _craft_db()
    try:
        user_id = f"usr_{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) VALUES (?, ?, ?, ?)",
            (user_id, display_name, role, time.time()),
        )
        token = _create_access_token(conn, user_id)
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "display name already exists"}), 409
    finally:
        conn.close()
    return (
        jsonify(
            {
                "user": {"id": user_id, "display_name": display_name, "role": role},
                "token": token,
            }
        ),
        201,
    )


@app.route("/api/admin/users", methods=["GET"])
def admin_users_get():
    admin = _session_user()
    if not _is_admin(admin):
        return jsonify({"error": "administrator access required"}), 403

    conn = _craft_db()
    try:
        rows = conn.execute(
            "SELECT users.id, users.display_name, users.role, users.created_at, "
            "users.disabled_at, access_tokens.id, access_tokens.created_at, "
            "access_tokens.last_used_at "
            "FROM users LEFT JOIN access_tokens "
            "ON access_tokens.user_id = users.id AND access_tokens.revoked_at IS NULL "
            "ORDER BY users.display_name COLLATE NOCASE, access_tokens.created_at"
        ).fetchall()
    finally:
        conn.close()
    users = {}
    for row in rows:
        user = users.setdefault(
            row[0],
            {
                "id": row[0],
                "display_name": row[1],
                "role": row[2],
                "created_at": row[3],
                "disabled_at": row[4],
                "tokens": [],
            },
        )
        if row[5]:
            user["tokens"].append(
                {
                    "id": row[5],
                    "created_at": row[6],
                    "last_used_at": row[7],
                }
            )
    return jsonify({"users": list(users.values())})


@app.route("/api/admin/users/<user_id>", methods=["DELETE"])
def admin_user_delete(user_id):
    admin = _session_user()
    if not _is_admin(admin):
        return jsonify({"error": "administrator access required"}), 403
    if user_id == admin["id"]:
        return jsonify({"error": "cannot delete your own account"}), 400

    conn = _craft_db()
    try:
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        if not cursor.rowcount:
            return jsonify({"error": "unknown user"}), 404
        # access_tokens/sessions aren't ON DELETE CASCADE (SQLite foreign
        # keys are declared but not enforced unless PRAGMA foreign_keys is
        # on for this connection) - removed explicitly so a deleted user's
        # credentials can never authenticate again.
        conn.execute("DELETE FROM access_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/users/<user_id>/tokens", methods=["POST"])
def admin_user_token_regenerate(user_id):
    admin = _session_user()
    if not _is_admin(admin):
        return jsonify({"error": "administrator access required"}), 403

    conn = _craft_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "unknown user"}), 404
        now = time.time()
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        conn.execute(
            "UPDATE sessions SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (now, user_id),
        )
        token = _create_access_token(conn, user_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"token": token})


@app.route("/api/admin/users/<user_id>/history", methods=["GET"])
def admin_user_history_get(user_id):
    admin = _session_user()
    if not _is_admin(admin):
        return jsonify({"error": "administrator access required"}), 403

    conn = _craft_db()
    try:
        user = conn.execute(
            "SELECT id, display_name FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user:
            return jsonify({"error": "unknown user"}), 404
        rows = conn.execute(
            "SELECT 'request', label, status, reason, cpu_name, created_at, resolved_at "
            "FROM craft_request_history WHERE user_id = ? "
            "UNION ALL "
            "SELECT 'cancel', cpu_name, status, reason, cpu_name, created_at, resolved_at "
            "FROM craft_cancel_history WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT 100",
            (user_id, user_id),
        ).fetchall()
    finally:
        conn.close()
    return jsonify(
        {
            "user": {"id": user[0], "display_name": user[1]},
            "events": [
                {
                    "type": row[0],
                    "target": row[1],
                    "status": row[2],
                    "reason": row[3],
                    "cpu_name": row[4],
                    "created_at": row[5],
                    "resolved_at": row[6],
                }
                for row in rows
            ],
        }
    )


@app.route("/api/admin/tokens/<token_id>/revoke", methods=["POST"])
def admin_token_revoke_post(token_id):
    admin = _session_user()
    if not _is_admin(admin):
        return jsonify({"error": "administrator access required"}), 403

    conn = _craft_db()
    try:
        now = time.time()
        cursor = conn.execute(
            "UPDATE access_tokens SET revoked_at = ? "
            "WHERE id = ? AND revoked_at IS NULL",
            (now, token_id),
        )
        if not cursor.rowcount:
            return jsonify({"error": "active token not found"}), 404
        conn.execute(
            "UPDATE sessions SET revoked_at = ? "
            "WHERE access_token_id = ? AND revoked_at IS NULL",
            (now, token_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


# Small ad-hoc debugging channel: the Lua side can POST a raw dump here
# instead of printing to the OC terminal (which has essentially no
# scrollback and clips anything longer than a screenful). Keeps the last
# few dumps only - this isn't meant to be a permanent log.
_debug_dumps = []
_DEBUG_DUMPS_KEEP = 10


@app.route("/api/debug", methods=["POST"])
def debug_post():
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    with _lock:
        _debug_dumps.append(
            {
                "tag": payload.get("tag", "?"),
                "dump": payload.get("dump", ""),
                "received_at": time.time(),
            }
        )
        del _debug_dumps[:-_DEBUG_DUMPS_KEEP]
    return jsonify({"ok": True})


@app.route("/api/debug", methods=["GET"])
def debug_get():
    if not _is_operator(_session_user()):
        return jsonify({"error": "operator access required"}), 403
    with _lock:
        if not _debug_dumps:
            return Response("(no debug dumps received yet)", mimetype="text/plain")
        parts = []
        for d in _debug_dumps:
            when = time.strftime("%H:%M:%S", time.localtime(d["received_at"]))
            parts.append(f"===== [{when}] {d['tag']} =====\n{d['dump']}")
        return Response("\n\n".join(parts), mimetype="text/plain")


@app.route("/api/crafts", methods=["POST"])
def crafts_post():
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400

    jobs = _attach_job_icons(payload.get("jobs", []))

    with _lock:
        _state["jobs"] = jobs
        _state["source"] = payload.get("source")
        _state["received_at"] = time.time()

    # Outside _lock (a separate DB, no shared state with _state beyond
    # the jobs list we already have a local reference to).
    _process_craft_transitions(jobs)

    return jsonify({"ok": True})


@app.route("/api/crafts", methods=["GET"])
def crafts_get():
    with _lock:
        received_at = _state["received_at"]
        age = (time.time() - received_at) if received_at else None
        return jsonify(
            {
                "jobs": _state["jobs"],
                "source": _state["source"],
                "age_seconds": age,
                "stale": age is None or age > STALE_AFTER_SECONDS,
            }
        )


@app.route("/api/pins", methods=["GET"])
def pins_get():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    conn = _craft_db()
    try:
        rows = conn.execute(
            "SELECT cpu_name FROM user_pins WHERE user_id = ?", (user_id,)
        ).fetchall()
    finally:
        conn.close()
    return jsonify({"pins": [r[0] for r in rows]})


@app.route("/api/pins", methods=["POST"])
def pins_post():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    cpu_name = payload.get("cpu_name")
    if not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400

    # Pinning only ever means "watch the job currently running on this
    # CPU" - an idle CPU has no job to watch, so this is rejected here
    # (not just discouraged client-side, since anyone could otherwise hit
    # this endpoint directly regardless of what the UI allows).
    with _lock:
        jobs = _state.get("jobs", [])
    job = next((j for j in jobs if j.get("name") == cpu_name), None)
    if job is None:
        return jsonify({"error": "unknown cpu_name"}), 404
    if not job.get("busy"):
        return jsonify({"error": "CPU is not currently busy - nothing to pin"}), 400

    conn = _craft_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_pins (user_id, cpu_name, pinned_at) VALUES (?, ?, ?)",
            (user_id, cpu_name, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/pins/unpin", methods=["POST"])
def pins_unpin():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    payload = request.get_json(silent=True) or {}
    cpu_name = payload.get("cpu_name")
    if not cpu_name:
        return jsonify({"error": "missing cpu_name"}), 400

    conn = _craft_db()
    try:
        conn.execute(
            "DELETE FROM user_pins WHERE user_id = ? AND cpu_name = ?",
            (user_id, cpu_name),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/completions", methods=["GET"])
def completions_get():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401

    conn = _craft_db()
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


@app.route("/api/completions/<int:completion_id>/ack", methods=["POST"])
def completions_ack(completion_id):
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    conn = _craft_db()
    try:
        conn.execute(
            "DELETE FROM user_completions WHERE id = ? AND user_id = ?",
            (completion_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/completions/ack-all", methods=["POST"])
def completions_ack_all():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    conn = _craft_db()
    try:
        conn.execute("DELETE FROM user_completions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/icons")
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
    zf = _get_images_zip()
    if zf is None:
        abort(404)
    with _zip_lock:
        try:
            data = zf.read(img_path)
        except KeyError:
            abort(404)
    return Response(
        data,
        mimetype="image/png",
        headers={
            # Icons for a given item never change, safe to cache hard.
            "Cache-Control": "public, max-age=604800, immutable",
        },
    )


# ---------------------------------------------------------------------
# Power monitor (SQLite-backed time series of GT machine energy level,
# fed by oc/power_monitor.lua)
# ---------------------------------------------------------------------


def _power_db():
    # A fresh connection per call rather than one shared connection -
    # sqlite3 connections aren't safe to share across Flask's threads,
    # and at this traffic level (one insert a minute, occasional reads)
    # the cost of opening a new one each time is irrelevant.
    conn = sqlite3.connect(POWER_DB_PATH, timeout=10)
    return conn


def _init_power_db():
    conn = _power_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS power_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                stored REAL NOT NULL,
                capacity REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_power_readings_ts ON power_readings (ts)"
        )
        # Migration for existing installs - these columns didn't exist
        # before this feature (avg EU in/out over a few windows, plus a
        # time-to-empty estimate - all confirmed available for free from
        # the SAME getSensorInformation() call power_monitor.lua already
        # makes, from a real captured dump off a Lapotronic Super
        # Capacitor). SQLite has no "ADD COLUMN IF NOT EXISTS", so check
        # the existing column list first rather than trying the ALTER
        # and swallowing a "duplicate column" error - more explicit
        # about what's actually happening, and safe to run on every
        # startup either way (only adds what's genuinely missing).
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(power_readings)")
        }
        new_cols = [
            ("avg_eu_in_5s", "REAL"),
            ("avg_eu_out_5s", "REAL"),
            ("avg_eu_in_5m", "REAL"),
            ("avg_eu_out_5m", "REAL"),
            ("avg_eu_in_1h", "REAL"),
            ("avg_eu_out_1h", "REAL"),
            ("time_to_empty_minutes", "REAL"),
        ]
        for col_name, col_type in new_cols:
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE power_readings ADD COLUMN {col_name} {col_type}"
                )
        conn.commit()
    finally:
        conn.close()


POWER_RANGE_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "lifetime": None,
}
POWER_MAX_POINTS = 800  # cap returned points regardless of range, so the
# chart stays fast and the payload stays small
# even after months of 1-minute-ish polling


def _downsample(rows, max_points=POWER_MAX_POINTS):
    """rows: list of (ts, stored, capacity), ascending by ts. Bucket-averages
    down to at most max_points rows, preserving overall shape."""
    n = len(rows)
    if n <= max_points:
        return rows
    bucket_size = n / max_points
    out = []
    i = 0.0
    while int(i) < n:
        start = int(i)
        end = min(n, max(start + 1, int(i + bucket_size)))
        chunk = rows[start:end]
        avg_stored = sum(r[1] for r in chunk) / len(chunk)
        avg_capacity = sum(r[2] for r in chunk) / len(chunk)
        mid_ts = chunk[len(chunk) // 2][0]
        out.append((mid_ts, avg_stored, avg_capacity))
        i += bucket_size
    return out


@app.route("/api/power", methods=["POST"])
def power_post():
    if not _require_api_key():
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid payload"}), 400

    stored = payload.get("stored")
    capacity = payload.get("capacity")
    ts = payload.get("timestamp")
    if stored is None or capacity is None:
        return jsonify({"error": "missing stored/capacity"}), 400
    if ts is None:
        ts = time.time()

    def _f(key):
        # All optional - older power_monitor.lua deployments (or a
        # component that doesn't report these at all) simply won't send
        # them, and that's fine, not an error.
        v = payload.get(key)
        return float(v) if v is not None else None

    conn = _power_db()
    try:
        conn.execute(
            "INSERT INTO power_readings "
            "(ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s, avg_eu_in_5m, avg_eu_out_5m, "
            "avg_eu_in_1h, avg_eu_out_1h, time_to_empty_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                float(ts),
                float(stored),
                float(capacity),
                _f("avg_eu_in_5s"),
                _f("avg_eu_out_5s"),
                _f("avg_eu_in_5m"),
                _f("avg_eu_out_5m"),
                _f("avg_eu_in_1h"),
                _f("avg_eu_out_1h"),
                _f("time_to_empty_minutes"),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True})


def _fetch_power_rows(range_key):
    if range_key not in POWER_RANGE_SECONDS:
        range_key = "day"
    seconds = POWER_RANGE_SECONDS[range_key]

    conn = _power_db()
    try:
        if seconds is None:
            cur = conn.execute(
                "SELECT ts, stored, capacity FROM power_readings ORDER BY ts ASC"
            )
        else:
            since = time.time() - seconds
            cur = conn.execute(
                "SELECT ts, stored, capacity FROM power_readings WHERE ts >= ? ORDER BY ts ASC",
                (since,),
            )
        rows = cur.fetchall()
    finally:
        conn.close()

    return range_key, _downsample(rows)


def _fetch_latest_power_reading():
    """The single most recent RAW reading, in full - deliberately a
    separate, unfiltered query rather than reusing whatever
    _fetch_power_rows(range_key) happened to return.

    CONFIRMED as a real, reported bug otherwise: the live "currently
    stored" readout used to come from rows[-1] of the RANGE-FILTERED,
    DOWNSAMPLED points array - the last bucket's AVERAGED value, not a
    true single reading. Different ranges downsample into different-
    sized buckets, so that last bucket's average genuinely differs
    between e.g. "day" and "week" - which is exactly why switching the
    chart range visibly changed the live percentage readout. Bucket
    timestamps are midpoints too, not real reading times, which is the
    same root cause behind "updated Xs ago" also drifting when
    switching ranges, just less consistently noticeable.

    Merged into one query covering stored/capacity AND the trend fields
    together (not two separate latest-row queries) - both correctness
    (no risk of the two disagreeing if a new reading lands between two
    separate queries) and efficiency."""
    conn = _power_db()
    try:
        row = conn.execute(
            "SELECT ts, stored, capacity, avg_eu_in_5s, avg_eu_out_5s FROM power_readings ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return row


@app.route("/api/power", methods=["GET"])
def power_get():
    range_key, rows = _fetch_power_rows(request.args.get("range", "day"))
    latest = _fetch_latest_power_reading()

    return jsonify(
        {
            "range": range_key,
            "points": [{"t": r[0], "stored": r[1], "capacity": r[2]} for r in rows],
            "latest": (
                {
                    "t": latest[0],
                    "stored": latest[1],
                    "capacity": latest[2],
                    "avg_eu_in_5s": latest[3],
                    "avg_eu_out_5s": latest[4],
                }
                if latest
                else None
            ),
        }
    )


# ---------------------------------------------------------------------
# Item/fluid quantity history (SQLite-backed, own db file - separate
# from power.db and craft_history.db, matching the established "one db
# per major concern" pattern rather than growing an existing file with
# an unrelated table).
#
# Change-only storage, NOT one row per scan: with ~6,000+ items scanned
# every 20 minutes, storing every scan unconditionally would mean ~470k
# rows/day - several GB/year and a table that keeps getting slower to
# query. Item quantity is fundamentally a step function (constant, then
# jumps), not a continuously-sampled signal like power draw, so a row is
# only written when a value actually CHANGES from what was last
# recorded for that item - most items (stockpiled materials, anything
# not currently being produced/consumed) simply don't change between
# most scans, which is what keeps this genuinely small in practice.
def _item_key(mod, internal, damage, kind):
    # Canonical, stable identifier - built explicitly rather than
    # relying on dict/JSON key ordering, since this is used both as the
    # SQLite storage key and as the browser's shareable URL parameter.
    return f"{mod or ''}|{internal or ''}|{damage if damage is not None else ''}|{kind or 'item'}"


def _item_history_db():
    conn = sqlite3.connect(ITEM_HISTORY_DB_PATH, timeout=10)
    return conn


def _init_item_history_db():
    conn = _item_history_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS item_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key TEXT NOT NULL,
                label TEXT,
                size REAL NOT NULL,
                recorded_at REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_item_history_key_time ON item_history (item_key, recorded_at)"
        )
        # A true MIRROR of the live network snapshot (unlike item_history
        # above, which is change-only) - wiped and fully rewritten on
        # every scan/finish, so it always reflects exactly what the last
        # completed scan saw. Purpose: surviving a SERVER restart without
        # the Network tab sitting empty until network_browser.lua's next
        # scan completes (which could be most of 20 minutes away). Loaded
        # back into memory once at server startup - see
        # _load_network_snapshot().
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_snapshot (
                item_key TEXT PRIMARY KEY,
                mod TEXT,
                internal TEXT NOT NULL,
                damage INTEGER,
                kind TEXT NOT NULL,
                name TEXT,
                size REAL NOT NULL,
                is_craftable INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _persist_network_snapshot(items):
    """Wholesale replace, not change-only - this is a MIRROR of the live
    snapshot, not a history log. Runs after every real scan; the DELETE+
    INSERT happens in one transaction so a reader never sees a half-
    written table.

    INSERT OR REPLACE, not a plain INSERT - confirmed from a real
    production crash: a scan's item list can apparently contain two or
    more entries resolving to the same item_key (exact cause not
    pinned down - a plain INSERT just surfaces it as an uncaught
    IntegrityError instead of handling it). Duplicates aren't corruption
    worth treating as fatal either way, so REPLACE just lets the later
    occurrence in the list win, matching ordinary "last write wins"
    semantics rather than crashing the entire scan/finish request over
    what's genuinely a best-effort persistence step, not the actual
    scan result the website itself depends on."""
    now = time.time()
    rows = []
    for it in items:
        key = _item_key(
            it.get("mod"), it.get("internal"), it.get("damage"), it.get("kind")
        )
        rows.append(
            (
                key,
                it.get("mod"),
                it.get("internal"),
                it.get("damage"),
                it.get("kind") or "item",
                it.get("name"),
                it.get("size", 0) or 0,
                1 if it.get("isCraftable") else 0,
                now,
            )
        )
    conn = _item_history_db()
    try:
        conn.execute("DELETE FROM network_snapshot")
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO network_snapshot "
                "(item_key, mod, internal, damage, kind, name, size, is_craftable, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def _load_network_snapshot():
    """Called once at server startup - seeds _network_state from whatever
    was last persisted, so the Network tab has SOMETHING to show
    immediately rather than sitting empty until the next real scan
    completes. Marks is_reconstructed=True; network_scan_finish() clears
    it the moment a real scan actually completes."""
    conn = _item_history_db()
    try:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind, name, size, is_craftable, updated_at FROM network_snapshot"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return

    items = []
    max_updated = 0.0
    for r in rows:
        items.append(
            {
                "mod": r[0],
                "internal": r[1],
                "damage": r[2],
                "kind": r[3],
                "name": r[4],
                "size": r[5],
                "isCraftable": bool(r[6]),
            }
        )
        max_updated = max(max_updated, r[7])

    # network_snapshot deliberately doesn't store an icon path - it's a
    # pure function of mod/internal/damage/label, always re-derivable,
    # so storing it separately would just be a cache that could drift if
    # icons_lookup.json itself ever changes between restarts. But that
    # means it has to be resolved HERE explicitly - live-scanned items
    # only ever get their icon attached inside network_scan_batch(), a
    # path reconstructed items never go through, so without this call
    # every icon on a freshly-restarted server's grid would silently be
    # missing (confirmed as a real, reported bug, not a hypothetical).
    _attach_item_icons(items)

    with _network_lock:
        _network_state["items"] = items
        _network_state["item_count"] = len(items)
        _network_state["updated_at"] = max_updated
        _network_state["is_reconstructed"] = True



def _record_item_history_changes(old_items, new_items):
    """old_items/new_items: lists of item dicts (mod, internal, damage,
    kind, name, size). Writes one row per item whose size differs from
    the last-known value, including items present before but absent now
    (size implicitly 0 - they only vanish from a scan by genuinely
    having zero stock and no craftable pattern, since getItemsInNetworkById/
    getFluidsInNetwork only ever return entries AE2 itself considers
    present)."""
    old_by_key = {}
    for it in old_items or []:
        key = _item_key(
            it.get("mod"), it.get("internal"), it.get("damage"), it.get("kind")
        )
        old_by_key[key] = it.get("size", 0)

    now = time.time()
    changes = []
    seen_keys = set()
    for it in new_items or []:
        key = _item_key(
            it.get("mod"), it.get("internal"), it.get("damage"), it.get("kind")
        )
        seen_keys.add(key)
        new_size = it.get("size", 0)
        if old_by_key.get(key) != new_size:
            changes.append((key, it.get("name"), new_size, now))

    for key, old_size in old_by_key.items():
        if key not in seen_keys and old_size != 0:
            changes.append((key, None, 0, now))

    if not changes:
        return

    conn = _item_history_db()
    try:
        conn.executemany(
            "INSERT INTO item_history (item_key, label, size, recorded_at) VALUES (?, ?, ?, ?)",
            changes,
        )
        conn.commit()
    finally:
        conn.close()


ITEM_HISTORY_RANGE_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 30 * 86400,
    "lifetime": None,
}
ITEM_HISTORY_MAX_POINTS = 800


def _downsample_steps(rows, max_points=ITEM_HISTORY_MAX_POINTS):
    """rows: list of (recorded_at, size), ascending. Unlike power's
    bucket-AVERAGING, this picks the LAST real recorded value in each
    bucket rather than an average - item quantity is a step function,
    so averaging would invent sizes that were never actually true at
    any real moment. Preserves genuine observed values instead."""
    n = len(rows)
    if n <= max_points:
        return rows
    bucket_size = n / max_points
    out = []
    i = 0.0
    while int(i) < n:
        start = int(i)
        end = min(n, max(start + 1, int(i + bucket_size)))
        out.append(rows[end - 1])
        i += bucket_size
    return out


# ---------------------------------------------------------------------
# Static chart images (matplotlib) for OpenGraph embeds - Discord (and
# any other link-unfurling crawler) does a plain HTTP GET and reads
# <meta property="og:..."> tags straight out of the raw HTML; it never
# runs JS, so the in-app Chart.js rendering is invisible to it entirely.
# This renders an actual PNG server-side, independent of the browser.
_CHART_BG = "#0f1115"
_CHART_PANEL = "#1a1d24"
_CHART_LINE = "#5fb3ff"
_CHART_MUTED = "#8a8f98"
_CHART_WIDTH_PX = 800
_CHART_HEIGHT_PX = 400  # 2:1 - landscape, which matters: Discord reads
# og:image:width/height as one of its signals
# for picking the large-image embed layout
# over a small thumbnail, alongside twitter:card
# below - a portrait/near-square image would
# work against that signal instead of for it.
CHART_CACHE_TTL_SECONDS = int(os.environ.get("CHART_CACHE_TTL_SECONDS", "60"))
CHART_CACHE_MAX_ENTRIES = int(os.environ.get("CHART_CACHE_MAX_ENTRIES", "64"))
CHART_RATE_LIMIT_PER_MINUTE = int(os.environ.get("CHART_RATE_LIMIT_PER_MINUTE", "30"))
CHART_MAX_TRACKED_CLIENTS = int(os.environ.get("CHART_MAX_TRACKED_CLIENTS", "4096"))
_chart_cache = OrderedDict()
_chart_cache_lock = threading.Lock()
_chart_request_times = OrderedDict()
_chart_rate_lock = threading.Lock()


def _chart_client_id():
    return request.remote_addr or "unknown"


def _chart_rate_limit_response():
    now = time.time()
    client_id = _chart_client_id()
    with _chart_rate_lock:
        timestamps = _chart_request_times.get(client_id)
        if timestamps is None:
            if len(_chart_request_times) >= CHART_MAX_TRACKED_CLIENTS:
                _chart_request_times.popitem(last=False)
            timestamps = deque()
            _chart_request_times[client_id] = timestamps
        _chart_request_times.move_to_end(client_id)
        while timestamps and timestamps[0] <= now - 60:
            timestamps.popleft()
        if len(timestamps) >= CHART_RATE_LIMIT_PER_MINUTE:
            retry_after = max(1, int(timestamps[0] + 60 - now) + 1)
            response = jsonify({"error": "chart rate limit exceeded"})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        timestamps.append(now)
    return None


def _cached_chart_png(cache_key, render):
    now = time.time()
    with _chart_cache_lock:
        cached = _chart_cache.get(cache_key)
        if cached and cached[0] > now:
            _chart_cache.move_to_end(cache_key)
            return cached[1]
        if cached:
            del _chart_cache[cache_key]

    png_bytes = render()
    with _chart_cache_lock:
        _chart_cache[cache_key] = (now + CHART_CACHE_TTL_SECONDS, png_bytes)
        _chart_cache.move_to_end(cache_key)
        while len(_chart_cache) > CHART_CACHE_MAX_ENTRIES:
            _chart_cache.popitem(last=False)
    return png_bytes


def _format_qty_py(n):
    # Same abbreviation scheme as the frontend's formatQty()/formatEU| -
    # doesn't need to be pixel-identical to the in-app JS version, just
    # informative for a preview image glanced at in a chat client.
    if n is None:
        return "0"
    n = float(n)
    a = abs(n)
    if a >= 1e12:
        return f"{n/1e12:.2f}T"
    if a >= 1e9:
        return f"{n/1e9:.2f}B"
    if a >= 1e6:
        return f"{n/1e6:.2f}M"
    if a >= 1e3:
        return f"{n/1e3:.1f}k"
    return f"{n:.0f}"


def _render_chart_png(
    rows, stepped, width_px=_CHART_WIDTH_PX, height_px=_CHART_HEIGHT_PX
):
    """rows: list of (unix_ts, value). stepped=True draws a step line
    (item quantity - a step function, matching the 'stepped: after'
    Chart.js config already used in the browser); stepped=False draws a
    smooth line (power draw, a continuously-sampled signal)."""
    dpi = 100
    fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    fig.patch.set_facecolor(_CHART_BG)
    ax.set_facecolor(_CHART_PANEL)

    if rows:
        times = [datetime.fromtimestamp(r[0]) for r in rows]
        values = [r[1] for r in rows]
        if stepped:
            ax.step(times, values, where="post", color=_CHART_LINE, linewidth=2)
            ax.fill_between(times, values, step="post", color=_CHART_LINE, alpha=0.15)
        else:
            ax.plot(times, values, color=_CHART_LINE, linewidth=2)
            ax.fill_between(times, values, color=_CHART_LINE, alpha=0.15)
        ax.set_ylim(bottom=0)
    else:
        ax.text(
            0.5,
            0.5,
            "No data yet",
            ha="center",
            va="center",
            color=_CHART_MUTED,
            fontsize=12,
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, pos: _format_qty_py(v)))
    # Explicit date locator/formatter - without this, matplotlib falls
    # back to raw numeric-ish date labels (confirmed by actually looking
    # at a rendered chart, not assumed) instead of readable times.
    # AutoDateLocator + ConciseDateFormatter picks a sensible format
    # automatically based on the actual span of data (HH:MM for a day,
    # month/day for longer ranges) rather than needing this function to
    # hand-roll that same range-based logic itself.
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.tick_params(colors=_CHART_MUTED, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(_CHART_MUTED)
        spine.set_alpha(0.3)
    ax.grid(True, color=_CHART_MUTED, alpha=0.15)
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


@app.route("/api/power/chart.png", methods=["GET"])
def power_chart_png():
    rate_limited = _chart_rate_limit_response()
    if rate_limited:
        return rate_limited
    range_key, rows = _fetch_power_rows(request.args.get("range", "day"))
    png_bytes = _cached_chart_png(
        ("power", range_key),
        lambda: _render_chart_png(
            [(r[0], r[1]) for r in rows], stepped=False
        ).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@app.route("/api/network/history/chart.png", methods=["GET"])
def network_history_chart_png():
    rate_limited = _chart_rate_limit_response()
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
    if not internal:
        abort(400)

    range_key, rows = _fetch_item_history_rows(
        mod, internal, damage, kind, request.args.get("range", "day")
    )
    png_bytes = _cached_chart_png(
        ("network", mod, internal, damage, kind, range_key),
        lambda: _render_chart_png(rows, stepped=True).getvalue(),
    )
    resp = Response(png_bytes, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


def _fetch_item_history_rows(mod, internal, damage, kind, range_key):
    key = _item_key(mod, internal, damage, kind)
    if range_key not in ITEM_HISTORY_RANGE_SECONDS:
        range_key = "day"
    seconds = ITEM_HISTORY_RANGE_SECONDS[range_key]

    conn = _item_history_db()
    try:
        if seconds is None:
            cur = conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_key = ? ORDER BY recorded_at ASC",
                (key,),
            )
            rows = cur.fetchall()
        else:
            since = time.time() - seconds
            cur = conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_key = ? AND recorded_at >= ? ORDER BY recorded_at ASC",
                (key, since),
            )
            rows = cur.fetchall()

            # A step chart starting mid-air (no point until the first
            # change INSIDE the range) looks wrong/misleading - prepend
            # the last known value from BEFORE the range started, if any,
            # so the line correctly holds its prior value from the very
            # left edge of the chart.
            lead = conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_key = ? AND recorded_at < ? "
                "ORDER BY recorded_at DESC LIMIT 1",
                (key, since),
            ).fetchone()
            if lead:
                rows = [(since, lead[1])] + rows
    finally:
        conn.close()

    return range_key, _downsample_steps(rows)


@app.route("/api/network/history", methods=["GET"])
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

    if not internal:
        return jsonify({"error": "missing internal"}), 400

    range_key, rows = _fetch_item_history_rows(
        mod, internal, damage, kind, request.args.get("range", "day")
    )
    latest = rows[-1] if rows else None

    return jsonify(
        {
            "range": range_key,
            "points": [{"t": r[0], "size": r[1]} for r in rows],
            "latest": {"t": latest[0], "size": latest[1]} if latest else None,
        }
    )


# ---------------------------------------------------------------------
# Item pins (Network tab "favorite this item" feature) - purely a
# personal browse preference, no in-game consequence, so unlike craft
# requests/cancellation this doesn't require the operator role - any
# signed-in user can pin items.
@app.route("/api/network/pins", methods=["GET"])
def network_item_pins_get():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    conn = _craft_db()
    try:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind FROM user_item_pins WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return jsonify(
        {
            "pins": [
                {"mod": r[0], "internal": r[1], "damage": r[2], "kind": r[3]}
                for r in rows
            ]
        }
    )


@app.route("/api/network/pins", methods=["POST"])
def network_item_pins_post():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    key = _item_key(mod, internal, damage, kind)
    conn = _craft_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_item_pins (user_id, item_key, mod, internal, damage, kind, pinned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, key, mod, internal, damage, kind, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@app.route("/api/network/pins/unpin", methods=["POST"])
def network_item_pins_unpin():
    user_id = _require_user_id()
    if not user_id:
        return jsonify({"error": "authentication required"}), 401
    payload = request.get_json(silent=True) or {}
    mod = payload.get("mod")
    internal = payload.get("internal")
    damage = payload.get("damage")
    kind = payload.get("kind") or "item"
    if not internal:
        return jsonify({"error": "missing internal"}), 400

    key = _item_key(mod, internal, damage, kind)
    conn = _craft_db()
    try:
        conn.execute(
            "DELETE FROM user_item_pins WHERE user_id = ? AND item_key = ?",
            (user_id, key),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


def _lookup_item_display_info(mod, internal, damage, kind):
    """Returns (label, size) for OG-tag purposes. Tries the live network
    snapshot first (freshest); falls back to item_history's most recent
    row if the item isn't currently in the snapshot (e.g. it dropped to
    genuinely zero stock and isn't craftable, so it's absent from the
    last scan entirely) - same fallback chain the frontend's history
    popup already relies on, just done server-side here."""
    with _network_lock:
        for it in _network_state["items"]:
            if (
                (it.get("mod") or None) == (mod or None)
                and it.get("internal") == internal
                and (it.get("damage") if it.get("damage") is not None else None)
                == (damage if damage is not None else None)
                and (it.get("kind") or "item") == (kind or "item")
            ):
                return it.get("name"), it.get("size")

    key = _item_key(mod, internal, damage, kind)
    conn = _item_history_db()
    try:
        row = conn.execute(
            "SELECT label, size FROM item_history WHERE item_key = ? AND label IS NOT NULL "
            "ORDER BY recorded_at DESC LIMIT 1",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return row[0], row[1]
    return None, None


NETWORK_ITEM_PATH_PREFIX = "/network/item/"


def _parse_item_url_path(identifier):
    """Mirrors the frontend's own parseItemUrlPath() exactly: mod:internal
    or mod:internal:damage for an item, bare internal for a fluid (kind
    inferred from whether a colon is present at all - a fluid's internal
    name never contains one, an item's mod:internal always does - same
    heuristic already trusted elsewhere in this codebase for exactly this
    distinction, see the Cryotheum fix)."""
    parts = [unquote(p) for p in identifier.split(":")]
    if len(parts) == 1:
        return {"mod": None, "internal": parts[0], "damage": None, "kind": "fluid"}
    mod = parts[0] or None
    internal = parts[1]
    damage = int(parts[2]) if len(parts) >= 3 and parts[2] else 0
    return {"mod": mod, "internal": internal, "damage": damage, "kind": "item"}


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
        with _lock:
            jobs = _state.get("jobs", [])
        total = len(jobs)
        busy = sum(1 for j in jobs if j.get("busy"))
        title = "Crafts"
        desc = (
            f"{busy} of {total} crafting CPUs busy" if total else "No crafting data yet"
        )

    elif path == "/power":
        _, rows = _fetch_power_rows(
            "hour"
        )  # just need the latest reading, not a full day
        title = "Power"
        if rows:
            stored, capacity = rows[-1][1], rows[-1][2]
            pct = round(stored / capacity * 100, 1) if capacity else 0
            desc = f"{_format_qty_py(stored)} EU stored of {_format_qty_py(capacity)} EU ({pct}%)"
            image = f"{base}/api/power/chart.png?range=day"
        else:
            desc = "No power data yet"

    elif path.startswith(NETWORK_ITEM_PATH_PREFIX):
        identifier = path[len(NETWORK_ITEM_PATH_PREFIX) :]
        parsed = _parse_item_url_path(identifier) if identifier else {}
        mod = parsed.get("mod")
        internal = parsed.get("internal")
        damage = parsed.get("damage")
        kind = parsed.get("kind") or "item"
        if internal:
            label, size = _lookup_item_display_info(mod, internal, damage, kind)
            title = label if label else "Item"
            unit = " mB" if kind == "fluid" else ""
            desc = (
                f"Currently stored: {size:,.0f}{unit}"
                if size is not None
                else "No data yet"
            )
            q = f"mod={mod or ''}&internal={internal}&damage={damage if damage is not None else ''}&kind={kind}"
            image = f"{base}/api/network/history/chart.png?{q}&range=day"
        else:
            title = "Item"

    elif path == "/network":
        with _network_lock:
            items = _network_state["items"]
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
            f'<meta property="og:image:width" content="{_CHART_WIDTH_PX}">\n'
            f'<meta property="og:image:height" content="{_CHART_HEIGHT_PX}">\n'
            f'<meta name="twitter:card" content="summary_large_image">\n'
        )
    return tags


@app.route("/", methods=["GET"])
@app.route("/crafts", methods=["GET"])
@app.route("/power", methods=["GET"])
@app.route("/network", methods=["GET"])
@app.route("/network/item/<identifier>", methods=["GET"])
def index(identifier=None):
    html = INDEX_HTML.replace(
        "<!--OG_TAGS-->", _build_og_tags(request.path, request.args)
    )
    return Response(html, mimetype="text/html")


INDEX_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
INDEX_HTML = None


def create_app(data_dir=None, api_key=None):
    """Points the app at its data directory and runs every startup step
    (schema creation/migration, restart cleanup, admin bootstrap, network
    snapshot reload). Importing this module has no side effects; this is
    the one place startup happens. data_dir/api_key default to the
    DATA_DIR/API_KEY environment variables."""
    global INDEX_HTML
    _configure(data_dir, api_key)
    _load_icon_lookup()
    _init_craft_db()
    _close_orphaned_request_history()
    _bootstrap_admin()
    _init_power_db()
    _init_item_history_db()
    _load_network_snapshot()
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        INDEX_HTML = f.read()
    return app


def _cli_new_token(display_name):
    """Revokes a user's tokens and sessions and prints a new token - the
    recovery path when the only admin has lost theirs."""
    conn = _craft_db()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE display_name = ?", (display_name,)
        ).fetchone()
        if not row:
            raise SystemExit(f"No user named {display_name!r}")
        now = time.time()
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, row[0]),
        )
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, row[0]),
        )
        token = _create_access_token(conn, row[0])
        conn.commit()
    finally:
        conn.close()
    print(token)


if __name__ == "__main__":
    create_app()
    if len(sys.argv) == 3 and sys.argv[1] == "new-token":
        _cli_new_token(sys.argv[2])
        sys.exit(0)
    _require_runtime_secrets()
    _require_initial_admin()
    import logging

    from waitress import serve

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("PORT", "8420"))
    # Single process on purpose: crafts, network scans, and craft/cancel
    # requests are held in module-level memory, so multiple worker
    # processes would each see a different copy.
    # clear_untrusted_proxy_headers=False: waitress otherwise strips every
    # X-Forwarded-* header (its own trusted_proxy is unset), so ProxyFix
    # never sees them and TRUSTED_PROXIES has no effect. The app already
    # decides which peers to trust in _trusted_proxy_wsgi_app().
    serve(app, host="0.0.0.0", port=port, threads=8, clear_untrusted_proxy_headers=False)
