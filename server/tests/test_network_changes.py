import time

import pytest

from gcm import store
from test_item_history_api import IRON, run_scan

GOLD = {"mod": "minecraft", "internal": "gold_ingot", "damage": 0, "kind": "item"}
COPPER = {"mod": "gregtech", "internal": "copper", "damage": 0, "kind": "item"}


def key(item):
    return store.items.key_of(item)


def record(item, size, ago):
    store.items.add_rows([(key(item), "x", size, time.time() - ago)])


def changes(client, range_key="day"):
    return client.get(f"/api/network/changes?range={range_key}").get_json()


class TestNetworkChanges:
    def test_falling_item(self, client, api_headers):
        record(IRON, 1000, 2 * 86400)
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=400)])
        change = changes(client)["changes"][key(IRON)]
        assert change["from"] == 1000
        assert change["per_second"] == pytest.approx(-600 / 86400, rel=0.01)
        assert change["seconds_to_empty"] == pytest.approx(400 / (600 / 86400), rel=0.01)

    def test_item_first_recorded_in_the_range_starts_from_its_first_value(self, client, api_headers):
        # A fresh install has no history before the range - every item
        # isn't new, so it's measured from when it was first seen.
        record(GOLD, 100, 6 * 3600)
        run_scan(client, api_headers, [dict(GOLD, name="Gold Ingot", size=50)])
        change = changes(client)["changes"][key(GOLD)]
        assert change["from"] == 100
        assert change["per_second"] == pytest.approx(-50 / (6 * 3600), rel=0.01)

    def test_item_first_seen_in_the_last_scan_has_not_changed(self, client, api_headers):
        run_scan(client, api_headers, [dict(GOLD, name="Gold Ingot", size=50)])
        assert key(GOLD) not in changes(client)["changes"]

    def test_unchanged_item_is_left_out(self, client, api_headers):
        record(COPPER, 10, 2 * 86400)
        run_scan(client, api_headers, [dict(COPPER, name="Copper", size=10)])
        assert key(COPPER) not in changes(client)["changes"]

    def test_range_picks_the_starting_value(self, client, api_headers):
        record(IRON, 800, 3 * 86400)
        record(IRON, 600, 36 * 3600)
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=500)])
        assert changes(client, "day")["changes"][key(IRON)]["from"] == 600
        week = changes(client, "week")
        assert week["range"] == "week"
        assert week["changes"][key(IRON)]["from"] == 800

    def test_week_on_a_fresh_install_starts_at_the_first_value(self, client, api_headers):
        record(IRON, 800, 3 * 86400)
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=500)])
        change = changes(client, "week")["changes"][key(IRON)]
        assert change["from"] == 800
        assert change["per_second"] == pytest.approx(-300 / (3 * 86400), rel=0.01)

    def test_unknown_range_is_a_day(self, client):
        assert changes(client, "year")["range"] == "day"

    def test_a_new_scan_is_not_hidden_by_the_cache(self, client, api_headers):
        record(IRON, 1000, 2 * 86400)
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=400)])
        assert changes(client)["changes"][key(IRON)]["from"] == 1000
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=1000)])
        assert key(IRON) not in changes(client)["changes"]
