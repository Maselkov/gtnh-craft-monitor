import pytest

from gcm import agents, gamedata
from conftest import login_as

HEARTBEAT = {
    "address": "comp-1", "version": "v1.0.0", "components": ["craft", "power"],
    "services": [{"name": "craft_monitor", "running": True}, {"name": "power_monitor", "running": False}],
    "free_memory": 1000, "total_memory": 4000, "uptime": 12.5,
}


@pytest.fixture(autouse=True)
def github(monkeypatch):
    """GitHub's tag list for the scripts' repository."""
    calls = []

    def fake(url):
        calls.append(url)
        return [{"name": "v1.0.0"}, {"name": "v1.2.0"}, {"name": "v1.10.0-rc1"}, {"name": "v1.1.3"}]
    monkeypatch.setattr(gamedata, "github_json", fake)
    return calls


def beat(client, api_headers, **extra):
    res = client.post("/api/agent/heartbeat", json={**HEARTBEAT, **extra}, headers=api_headers)
    assert res.status_code == 200
    return res.get_json()["command"]


def computers(client):
    return client.get("/api/admin/agents").get_json()


def command(client, action, ref=None, address="comp-1"):
    return client.post(f"/api/admin/agents/{address}/command", json={"action": action, "ref": ref})


class TestHeartbeat:
    def test_listed_for_admins_with_update_available(self, client, api_headers):
        assert beat(client, api_headers) is None
        login_as(client, "root", role="admin")
        data = computers(client)
        assert data["latest_release"] == "v1.2.0"  # highest vX.Y.Z, pre-releases skipped
        [c] = data["computers"]
        assert (c["address"], c["version"], c["behind"], c["offline"]) == ("comp-1", "v1.0.0", True, False)
        assert c["services"] == HEARTBEAT["services"]
        assert (c["free_memory"], c["total_memory"]) == (1000, 4000)
        assert c["command"] is None

    def test_junk_fields_are_dropped(self, client, api_headers):
        beat(client, api_headers, version=5, components=[], services={"x": 1}, free_memory="lots")
        login_as(client, "root", role="admin")
        [c] = computers(client)["computers"]
        assert (c["version"], c["components"], c["services"], c["free_memory"]) == (None, [], [], None)

    def test_needs_the_api_key_and_an_address(self, client, api_headers):
        assert client.post("/api/agent/heartbeat", json=HEARTBEAT).status_code == 401
        assert client.post("/api/agent/heartbeat", json={"version": "x"}, headers=api_headers).status_code == 400

    def test_offline_after_three_missed_beats(self, client, api_headers, monkeypatch):
        beat(client, api_headers)
        monkeypatch.setattr(agents, "OFFLINE_AFTER_SECONDS", -1)
        login_as(client, "root", role="admin")
        assert computers(client)["computers"][0]["offline"] is True

    def test_main_is_never_behind(self, client, api_headers):
        beat(client, api_headers, version="main")
        login_as(client, "root", role="admin")
        assert computers(client)["computers"][0]["behind"] is False

    def test_latest_release_is_cached_and_errors_shown(self, client, api_headers, github, monkeypatch):
        login_as(client, "root", role="admin")
        computers(client)
        computers(client)
        assert len(github) == 1
        agents.reset()

        def down(url):
            raise OSError("rate limited")
        monkeypatch.setattr(gamedata, "github_json", down)
        data = computers(client)
        assert data["latest_release"] is None and "rate limited" in data["latest_error"]


class TestCommands:
    def test_update_handed_over_once_then_reported(self, client, api_headers):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        cmd_id = command(client, "update", "release").get_json()["id"]
        assert computers(client)["computers"][0]["command"]["status"] == "queued"
        assert beat(client, api_headers) == {"id": cmd_id, "action": "update", "ref": "release"}
        assert beat(client, api_headers) is None
        res = client.post(f"/api/agent/commands/{cmd_id}/result",
                          json={"ok": True, "message": "Updated v1.0.0 -> v1.2.0, rebooting.", "version": "v1.2.0"},
                          headers=api_headers)
        assert res.status_code == 200
        c = computers(client)["computers"][0]
        assert c["command"]["status"] == "done" and c["command"]["ok"] is True
        assert c["command"]["message"] == "Updated v1.0.0 -> v1.2.0, rebooting."
        assert (c["version"], c["behind"]) == ("v1.2.0", False)

    def test_only_for_its_own_computer(self, client, api_headers):
        beat(client, api_headers)
        beat(client, api_headers, address="comp-2")
        login_as(client, "root", role="admin")
        command(client, "reboot", address="comp-2")
        assert beat(client, api_headers) is None
        assert beat(client, api_headers, address="comp-2")["action"] == "reboot"

    @pytest.mark.parametrize("action,ref", [
        ("update", "https://evil.test/x"), ("update", None), ("reboot", "main"), ("run", None),
    ])
    def test_only_known_commands(self, client, api_headers, action, ref):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        res = command(client, action, ref)
        assert res.status_code == 400 and res.get_json()["error"] == "unknown command"

    def test_unknown_computer_and_one_at_a_time(self, client, api_headers):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        assert command(client, "reboot", address="nope").get_json()["error"] == "unknown computer"
        assert command(client, "update", "main").status_code == 200
        assert command(client, "reboot").get_json()["error"] == "this computer already has a command waiting"

    def test_unclaimed_command_expires(self, client, api_headers, monkeypatch):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        command(client, "reboot")
        monkeypatch.setattr(agents, "PICKUP_TIMEOUT_SECONDS", -1)
        cmd = computers(client)["computers"][0]["command"]
        assert (cmd["status"], cmd["ok"]) == ("done", False)
        assert "gcm_agent" in cmd["message"]
        assert beat(client, api_headers) is None  # never handed over late

    def test_admins_only(self, client, api_headers):
        beat(client, api_headers)
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/agents").status_code == 403
        assert command(client, "reboot").status_code == 403

    def test_result_for_unknown_command(self, client, api_headers):
        res = client.post("/api/agent/commands/99/result", json={"ok": True}, headers=api_headers)
        assert res.status_code == 404
