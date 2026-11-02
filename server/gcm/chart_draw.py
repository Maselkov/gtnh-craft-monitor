"""Draws the OpenGraph chart PNGs (gcm/charts.py) with matplotlib - in
a child process: `python -I chart_draw.py`, the chart's arguments as
JSON on stdin, the PNG on stdout.

matplotlib and numpy take ~40 MB once imported, and Python never unloads
a module, so drawing in the server kept that for good after the first
link to a chart was shared - a quarter of a hosted base's 160 MB. A
child gives it all back when it exits, for about a second per chart,
and charts are cached and rate-limited (charts.py). So this file imports
nothing from gcm: run as a script with -I, gcm isn't on its path."""

import json
import sys
from datetime import datetime
from io import BytesIO

_BG = "#0f1115"
_PANEL = "#1a1d24"
_LINE = "#5fb3ff"
_MUTED = "#8a8f98"


def format_qty(n):
    # Same abbreviation scheme as the frontend's formatQty()/formatEU| -
    # doesn't need to be pixel-identical to the in-app JS version, just
    # informative for a preview image glanced at in a chat client.
    if n is None:
        return "0"
    n = float(n)
    a = abs(n)
    if a >= 1e21:
        return f"{n:.2e}"
    if a >= 1e18:
        return f"{n/1e18:.2f}E"
    if a >= 1e15:
        return f"{n/1e15:.2f}P"
    if a >= 1e12:
        return f"{n/1e12:.2f}T"
    if a >= 1e9:
        return f"{n/1e9:.2f}G"
    if a >= 1e6:
        return f"{n/1e6:.2f}M"
    if a >= 1e3:
        return f"{n/1e3:.1f}k"
    return f"{n:.0f}"


def figure(rows, stepped, span_seconds, now, width_px, height_px):
    """The chart as a matplotlib Figure. rows: list of (unix_ts, value).
    stepped=True draws a step line (item quantity - a step function,
    matching the 'stepped: after' Chart.js config already used in the
    browser); stepped=False draws a smooth line (power draw, a
    continuously-sampled signal).

    The x axis runs from now - span_seconds (span_seconds None: the
    first row) to now, whatever the rows cover. Left to autoscale, one
    row - an item that hasn't changed all day - is a zero-width range
    that matplotlib's date locator widens to +-2 years."""
    # The Figure API directly, never pyplot: pyplot keeps global figure
    # state. A bare Figure needs no backend selection either - savefig()
    # renders with Agg.
    import matplotlib.dates as mdates
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter

    dpi = 100
    fig = Figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.subplots()
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_PANEL)

    if rows:
        left = rows[0][0] if span_seconds is None else now - span_seconds
        if stepped and rows[-1][0] < now:
            # Item history only records changes: hold the latest value
            # out to now instead of ending the line at the last change.
            rows = list(rows) + [(now, rows[-1][1])]
        times = [datetime.fromtimestamp(r[0]) for r in rows]
        values = [r[1] for r in rows]
        if stepped:
            ax.step(times, values, where="post", color=_LINE, linewidth=2)
            ax.fill_between(times, values, step="post", color=_LINE, alpha=0.15)
        else:
            ax.plot(times, values, color=_LINE, linewidth=2)
            ax.fill_between(times, values, color=_LINE, alpha=0.15)
        ax.set_ylim(bottom=0)
        ax.set_xlim(datetime.fromtimestamp(left), datetime.fromtimestamp(now))
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
    return fig


def png(**chart):
    """figure()'s arguments -> PNG bytes."""
    fig = figure(**chart)
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    return buf.getvalue()


if __name__ == "__main__":
    sys.stdout.buffer.write(png(**json.load(sys.stdin)))
