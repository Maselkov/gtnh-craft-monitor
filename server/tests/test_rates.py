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


def lsc_sawtooth(now, rate, threshold=6e15, every=64, span=3600):
    """(ts, stored, capacity, threshold) readings of an LSC in wireless
    mode whose own surplus is `rate` EU/s: reset to its threshold every
    5 minutes, read every `every` seconds."""
    return [(t, threshold + rate * (t % 300), 9.2e18, threshold) for t in range(int(now - span), int(now), every)]


class TestWirelessTrade:
    def test_sending(self):
        now = 1_000_000.0
        # Its in/out averages aren't used: they count the uploads too.
        assert rates.wireless_trade(lsc_sawtooth(now, 425e9), now) == pytest.approx(425e9, rel=0.1)

    def test_drawing(self):
        now = 1_000_000.0
        assert rates.wireless_trade(lsc_sawtooth(now, -2e9), now) == pytest.approx(-2e9, rel=0.1)

    def test_too_few_readings_say_nothing(self):
        now = 1_000_000.0
        assert rates.wireless_trade(lsc_sawtooth(now, 1e9, every=120, span=1080), now) is None  # 9 in 18 minutes
        assert rates.wireless_trade(lsc_sawtooth(now, 1e9, every=30, span=500), now) is None  # too short
        assert rates.wireless_trade([], now) is None

    def test_not_in_wireless_mode_says_nothing(self):
        now = 1_000_000.0
        rows = [(t, 5e15, 9.2e18, None) for t in range(int(now - 3600), int(now), 60)]
        assert rates.wireless_trade(rows, now) is None

    def test_a_new_threshold_starts_over(self):
        now = 1_000_000.0
        # A capacitor added 40 minutes ago: only the readings since count.
        before = lsc_sawtooth(now - 2400, 5e12, threshold=6e13, span=1200)
        after = lsc_sawtooth(now, 1e9, span=2400)
        assert rates.wireless_trade(before + after, now) == pytest.approx(1e9, rel=0.1)


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

    def test_power_reports_what_an_lsc_trades_with_the_network(self, client):
        now = time.time()
        for t, stored, capacity, threshold in lsc_sawtooth(now, 425e9):
            # Averages as a real one sends them: in and out nearly equal.
            store.power.add_reading(t, stored, capacity, {"avg_eu_in_1h": 105.5e9, "avg_eu_out_1h": 106.1e9},
                                    threshold=threshold, wireless_mode=True)
        assert client.get("/api/power").get_json()["wireless_trade"] == pytest.approx(425e9, rel=0.1)

    def test_power_without_enough_readings_has_no_trend(self, client):
        store.power.add_reading(time.time(), 5000, 10000, {})
        data = client.get("/api/power").get_json()
        assert data["trend"] is None and data["wireless_trade"] is None

    def test_item_history_trend_follows_the_range(self, client):
        key = store.items.item_key("minecraft", "iron_ingot", 0, "item")
        now = time.time()
        store.items.add_rows([(key, "Iron Ingot", 10000, now - 20 * 86400),
                              (key, "Iron Ingot", 8640, now - 43200)])
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
