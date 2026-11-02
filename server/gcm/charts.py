"""Static chart images (matplotlib) for OpenGraph embeds - Discord (and
any other link-unfurling crawler) does a plain HTTP GET and reads
<meta property="og:..."> tags straight out of the raw HTML; it never
runs JS, so the in-app Chart.js rendering is invisible to it entirely.
This renders an actual PNG server-side, independent of the browser,
with a small TTL cache and a per-client rate limit in front of it."""

import hashlib
import json
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque
from io import BytesIO

from flask import jsonify, request

from gcm import chart_draw, config
from gcm.chart_draw import format_qty  # noqa: F401 (charts.format_qty, for stock alerts and og tags)

WIDTH_PX = 800
# 2:1 - landscape, which matters: Discord reads og:image:width/height
# as one of its signals for picking the large-image embed layout over a
# small thumbnail, alongside twitter:card - a portrait/near-square image
# would work against that signal instead of for it.
HEIGHT_PX = 400
# Python's start and matplotlib's import are most of a chart's second.
RENDER_TIMEOUT_SECONDS = 30

_chart_cache = OrderedDict()
_chart_cache_lock = threading.Lock()
_chart_request_times = OrderedDict()
_chart_rate_lock = threading.Lock()


def request_client_id():
    return request.remote_addr or "unknown"


def rate_limit_response():
    now = time.time()
    client_id = request_client_id()
    with _chart_rate_lock:
        timestamps = _chart_request_times.get(client_id)
        if timestamps is None:
            if len(_chart_request_times) >= config.CHART_MAX_TRACKED_CLIENTS:
                _chart_request_times.popitem(last=False)
            timestamps = deque()
            _chart_request_times[client_id] = timestamps
        _chart_request_times.move_to_end(client_id)
        while timestamps and timestamps[0] <= now - 60:
            timestamps.popleft()
        if len(timestamps) >= config.CHART_RATE_LIMIT_PER_MINUTE:
            retry_after = max(1, int(timestamps[0] + 60 - now) + 1)
            response = jsonify({"error": "chart rate limit exceeded"})
            response.headers["Retry-After"] = str(retry_after)
            return response, 429
        timestamps.append(now)
    return None


def cached_png(cache_key, render):
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
        _chart_cache[cache_key] = (now + config.CHART_CACHE_TTL_SECONDS, png_bytes)
        _chart_cache.move_to_end(cache_key)
        while len(_chart_cache) > config.CHART_CACHE_MAX_ENTRIES:
            _chart_cache.popitem(last=False)
    return png_bytes


def version(rows):
    """A short hash of a chart's rows, for its og:image URL. Discord's
    media proxy keeps an image by its URL for far longer than our
    max-age, so a URL that changes with the data is what gets a fresh
    chart into a new link preview."""
    return hashlib.sha1(repr(list(rows)).encode()).hexdigest()[:12]


def render_png(
    rows, stepped, span_seconds=None, now=None, width_px=WIDTH_PX, height_px=HEIGHT_PX
):
    """rows: list of (unix_ts, value) -> the chart as PNG, in a BytesIO.
    Drawn by gcm/chart_draw.py in a child process, so matplotlib's ~40 MB
    goes when it's done (see there for what each argument draws)."""
    chart = {
        "rows": [[ts, value] for ts, value in rows],
        "stepped": stepped,
        "span_seconds": span_seconds,
        "now": time.time() if now is None else now,
        "width_px": width_px,
        "height_px": height_px,
    }
    result = subprocess.run(
        [sys.executable, "-I", chart_draw.__file__],
        input=json.dumps(chart).encode(),
        capture_output=True,
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"drawing a chart failed (exit status {result.returncode}): "
            + result.stderr.decode(errors="replace").strip()
        )
    return BytesIO(result.stdout)


def reset():
    with _chart_cache_lock:
        _chart_cache.clear()
    with _chart_rate_lock:
        _chart_request_times.clear()
