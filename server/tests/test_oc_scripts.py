"""The real oc/*.lua scripts talking to the real routes, through the
harness in oc/tests/ (see oc_game.py). These catch the two sides
drifting apart: a field renamed on one side only, a response the Lua
can't read. Script logic on its own is tested in oc/tests/*_test.lua."""

import pytest

from conftest import login_as
from oc_game import OcGame, find_lua

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
