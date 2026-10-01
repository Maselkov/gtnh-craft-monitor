import time

import pytest

from gcm import rates, store


class TestSlope:
    def test_fits_a_straight_line(self):
        assert rates.slope([(0, 10), (10, 30), (20, 50)]) == pytest.approx(2)

    def test_needs_two_distinct_times(self):
        assert rates.slope([(5, 1)]) is None
        assert rates.slope([(5, 1), (5, 9)]) is None


class TestPowerTrend:
    def test_filling_gives_time_to_full(self):
        now = 10000.0
        rows = [(now - 1800 + i * 60, 500 + i * 60, 10000) for i in range(31)]  # +1 EU/s
        trend = rates.power_trend(rows, now)
        assert trend["per_second"] == pytest.approx(1)
        assert trend["seconds_to_full"] == pytest.approx(10000 - 2300)
        assert trend["seconds_to_empty"] is None

    def test_draining_gives_time_to_empty(self):
        now = 10000.0
        rows = [(now - 1200 + i * 60, 5000 - i * 120, 10000) for i in range(21)]  # -2 EU/s
        trend = rates.power_trend(rows, now)
        assert trend["seconds_to_empty"] == pytest.approx(2600 / 2)
        assert trend["seconds_to_full"] is None

    def test_only_the_last_hour_counts(self):
        now = 100000.0
        old = [(now - 7200 + i * 60, 0, 10000) for i in range(30)]
        recent = [(now - 1200 + i * 60, 5000, 10000) for i in range(21)]
        assert rates.power_trend(old + recent, now)["per_second"] == pytest.approx(0)

    def test_too_short_a_span_says_nothing(self):
        now = 10000.0
        assert rates.power_trend([(now - 120, 0, 10), (now, 5, 10)], now) is None
        assert rates.power_trend([], now) is None

    def test_an_eta_years_off_is_dropped(self):
        now = 10000.0
        rows = [(now - 1800, 5000, 1e15), (now, 5001, 1e15)]
        assert rates.power_trend(rows, now)["seconds_to_full"] is None


class TestStockTrend:
    def test_first_to_last_over_the_window(self):
        now = 7200.0
        trend = rates.stock_trend([(0, 1000), (3600, 600), (5000, 280)], now)
        assert trend["per_second"] == pytest.approx(-0.1)
        assert trend["window_seconds"] == 7200
        assert trend["seconds_to_empty"] == pytest.approx(2800)

    def test_growing_stock_never_runs_out(self):
        trend = rates.stock_trend([(0, 10), (100, 20)], 3600.0)
        assert trend["per_second"] > 0
        assert trend["seconds_to_empty"] is None

    def test_too_short_a_span_says_nothing(self):
        assert rates.stock_trend([(0, 10)], 60.0) is None
        assert rates.stock_trend([], 60.0) is None


class TestEndpoints:
    def test_power_reports_a_trend(self, client):
        now = time.time()
        for i in range(21):
            store.power.add_reading(now - 1200 + i * 60, 5000 - i * 120, 10000, {})
        trend = client.get("/api/power").get_json()["trend"]
        assert trend["per_second"] == pytest.approx(-2, rel=0.01)
        assert trend["seconds_to_empty"] == pytest.approx(1300, rel=0.05)

    def test_power_without_enough_readings_has_no_trend(self, client):
        store.power.add_reading(time.time(), 5000, 10000, {})
        assert client.get("/api/power").get_json()["trend"] is None

    def test_item_history_trend_follows_the_range(self, client):
        from gcm import db
        key = store.items.item_key("minecraft", "iron_ingot", 0, "item")
        now = time.time()
        with db.transaction(db.item_history_db) as conn:
            conn.executemany(
                "INSERT INTO item_history (item_key, label, size, recorded_at) VALUES (?, ?, ?, ?)",
                [(key, "Iron Ingot", 10000, now - 20 * 86400),
                 (key, "Iron Ingot", 8640, now - 43200)],
            )
        base = "/api/network/history?mod=minecraft&internal=iron_ingot&damage=0&kind=item"
        day = client.get(base + "&range=day").get_json()["trend"]
        # The day starts holding 10000, so it lost 1360 over it.
        assert day["per_second"] == pytest.approx(-1360 / 86400, rel=0.01)
        assert day["seconds_to_empty"] == pytest.approx(8640 / (1360 / 86400), rel=0.01)
        lifetime = client.get(base + "&range=lifetime").get_json()["trend"]
        assert lifetime["window_seconds"] == pytest.approx(20 * 86400, rel=0.01)

    def test_item_without_history_has_no_trend(self, client):
        res = client.get("/api/network/history?mod=minecraft&internal=iron_ingot&damage=0&kind=item")
        assert res.get_json()["trend"] is None
