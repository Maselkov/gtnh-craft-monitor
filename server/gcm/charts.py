"""Static chart images (matplotlib) for OpenGraph embeds - Discord (and
any other link-unfurling crawler) does a plain HTTP GET and reads
<meta property="og:..."> tags straight out of the raw HTML; it never
runs JS, so the in-app Chart.js rendering is invisible to it entirely.
This renders an actual PNG server-side, independent of the browser,
with a small TTL cache and a per-client rate limit in front of it."""

import os
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # no display in a container - must be set before
# importing pyplot, or it tries (and fails) to find a GUI backend
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from flask import jsonify, request  # noqa: E402

_BG = "#0f1115"
_PANEL = "#1a1d24"
_LINE = "#5fb3ff"
_MUTED = "#8a8f98"
WIDTH_PX = 800
# 2:1 - landscape, which matters: Discord reads og:image:width/height
# as one of its signals for picking the large-image embed layout over a
# small thumbnail, alongside twitter:card - a portrait/near-square image
# would work against that signal instead of for it.
HEIGHT_PX = 400

CHART_CACHE_TTL_SECONDS = int(os.environ.get("CHART_CACHE_TTL_SECONDS", "60"))
CHART_CACHE_MAX_ENTRIES = int(os.environ.get("CHART_CACHE_MAX_ENTRIES", "64"))
CHART_RATE_LIMIT_PER_MINUTE = int(os.environ.get("CHART_RATE_LIMIT_PER_MINUTE", "30"))
CHART_MAX_TRACKED_CLIENTS = int(os.environ.get("CHART_MAX_TRACKED_CLIENTS", "4096"))
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
        _chart_cache[cache_key] = (now + CHART_CACHE_TTL_SECONDS, png_bytes)
        _chart_cache.move_to_end(cache_key)
        while len(_chart_cache) > CHART_CACHE_MAX_ENTRIES:
            _chart_cache.popitem(last=False)
    return png_bytes


def format_qty(n):
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


def render_png(
    rows, stepped, width_px=WIDTH_PX, height_px=HEIGHT_PX
):
    """rows: list of (unix_ts, value). stepped=True draws a step line
    (item quantity - a step function, matching the 'stepped: after'
    Chart.js config already used in the browser); stepped=False draws a
    smooth line (power draw, a continuously-sampled signal)."""
    dpi = 100
    fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_PANEL)

    if rows:
        times = [datetime.fromtimestamp(r[0]) for r in rows]
        values = [r[1] for r in rows]
        if stepped:
            ax.step(times, values, where="post", color=_LINE, linewidth=2)
            ax.fill_between(times, values, step="post", color=_LINE, alpha=0.15)
        else:
            ax.plot(times, values, color=_LINE, linewidth=2)
            ax.fill_between(times, values, color=_LINE, alpha=0.15)
        ax.set_ylim(bottom=0)
    else:
        ax.text(
            0.5,
            0.5,
            "No data yet",
            ha="center",
            va="center",
            color=_MUTED,
            fontsize=12,
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, pos: format_qty(v)))
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
    ax.tick_params(colors=_MUTED, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(_MUTED)
        spine.set_alpha(0.3)
    ax.grid(True, color=_MUTED, alpha=0.15)
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def reset():
    with _chart_cache_lock:
        _chart_cache.clear()
    with _chart_rate_lock:
        _chart_request_times.clear()
