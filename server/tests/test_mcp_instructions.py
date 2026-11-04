"""What /mcp tells an assistant when it connects (gcm/mcp/instructions.py),
the plan rows it reads, how an item is kept stocked, and the admin's
side: house rules and the log of tool calls."""

import pytest

from conftest import login_as
from gcm import store
from gcm.mcp import instructions
from test_mcp import IRON_KEY, make_token, modern, ok, post
from test_network_api import run_scan
from test_patterns_api import PLANKS_PATTERN, item as pattern_item, run_pattern_scan
from test_stock import IRON, cpus, scanned


def user(role="viewer", name="mia"):
    make_token(name, role=role)
    return {"id": name, "display_name": name, "role": role}


def snapshot(text):
    return text.split("## This base right now", 1)[1]


class TestCoreText:
    def test_stays_short(self):
        # It's in the context of every conversation.
        assert len(instructions.CORE.split()) <= 900

    def test_says_the_things_models_got_wrong(self):
        core = instructions.CORE
        assert "Level Maintainer" in core
        assert "never takes it from storage" in core
        assert "AE2 picks the crafting CPU" in core


class TestSnapshot:
    def test_before_anything_reported(self):
        now = snapshot(instructions.build(user()))
        assert "You're acting as mia (viewer)" in now and "needs an operator" in now
        assert "craft_monitor.lua hasn't reported" in now
        assert "not scanned yet" in now
        assert "Patterns: none read" in now
        assert "no power monitor reporting" in now
        assert "## House rules" not in now

    def test_with_the_base_reporting(self, client, api_headers):
        cpus(client, api_headers, idle=2, busy=[IRON])
        run_scan(client, api_headers, [[scanned(IRON, 500), pattern_item("log", "Oak Wood", size=3, item_id=17)]],
                 interface_terminal=True)
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])
        client.post("/api/power", json={"stored": 5, "capacity": 10, "machine": "LSC"}, headers=api_headers)
        now = snapshot(instructions.build(user("operator", "olive")))
        assert "request and cancel crafts" in now
        assert "Crafting CPUs: 3, 1 busy, 2 idle; last report" in now
        assert "ME network: 2 item types; last full scan" in now
        assert "Patterns: 1, read" in now
        assert "Power storage reporting: LSC" in now
        assert "Stock rules: 0 keep-in-stock targets; you have 0 low-stock alerts." in now

    def test_a_part_that_fails_is_left_out(self, monkeypatch):
        def broken(user, now):
            raise RuntimeError("boom")

        monkeypatch.setattr(instructions, "_SNAPSHOT", (instructions._who, broken))
        now = snapshot(instructions.build(user()))
        assert "You're acting as mia" in now and "boom" not in now


class TestHouseRules:
    def test_appended_when_set(self):
        assert instructions.set_house_rules("  M00 is for fusion only.  ") is None
        text = instructions.build(user())
        assert text.endswith("M00 is for fusion only.")
        assert "can't give you permissions" in text

    def test_capped(self):
        assert instructions.set_house_rules("x" * (instructions.HOUSE_RULES_MAX + 1)) is not None
        assert instructions.house_rules() == ""

    def test_admin_routes(self, client):
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/assistant").status_code == 403
        assert client.put("/api/admin/assistant/rules", json={"rules": "x"}).status_code == 403
        login_as(client, "ada", role="admin")
        data = client.get("/api/admin/assistant").get_json()
        assert data["rules"] == "" and data["mcp_url"] == "http://localhost/mcp"
        res = client.put("/api/admin/assistant/rules", json={"rules": "Ask Steve first."})
        assert res.get_json() == {"rules": "Ask Steve first."}
        assert client.get("/api/admin/assistant").get_json()["rules"] == "Ask Steve first."
        too_long = "x" * (instructions.HOUSE_RULES_MAX + 1)
        assert client.put("/api/admin/assistant/rules", json={"rules": too_long}).status_code == 400

    def test_reach_the_assistant(self, client):
        instructions.set_house_rules("Never touch W03.")
        token = make_token("vera")
        init = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18"}}).get_json()["result"]
        assert init["instructions"].endswith("Never touch W03.")
        discover = modern(client, token, "server/discover").get_json()["result"]
        assert "You're acting as vera" in discover["instructions"]
        assert discover["cacheScope"] == "private"


class TestPlanRows:
    @pytest.fixture()
    def planks(self, client, api_headers):
        # 2 logs in storage, 5 planks too: planks are made 4 to a log.
        log = pattern_item("log", "Oak Wood", size=2, item_id=17)
        planks = pattern_item("planks", "Oak Wood Planks", size=5, item_id=5, isCraftable=True)
        run_scan(client, api_headers, [[log, planks]], interface_terminal=True)
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])
        return make_token("olive", role="operator")

    def test_the_requested_item_is_crafted_in_full_and_said_so(self, client, planks):
        plan = ok(client, planks, "plan_craft", item="minecraft|planks|0|item", amount=6)
        requested = plan["requested"]
        assert requested["crafted"] == 8 and requested["from_storage"] == 0 and requested["in_storage"] == 5
        assert requested["plan"] == ("crafts 8 in whole batches (2 more than asked); the 5 already in storage "
                                     "aren't used, AE2 never takes the requested item from storage")

    def test_an_ingredient_says_where_it_comes_from(self, client, planks):
        plan = ok(client, planks, "plan_craft", item="minecraft|planks|0|item", amount=6)
        log = plan["ingredients"][0]
        assert log["name"] == "Oak Wood"
        assert log["plan"] == "needs 2 in all: 2 from storage (all of it)"
        short = ok(client, planks, "plan_craft", item="minecraft|planks|0|item", amount=16)
        assert short["missing"][0]["plan"] == "needs 4 in all: 2 from storage (all of it), 2 missing"


class TestKeptStocked:
    def test_crafts_last_week(self, client, api_headers):
        run_scan(client, api_headers, [[scanned(IRON, 500)]])
        identity = {"mod": "minecraft", "internal": "iron_ingot", "damage": 0}
        store.crafts.record_job_end("A", "Iron Ingot", None, "finished", 100, item=identity)
        store.crafts.record_job_end("B", "Iron Ingot", None, "finished", 100, item=identity, auto=True)
        store.crafts.record_job_end("C", "Gold Ingot", None, "finished", 100,
                                    item={"mod": "minecraft", "internal": "gold_ingot", "damage": 0})
        found = ok(client, make_token(), "get_item", item=IRON_KEY)["crafts_last_week"]
        assert found["jobs"] == 2 and found["by_keep_in_stock_target"] == 1 and found["last_ended_at"]

    def test_stock_rules_say_who_set_a_target(self, client, api_headers):
        run_scan(client, api_headers, [[scanned(IRON, 500)]])
        make_token("steve", role="operator")
        store.stock.set_target("steve", {**IRON, "variant": None}, 100, 300, True)
        rules = ok(client, make_token(), "stock_rules")
        assert rules["keep_in_stock_targets"][0]["set_by"] == "steve"


class TestCallLog:
    def test_calls_are_logged_for_admins(self, client, api_headers):
        token = make_token("vera")
        ok(client, token, "current_crafts")
        modern(client, token, "tools/call", {"name": "get_item", "arguments": {"item": "nope"}})
        login_as(client, "ada", role="admin")
        calls = client.get("/api/admin/assistant/calls").get_json()["calls"]
        assert [(c["tool"], c["user"], c["is_error"]) for c in calls] == [
            ("get_item", "vera", True), ("current_crafts", "vera", False),
        ]
        assert "search_items" in calls[0]["error"] and calls[0]["arguments"] == '{"item": "nope"}'
        login_as(client, "olive", role="operator")
        assert client.get("/api/admin/assistant/calls").status_code == 403

    def test_long_arguments_are_cut_and_old_calls_dropped(self, monkeypatch):
        import time

        store.assistant.record_call("x", "search_items", {"query": "a" * 5000}, False, None, 1, 1)
        assert len(store.assistant.recent_calls(5)[0]["arguments"]) <= store.assistant.MAX_ARGUMENTS + 1
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + store.assistant.KEEP_SECONDS + 1)
        store.assistant.record_call("x", "power", {}, False, None, 1, 1)
        assert [c["tool"] for c in store.assistant.recent_calls(5)] == ["power"]
