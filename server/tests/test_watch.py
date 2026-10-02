"""Checks of just the stock rules' items between full scans:
/api/network/watch says what to check, /api/network/levels takes the
result (network_browser.lua's side is in oc/tests/)."""

from gcm import state, store
from conftest import login_as
from test_stock import GOLD, IRON, auto_requests, cpus, pushes, scan, scanned, set_target  # noqa: F401

WATER = {"label": "Water", "mod": None, "internal": "water", "damage": None, "kind": "fluid"}
CHECK_IRON = {"items": [{"name": "minecraft:iron_ingot", "damage": 0}], "fluids": []}


def watch(client, api_headers):
    return client.get("/api/network/watch", headers=api_headers).get_json()


def levels(client, api_headers, *items, checked=CHECK_IRON, elapsed=1):
    return client.post("/api/network/levels", headers=api_headers,
                       json={"checked": checked, "items": list(items), "elapsed": elapsed}).get_json()


def sizes(client):
    return {it["name"]: it["size"] for it in client.get("/api/network").get_json()["items"]}


def history_sizes(item):
    key = store.items.item_key(item["mod"], item["internal"], item["damage"], item["kind"])
    return [size for _, size in store.items.history(key, None)]


class TestWatchList:
    def test_needs_the_api_key(self, client):
        assert client.get("/api/network/watch").status_code == 401
        assert client.post("/api/network/levels", json={"items": []}).status_code == 401

    def test_lists_targets_and_alerts_once_each(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500), scanned(GOLD, 500))
        login_as(client, "olive", role="operator")
        set_target(client)
        client.post("/api/stock/alert", json={**IRON, "below": 5})
        client.post("/api/stock/alert", json={**WATER, "below": 1000})
        login_as(client, "alice")
        client.post("/api/stock/alert", json={**GOLD, "below": 5})
        assert watch(client, api_headers) == {
            "items": [{"name": "minecraft:gold_ingot", "damage": 0}, {"name": "minecraft:iron_ingot", "damage": 0}],
            "fluids": ["water"],
        }

    def test_paused_targets_are_left_out(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500))
        login_as(client, "olive", role="operator")
        set_target(client, enabled=False)
        assert watch(client, api_headers) == {"items": [], "fluids": []}


class TestLevels:
    def test_updates_only_the_checked_items(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500), scanned(GOLD, 500))
        updated_at = client.get("/api/network").get_json()["updated_at"]
        # Gold shares nothing with iron, and wasn't asked for: ignored.
        assert levels(client, api_headers, scanned(IRON, 420), scanned(GOLD, 1)) == {"ok": True, "found": 1}
        assert sizes(client) == {"Iron Ingot": 420, "Gold Ingot": 500}
        assert history_sizes(IRON) == [500, 420]
        data = client.get("/api/network").get_json()
        assert data["updated_at"] == updated_at  # still the last full scan's time
        assert data["item_count"] == 2

    def test_a_checked_item_not_found_is_gone(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500), scanned(GOLD, 500))
        levels(client, api_headers)
        assert sizes(client) == {"Gold Ingot": 500}
        assert history_sizes(IRON) == [500, 0]

    def test_fluids(self, client, api_headers):
        water = {"name": "Water", "internal": "water", "kind": "fluid", "size": 64000}
        scan(client, api_headers, water)
        levels(client, api_headers, {**water, "size": 1000}, checked={"items": [], "fluids": ["water"]})
        assert sizes(client) == {"Water": 1000}

    def test_survives_a_restart(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500), scanned(GOLD, 500))
        levels(client, api_headers, scanned(IRON, 7))
        items, _ = store.items.load_snapshot()
        assert {it["name"]: it["size"] for it in items} == {"Iron Ingot": 7, "Gold Ingot": 500}

    def test_new_numbers_change_the_etag(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500))
        etag = client.get("/api/network").headers["ETag"]
        levels(client, api_headers, scanned(IRON, 7))
        assert client.get("/api/network", headers={"If-None-Match": etag}).status_code == 200

    def test_refused_during_a_full_scan(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500))
        client.post("/api/network/scan/start", headers=api_headers)
        assert levels(client, api_headers, scanned(IRON, 7)) == {"ok": False, "error": "scan_in_progress"}
        with state.network_lock:
            assert state.network["items"][0]["size"] == 500

    def test_bad_payload(self, client, api_headers):
        res = client.post("/api/network/levels", headers=api_headers, json={"checked": CHECK_IRON})
        assert res.status_code == 400
        # Junk entries are skipped rather than failing the check.
        scan(client, api_headers, scanned(IRON, 500))
        assert levels(client, api_headers, "junk", scanned(IRON, 3),
                      checked={"items": [{"name": 5}, "x", {"name": "minecraft:iron_ingot", "damage": 0}]})["ok"]


class TestRulesBetweenScans:
    def test_an_alert_fires_from_a_check(self, client, api_headers, pushes):
        scan(client, api_headers, scanned(IRON, 500))
        login_as(client, "alice")
        client.post("/api/stock/alert", json={**IRON, "below": 100})
        levels(client, api_headers, scanned(IRON, 50))
        assert "is at 50" in pushes[0][1]["body"]

    def test_a_target_restocks_from_a_check(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500))
        login_as(client, "olive", role="operator")
        set_target(client)
        cpus(client, api_headers)
        levels(client, api_headers, scanned(IRON, 40))
        assert [r["amount"] for r in auto_requests()] == [260]

    def test_a_job_that_ended_during_the_check_holds_it_off(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500))
        login_as(client, "olive", role="operator")
        set_target(client)
        cpus(client, api_headers, busy=[IRON])
        client.post("/api/crafts", headers=api_headers, json={"source": "me_controller", "jobs": [  # the job ends
            {"name": f"I{n}", "busy": False} for n in range(3)] + [{"name": "B0", "busy": False}]})
        levels(client, api_headers, scanned(IRON, 40), elapsed=30)
        assert auto_requests() == []
        levels(client, api_headers, scanned(IRON, 40), elapsed=0)
        assert len(auto_requests()) == 1
