"""Crafting plans (gcm/planner.py) and GET /api/network/plan."""

import json

import pytest

from gcm import oredict, patterns, planner, store
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

    def test_the_last_slot_is_tried_first_and_others_can_be_chosen(self):
        # As AE2 orders patterns: a pattern's priority is its slot (with no
        # interface priority, which OC doesn't report), the highest first.
        side = pat([it("Dust"), it("Plate")], [it("Ore")], "Washer", slot=1)
        result = plan([PLATE, side], {"Ingot": 10, "Ore": 10}, "Plate", 1)
        root = result["root"]
        assert root["pattern"]["provider"] == "Washer"
        assert [a["provider"] for a in root["alternatives"]] == ["Washer", "Bender"]
        bender = root["alternatives"][1]["id"]
        chosen = plan([PLATE, side], {"Ingot": 10, "Ore": 10}, "Plate", 1, choices={key("Plate"): bender})["root"]
        assert chosen["pattern"]["provider"] == "Bender"
        assert [c["name"] for c in chosen["children"]] == ["Ingot"]

    def test_a_short_pattern_makes_what_it_can_and_the_next_makes_the_rest(self):
        # Dust from diamonds (tried first: the later slot) or from industrial
        # diamonds; 3 diamonds in stock, 10 dust asked for.
        from_gem = pat([it("Dust")], [it("Diamond")], "Macerator", slot=1)
        from_industrial = pat([it("Dust")], [it("Industrial Diamond")], "Macerator", slot=0)
        result = plan([from_industrial, from_gem], {"Diamond": 3, "Industrial Diamond": 100}, "Dust", 10)
        root = result["root"]
        assert root["split"] is True and root["craft"] == 10 and result["missing"] == []
        assert [(c["status"], c["craft"], c["children"][0]["name"]) for c in root["children"]] == [
            ("via", 3, "Diamond"), ("via", 7, "Industrial Diamond")]
        assert "alternatives" not in root

    def test_when_every_pattern_is_short_the_first_takes_the_rest(self):
        from_gem = pat([it("Dust")], [it("Diamond")], "Macerator", slot=1)
        from_industrial = pat([it("Dust")], [it("Industrial Diamond")], "Macerator", slot=0)
        result = plan([from_industrial, from_gem], {"Diamond": 3, "Industrial Diamond": 2}, "Dust", 10)
        parts = result["root"]["children"]
        assert [(c["children"][0]["name"], c["craft"]) for c in parts] == [
            ("Diamond", 3), ("Industrial Diamond", 2), ("Diamond", 5)]
        assert [(m["name"], m["missing"]) for m in result["missing"]] == [("Diamond", 5)]

    def test_leftovers_are_used_before_stock(self):
        # The frame and the gear each need a bolt; bolts come 4 a batch, so
        # the gear's comes from what the frame's batch left over.
        gear = pat([it("Gear")], [it("Frame"), it("Bolt")], "Assembler")
        frame = pat([it("Frame")], [it("Bolt"), it("Ingot")], "Assembler", slot=1)
        result = plan([gear, frame, BOLTS], {"Ingot": 100, "Bolt": 0}, "Gear", 1)
        frame_step, bolt = result["root"]["children"]
        assert (frame_step["children"][0]["craft"], frame_step["children"][0]["batches"]) == (4, 1)
        assert (bolt["status"], bolt["from_leftovers"], bolt["from_stock"]) == ("stock", 1, 0)
        assert (totals(result, "Bolt")["craft"], totals(result, "Bolt")["from_leftovers"]) == (4, 1)
        # Leftovers go before stock too.
        result = plan([gear, frame, BOLTS], {"Ingot": 100, "Bolt": 50}, "Gear", 1)
        assert result["root"]["children"][1]["from_stock"] == 1  # nothing was made to be left over

    def test_byproducts_are_leftovers_too(self):
        washer = pat([it("Dust"), it("Tiny Dust", 2)], [it("Ore")], "Washer")
        both = pat([it("Mix")], [it("Dust"), it("Tiny Dust", 2)], "Mixer")
        result = plan([washer, both], {"Ore": 10}, "Mix", 1)
        dust, tiny = result["root"]["children"]
        assert dust["status"] == "craft" and tiny["from_leftovers"] == 2 and tiny["status"] == "stock"

    def test_a_pattern_is_not_used_again_below_itself(self):
        # Ingot <- Molten <- Ingot: the inner ingot can't use the solidifier
        # again, but another pattern for ingots still can be.
        to_ingot = pat([it("Ingot")], [fluid("Molten Iron", 144)], "Solidifier", slot=1)
        to_molten = pat([fluid("Molten Iron", 144)], [it("Ingot")], "Extractor")
        smelt = pat([it("Ingot")], [it("Dust")], "Furnace", slot=0)
        root = plan([to_ingot, to_molten, smelt], {"Dust": 0}, "Ingot", 1)["root"]
        assert root["pattern"]["provider"] == "Solidifier"
        inner = child(child(root, "Molten Iron"), "Ingot")
        assert inner["pattern"]["provider"] == "Furnace"

    def test_past_its_work_budget_a_plan_stops_trying_patterns(self, monkeypatch):
        from_gem = pat([it("Dust")], [it("Diamond")], "Macerator", slot=1)
        from_industrial = pat([it("Dust")], [it("Industrial Diamond")], "Macerator", slot=0)
        monkeypatch.setattr(planner, "MAX_WORK", 0)
        result = plan([from_industrial, from_gem], {"Diamond": 3, "Industrial Diamond": 100}, "Dust", 10)
        assert result["truncated"] is True
        assert result["root"]["pattern"]["provider"] == "Macerator" and "split" not in result["root"]

    def test_a_pattern_needing_what_it_makes_asks_for_it_once(self):
        # A catalyst: 1 Seed in, 1 Seed back, plus the crop, per batch.
        farm = pat([it("Seed"), it("Crop", 4)], [it("Seed"), it("Water")], "Farm")
        result = plan([farm], {"Seed": 1, "Water": 100}, "Crop", 40)
        root = result["root"]
        assert root["batches"] == 10
        assert [(c["name"], c["need"]) for c in root["children"]] == [("Seed", 1), ("Water", 10)]
        assert result["missing"] == []

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


@pytest.fixture
def ore_dict(tmp_path):
    """Loads an ore dictionary of {name: [item names]} (items as it()
    makes them), and unloads it afterwards."""
    def load(names):
        path = tmp_path / "ore_dict.json"
        path.write_text(json.dumps({
            name: [f"gregtech:gt.metaitem.01:{it(n)['damage']}" for n in members] for name, members in names.items()
        }))
        oredict.load(str(path))
    yield load
    oredict.load(None)


def crafting(outputs, inputs, provider="Molecular Assembler", slot=0, substitute=False):
    p = pat(outputs, inputs, provider, slot)
    p["crafting"] = True
    p["substitute"] = substitute
    return p


class TestSubstitutes:
    """A crafting pattern with Substitute ticked, as AE2 plans one: Block
    of Diamond from 9 diamonds, where Industrial Diamond is a diamond too
    (gemDiamond)."""

    def patterns(self, substitute=True):
        block = crafting([it("Block")], [it("Diamond", 9)], substitute=substitute)
        sledge = pat([it("Diamond", 2)], [it("Flawless")], "Sledgehammer", slot=2)
        implosion = pat([it("Industrial Diamond", 3)], [it("Diamond Dust", 4)], "Implosion", slot=1)
        implosion["be_substitute"] = True
        return [block, sledge, implosion]

    def test_the_item_then_alternatives_from_stock_then_patterns_then_stand_ins(self, ore_dict):
        ore_dict({"gemDiamond": ["Diamond", "Industrial Diamond"]})
        stock = {"Diamond": 9, "Industrial Diamond": 9, "Flawless": 9, "Diamond Dust": 400}
        result = plan(self.patterns(), stock, "Block", 10)
        diamond = child(result["root"], "Diamond")
        assert result["missing"] == []
        assert diamond["from_stock"] == 9
        assert [(s["name"], s["from_stock"]) for s in diamond["substitutes"]] == [("Industrial Diamond", 9)]
        # 72 to make: the diamonds' own pattern makes the 18 its flawless
        # diamonds cover, the implosion compressor the other 54, as
        # Industrial Diamond.
        assert diamond["split"] is True
        assert [(c["name"], c["pattern"]["provider"], c["craft"], c.get("substitute", False))
                for c in diamond["children"]] == [
            ("Diamond", "Sledgehammer", 18, False), ("Industrial Diamond", "Implosion", 54, True)]
        industrial = totals(result, "Industrial Diamond")
        assert (industrial["from_stock"], industrial["craft"]) == (9, 54)

    def test_an_alternative_alone_still_shows_which(self, ore_dict):
        ore_dict({"gemDiamond": ["Diamond", "Industrial Diamond"]})
        diamond = child(plan(self.patterns(), {"Diamond Dust": 400}, "Block", 1)["root"], "Diamond")
        # The sledgehammer has nothing to work with: everything from the
        # implosion compressor, a part of its own row.
        assert [(c["name"], c["craft"]) for c in diamond["children"]] == [("Industrial Diamond", 9)]

    def test_without_substitute_ticked_only_the_item_will_do(self, ore_dict):
        ore_dict({"gemDiamond": ["Diamond", "Industrial Diamond"]})
        result = plan(self.patterns(substitute=False), {"Industrial Diamond": 900}, "Block", 1)
        assert [(m["name"], m["missing"]) for m in result["missing"]] == [("Flawless", 5)]

    def test_without_an_ore_dictionary_nothing_substitutes(self):
        result = plan(self.patterns(), {"Industrial Diamond": 900}, "Block", 1)
        assert "substitutes" not in child(result["root"], "Diamond")


class TestPaging:
    def test_plans_far_past_the_old_2000_step_cap(self):
        # A chain 3000 deep: each step needs the next.
        chain = [pat([it(f"Part {i}")], [it(f"Part {i + 1}")], slot=i) for i in range(3000)]
        result = plan(chain, {}, "Part 0", 1)
        assert result["steps"] == 3001 and result["truncated"] is False
        assert [m["name"] for m in result["missing"]] == ["Part 3000"]

    def test_trim_keeps_levels_and_counts_the_rest(self):
        root = plan(CHAIN, {"Ingot": 100}, "Gear", 2)["root"]  # nothing short
        top = planner.trim(root, 1)
        plate = child(top, "Plate")
        assert "children" not in plate and plate["more"] == 1
        assert planner.trim(root, 2)["children"][0]["children"][0]["name"] == "Ingot"
        # A step that was planned but has nothing below keeps an empty list.
        assert planner.trim(plan(CHAIN, {"Plate": 10, "Bolt": 10}, "Gear", 1)["root"], 0)["more"] == 2

    def test_missing_below_counts_short_steps_under_each_step(self):
        # 8 ingots for 9 needed: the plates take them all, the bolt is short.
        root = plan(CHAIN, {"Ingot": 8}, "Gear", 2)["root"]
        plate, bolt = child(root, "Plate"), child(root, "Bolt")
        assert root["missing_below"] == 1 and bolt["missing_below"] == 1
        assert "missing_below" not in plate and "missing_below" not in child(plate, "Ingot")
        # Nothing in stock: both ingot steps are short.
        assert plan(CHAIN, {}, "Gear", 2)["root"]["missing_below"] == 2

    def test_a_missing_item_lists_where_it_is_short(self, monkeypatch):
        result = plan(CHAIN, {}, "Gear", 2)
        ingot = totals(result, "Ingot")
        assert (ingot["at"], ingot["places"]) == (["0.0", "1.0"], 2)
        assert planner.subtree(result["root"], [1, 0])["missing"] == 1
        monkeypatch.setattr(planner, "MAX_JUMPS", 1)
        ingot = totals(plan(CHAIN, {}, "Gear", 2), "Ingot")
        assert (ingot["at"], ingot["places"]) == (["0.0"], 2)

    def test_trim_follows_the_way_to_what_is_missing(self):
        # Only the bolt's branch is short: it comes whole, the plates' doesn't.
        root = planner.trim(plan(CHAIN, {"Ingot": 8}, "Gear", 2)["root"], 0)
        assert [c["name"] for c in root["children"]] == ["Plate", "Bolt"]
        plate, bolt = child(root, "Plate"), child(root, "Bolt")
        assert plate["more"] == 1 and "children" not in plate
        assert child(bolt, "Ingot")["status"] == "missing"

    def test_trim_follows_a_deep_missing_path_until_its_budget(self):
        chain = [pat([it(f"Part {i}")], [it(f"Part {i + 1}")], slot=i) for i in range(3000)]
        root = plan(chain, {}, "Part 0", 1)["root"]
        node, depth = planner.trim(root, 2, budget=100), 0
        while node.get("children"):
            node, depth = node["children"][0], depth + 1
        assert depth == 102 and node["more"] == 1
        node = planner.trim(root, 2)
        while node.get("children"):
            node = node["children"][0]
        assert node["name"] == f"Part {2 + planner.MISSING_PATH_BUDGET}"

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
        # Where it's short, once: in `missing`, not again in `items`.
        assert data["plan"]["missing"][0]["at"] == ["0.0", "1.0"]  # under the plates and the bolt
        assert all("at" not in i for i in data["plan"]["items"])
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
