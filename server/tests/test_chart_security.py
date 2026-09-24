from io import BytesIO

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
