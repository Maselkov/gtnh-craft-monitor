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
        assert c["services"] == [{**s, "restarts": 0, "gave_up": False} for s in HEARTBEAT["services"]]
        assert (c["free_memory"], c["total_memory"]) == (1000, 4000)
        assert c["command"] is None

    def test_junk_fields_are_dropped(self, client, api_headers):
        beat(client, api_headers, version=5, components=[], services={"x": 1}, free_memory="lots")
        login_as(client, "root", role="admin")
        [c] = computers(client)["computers"]
        assert (c["version"], c["components"], c["services"], c["free_memory"]) == (None, [], [], None)

    def test_auto_restarts_reported(self, client, api_headers):
        beat(client, api_headers, services=[{"name": "power_monitor", "running": False, "restarts": 3, "gave_up": True}])
        login_as(client, "root", role="admin")
        [c] = computers(client)["computers"]
        assert c["services"] == [{"name": "power_monitor", "running": False, "restarts": 3, "gave_up": True}]

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

    def test_reboot_is_followed_until_the_computer_is_back(self, client, api_headers, monkeypatch):
        beat(client, api_headers, uptime=5000)
        login_as(client, "root", role="admin")
        command(client, "reboot")
        cmd_id = beat(client, api_headers, uptime=5060)["id"]
        client.post(f"/api/agent/commands/{cmd_id}/result",
                    json={"ok": True, "message": "Rebooting.", "rebooting": True}, headers=api_headers)
        cmd = computers(client)["computers"][0]["command"]
        assert (cmd["status"], cmd["awaiting_boot"], cmd["back_at"]) == ("done", True, None)
        assert command(client, "update", "main").get_json()["error"] == "this computer already has a command waiting"
        beat(client, api_headers, uptime=5070)  # a heartbeat sent before the reboot
        assert computers(client)["computers"][0]["command"]["awaiting_boot"] is True
        beat(client, api_headers, uptime=4)
        cmd = computers(client)["computers"][0]["command"]
        assert cmd["awaiting_boot"] is False and cmd["back_at"] and cmd["ok"] is True
        monkeypatch.setattr(agents, "COMMAND_SHOWN_SECONDS", -1)
        assert computers(client)["computers"][0]["command"] is None
        assert command(client, "reboot").status_code == 200

    def test_old_agents_reboot_is_read_from_the_message(self, client, api_headers):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        command(client, "update", "release")
        cmd_id = beat(client, api_headers)["id"]
        client.post(f"/api/agent/commands/{cmd_id}/result",
                    json={"ok": True, "message": "Updated v1.0.0 -> v1.2.0, rebooting.", "version": "v1.2.0"},
                    headers=api_headers)
        assert computers(client)["computers"][0]["command"]["awaiting_boot"] is True

    def test_no_reboot_no_waiting_and_hidden_later(self, client, api_headers, monkeypatch):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        command(client, "update", "release")
        cmd_id = beat(client, api_headers)["id"]
        client.post(f"/api/agent/commands/{cmd_id}/result",
                    json={"ok": True, "message": "Already up to date.", "rebooting": False}, headers=api_headers)
        assert computers(client)["computers"][0]["command"]["awaiting_boot"] is None
        monkeypatch.setattr(agents, "COMMAND_SHOWN_SECONDS", -1)
        assert computers(client)["computers"][0]["command"] is None

    def test_failures_stay_shown(self, client, api_headers, monkeypatch):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        command(client, "update", "release")
        cmd_id = beat(client, api_headers)["id"]
        client.post(f"/api/agent/commands/{cmd_id}/result", json={"ok": False, "message": "HTTP 404"},
                    headers=api_headers)
        monkeypatch.setattr(agents, "COMMAND_SHOWN_SECONDS", -1)
        assert computers(client)["computers"][0]["command"]["message"] == "HTTP 404"

    def test_not_coming_back_turns_failed(self, client, api_headers, monkeypatch):
        beat(client, api_headers)
        login_as(client, "root", role="admin")
        command(client, "reboot")
        cmd_id = beat(client, api_headers)["id"]
        client.post(f"/api/agent/commands/{cmd_id}/result", json={"ok": True, "message": "Rebooting."},
                    headers=api_headers)
        monkeypatch.setattr(agents, "BOOT_TIMEOUT_SECONDS", -1)
        cmd = computers(client)["computers"][0]["command"]
        assert (cmd["ok"], cmd["awaiting_boot"]) == (False, False)
        assert "come back" in cmd["message"]

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


class TestConnect:
    def test_ping_checks_the_api_key(self, client, api_headers):
        assert client.post("/api/agent/ping", json={}).status_code == 401
        assert client.post("/api/agent/ping", json={}, headers={"X-API-Key": "nope"}).status_code == 401
        assert client.post("/api/agent/ping", json={}, headers=api_headers).get_json() == {"ok": True}

    def test_admins_get_the_key_and_public_url(self, client, api_key, monkeypatch):
        from gcm import config

        monkeypatch.setattr(config, "PUBLIC_URL", "https://gtnh.example.com")
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/connect").status_code == 403
        login_as(client, "root", role="admin")
        res = client.get("/api/admin/connect")
        assert res.get_json() == {"api_key": api_key, "public_url": "https://gtnh.example.com"}
        assert res.headers["Cache-Control"] == "no-store"


class TestSettings:
    def test_defaults_saved_and_admins_only(self, client, api_headers):
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/agent-settings").status_code == 403
        assert client.put("/api/admin/agent-settings", json={"auto_update": True}).status_code == 403
        login_as(client, "root", role="admin")
        assert client.get("/api/admin/agent-settings").get_json() == {
            "auto_update": False, "auto_restart": True, "server_version": None,
        }
        res = client.put("/api/admin/agent-settings", json={"auto_restart": False})
        assert res.get_json()["auto_restart"] is False
        agents.reset()  # stored, not in memory
        assert client.get("/api/admin/agent-settings").get_json()["auto_restart"] is False

    @pytest.mark.parametrize("body", [{}, {"auto_update": "yes"}, {"run": True}, [True]])
    def test_bad_bodies(self, client, body):
        login_as(client, "root", role="admin")
        assert client.put("/api/admin/agent-settings", json=body).status_code == 400

    def test_heartbeat_passes_auto_restart_along(self, client, api_headers):
        res = client.post("/api/agent/heartbeat", json=HEARTBEAT, headers=api_headers).get_json()
        assert res["auto_restart"] is True
        login_as(client, "root", role="admin")
        client.put("/api/admin/agent-settings", json={"auto_restart": False})
        res = client.post("/api/agent/heartbeat", json=HEARTBEAT, headers=api_headers).get_json()
        assert res["auto_restart"] is False


class TestAutoUpdate:
    @pytest.fixture
    def server_at(self, client, monkeypatch):
        from gcm import config

        def at(version, auto_update=True):
            monkeypatch.setattr(config, "SERVER_VERSION", version)
            login_as(client, "root", role="admin")
            client.put("/api/admin/agent-settings", json={"auto_update": auto_update})
            computers(client)  # fetches the latest release (v1.2.0)
        return at

    def test_behind_computer_updated_once(self, client, api_headers, server_at):
        server_at("v1.2.0")
        cmd = beat(client, api_headers)
        assert (cmd["action"], cmd["ref"]) == ("update", "release")
        c = computers(client)["computers"][0]["command"]
        assert (c["auto"], c["status"]) == (True, "sent")
        client.post(f"/api/agent/commands/{cmd['id']}/result", json={"ok": False, "message": "HTTP 404"},
                    headers=api_headers)
        assert beat(client, api_headers) is None  # not retried

    @pytest.mark.parametrize("version,auto_update,computer", [
        ("v1.2.0", False, "v1.0.0"),  # off
        ("v1.1.3", True, "v1.0.0"),   # the latest release isn't this server's
        (None, True, "v1.0.0"),       # run from source
        ("main", True, "v1.0.0"),
        ("v1.2.0", True, "v1.2.0"),   # up to date
        ("v1.2.0", True, "main"),
    ])
    def test_left_alone(self, client, api_headers, server_at, version, auto_update, computer):
        server_at(version, auto_update)
        assert beat(client, api_headers, version=computer) is None

    def test_not_while_another_command_is_under_way(self, client, api_headers, server_at):
        beat(client, api_headers)
        server_at("v1.2.0", False)
        command(client, "reboot")
        client.put("/api/admin/agent-settings", json={"auto_update": True})
        assert beat(client, api_headers)["action"] == "reboot"
        assert beat(client, api_headers) is None  # still awaiting the result

    def test_heartbeat_never_waits_on_github(self, client, api_headers, github, monkeypatch):
        from gcm import config

        monkeypatch.setattr(config, "SERVER_VERSION", "v1.2.0")
        login_as(client, "root", role="admin")
        client.put("/api/admin/agent-settings", json={"auto_update": True})
        started = []
        monkeypatch.setattr(agents, "_fetch_in_background", lambda: started.append(1))
        assert beat(client, api_headers) is None  # nothing cached yet
        assert beat(client, api_headers) is None
        assert started == [1] and github == []  # one fetch at a time, and not in the heartbeat
        agents.latest_release()
        assert beat(client, api_headers)["action"] == "update"


class TestScriptsNotice:
    # Computers whose scripts are older than this server's: only a release
    # that changed the scripts moves config.SCRIPTS_VERSION.
    @pytest.fixture
    def scripts_at(self, client, monkeypatch):
        from gcm import config

        def at(version, server=None, auto_update=False):
            monkeypatch.setattr(config, "SCRIPTS_VERSION", version)
            monkeypatch.setattr(config, "SERVER_VERSION", server)
            login_as(client, "root", role="admin")
            client.put("/api/admin/agent-settings", json={"auto_update": auto_update})
            computers(client)  # fetches the latest release (v1.2.0)
        return at

    def notice(self, client):
        return client.get("/api/admin/scripts-notice").get_json()

    def test_admins_only(self, client):
        assert client.get("/api/admin/scripts-notice").status_code == 403
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/scripts-notice").status_code == 403

    def test_a_computer_behind_is_named(self, client, api_headers, scripts_at):
        beat(client, api_headers)
        scripts_at("v1.1.0")
        assert self.notice(client) == {"version": "v1.1.0", "computers": ["comp-1"]}
        [c] = computers(client)["computers"]
        assert c["scripts_behind"] is True and computers(client)["scripts_version"] == "v1.1.0"

    @pytest.mark.parametrize("scripts,computer", [
        ("v1.0.0", "v1.0.0"),  # the release since only changed the server
        ("v1.0.0", "v1.1.0"),
        ("v1.1.0", "main"),
        ("v1.1.0", None),
        (None, "v1.0.0"),      # run from source
    ])
    def test_nothing_to_say(self, client, api_headers, scripts_at, scripts, computer):
        beat(client, api_headers, version=computer)
        scripts_at(scripts)
        assert self.notice(client) == {}

    def test_not_while_a_command_is_under_way(self, client, api_headers, scripts_at):
        beat(client, api_headers)
        scripts_at("v1.1.0")
        command(client, "update", "release")
        assert self.notice(client) == {}

    def test_not_for_an_offline_computer(self, client, api_headers, scripts_at):
        beat(client, api_headers)
        agents._agents["comp-1"]["last_seen"] -= agents.OFFLINE_AFTER_SECONDS + 1
        scripts_at("v1.1.0")
        assert self.notice(client) == {}

    def test_not_while_auto_update_will_do_it(self, client, api_headers, scripts_at):
        beat(client, api_headers)
        scripts_at("v1.1.0", server="v1.2.0", auto_update=True)
        assert self.notice(client) == {}
        # Its one try fails: now it's up to the admin.
        cmd = beat(client, api_headers)
        client.post(f"/api/agent/commands/{cmd['id']}/result", json={"ok": False, "message": "HTTP 404"},
                    headers=api_headers)
        assert self.notice(client)["computers"] == ["comp-1"]

    def test_auto_update_on_but_held_back(self, client, api_headers, scripts_at):
        # A newer release than this server is out, so auto_update waits.
        beat(client, api_headers)
        scripts_at("v1.1.0", server="v1.1.3", auto_update=True)
        assert self.notice(client)["computers"] == ["comp-1"]
