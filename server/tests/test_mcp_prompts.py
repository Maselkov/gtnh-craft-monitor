"""MCP prompts (gcm/mcp/prompts.py) in both protocol eras, and
search_items' fallback for names typed differently."""

import base64
import re

from gcm.mcp import prompts, protocol, tools
from test_mcp import make_token, modern, ok, post
from test_network_api import run_scan


def legacy(client, token, method, params=None):
    return post(client, token, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                {"MCP-Protocol-Version": "2025-06-18"})


class TestPrompts:
    def test_listed_by_role(self, client):
        viewer, operator = make_token("vera"), make_token("olive", role="operator")
        names = [p["name"] for p in legacy(client, viewer, "prompts/list").get_json()["result"]["prompts"]]
        assert names == ["base_report", "slow_craft", "restock_check"]
        result = modern(client, operator, "prompts/list").get_json()["result"]
        assert [p["name"] for p in result["prompts"]] == [p["name"] for p in prompts.PROMPTS]
        assert result["cacheScope"] == "private" and result["resultType"] == "complete"
        slow = next(p for p in result["prompts"] if p["name"] == "slow_craft")
        assert slow["arguments"] == [{"name": "cpu", "required": False, "description": slow["arguments"][0]["description"]}]

    def test_get(self, client):
        token = make_token("olive", role="operator")
        result = legacy(client, token, "prompts/get", {"name": "slow_craft", "arguments": {"cpu": "M00"}}).get_json()["result"]
        text = result["messages"][0]["content"]["text"]
        assert result["messages"][0]["role"] == "user" and "CPU M00" in text and "`craft_job`" in text
        anyone = legacy(client, token, "prompts/get", {"name": "slow_craft"}).get_json()["result"]
        assert "longest" in anyone["messages"][0]["content"]["text"]

    def test_modern_get_checks_the_name_header(self, client):
        token = make_token()
        res = modern(client, token, "prompts/get", {"name": "base_report"}, headers={"Mcp-Name": "base_report"})
        assert res.get_json()["result"]["messages"]
        res = modern(client, token, "prompts/get", {"name": "base_report"}, headers={"Mcp-Name": "restock_check"})
        assert res.status_code == 400 and res.get_json()["error"]["code"] == protocol.HEADER_MISMATCH
        encoded = "=?base64?" + base64.b64encode(b"base_report").decode() + "?="
        res = modern(client, token, "prompts/get", {"name": "base_report"}, headers={"Mcp-Name": encoded})
        assert res.status_code == 200

    def test_errors(self, client):
        viewer = make_token("vera")
        for params in ({"name": "nope"}, {"name": "update_check"}, {"name": "slow_craft", "arguments": {"x": "1"}},
                       {"name": "slow_craft", "arguments": {"cpu": 5}}):
            error = legacy(client, viewer, "prompts/get", params).get_json()["error"]
            assert error["code"] == protocol.INVALID_PARAMS, params

    def test_every_tool_a_prompt_names_exists(self):
        # Backticks also mark the fields a tool answers with.
        names = {t["name"] for t in tools.TOOLS} | {"machine_busy_with", "crafts_last_week"}
        for p in prompts.PROMPTS:
            for text in (p["text"]({}), p["text"]({"cpu": "X"})):
                for used in re.findall(r"`([a-z_]+)`", text):
                    assert used in names, (p["name"], used)


class TestApproximateSearch:
    def test_joined_or_misspelt_words_still_find_it(self, client, api_headers):
        run_scan(client, api_headers, [[
            {"name": "Inconel-625 Plate", "mod": "gregtech", "internal": "gt.metaitem.01", "damage": 1, "size": 9},
            {"name": "Tungstensteel Ingot", "mod": "gregtech", "internal": "gt.metaitem.01", "damage": 2, "size": 9},
            {"name": "Iron Ingot", "mod": "minecraft", "internal": "iron_ingot", "damage": 0, "size": 9},
        ]])
        token = make_token()
        joined = ok(client, token, "search_items", query="inconel625 plate")
        assert joined["approximate"] and [i["name"] for i in joined["items"]] == ["Inconel-625 Plate"]
        typo = ok(client, token, "search_items", query="tungstenstel ingot")
        assert typo["approximate"] and typo["items"][0]["name"] == "Tungstensteel Ingot"

    def test_only_when_nothing_matched(self, client, api_headers):
        run_scan(client, api_headers, [[
            {"name": "Iron Ingot", "mod": "minecraft", "internal": "iron_ingot", "damage": 0, "size": 9},
        ]])
        token = make_token()
        exact = ok(client, token, "search_items", query="iron")
        assert "approximate" not in exact
        assert ok(client, token, "search_items", query="unobtainium")["matches"] == 0
        # A query with NEI prefixes means what it says.
        assert ok(client, token, "search_items", query="@gregtech iron")["matches"] == 0
