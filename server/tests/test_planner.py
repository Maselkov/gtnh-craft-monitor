"""Crafting plans (gcm/planner.py) and GET /api/network/plan."""

import json

from gcm import patterns, planner, store
from conftest import login_as
from test_network_api import run_scan
from test_patterns_api import run_pattern_scan


def item(name, size=1, damage=0):
    return {"kind": "item", "mod": "gregtech", "internal": "gt.metaitem.01", "damage": damage,
            "name": name, "size": size}


def fluid(name, amount):
    return {"kind": "fluid", "internal": name.lower().replace(" ", "."), "name": name, "size": amount}


DAMAGE = {}


def it(name, size=1):
    """An item with its own damage value, so each name is its own key."""
    return item(name, size, DAMAGE.setdefault(name, 1000 + len(DAMAGE)))


def key(name):
    return store.items.key_of(it(name))


def pat(outputs, inputs, provider="Machine", slot=0):
    return patterns.normalize({
        "provider": {"name": provider, "x": 0, "y": 0, "z": slot, "dim": 0},
        "slot": slot, "crafting": False, "inputs": inputs, "outputs": outputs,
    })


# Gear <- 4 plates + 1 bolt; plate <- 1 ingot; 4 bolts <- 1 ingot.
GEAR = pat([it("Gear")], [it("Plate", 4), it("Bolt")], "Assembler")
PLATE = pat([it("Plate")], [it("Ingot")], "Bender")
BOLTS = pat([it("Bolt", 4)], [it("Ingot")], "Lathe")
CHAIN = [GEAR, PLATE, BOLTS]


def plan(pats, stock, name, amount, **kw):
    return planner.plan(pats, {key(n): s for n, s in stock.items()}, key(name), amount, **kw)


def child(node, name):
    return next(c for c in node["children"] if c["name"] == name)


def totals(result, name):
    return next(i for i in result["items"] if i["name"] == name)


class TestPlan:
    def test_the_requested_item_is_always_crafted(self):
        # AE2 doesn't take the requested item out of storage.
        root = plan(CHAIN, {"Gear": 50, "Ingot": 100}, "Gear", 2)["root"]
        assert (root["status"], root["from_stock"], root["craft"], root["batches"]) == ("craft", 0, 2, 2)
        assert root["pattern"]["provider"] == "Assembler"

    def test_lower_steps_come_from_stock_first(self):
        result = plan(CHAIN, {"Plate": 5, "Ingot": 100}, "Gear", 2)
        plate = child(result["root"], "Plate")
        assert (plate["need"], plate["from_stock"], plate["craft"]) == (8, 5, 3)
        assert child(plate, "Ingot")["need"] == 3
        assert result["missing"] == []

    def test_batches_round_up(self):
        # 2 bolts from a pattern making 4: one batch, 4 made.
        bolt = child(plan(CHAIN, {"Ingot": 100}, "Gear", 2)["root"], "Bolt")
        assert (bolt["need"], bolt["batches"], bolt["craft"]) == (2, 1, 4)

    def test_shared_stock_is_counted_once(self):
        # Plates take 8 ingots, the bolts 1 more - only 6 in stock.
        result = plan(CHAIN, {"Ingot": 6}, "Gear", 2)
        root = result["root"]
        assert child(child(root, "Plate"), "Ingot")["from_stock"] == 6
        bolt_ingot = child(child(root, "Bolt"), "Ingot")
        assert (bolt_ingot["from_stock"], bolt_ingot["status"], bolt_ingot["missing"]) == (0, "missing", 1)
        ingot = totals(result, "Ingot")
        assert (ingot["need"], ingot["from_stock"], ingot["missing"], ingot["available"]) == (9, 6, 3, 6)
        assert [m["name"] for m in result["missing"]] == ["Ingot"]

    def test_fluid_amounts(self):
        ingot = pat([it("Ingot")], [fluid("Molten Iron", 144)], "Solidifier")
        result = plan([PLATE, ingot], {}, "Plate", 10)
        molten = child(child(result["root"], "Ingot"), "Molten Iron")
        assert (molten["kind"], molten["need"], molten["status"]) == ("fluid", 1440, "missing")

    def test_a_cycle_stops_and_counts_as_missing(self):
        # Ingot <- molten <- ingot, as with GT's extractor and solidifier.
        to_ingot = pat([it("Ingot")], [fluid("Molten Iron", 144)], "Solidifier")
        to_molten = pat([fluid("Molten Iron", 144)], [it("Ingot")], "Extractor")
        root = plan([to_ingot, to_molten], {}, "Ingot", 1)["root"]
        inner = child(child(root, "Molten Iron"), "Ingot")
        assert (inner["status"], inner["missing"]) == ("cycle", 1)

    def test_byproducts_are_listed_not_counted(self):
        tesseract = pat([it("Tesseract"), fluid("Depleted Fuel", 64)], [it("Raw Tesseract"), fluid("Fuel", 64)])
        root = plan([tesseract], {"Raw Tesseract": 5}, "Tesseract", 3)["root"]
        assert [(a["name"], a["amount"]) for a in root["also_makes"]] == [("Depleted Fuel", 192)]

    def test_the_main_output_pattern_is_the_default_and_others_can_be_chosen(self):
        # A byproduct-only pattern for plates, listed first, isn't the default.
        side = pat([it("Dust"), it("Plate")], [it("Ore")], "Washer", slot=1)
        result = plan([side, PLATE], {"Ingot": 10, "Ore": 10}, "Plate", 1)
        root = result["root"]
        assert root["pattern"]["provider"] == "Bender"
        assert [a["provider"] for a in root["alternatives"]] == ["Bender", "Washer"]
        washer = root["alternatives"][1]["id"]
        chosen = plan([side, PLATE], {"Ore": 10}, "Plate", 1, choices={key("Plate"): washer})["root"]
        assert chosen["pattern"]["provider"] == "Washer"
        assert [c["name"] for c in chosen["children"]] == ["Ore"]

    def test_an_unknown_choice_falls_back_to_the_default(self):
        root = plan(CHAIN, {}, "Plate", 1, choices={key("Plate"): "nope"})["root"]
        assert root["pattern"]["provider"] == "Bender"

    def test_an_unknown_output_count_counts_as_one(self):
        # A crafting pattern read without its NBT.
        planks = patterns.normalize({"provider": {"name": "MA"}, "crafting": True,
                                     "inputs": [it("Log", 0)], "outputs": [it("Planks", 0)]})
        root = plan([planks], {"Log": 10}, "Planks", 3)["root"]
        assert (root["craft"], root["inexact"]) == (3, True)

    def test_no_pattern_no_plan(self):
        assert plan(CHAIN, {}, "Ingot", 1) is None

    def test_a_huge_plan_is_cut_short(self, monkeypatch):
        monkeypatch.setattr(planner, "MAX_NODES", 3)
        result = plan(CHAIN, {}, "Gear", 1)
        assert result["truncated"] is True
        bolt = child(result["root"], "Bolt")
        assert (bolt["truncated"], bolt["children"]) == (True, [])

    def test_stock_rule_warnings(self):
        result = plan(CHAIN, {"Ingot": 20}, "Gear", 2, rules=[(key("Ingot"), 15, "below your alert")])
        ingot = totals(result, "Ingot")
        assert (ingot["left"], ingot["warnings"]) == (11, [{"rule": "below your alert", "threshold": 15}])
        assert "warnings" not in totals(result, "Plate")

    def test_no_warning_for_an_item_already_below_its_rule(self):
        # 20 in stock under a rule of 30: low before the plan touched it.
        result = plan(CHAIN, {"Ingot": 20}, "Gear", 2, rules=[(key("Ingot"), 30, "below your alert")])
        assert "warnings" not in totals(result, "Ingot")

    def test_a_warning_when_the_plan_crosses_the_rule_exactly(self):
        # 20 in stock, rule at 20: at it before, 11 after.
        result = plan(CHAIN, {"Ingot": 20}, "Gear", 2, rules=[(key("Ingot"), 20, "below your alert")])
        assert totals(result, "Ingot")["warnings"] == [{"rule": "below your alert", "threshold": 20}]

    def test_two_same_named_interfaces_on_one_block_get_different_ids(self):
        a = pat([it("Plate")], [it("Ingot")], "Interface")
        b = pat([it("Plate")], [it("Dust")], "Interface")
        ids = [a_["id"] for a_ in plan([a, b], {}, "Plate", 1)["root"]["alternatives"]]
        assert len(set(ids)) == 2


class TestPaging:
    def test_plans_far_past_the_old_2000_step_cap(self):
        # A chain 3000 deep: each step needs the next.
        chain = [pat([it(f"Part {i}")], [it(f"Part {i + 1}")], slot=i) for i in range(3000)]
        result = plan(chain, {}, "Part 0", 1)
        assert result["steps"] == 3001 and result["truncated"] is False
        assert [m["name"] for m in result["missing"]] == ["Part 3000"]

    def test_trim_keeps_levels_and_counts_the_rest(self):
        root = plan(CHAIN, {}, "Gear", 2)["root"]
        top = planner.trim(root, 1)
        plate = child(top, "Plate")
        assert "children" not in plate and plate["more"] == 1
        assert planner.trim(root, 2)["children"][0]["children"][0]["name"] == "Ingot"
        # A step that was planned but has nothing below keeps an empty list.
        assert planner.trim(plan(CHAIN, {"Plate": 10, "Bolt": 10}, "Gear", 1)["root"], 0)["more"] == 2

    def test_subtree_by_child_positions(self):
        root = plan(CHAIN, {}, "Gear", 2)["root"]
        assert planner.subtree(root, [])["name"] == "Gear"
        assert planner.subtree(root, [1, 0])["name"] == "Ingot"
        assert planner.subtree(root, [5]) is None
        assert planner.subtree(root, [0, 0, 0]) is None

    def test_the_last_plans_are_reused_until_stock_changes(self, monkeypatch):
        calls = []
        real = planner.plan
        monkeypatch.setattr(planner, "plan", lambda *a, **k: calls.append(1) or real(*a, **k))
        stock = {key("Ingot"): 5}
        first = planner.cached_plan(CHAIN, stock, "v1", key("Gear"), 2)
        assert planner.cached_plan(CHAIN, stock, "v1", key("Gear"), 2) is first
        planner.cached_plan(CHAIN, stock, "v2", key("Gear"), 2)
        planner.cached_plan(list(CHAIN), stock, "v2", key("Gear"), 2)  # a new pattern scan
        assert len(calls) == 3


class TestRoute:
    URL = "/api/network/plan"

    def params(self, name, amount=1, **extra):
        return {"mod": "gregtech", "internal": "gt.metaitem.01", "damage": DAMAGE[name],
                "amount": amount, **extra}

    def scan(self, client, api_headers, stock):
        raw = lambda p: {  # noqa: E731 - back to what network_browser.lua sends
            "provider": p["provider"], "slot": p["slot"], "crafting": p["crafting"],
            "inputs": [{k: v for k, v in e.items() if k != "icon"} for e in p["inputs"]],
            "outputs": [{k: v for k, v in e.items() if k != "icon"} for e in p["outputs"]],
        }
        run_pattern_scan(client, api_headers, [[raw(p) for p in CHAIN]])
        run_scan(client, api_headers, [[{**it(n), "size": s, "isCraftable": False} for n, s in stock.items()]])

    def test_needs_a_sign_in(self, client):
        assert client.get(self.URL, query_string={"internal": "x"}).status_code == 401

    def test_bad_requests(self, client):
        login_as(client, "usr_alice")
        assert client.get(self.URL).status_code == 400
        for amount in ("0", "-3", "1.5", "lots", str(10**13)):
            res = client.get(self.URL, query_string={"internal": "x", "amount": amount})
            assert res.status_code == 400, amount

    def test_before_any_pattern_scan(self, client):
        login_as(client, "usr_alice")
        it("Gear")
        data = client.get(self.URL, query_string=self.params("Gear")).get_json()
        assert data["plan"] is None
        assert "No pattern scan yet" in data["reason"]

    def test_essentia_comes_from_the_scanned_stock(self, client, api_headers):
        # The Interface Terminal and getEssentiaInNetwork() both name an
        # aspect by its tag, so the two scans' keys meet.
        ordo = {"kind": "essentia", "internal": "ordo", "name": "Ordo"}
        frame = {**it("Frame"), "size": 1}
        run_pattern_scan(client, api_headers, [[{
            "provider": {"name": "Infusion", "x": 0, "y": 0, "z": 0, "dim": 0}, "slot": 0, "crafting": False,
            "inputs": [{**ordo, "size": 64}], "outputs": [frame],
        }]])
        run_scan(client, api_headers, [[{**ordo, "size": 100, "isCraftable": False}]])
        login_as(client, "usr_alice")
        root = client.get(self.URL, query_string=self.params("Frame", 2)).get_json()["plan"]["root"]
        essentia = child(root, "Ordo")
        assert (essentia["kind"], essentia["need"], essentia["from_stock"], essentia.get("missing")) == (
            "essentia", 128, 100, 28)

    def test_a_plan_from_the_scans(self, client, api_headers):
        self.scan(client, api_headers, {"Ingot": 6, "Plate": 1})
        login_as(client, "usr_alice")
        data = client.get(self.URL, query_string=self.params("Gear", 2)).get_json()
        root = data["plan"]["root"]
        assert (root["name"], root["craft"]) == ("Gear", 2)
        assert child(root, "Plate")["from_stock"] == 1
        assert [m["name"] for m in data["plan"]["missing"]] == ["Ingot"]
        assert data["patterns_updated_at"] and data["stock_updated_at"]

    def test_nothing_makes_it(self, client, api_headers):
        self.scan(client, api_headers, {})
        login_as(client, "usr_alice")
        data = client.get(self.URL, query_string=self.params("Ingot")).get_json()
        assert data == {"plan": None, "reason": "None of the network's patterns makes this item."}

    def test_choose_and_the_users_own_alerts(self, client, api_headers):
        self.scan(client, api_headers, {"Ingot": 20})
        login_as(client, "usr_alice")
        res = client.post("/api/stock/alert", json={"label": "Ingot", "mod": "gregtech", "internal": "gt.metaitem.01",
                                                     "damage": DAMAGE["Ingot"], "below": 15})
        assert res.status_code == 200
        params = self.params("Gear", 2, choose=json.dumps({key("Plate"): "nope"}))
        data = client.get(self.URL, query_string=params).get_json()
        assert totals(data["plan"], "Ingot")["warnings"] == [{"rule": "below your alert", "threshold": 15}]
        assert child(data["plan"]["root"], "Plate")["pattern"]["provider"] == "Bender"

    def test_the_tree_comes_two_levels_at_a_time(self, client, api_headers):
        self.scan(client, api_headers, {})
        login_as(client, "usr_alice")
        first = client.get(self.URL, query_string=self.params("Gear", 2)).get_json()
        plate = child(first["plan"]["root"], "Plate")
        ingot = child(plate, "Ingot")
        assert "children" not in ingot and "more" not in ingot  # missing: nothing below
        # The whole plan still counts: the list view and Missing see every step.
        assert {m["name"] for m in first["plan"]["missing"]} == {"Ingot"}

        bigger = client.get(self.URL, query_string=self.params("Gear", 2, path="0", version=first["version"]))
        assert child(bigger.get_json()["node"], "Ingot")["need"] == 8
        bad = client.get(self.URL, query_string=self.params("Gear", 2, path="9.9", version=first["version"]))
        assert bad.get_json() == {"stale": True}
        assert client.get(self.URL, query_string=self.params("Gear", 2, path="x")).status_code == 400

    def test_a_branch_asked_for_after_a_new_scan_is_stale(self, client, api_headers):
        self.scan(client, api_headers, {})
        login_as(client, "usr_alice")
        first = client.get(self.URL, query_string=self.params("Gear", 2)).get_json()
        run_scan(client, api_headers, [[{**it("Ingot"), "size": 3, "isCraftable": False}]])
        res = client.get(self.URL, query_string=self.params("Gear", 2, path="0", version=first["version"]))
        assert res.get_json() == {"stale": True}

    def test_a_malformed_choose_is_ignored(self, client, api_headers):
        self.scan(client, api_headers, {})
        login_as(client, "usr_alice")
        for choose in ("not json", "[1, 2]", '{"a": 5}'):
            res = client.get(self.URL, query_string=self.params("Gear", choose=choose))
            assert res.status_code == 200 and res.get_json()["plan"], choose
