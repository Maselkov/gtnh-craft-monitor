from gcm import config, retention, state, store, webhooks
from conftest import login_as
from test_item_history_api import IRON, run_scan
from test_network_api import run_scan as run_batched_scan


def health(client):
    login_as(client, "root", role="admin")
    res = client.get("/api/admin/health")
    assert res.status_code == 200
    return res.get_json()


def feed(data, key):
    return next(f for f in data["feeds"] if f["key"] == key)


class TestHealth:
    def test_admins_only(self, client):
        assert client.get("/api/admin/health").status_code == 403
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/health").status_code == 403

    def test_no_interface_terminal_is_said_without_a_warning(self, client, api_headers):
        assert feed(health(client), "patterns")["off"] is None
        run_batched_scan(client, api_headers, [], interface_terminal=False)
        assert feed(health(client), "patterns")["off"] == "no ME Interface Terminal connected (optional)"

    def test_nothing_reported_yet(self, client):
        data = health(client)
        assert feed(data, "crafts")["ok"] is False
        assert feed(data, "crafts")["age_seconds"] is None
        assert feed(data, "network")["ok"] is False
        assert not [f for f in data["feeds"] if f["key"].startswith("power:")]
        assert data["retention"]["last_run"] is None
        assert data["webhooks"] == {"configured": 0, "last": None}

    def test_fresh_feeds_are_ok(self, client, api_headers):
        client.post("/api/crafts", json={"source": "me", "jobs": [{"name": "A", "busy": False}]}, headers=api_headers)
        client.post("/api/power", json={"stored": 5, "capacity": 10}, headers=api_headers)
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=5)])
        data = health(client)
        crafts = feed(data, "crafts")
        assert crafts["ok"] is True and crafts["cpus"] == 1
        network = feed(data, "network")
        assert network["ok"] is True and network["items"] == 1
        assert network["scan_seconds"] >= 0
        assert feed(data, "power:")["ok"] is True

    def test_old_crafts_report_is_stale(self, client, api_headers):
        client.post("/api/crafts", json={"source": "me", "jobs": []}, headers=api_headers)
        state.crafts["received_at"] -= config.STALE_AFTER_SECONDS + 1
        assert feed(health(client), "crafts")["ok"] is False

    def test_rejected_scan_shows_until_one_is_kept(self, client, api_headers):
        token = client.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
        client.post("/api/network/scan/finish", json={"scan_token": token, "chunks_sent": 3, "total_errors": 0},
                    headers=api_headers)
        network = feed(health(client), "network")
        assert network["ok"] is False
        assert "incomplete" in network["rejected"]["reason"]
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=5)])
        assert feed(health(client), "network")["rejected"] is None

    def test_storage_lists_each_database(self, client):
        storage = health(client)["storage"]
        assert {f["name"] for f in storage["files"]} == {"app.db", "item_history.db", "power.db"}
        assert all(f["bytes"] > 0 for f in storage["files"])
        assert storage["free_bytes"] > 0

    def test_last_retention_run(self, client):
        retention.run_once()
        assert health(client)["retention"]["last_run"]["deleted"] == {"hourly": 0, "daily": 0}

    def test_last_webhook_delivery(self, client, monkeypatch):
        monkeypatch.setattr(config, "WEBHOOK_URLS", ["https://ok.example.test/a", "https://bad.example.test/b"])

        def post(url, payload):
            if "bad" in url:
                raise OSError("refused")

        monkeypatch.setattr(webhooks, "_post", post)
        webhooks.send(webhooks.event("crafts", "t", "b", "ok"))
        last = health(client)["webhooks"]["last"]
        assert last["sent"] == 1
        assert last["failed"] == [{"host": "bad.example.test", "error": "refused"}]
        assert last["age_seconds"] < 5

    def test_push_subscriptions_counted(self, client):
        assert health(client)["push_subscriptions"] == 0  # signs root in
        store.push.save_subscription("root", "https://push.example.test/1", "k", "a", "https://x")
        assert health(client)["push_subscriptions"] == 1


def test_a_scan_past_the_item_limit_shows_why(client, api_headers, monkeypatch):
    monkeypatch.setattr(config, "MAX_SCAN_ITEMS", 0)
    run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=5)])
    network = feed(health(client), "network")
    assert network["ok"] is False
    assert "MAX_SCAN_ITEMS" in network["rejected"]["reason"]
