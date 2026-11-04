"""The MCP tools that change something (gcm/mcp/tools.py): stock rules,
craft requests and cancels, and following a request to its outcome,
with the same role checks as the page's routes."""

import pytest

from gcm import commands, store
from gcm.mcp import tools
from test_mcp import IRON_KEY, call, make_token, modern, ok
from test_network_api import run_scan
from test_patterns_api import PLANKS_PATTERN, item as pattern_item, run_pattern_scan
from test_stock import GOLD, IRON, cpus, scanned

GOLD_KEY = "minecraft|gold_ingot|0|item"
PLANKS_KEY = "minecraft|planks|0|item"
OPERATOR_TOOLS = {"set_stock_target", "remove_stock_target", "request_craft", "cancel_craft"}


@pytest.fixture()
def viewer():
    return make_token("vera", role="viewer")


@pytest.fixture()
def operator():
    return make_token("olive", role="operator")


@pytest.fixture()
def network(client, api_headers):
    run_scan(client, api_headers, [[scanned(IRON, 500), scanned(GOLD, 40, craftable=False)]])


def tool_names(client, token):
    return {t["name"] for t in modern(client, token, "tools/list").get_json()["result"]["tools"]}


class TestRoles:
    def test_viewers_arent_offered_operators_tools(self, client, viewer, operator):
        assert not tool_names(client, viewer) & OPERATOR_TOOLS
        assert OPERATOR_TOOLS <= tool_names(client, operator)
        assert {"set_stock_alert", "remove_stock_alert", "request_status"} <= tool_names(client, viewer)

    def test_and_cant_call_them(self, client, viewer, network):
        for name, args in [
            ("request_craft", {"item": IRON_KEY, "amount": 1}),
            ("cancel_craft", {"cpu": "B0"}),
            ("set_stock_target", {"item": IRON_KEY, "keep_at_least": 1, "refill_to": 2}),
            ("remove_stock_target", {"item": IRON_KEY}),
        ]:
            answer, is_error = call(client, viewer, name, **args)
            assert is_error and "Only operators and admins" in answer, name

    def test_annotations_say_which_change_the_game(self):
        for tool in tools.TOOLS:
            hints = tool["annotations"]
            if tool["name"] in OPERATOR_TOOLS:
                assert hints["readOnlyHint"] is False and hints["destructiveHint"] is True, tool["name"]
            elif tool["name"] in ("set_stock_alert", "remove_stock_alert"):
                assert hints["readOnlyHint"] is False and hints["destructiveHint"] is False
            else:
                assert hints["readOnlyHint"] is True, tool["name"]


class TestStockRules:
    def test_alerts(self, client, viewer, network):
        saved = ok(client, viewer, "set_stock_alert", item=IRON_KEY, below=1000)
        assert saved["saved"] and saved["name"] == "Iron Ingot" and saved["now"] == 500
        assert store.stock.alerts_for_user("vera")[0]["below"] == 1000
        assert ok(client, viewer, "get_item", item=IRON_KEY)["your_low_stock_alert"] == {"below": 1000}
        assert ok(client, viewer, "remove_stock_alert", item=IRON_KEY) == {"removed": True, "item": IRON_KEY}
        assert store.stock.alerts_for_user("vera") == []
        answer, is_error = call(client, viewer, "remove_stock_alert", item=IRON_KEY)
        assert is_error and "no alert" in answer

    def test_alert_checks(self, client, viewer, network):
        assert call(client, viewer, "set_stock_alert", item=IRON_KEY, below=0)[1] is True
        answer, is_error = call(client, viewer, "set_stock_alert", item="minecraft|diamond|0|item", below=5)
        assert is_error and "isn't in the network" in answer

    def test_targets(self, client, operator, network):
        saved = ok(client, operator, "set_stock_target", item=IRON_KEY, keep_at_least=600, refill_to=900)
        assert saved["saved"] and "below that now" in saved["note"]
        target = store.stock.targets()[0]
        assert (target["key"], target["keep_at_least"], target["refill_to"]) == (IRON_KEY, 600, 900)
        assert ok(client, operator, "remove_stock_target", item=IRON_KEY)["removed"]
        assert store.stock.targets() == []
        assert call(client, operator, "remove_stock_target", item=IRON_KEY)[1] is True

    def test_target_checks_are_the_pages(self, client, operator, network):
        answer, is_error = call(client, operator, "set_stock_target", item=GOLD_KEY, keep_at_least=1, refill_to=2)
        assert is_error and answer == "only items the network can craft can be kept in stock"
        answer, is_error = call(client, operator, "set_stock_target", item=IRON_KEY, keep_at_least=5, refill_to=5)
        assert is_error and answer == "refill_to must be more than keep_at_least"


class TestRequestCraft:
    def test_without_patterns_it_is_sent_unchecked(self, client, operator, network, api_headers):
        sent = ok(client, operator, "request_craft", item=IRON_KEY, amount=64)
        assert sent["queued"] and sent["name"] == "Iron Ingot" and sent["amount"] == 64
        assert sent["unit"] == "items" and sent["in_stock"] == 500
        assert "Couldn't check the ingredients" in sent["plan_note"]
        pending = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
        assert [(r["id"], r["label"], r["amount"], r["user_id"]) for r in pending] == [
            (sent["request_id"], "Iron Ingot", 64, "olive")
        ]

    def test_missing_ingredients_stop_it_unless_forced(self, client, operator, api_headers):
        log = pattern_item("log", "Oak Wood", size=1, item_id=17)
        planks = pattern_item("planks", "Oak Wood Planks", size=0, item_id=5, isCraftable=True)
        run_scan(client, api_headers, [[log, planks]], interface_terminal=True)
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])

        held = ok(client, operator, "request_craft", item=PLANKS_KEY, amount=16)
        assert held["queued"] is False
        assert [(m["name"], m["missing"]) for m in held["missing"]] == [("Oak Wood", 3)]
        assert commands.craft_requests.select(lambda r: True) == []

        assert ok(client, operator, "request_craft", item=PLANKS_KEY, amount=4)["queued"] is True
        forced = ok(client, operator, "request_craft", item=PLANKS_KEY, amount=16, force=True)
        assert forced["queued"] and "force" in forced["plan_note"]

    def test_only_craftable_items(self, client, operator, network):
        answer, is_error = call(client, operator, "request_craft", item=GOLD_KEY, amount=1)
        assert is_error and "no pattern" in answer
        assert call(client, operator, "request_craft", item=IRON_KEY, amount=0)[1] is True

    def test_status_follows_it_to_the_end(self, client, operator, viewer, network, api_headers):
        req_id = ok(client, operator, "request_craft", item=IRON_KEY, amount=8)["request_id"]
        assert ok(client, operator, "request_status", request_id=req_id)["status"] == "pending"
        client.get("/api/craft/requests/pending", headers=api_headers)
        client.post(f"/api/craft/requests/{req_id}/result", json={"status": "accepted", "cpu_name": "B0"},
                    headers=api_headers)
        done = ok(client, operator, "request_status", request_id=req_id)
        assert (done["status"], done["cpu"]) == ("accepted", "B0")
        # Someone else's request isn't yours to look at.
        assert call(client, viewer, "request_status", request_id=req_id)[1] is True

    def test_a_failed_request_says_why(self, client, operator, network, api_headers):
        req_id = ok(client, operator, "request_craft", item=IRON_KEY, amount=8)["request_id"]
        client.get("/api/craft/requests/pending", headers=api_headers)
        client.post(f"/api/craft/requests/{req_id}/result",
                    json={"status": "failed", "reason": "missing Iron Ore"}, headers=api_headers)
        failed = ok(client, operator, "request_status", request_id=req_id)
        assert (failed["status"], failed["reason"]) == ("failed", "missing Iron Ore")


class TestCancelCraft:
    def test_cancels_the_job_current_crafts_showed(self, client, operator, api_headers):
        cpus(client, api_headers, idle=1, busy=[IRON])
        job = ok(client, operator, "current_crafts")["busy_cpus"][0]
        assert job["job_id"] == "minecraft|iron_ingot|0"
        sent = ok(client, operator, "cancel_craft", cpu="B0", job_id=job["job_id"])
        assert sent["queued"]
        status = ok(client, operator, "request_status", request_id=sent["request_id"], type="cancel")
        assert status["status"] == "pending" and status["making"] == "Iron Ingot"
        client.get("/api/craft/cancel/pending", headers=api_headers)
        client.post(f"/api/craft/cancel/{sent['request_id']}/result", json={"success": True}, headers=api_headers)
        status = ok(client, operator, "request_status", request_id=sent["request_id"], type="cancel")
        assert status["status"] == "cancelled"

    def test_not_when_the_cpu_moved_on(self, client, operator, api_headers):
        cpus(client, api_headers, idle=1, busy=[GOLD])
        answer, is_error = call(client, operator, "cancel_craft", cpu="B0", job_id="minecraft|iron_ingot|0")
        assert is_error and "different job" in answer
        assert commands.cancel_requests.select(lambda r: True) == []

    def test_not_an_idle_or_unknown_cpu(self, client, operator, api_headers):
        cpus(client, api_headers, idle=1)
        assert "not currently busy" in call(client, operator, "cancel_craft", cpu="I0")[0]
        assert "unknown" in call(client, operator, "cancel_craft", cpu="Z9")[0]
        assert call(client, operator, "cancel_craft", cpu="I0", job_id="nonsense")[1] is True
