"""The MCP endpoint (/mcp, gcm/mcp/): the protocol in both eras it
serves, bearer-token auth, and each tool against the real app."""

import base64
import json
import time

import pytest

from gcm import db, store
from gcm.mcp import protocol, tools
from test_network_api import run_scan
from test_patterns_api import PLANKS_PATTERN, item as pattern_item, run_pattern_scan
from test_stock import IRON, GOLD, cpus, scanned


MODERN = "2026-07-28"


def make_token(user_id="mia", role="viewer"):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, role, created_at) VALUES (?, ?, ?, ?)",
            (user_id, user_id, role, time.time()),
        )
        token = store.users.create_access_token(conn, user_id)
        conn.commit()
    finally:
        conn.close()
    return token


@pytest.fixture()
def token():
    return make_token()


def post(client, token, message, headers=None):
    all_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    all_headers.update(headers or {})
    return client.post("/mcp", json=message, headers=all_headers)


def modern(client, token, method, params=None, msg_id=1, headers=None):
    """A 2026-07-28 request, its headers mirroring the body."""
    params = {**(params or {}), "_meta": {"io.modelcontextprotocol/protocolVersion": MODERN}}
    mirrored = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
    if method == "tools/call":
        mirrored["Mcp-Name"] = params["name"]
    mirrored.update(headers or {})
    return post(client, token, {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}, mirrored)


def call(client, token, name, **arguments):
    """A tool's answer: (decoded JSON or the error text, is_error)."""
    res = modern(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert res.status_code == 200, res.get_json()
    result = res.get_json()["result"]
    text = result["content"][0]["text"]
    return (text if result["isError"] else json.loads(text)), result["isError"]


def ok(client, token, name, **arguments):
    answer, is_error = call(client, token, name, **arguments)
    assert not is_error, answer
    return answer


IRON_KEY = "minecraft|iron_ingot|0|item"


# ---------------------------------------------------------------------
# Auth


class TestAuth:
    def test_no_token_is_401_with_a_bearer_challenge(self, client):
        res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert res.status_code == 401
        assert res.headers["WWW-Authenticate"].startswith("Bearer")

    def test_a_wrong_or_revoked_token_is_401(self, client, token):
        assert post(client, "gcm_tok-nope_nope", {"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
        token_id = store.users.parse_access_token(token)[0]
        assert store.users.revoke_token(token_id)
        assert post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401

    def test_a_session_cookie_alone_is_not_enough(self, client):
        from conftest import login_as

        login_as(client, "sam")
        assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401

    def test_another_sites_origin_is_refused(self, client, token):
        message = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        assert post(client, token, message, {"Origin": "https://evil.example"}).status_code == 403
        assert post(client, token, message, {"Origin": "http://localhost"}).status_code == 200

    def test_get_and_delete_are_405(self, client):
        assert client.get("/mcp").status_code == 405
        assert client.delete("/mcp").status_code == 405

    def test_answers_are_not_cached(self, client, token):
        res = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert "no-store" in res.headers["Cache-Control"]


# ---------------------------------------------------------------------
# Protocol


class TestLegacy:
    def test_initialize_picks_the_clients_version_when_supported(self, client, token):
        res = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        result = res.get_json()["result"]
        assert result["protocolVersion"] == "2025-06-18"
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert result["serverInfo"]["name"] == "gtnh-craft-monitor"
        assert "search_items" in result["instructions"]

    def test_initialize_offers_the_latest_legacy_version_otherwise(self, client, token):
        res = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2024-11-05"}})
        assert res.get_json()["result"]["protocolVersion"] == "2025-11-25"

    def test_notifications_are_accepted_with_202(self, client, token):
        res = post(client, token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert res.status_code == 202 and res.data == b""

    def test_requests_go_by_the_header_or_2025_03_26_without_one(self, client, token):
        message = {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
        for headers in ({}, {"MCP-Protocol-Version": "2025-06-18"}):
            res = post(client, token, message, headers)
            body = res.get_json()
            assert res.status_code == 200 and body["id"] == 7
            assert "resultType" not in body["result"]
            assert [t["name"] for t in body["result"]["tools"]][0] == "search_items"

    def test_an_unknown_header_version_is_refused(self, client, token):
        res = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                   {"MCP-Protocol-Version": "1999-01-01"})
        assert res.status_code == 400
        error = res.get_json()["error"]
        assert error["code"] == protocol.UNSUPPORTED_VERSION
        assert error["data"]["requested"] == "1999-01-01" and MODERN in error["data"]["supported"]

    def test_an_unknown_method_is_a_jsonrpc_error(self, client, token):
        res = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        assert res.get_json()["error"]["code"] == protocol.METHOD_NOT_FOUND


class TestModern:
    def test_discover(self, client, token):
        result = modern(client, token, "server/discover").get_json()["result"]
        assert result["resultType"] == "complete"
        assert result["supportedVersions"][0] == MODERN and "2025-06-18" in result["supportedVersions"]
        assert result["capabilities"] == {"tools": {}}
        assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "gtnh-craft-monitor"

    def test_tools_list_is_cacheable_and_in_a_fixed_order(self, client, token):
        result = modern(client, token, "tools/list").get_json()["result"]
        assert result["resultType"] == "complete"
        assert result["ttlMs"] > 0 and result["cacheScope"] == "private"
        names = [t["name"] for t in result["tools"]]
        assert names == [t["name"] for t in tools.TOOLS] and len(set(names)) == len(names)
        for tool in result["tools"]:
            assert tool["inputSchema"]["type"] == "object" and tool["description"]
            assert tool["annotations"]["readOnlyHint"] is True

    def test_headers_must_match_the_body(self, client, token):
        cases = [
            {"MCP-Protocol-Version": None},
            {"MCP-Protocol-Version": "2025-11-25"},
            {"Mcp-Method": None},
            {"Mcp-Method": "tools/call"},
        ]
        for headers in cases:
            params = {"_meta": {"io.modelcontextprotocol/protocolVersion": MODERN}}
            sent = {"MCP-Protocol-Version": MODERN, "Mcp-Method": "tools/list", **headers}
            sent = {k: v for k, v in sent.items() if v is not None}
            res = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": params}, sent)
            assert res.status_code == 400, headers
            assert res.get_json()["error"]["code"] == protocol.HEADER_MISMATCH, headers

    def test_a_tool_calls_name_header_must_match(self, client, token):
        res = modern(client, token, "tools/call", {"name": "current_crafts", "arguments": {}},
                     headers={"Mcp-Name": "power"})
        assert res.status_code == 400 and res.get_json()["error"]["code"] == protocol.HEADER_MISMATCH
        encoded = "=?base64?" + base64.b64encode(b"current_crafts").decode() + "?="
        res = modern(client, token, "tools/call", {"name": "current_crafts", "arguments": {}},
                     headers={"Mcp-Name": encoded})
        assert res.status_code == 200 and res.get_json()["result"]["isError"] is False

    def test_an_unsupported_version_lists_the_supported_ones(self, client, token):
        params = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2030-01-01"}}
        res = post(client, token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": params},
                   {"MCP-Protocol-Version": "2030-01-01", "Mcp-Method": "tools/list"})
        assert res.status_code == 400
        assert res.get_json()["error"]["data"]["supported"] == protocol.SUPPORTED_VERSIONS

    def test_an_unknown_method_is_404(self, client, token):
        res = modern(client, token, "prompts/list")
        assert res.status_code == 404 and res.get_json()["error"]["code"] == protocol.METHOD_NOT_FOUND

    def test_an_unknown_tool_is_invalid_params(self, client, token):
        res = modern(client, token, "tools/call", {"name": "launch_rocket", "arguments": {}})
        assert res.get_json()["error"]["code"] == protocol.INVALID_PARAMS


class TestMalformed:
    def test_not_json_rpc(self, client, token):
        assert post(client, token, {"hello": 1}).status_code == 400
        assert post(client, token, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).status_code == 400

    def test_bad_arguments_come_back_as_tool_errors(self, client, token):
        assert call(client, token, "search_items") == ("query is required", True)
        assert call(client, token, "search_items", query="x", bogus=1) == ("unknown argument(s): bogus", True)
        assert call(client, token, "search_items", query="x", limit=0)[1] is True
        answer, is_error = call(client, token, "get_item", item="Iron Ingot")
        assert is_error and "search_items" in answer


# ---------------------------------------------------------------------
# Tools


@pytest.fixture()
def network(client, api_headers):
    run_scan(client, api_headers, [[
        scanned(IRON, 500), scanned(GOLD, 40, craftable=False),
        {"name": "Iron Plate", "mod": "gregtech", "internal": "gt.metaitem.01", "damage": 17032,
         "size": 64, "isCraftable": True},
        {"kind": "fluid", "internal": "water", "name": "Water", "size": 144000, "isCraftable": False},
    ]])


class TestNetworkTools:
    def test_search_ranks_the_closest_names_first(self, client, token, network):
        found = ok(client, token, "search_items", query="iron")
        assert [i["name"] for i in found["items"]] == ["Iron Ingot", "Iron Plate"]
        assert found["items"][0] == {"item": IRON_KEY, "name": "Iron Ingot", "amount": 500,
                                     "unit": "items", "craftable": True}
        assert ok(client, token, "search_items", query="iron plate")["items"][0]["name"] == "Iron Plate"

    def test_search_filters(self, client, token, network):
        assert ok(client, token, "search_items", query="ingot", craftable_only=True)["matches"] == 1
        assert ok(client, token, "search_items", query="@gregtech")["items"][0]["name"] == "Iron Plate"
        water = ok(client, token, "search_items", query="water")["items"][0]
        assert water["unit"] == "mB" and water["item"] == "|water||fluid"
        assert ok(client, token, "search_items", query="iron", limit=1)["matches"] == 2

    def test_get_item(self, client, token, network, api_headers):
        cpus(client, api_headers, idle=1, busy=[IRON])
        found = ok(client, token, "get_item", item=IRON_KEY)
        assert found["name"] == "Iron Ingot" and found["amount"] == 500
        assert found["crafting_on_cpus"] == ["B0"]
        assert found["keep_in_stock_target"] is None and found["your_low_stock_alert"] is None
        answer, is_error = call(client, token, "get_item", item="minecraft|diamond|0|item")
        assert is_error and "isn't in the network" in answer

    def test_get_item_shows_stock_rules(self, client, token, network):
        store.stock.set_target("mia", {**IRON, "variant": None}, 100, 300, True)
        store.stock.set_alert("mia", {**IRON, "variant": None}, 50)
        found = ok(client, token, "get_item", item=IRON_KEY)
        assert found["keep_in_stock_target"] == {"keep_at_least": 100, "refill_to": 300, "enabled": True}
        assert found["your_low_stock_alert"] == {"below": 50}

    def test_history_and_changes(self, client, token, network, api_headers):
        run_scan(client, api_headers, [[scanned(IRON, 200), scanned(GOLD, 90, craftable=False)]])
        history = ok(client, token, "item_history", item=IRON_KEY, range="hour")
        assert history["now"] == 200 and history["min"] == 200 and history["max"] == 500
        assert history["points"][-1]["amount"] == 200 and history["points"][-1]["t"].endswith("+00:00")

        falling = ok(client, token, "inventory_changes", sort="falling")
        assert [(i["name"], i["was"], i["now"], i["change"]) for i in falling["items"]] == [
            ("Iron Ingot", 500, 200, -300)
        ]
        rising = ok(client, token, "inventory_changes", sort="rising")
        assert [i["name"] for i in rising["items"]] == ["Gold Ingot"]
        assert len(ok(client, token, "inventory_changes")["items"]) == 2


class TestCraftTools:
    def test_current_crafts(self, client, token, api_headers):
        cpus(client, api_headers, idle=2, busy=[IRON])
        now = ok(client, token, "current_crafts")
        assert now["idle_cpus"] == ["I0", "I1"]
        assert [(c["cpu"], c["making"]) for c in now["busy_cpus"]] == [("B0", "Iron Ingot")]

    def test_craft_job(self, client, token, api_headers):
        cpus(client, api_headers, idle=1)
        assert ok(client, token, "craft_job", cpu="I0") == {"cpu": "I0", "busy": False}
        answer, is_error = call(client, token, "craft_job", cpu="Z9")
        assert is_error and "current_crafts" in answer

    def test_history_and_stats_start_empty(self, client, token):
        assert ok(client, token, "craft_history") == {"events": [], "more": False}
        stats = ok(client, token, "craft_stats", range="day")
        assert stats["total"] == 0 and stats["slowest_machines"] == []

    def test_plan_craft(self, client, token, api_headers):
        log = pattern_item("log", "Oak Wood", size=3, item_id=17)
        run_scan(client, api_headers, [[log]], interface_terminal=True)
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])
        plan = ok(client, token, "plan_craft", item="minecraft|planks|0|item", amount=16)
        assert plan["can_craft"] is False
        assert [(m["name"], m["missing"]) for m in plan["missing"]] == [("Oak Wood", 1)]
        answer, is_error = call(client, token, "plan_craft", item="minecraft|stone|0|item", amount=1)
        assert is_error and "patterns" in answer

    def test_plan_before_any_pattern_scan(self, client, token):
        answer, is_error = call(client, token, "plan_craft", item=IRON_KEY, amount=1)
        assert is_error and "pattern" in answer.lower()

    def test_stock_rules(self, client, token, network):
        make_token("someone")
        store.stock.set_target("someone", {**IRON, "variant": None}, 100, 300, True)
        store.stock.set_alert("mia", {**GOLD, "variant": None}, 50)
        store.stock.set_alert("someone", {**IRON, "variant": None}, 10)
        rules = ok(client, token, "stock_rules")
        target = rules["keep_in_stock_targets"][0]
        assert target["item"] == IRON_KEY and target["current"] == 500 and target["status"] == "ok"
        assert [(a["label"], a["below"], a["low"]) for a in rules["your_low_stock_alerts"]] == [("Gold Ingot", 50, True)]


class TestPowerTool:
    def test_no_readings_yet(self, client, token):
        answer, is_error = call(client, token, "power")
        assert is_error and "power_monitor.lua" in answer

    def test_latest_and_points(self, client, token, api_headers):
        client.post("/api/power", json={"stored": 2500.5, "capacity": 10000}, headers=api_headers)
        found = ok(client, token, "power")
        assert found["machine"] == "(unnamed)" and found["machines"] == ["(unnamed)"]
        assert found["latest"]["stored"] == 2500 and found["latest"]["percent_full"] == 25
        assert ok(client, token, "power", machine="(unnamed)")["latest"]["capacity"] == 10000


def test_numbers_are_compacted():
    assert tools._compact_numbers({"a": 3780.0, "b": 0.019050925, "c": 2575050689.29, "d": [1.5]}) == {
        "a": 3780, "b": 0.0191, "c": 2575050689, "d": [1.5]
    }
