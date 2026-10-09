from io import BytesIO

import pytest

from gcm import charts, config


def test_power_chart_reuses_cached_png(client, monkeypatch):
    render_count = 0

    def render_chart(*args, **kwargs):
        nonlocal render_count
        render_count += 1
        return BytesIO(b"cached-chart")

    monkeypatch.setattr(charts, "render_png", render_chart)

    first = client.get("/api/power/chart.png?range=day")
    second = client.get("/api/power/chart.png?range=day")

    assert first.data == b"cached-chart"
    assert second.data == b"cached-chart"
    assert render_count == 1
    assert first.headers["Cache-Control"] == "public, max-age=60"


def test_chart_rate_limit_rejects_excess_requests(client, monkeypatch):
    monkeypatch.setattr(config, "CHART_RATE_LIMIT_PER_MINUTE", 1)

    assert client.get("/api/power/chart.png").status_code == 200
    response = client.get("/api/power/chart.png")

    assert response.status_code == 429
    assert response.headers["Retry-After"]


def test_chart_rate_limiter_caps_tracked_clients(client, monkeypatch):
    monkeypatch.setattr(config, "CHART_MAX_TRACKED_CLIENTS", 2)

    for address in ("198.51.100.1", "198.51.100.2", "198.51.100.3"):
        response = client.get(
            "/api/power/chart.png",
            environ_overrides={"REMOTE_ADDR": address},
        )
        assert response.status_code == 200

    assert len(charts._chart_request_times) == 2


def x_of(ts):
    """Where render_png plots a unix timestamp on the x axis."""
    return charts.mdates.date2num(charts.datetime.fromtimestamp(ts))


def render_axes(monkeypatch, *args, **kwargs):
    """Render a chart and return its Axes."""
    figures = []

    class CapturingFigure(charts.Figure):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            figures.append(self)

    monkeypatch.setattr(charts, "Figure", CapturingFigure)
    charts.render_png(*args, **kwargs)
    return figures[0].axes[0]


def test_chart_x_axis_spans_the_range_for_one_row(monkeypatch):
    # An item unchanged all day is one row; autoscaled, that axis ran
    # +-2 years around it.
    now = 1_760_000_000
    ax = render_axes(monkeypatch, [(now - 86400, 5)], stepped=True, span_seconds=86400, now=now)

    assert ax.get_xlim() == pytest.approx((x_of(now - 86400), x_of(now)))


def test_stepped_chart_holds_the_last_value_until_now(monkeypatch):
    now = 1_760_000_000
    ax = render_axes(
        monkeypatch, [(now - 3600, 5), (now - 1800, 9)], stepped=True, span_seconds=86400, now=now
    )

    xs, ys = ax.lines[0].get_data()
    assert charts.mdates.date2num(xs[-1]) == pytest.approx(x_of(now))
    assert ys[-1] == 9


def test_lifetime_chart_starts_at_the_first_row(monkeypatch):
    now = 1_760_000_000
    ax = render_axes(monkeypatch, [(now - 500_000, 5)], stepped=False, now=now)

    assert ax.get_xlim() == pytest.approx((x_of(now - 500_000), x_of(now)))
