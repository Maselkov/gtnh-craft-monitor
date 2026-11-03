"""The real oc/*.lua scripts talking to the real routes, through the
harness in oc/tests/ (see oc_game.py). These catch the two sides
drifting apart: a field renamed on one side only, a response the Lua
can't read. Script logic on its own is tested in oc/tests/*_test.lua."""

import pytest

from conftest import login_as
from gcm import agents, config, nbt, state
from gcm.routes.crafts import name_cpus
from nbt_fixtures import tag_hex
from oc_game import OcGame, lua_string, find_lua

pytestmark = pytest.mark.skipif(find_lua() is None, reason="no Lua interpreter installed")

GEAR = "ae2.stack('gregtech:gt.metaitem.01', 'Titanium Gear', 1, { damage = 32600 })"


@pytest.fixture()
def game(flask_app, api_key):
    g = OcGame(flask_app.test_client(), api_key)
    yield g
    g.close()


def test_craft_report_reaches_the_crafts_endpoint(game, client):
    game.lua(
        f"""
        me = ae2.new(env)
        me:add_cpu({{ name = "CPU 1" }})
        me:add_cpu({{ name = "CPU 2" }})
        local gear = {GEAR}
        gear.size = 16
        me:start_job("CPU 1", {{ output = gear, duration = 100 }})
        env:start_service("craft_monitor")
        """
    )
    game.run(1)

    data = client.get("/api/crafts").get_json()
    assert data["source"] == "me_controller"
    jobs = {j["name"]: j for j in data["jobs"]}
    assert jobs["CPU 1"]["busy"] is True
    assert jobs["CPU 1"]["final_output"] == "Titanium Gear"
    assert jobs["CPU 1"]["pending"][0]["size"] == 16
    assert jobs["CPU 2"]["busy"] is False
    assert data["stale_after_seconds"] == config.STALE_AFTER_SECONDS  # 3 x 5s is under the default
    assert state.crafts["poll_seconds"] == 5


def test_the_script_and_the_server_make_up_cpu_names_alike(game, client):
    # Both make up a name for a CPU without one of its own (craft_monitor
    # .lua's get_cpus(), routes/crafts.py's name_cpus()): the server for
    # scripts that still send the empty or shared name. They must agree,
    # or a cancel from the page can't find the CPU.
    cpus = [("", 69632, 1), ("", 69632, 4), ("Big", 69632, 1), ("", 1048576, 1), ("4k #1", 4096, 1),
            ("", 4096, 1), ("", 4096, 2), ("", 1e300, 1), ("", 1536 * 1024, 1), ("", 0, 1),
            ("Main", 65536, 1), ("Main", 65536, 8), ("Main", 4096, 1), ("Main 4k #1", 4096, 1)]
    added = "\n".join(
        f'me:add_cpu({{ name = "tmp", storage = {storage!r}, coprocessors = {co} }}).name = {lua_string(name)}'
        for name, storage, co in cpus
    )
    game.lua(f"""
        me = ae2.new(env)
        {added}
        env:start_service("craft_monitor")
        """)
    game.run(1)
    from_script = [j["name"] for j in client.get("/api/crafts").get_json()["jobs"]]
    from_server = [j["name"] for j in name_cpus(
        [{"name": name, "storage": storage, "coprocessors": co} for name, storage, co in cpus])]
    assert from_script == from_server == [
        "68k #2", "68k #1", "Big", "1M #1", "4k #1", "4k #3", "4k #2", "0k #1", "1536k #1", "0k #2",
        "Main 64k #2", "Main 64k #1", "Main 4k #2", "Main 4k #1"]


def test_craft_request_round_trip(game, client):
    game.lua(
        f"""
        me = ae2.new(env)
        me:add_cpu({{ name = "CPU 1" }})
        me:add_craftable({{ stack = {GEAR}, plan_seconds = 2, duration = 30 }})
        env:start_service("craft_monitor")
        """
    )
    game.run(1)
    login_as(client, "usr_operator", role="operator")
    res = client.post(
        "/api/craft/request",
        json={"label": "Titanium Gear", "mod": "gregtech", "internal": "gt.metaitem.01",
              "damage": 32600, "amount": 4},
    )
    assert res.status_code == 200

    game.run(5)
    # Accepted: the request has left the queue for a pin on its CPU.
    assert client.get("/api/craft/requests").get_json()["requests"] == []
    assert client.get("/api/pins").get_json()["pins"] == ["CPU 1"]
    assert game.lua("return me:cpu('CPU 1').job.output.size") == 4

    game.run(40)
    assert ("POST", f"/api/craft/requests/{res.get_json()['id']}/outcome", 200) in game.requests


def test_failed_craft_request_shows_its_reason(game, client):
    game.lua(
        f"""
        me = ae2.new(env)
        me:add_cpu({{ name = "CPU 1" }})
        me:add_craftable({{ stack = {GEAR}, fail = "missing 64x Titanium Plate" }})
        env:start_service("craft_monitor")
        """
    )
    login_as(client, "usr_operator", role="operator")
    client.post(
        "/api/craft/request",
        json={"label": "Titanium Gear", "mod": "gregtech", "internal": "gt.metaitem.01",
              "damage": 32600, "amount": 1},
    )
    game.run(5)
    [req] = client.get("/api/craft/requests").get_json()["requests"]
    assert req["status"] == "failed"
    assert req["reason"] == "missing 64x Titanium Plate"


def test_cancel_round_trip(game, client):
    game.lua(
        f"""
        me = ae2.new(env)
        me:add_cpu({{ name = "CPU 1" }})
        me:start_job("CPU 1", {{ output = {GEAR}, duration = 100 }})
        env:start_service("craft_monitor")
        """
    )
    game.run(1)
    login_as(client, "usr_operator", role="operator")
    res = client.post("/api/craft/cancel", json={"cpu_name": "CPU 1"})
    assert res.status_code == 200
    cancel_id = res.get_json()["id"]

    game.run(3)
    assert game.lua("return me:cpu('CPU 1').job == nil") is True
    result = client.get(f"/api/craft/cancel/{cancel_id}").get_json()
    assert result["success"] is True


def test_power_reading_lands(game, client):
    game.lua(
        """
        gt.new(env, { stored = 4e9, capacity = 1e10, trend = { avg_eu_in_5s = 1200, avg_eu_out_5s = 800 } })
        env:start_service("power_monitor")
        """
    )
    game.run(1)
    latest = client.get("/api/power").get_json()["latest"]
    assert latest["stored"] == 4e9
    assert latest["capacity"] == 1e10
    assert latest["avg_eu_in_5s"] == 1200
    assert state.power_poll_seconds == {"": 60}


def test_wireless_network_lands_as_its_own_machine(game, client):
    game.lua(
        """
        gt.new(env, { stored = 4e9, capacity = 1e10, wireless_eu = '12345678901234567890123' })
        env:start_service("power_monitor")
        """
    )
    game.run(1)
    data = client.get("/api/power").get_json()
    assert [m["name"] for m in data["machines"]] == ["", "Wireless"]
    wireless = client.get("/api/power?machine=Wireless").get_json()["latest"]
    assert wireless["stored"] == pytest.approx(1.2345678901234568e22)
    assert wireless["capacity"] == 0


def test_network_scan_lands(game, client):
    game.write_file("/home/item_catalog.txt", "testmod:a\ntestmod:b\n")
    game.lua(
        """
        me = ae2.new(env)
        me.items = {
          ae2.stack("testmod:a", "Thing A", 5),
          ae2.stack("testmod:b", "Thing B", 7, { isCraftable = true }),
        }
        me.fluids = { ae2.fluid("molten.neutronium", "Molten Neutronium", 144000) }
        env:start_service("network_browser")
        """
    )
    game.run(30)
    assert all(status == 200 for _, _, status in game.requests)
    data = client.get("/api/network").get_json()
    assert data["in_progress"] is False
    sizes = {i["name"]: i["size"] for i in data["items"]}
    assert sizes == {"Thing A": 5, "Thing B": 7, "Molten Neutronium": 144000}
    # No ME Interface Terminal in this game, so the page suggests one.
    assert data["needs_terminal"] is True
    assert state.network["scan_interval_seconds"] == 600


def test_a_label_with_control_characters_is_sent_as_valid_json(game, client, caplog):
    # A raw one made the server refuse every batch holding it, so the
    # scan never completed (gcm/lenient_json.py now reads such a batch
    # anyway, and logs it).
    game.write_file("/home/item_catalog.txt", "testmod:a\n")
    game.lua(
        """
        me = ae2.new(env)
        me.items = { ae2.stack("testmod:a", "Odd\\0\\7\\27\\31 Thing", 5) }
        env:start_service("network_browser")
        """
    )
    game.run(30)
    assert all(status == 200 for _, _, status in game.requests)
    names = [i["name"] for i in client.get("/api/network").get_json()["items"]]
    assert names == ["Odd\x00\x07\x1b\x1f Thing"]
    assert "leniently" not in caplog.text


def test_a_scan_past_the_item_limit_stops_without_finishing(game, client, monkeypatch):
    monkeypatch.setattr(config, "MAX_SCAN_ITEMS", 2)
    game.write_file("/home/item_catalog.txt", "testmod:a\ntestmod:b\ntestmod:c\n")
    game.lua(
        """
        me = ae2.new(env)
        me.items = {
          ae2.stack("testmod:a", "Thing A", 5),
          ae2.stack("testmod:b", "Thing B", 7),
          ae2.stack("testmod:c", "Thing C", 9),
        }
        env:start_service("network_browser")
        """
    )
    game.run(30)
    paths = [url for _, url, _ in game.requests]
    assert sum(p.endswith("/api/network/scan/batch") for p in paths) == 1
    assert not any(p.endswith("/api/network/scan/finish") for p in paths)
    data = client.get("/api/network").get_json()
    assert data["items"] == [] and data["in_progress"] is False and data["crash"] is None
    assert "MAX_SCAN_ITEMS" in state.network["last_rejected"]["reason"]


def test_watched_items_are_checked_between_scans(game, client):
    # Two materials sharing one GT id; an alert on one, then the game's
    # stock of it drops between full scans.
    game.write_file("/home/item_catalog.txt", "gregtech:gt.metaitem.01\n")
    game.lua(
        f"""
        me = ae2.new(env)
        gear = {GEAR}
        gear.size = 50
        me.items = {{ gear, ae2.stack("gregtech:gt.metaitem.01", "Iron Dust", 900, {{ damage = 2032 }}) }}
        me.fluids = {{ ae2.fluid("water", "Water", 64000) }}
        me.essentia = {{ ae2.essentia("ordo", "Ordo", 500), ae2.essentia("aer", "Aer", 12) }}
        env:start_service("network_browser")
        """
    )
    game.run(30)
    login_as(client, "alice")
    for item in (
        {"label": "Titanium Gear", "mod": "gregtech", "internal": "gt.metaitem.01", "damage": 32600},
        {"label": "Water", "internal": "water", "kind": "fluid"},
        {"label": "Ordo", "internal": "ordo", "kind": "essentia"},
    ):
        assert client.post("/api/stock/alert", json={**item, "below": 10}).status_code == 200
    game.lua("gear.size = 3; me.fluids[1].amount = 5; me.essentia[1].amount = 4")
    game.run(60)

    assert all(status == 200 for _, _, status in game.requests)
    levels = [r for r in game.requests if r[1].endswith("/api/network/levels")]
    assert levels
    sizes = {i["name"]: i["size"] for i in client.get("/api/network").get_json()["items"]}
    assert sizes == {"Titanium Gear": 3, "Iron Dust": 900, "Water": 5, "Ordo": 4, "Aer": 12}
    assert {a["label"]: a["low"] for a in client.get("/api/stock/rules").get_json()["alerts"]} == {
        "Titanium Gear": True, "Water": True, "Ordo": True,
    }


def test_a_crashed_scan_is_reported_and_scanning_starts_over(game, client):
    game.write_file("/home/item_catalog.txt", "testmod:a\n")
    game.lua(
        """
        me = ae2.new(env)
        me.items = { ae2.stack("testmod:a", "Thing A", 5) }
        local real, crashed = me.proxy.getItemsInNetworkById, false
        me.proxy.getItemsInNetworkById = function(ids)
          if not crashed then
            crashed = true
            return setmetatable({}, { __len = function() error("not enough memory") end })
          end
          return real(ids)
        end
        env:start_service("network_browser")
        """
    )
    game.run(10)
    data = client.get("/api/network").get_json()
    assert data["in_progress"] is False
    assert "not enough memory" in data["crash"]["error"]

    game.run(60)  # CRASH_RESTART_SECONDS, then a full scan
    data = client.get("/api/network").get_json()
    assert data["crash"] is None
    assert {i["name"]: i["size"] for i in data["items"]} == {"Thing A": 5}


def test_pattern_scan_lands_with_crafting_counts(game, client):
    # Logs -> 4 planks: OC reports the sizes as 0, the pattern's NBT
    # has the counts.
    def stack(item_id, count):
        return [("id", nbt.SHORT, item_id), ("Count", nbt.BYTE, count), ("Damage", nbt.SHORT, 0)]

    planks_nbt = tag_hex([
        ("in", nbt.LIST, (nbt.COMPOUND, [stack(17, 1)] + [[]] * 8)),
        ("out", nbt.LIST, (nbt.COMPOUND, [stack(5, 4)])),
        ("crafting", nbt.BYTE, 1),
    ])
    game.write_file("/home/item_catalog.txt", "testmod:a\n")
    game.lua(
        f"""
        me = ae2.new(env)
        me.items = {{ ae2.stack("testmod:a", "Thing A", 5) }}
        local planks = terminal.pattern(
          {{ [1] = terminal.item("minecraft:log", "Oak Wood", 0, {{ id = 17 }}) }},
          {{ [1] = terminal.item("minecraft:planks", "Oak Wood Planks", 0, {{ id = 5 }}) }},
          {{ crafting = true, tag = ("{planks_nbt}"):gsub("..", function(h) return string.char(tonumber(h, 16)) end) }})
        local melt = terminal.pattern(
          {{ [1] = terminal.item("gregtech:gt.metaitem.01", "Neodymium Ingot", 16, {{ damage = 11067 }}) }},
          {{ [1] = terminal.fluid("molten.neodymium", "Molten Neodymium", 2304) }})
        terminal.new(env, {{
          terminal.interface("Molecular Assembler", -1413, 57, -123, {{ planks }}),
          terminal.interface("Fluid Extractor p1", 1, 2, 3, {{ melt }}),
        }})
        env:start_service("network_browser")
        """
    )
    game.run(30)
    assert all(status == 200 for _, _, status in game.requests)
    login_as(client, "alice")
    assert client.get("/api/network").get_json()["needs_terminal"] is False
    data = client.get("/api/network/patterns").get_json()
    assert data["summary"] == {"patterns": 2, "crafting": 1, "processing": 1, "inexact": 0, "providers": 2}
    made = {
        p["provider"]["name"]: (
            [(e["name"], e["size"]) for e in p["inputs"]],
            [(e["name"], e["size"], e["kind"]) for e in p["outputs"]],
        )
        for p in data["patterns"]
    }
    assert made == {
        "Molecular Assembler": ([("Oak Wood", 1)], [("Oak Wood Planks", 4, "item")]),
        "Fluid Extractor p1": ([("Neodymium Ingot", 16)], [("Molten Neodymium", 2304, "fluid")]),
    }


def test_a_refused_pattern_scan_says_why_on_the_status_card(game, client):
    game.write_file("/home/item_catalog.txt", "testmod:a\n")
    game.lua(
        """
        me = ae2.new(env)
        me.items = { ae2.stack("testmod:a", "Thing A", 5) }
        local melt = terminal.pattern(
          { [1] = terminal.item("gregtech:gt.metaitem.01", "Neodymium Ingot", 16, { damage = 11067 }) },
          { [1] = terminal.fluid("molten.neodymium", "Molten Neodymium", 2304) })
        local term = terminal.new(env, {
          terminal.interface("A", 0, 0, 0, { melt }),
          terminal.interface("B", 0, 0, 1, { melt }),
        })
        term.fail_list_at = 2
        env:start_service("network_browser")
        """
    )
    game.run(30)
    login_as(client, "root", role="admin")
    feeds = client.get("/api/admin/health").get_json()["feeds"]
    patterns = next(f for f in feeds if f["key"] == "patterns")
    assert patterns["rejected"]["reason"].startswith(
        "incomplete (1/1 chunks, 1 errors), last: pattern list failed after 1 interfaces: "
    )


def test_several_power_machines_land_separately(game, client, api_key):
    game.lua(
        """
        env:write_config({ SERVER_URL = 'http://gcm.test', API_KEY = '%s', power_monitor = { MACHINES = {
          { address = 'lsc' }, { address = 'bat', name = 'Batteries' },
        } } })
        gt.new(env, { stored = 4e9, capacity = 1e10, address = 'lsc' })
        gt.new(env, { stored = 7, capacity = 10, address = 'bat' })
        env:start_service("power_monitor")
        """ % api_key
    )
    game.run(1)
    data = client.get("/api/power").get_json()
    assert [m["name"] for m in data["machines"]] == ["", "Batteries"]
    assert data["latest"]["stored"] == 4e9
    assert client.get("/api/power?machine=Batteries").get_json()["latest"]["stored"] == 7


def test_agent_reports_and_reboots_on_command(game, client, monkeypatch):
    monkeypatch.setattr(agents, "latest_release", lambda: ("v1.0.0", None))  # not GitHub
    game.write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" }, services = { "power_monitor" } }')
    game.lua('env:start_service("gcm_agent")')
    game.run(1)
    login_as(client, "root", role="admin")
    [computer] = client.get("/api/admin/agents").get_json()["computers"]
    assert (computer["address"], computer["version"]) == ("computer-0000", "v1.0.0")
    assert computer["services"] == [{"name": "power_monitor", "running": False, "restarts": 0, "gave_up": False,
                                     "stopped_on_purpose": False, "last_error": None}]
    assert computer["poll_seconds"] == 60

    assert client.post("/api/admin/agents/computer-0000/command", json={"action": "reboot"}).status_code == 200
    game.run(61)
    assert game.lua("return env.shutdown and env.shutdown.reboot") is True
    cmd = client.get("/api/admin/agents").get_json()["computers"][0]["command"]
    assert (cmd["status"], cmd["ok"], cmd["message"], cmd["awaiting_boot"]) == ("done", True, "Rebooting.", True)


def test_agent_restarts_a_crashed_script_unless_turned_off(game, client, monkeypatch):
    monkeypatch.setattr(agents, "latest_release", lambda: ("v1.0.0", None))
    game.write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" }, services = { "power_monitor" } }')
    game.write_file("/etc/rc.cfg", 'enabled = {"power_monitor", "gcm_agent"}')
    game.lua('gt.new(env); power = env:start_service("power_monitor"); env:start_service("gcm_agent")')

    def crash():
        game.lua('for i, th in ipairs(env.threads) do if i ~= 2 then th.dead = true end end')

    game.run(1)
    crash()
    game.run(185)  # past the boot grace, then one more heartbeat
    login_as(client, "root", role="admin")
    [service] = client.get("/api/admin/agents").get_json()["computers"][0]["services"]
    assert (service["running"], service["restarts"]) == (True, 1)

    client.put("/api/admin/agent-settings", json={"auto_restart": False})
    game.run(60)  # the agent hears about it
    crash()
    game.run(125)
    assert game.lua("return power.is_running()") is False


def test_agent_reports_what_a_script_died_of(game, client, monkeypatch):
    monkeypatch.setattr(agents, "latest_release", lambda: ("v1.0.0", None))
    game.write_file("/etc/gcm.cfg", 'return { version = "v1.0.0", components = { "power" }, services = { "power_monitor" } }')
    # A sensor line that isn't text makes power_monitor's loop throw.
    game.lua('gt.new(env, { sensor = { {} } }); env:start_service("power_monitor")')
    game.lua("env:run({ seconds = 1, allow_errors = true })")
    game.lua('env:start_service("gcm_agent")')
    game.run(1)
    login_as(client, "root", role="admin")
    [service] = client.get("/api/admin/agents").get_json()["computers"][0]["services"]
    assert service["running"] is False and service["stopped_on_purpose"] is False
    assert "attempt to" in service["last_error"]["message"]


def test_install_checks_the_key_with_the_server(game, api_key):
    # A fresh computer, the way "Connect a computer" sets one up; no to
    # starting on boot and to rebooting.
    game.lua('os.remove(env:path("/home/config.lua")); env:mkdir("/etc"); env:mkdir("/usr/bin"); env.stdin = { "n", "n" }')

    def install(key):
        game.lua(
            'env.output = {}; local gcm = assert(loadfile(require("oc_env").OC_DIR .. "/gcm.lua", "t", env.globals));'
            'gcm("install", "power", "--server=http://gcm.test", "--api-key=" .. %s)' % lua_string(key)
        )
        return game.lua('return env:read_file("/home/config.lua")')

    config = install(api_key)
    assert ("POST", "/api/agent/ping", 200) in game.requests
    assert '"%s"' % api_key in config
    assert game.lua('return env:printed("The server accepted the API key.")') is True

    game.lua('os.remove(env:path("/home/config.lua")); env.stdin = { "n", "n" }')
    install("not-the-key")
    assert ("POST", "/api/agent/ping", 401) in game.requests
    assert game.lua('return env:printed("The server refused the API key.")') is True
