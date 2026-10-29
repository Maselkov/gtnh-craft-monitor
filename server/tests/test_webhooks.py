import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gcm import config, webhooks
from conftest import login_as


def end_pinned_job(client, api_headers, pinned=True, label="Iron Ingot"):
    client.post("/api/crafts", json={"jobs": [{
        "name": "W01", "busy": True, "final_output": label, "final_output_mod": "minecraft",
        "final_output_internal": "iron_ingot", "final_output_damage": 0}]}, headers=api_headers)
    if pinned:
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
    client.post("/api/crafts", json={"jobs": [{"name": "W01", "busy": False}]}, headers=api_headers)


class TestEvents:
    def test_pinned_craft_ending(self, client, api_headers, webhook_events):
        end_pinned_job(client, api_headers)
        [event] = webhook_events
        assert (event["event"], event["title"]) == ("crafts", "Craft finished")
        assert event["body"] == "Iron Ingot is done crafting."
        assert event["url"] == "https://gtnh.example.test/network/item/minecraft:iron_ingot"

    def test_unpinned_craft_is_not_sent(self, client, api_headers, webhook_events):
        end_pinned_job(client, api_headers, pinned=False)
        assert webhook_events == []

    def test_event_turned_off(self, client, api_headers, webhook_events, monkeypatch):
        monkeypatch.setattr(config, "WEBHOOK_EVENTS", {"autocraft"})
        end_pinned_job(client, api_headers)
        assert webhook_events == []

    def test_no_urls_no_events(self, client, api_headers, webhook_events, monkeypatch):
        monkeypatch.setattr(config, "WEBHOOK_URLS", [])
        end_pinned_job(client, api_headers)
        assert webhook_events == []


@pytest.mark.parametrize("item,path", [
    ({"mod": "minecraft", "internal": "iron_ingot", "damage": 0}, "minecraft:iron_ingot"),
    ({"mod": "gregtech", "internal": "gt.metaitem.01", "damage": 11305.0}, "gregtech:gt.metaitem.01:11305"),
    ({"mod": "gregtech", "internal": "gt.metatool.01", "damage": 0, "variant": "ab12"}, "gregtech:gt.metatool.01:0~ab12"),
    ({"internal": "water", "kind": "fluid"}, "water"),
    ({"internal": "ordo", "kind": "essentia"}, "@ordo"),
    ({"mod": "x", "internal": "a b/c"}, "x:a%20b%2Fc"),
])
def test_item_links_match_the_page(item, path, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "https://h")
    assert webhooks.item_link(item) == "https://h/network/item/" + path


def test_no_public_url_no_links(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "")
    ev = webhooks.event("crafts", "T", "B", "good", icon="minecraft/iron.png", item={"internal": "x"})
    assert (ev["icon"], ev["url"]) == (None, None)
    assert "thumbnail" not in webhooks.discord_payload(ev)["embeds"][0]


@pytest.mark.parametrize("url,discord", [
    ("https://discord.com/api/webhooks/1/abc", True),
    ("https://discordapp.com/api/webhooks/1/abc", True),
    ("https://discord.com.evil.test/api/webhooks/1/abc", False),
    ("https://hooks.slack.com/services/x", False),
])
def test_discord_detection(url, discord):
    assert webhooks.is_discord(url) is discord


def test_event_text_drops_formatting_codes():
    ev = webhooks.event("crafts", "Craft finished", "§a16k§r ME Storage Component is done crafting.", "good")
    assert ev["body"] == "16k ME Storage Component is done crafting."


def test_discord_embed_never_pings():
    ev = webhooks.event("crafts", "Craft finished", "@everyone is done", "good")
    payload = webhooks.discord_payload(ev)
    assert payload["allowed_mentions"] == {"parse": []}
    assert payload["embeds"][0]["title"] == "Craft finished"


class Sink(BaseHTTPRequestHandler):
    received = []
    status = 204

    def do_POST(self):
        Sink.received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send_response(Sink.status)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def sink(monkeypatch):
    Sink.received, Sink.status = [], 204
    server = HTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook"
    monkeypatch.setattr(config, "WEBHOOK_URLS", [url])
    yield Sink
    server.shutdown()


class TestAdminTest:
    def test_posts_now_and_reports(self, client, sink):
        login_as(client, "root", role="admin")
        res = client.post("/api/admin/webhooks/test")
        assert res.get_json() == {"results": [{"host": "127.0.0.1", "error": None}]}
        [(path, body)] = sink.received
        assert path == "/hook" and body["title"] == "Test" and body["text"].startswith("*Test*: Webhooks work.")

    def test_reports_failures(self, client, sink):
        sink.status = 500
        login_as(client, "root", role="admin")
        [result] = client.post("/api/admin/webhooks/test").get_json()["results"]
        assert "500" in result["error"]

    def test_none_set(self, client, monkeypatch):
        monkeypatch.setattr(config, "WEBHOOK_URLS", [])
        login_as(client, "root", role="admin")
        res = client.post("/api/admin/webhooks/test")
        assert res.status_code == 400 and res.get_json()["error"] == "No webhooks set."

    def test_admins_only(self, client, sink):
        login_as(client, "olive", role="operator")
        assert client.post("/api/admin/webhooks/test").status_code == 403
        assert sink.received == []


class TestPageSettings:
    @pytest.fixture(autouse=True)
    def no_environment(self, monkeypatch):
        monkeypatch.setattr(config, "WEBHOOK_URLS", [])
        monkeypatch.setattr(config, "WEBHOOK_EVENTS", {"crafts", "autocraft"})
        monkeypatch.setattr(config, "WEBHOOK_HOSTS", set())

    def put(self, client, body):
        login_as(client, "root", role="admin")
        return client.put("/api/admin/webhooks", json=body)

    def test_defaults(self, client):
        login_as(client, "root", role="admin")
        assert client.get("/api/admin/webhooks").get_json() == {
            "urls": [], "events": ["autocraft", "crafts"], "environment": False, "hosts": []}

    def test_saved_and_used(self, client, api_headers, monkeypatch):
        res = self.put(client, {"urls": [" https://hooks.example.test/a ", "", "https://hooks.example.test/a"],
                                "events": ["crafts"]})
        assert res.get_json()["urls"] == ["https://hooks.example.test/a"]
        assert res.get_json()["events"] == ["crafts"]
        events = []
        monkeypatch.setattr(webhooks, "_put", events.append)
        end_pinned_job(client, api_headers)
        assert [e["title"] for e in events] == ["Craft finished"]
        assert webhooks.enabled("autocraft") is False

    def test_test_post_goes_to_page_urls(self, client, sink, monkeypatch):
        url = config.WEBHOOK_URLS[0]
        monkeypatch.setattr(config, "WEBHOOK_URLS", [])
        self.put(client, {"urls": [url]})
        assert client.post("/api/admin/webhooks/test").get_json() == {
            "results": [{"host": "127.0.0.1", "error": None}]}

    @pytest.mark.parametrize("body,error", [
        ({"urls": ["file:///etc/passwd"]}, "Not a web address"),
        ({"urls": ["https://"]}, "Not a web address"),
        ({"urls": "https://a.test/x"}, "urls must be a list"),
        ({"urls": [f"https://a.test/{i}" for i in range(11)]}, "At most 10"),
        ({"events": ["crafts", "everything"]}, "events must be"),
        ({}, "nothing to save"),
    ])
    def test_refused(self, client, body, error):
        res = self.put(client, body)
        assert res.status_code == 400 and error in res.get_json()["error"]

    def test_allowed_hosts(self, client, monkeypatch):
        monkeypatch.setattr(config, "WEBHOOK_HOSTS", {"discord.com"})
        res = self.put(client, {"urls": ["https://evil.test/x"]})
        assert res.get_json()["error"] == "This server only posts to discord.com, not evil.test."
        res = self.put(client, {"urls": ["https://Discord.com/api/webhooks/1/a"]})
        assert res.status_code == 200 and res.get_json()["hosts"] == ["discord.com"]

    def test_url_no_longer_allowed_is_dropped(self, client, monkeypatch):
        self.put(client, {"urls": ["https://hooks.example.test/a", "https://discord.com/api/webhooks/1/a"]})
        monkeypatch.setattr(config, "WEBHOOK_HOSTS", {"discord.com"})
        assert webhooks.settings()["urls"] == ["https://discord.com/api/webhooks/1/a"]

    def test_environment_wins_and_hides_urls(self, client, monkeypatch):
        self.put(client, {"urls": ["https://hooks.example.test/page"]})
        monkeypatch.setattr(config, "WEBHOOK_URLS", ["https://hooks.example.test/env-secret"])
        data = client.get("/api/admin/webhooks").get_json()
        assert data == {"urls": [], "count": 1, "events": ["autocraft", "crafts"], "environment": True, "hosts": []}
        res = client.put("/api/admin/webhooks", json={"urls": []})
        assert res.status_code == 400 and "WEBHOOK_URLS" in res.get_json()["error"]

    def test_admins_only(self, client):
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/webhooks").status_code == 403
        assert client.put("/api/admin/webhooks", json={"urls": []}).status_code == 403


def test_background_delivery(sink, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_EVENTS", {"crafts"})
    webhooks.craft_ended("Gear", None, "incomplete", None)
    webhooks.wait_idle()
    [(_, body)] = sink.received
    assert body["title"] == "Craft stopped" and body["event"] == "crafts"
