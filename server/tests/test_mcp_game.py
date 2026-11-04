"""The MCP tools that know the game (gcm/mcp/tools.py): GregTech recipes
and item tooltips from the game data, the base's own patterns, and the
pattern check."""

import json

import pytest

from conftest import bundle_index
from gcm import gamedata, item_info, recipe_index, recipes
from test_mcp import call, make_token, ok
from test_network_api import run_scan
from test_patterns_api import PLANKS_PATTERN, item as pattern_item, run_pattern_scan
from test_recipes import DUST, EBF, FREEZER, HOT, INGOT, ebf, freezer, write_recipes

INGOT_KEY = "gregtech|gt.metaitem.01|11028|item"
HOT_KEY = "gregtech|gt.metaitem.01|12028|item"
EBF_CONTROLLER = "gregtech|gt.blockmachines|1000|item"


@pytest.fixture()
def token():
    return make_token("mia", role="operator")


@pytest.fixture()
def game_recipes(tmp_path, monkeypatch):
    """GTNH 2.9.0 selected, with an EBF and a vacuum freezer recipe."""
    source = write_recipes(str(tmp_path / "r.json.gz"), {
        EBF: [ebf(eut=1920, heat=2700), ebf(dust=2, eut=120, heat=1700)],
        FREEZER: [freezer()],
    })
    path = str(tmp_path / "r.sqlite")
    recipe_index.build(source, path)
    idx = recipe_index.RecipeIndex(path)
    monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
    monkeypatch.setattr(recipes, "index", lambda version: idx)
    yield idx
    idx.close()


@pytest.fixture()
def tooltips(tmp_path, monkeypatch):
    """Game data with the EBF controller's tooltips."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "item_info.json").write_text(json.dumps({
        "tooltips": {"by_key": {"gregtech:gt.blockmachines:1000": "Controller block for the Electric Blast Furnace"}},
        "shift_tooltips": {"by_key": {"gregtech:gt.blockmachines:1000": "3x4x3 hollow\nHeating Coils: 2 rings\nMuffler Hatch: top middle"}},
        "mods": {"gregtech": "GregTech"},
    }))
    idx = bundle_index(bundle)
    for name in ("_index", "_loaded", "_mods", "_mods_by_lower"):
        monkeypatch.setattr(item_info, name, getattr(item_info, name))
    item_info.load(idx)
    return idx


class TestFindRecipes:
    def test_cheapest_first_with_names_tiers_and_heat(self, client, token, game_recipes):
        found = ok(client, token, "find_recipes", item=HOT_KEY)
        assert found["gtnh_version"] == "2.9.0" and found["name"] == f"Name of {HOT}"
        assert found["recipes_total"] == 2
        first, second = found["recipes"]
        assert (first["machine"], first["eu_per_tick"], first["tier"], first["coil_heat"]) == (
            "Electric Blast Furnace", 120, "MV", 1700)
        assert first["seconds"] == 20
        assert first["inputs"][0] == {"name": f"Name of {DUST}", "amount": 2, "unit": "items"}
        assert {"name": "Nitrogen", "amount": 1000, "unit": "mB"} in first["inputs"]
        assert second["tier"] == "EV"
        assert found["used_in_machines"] == ["Vacuum Freezer"]

    def test_by_name(self, client, token, game_recipes):
        found = ok(client, token, "find_recipes", item=f"Name of {INGOT}")
        assert found["item"] == INGOT_KEY and found["recipes"][0]["machine"] == "Vacuum Freezer"

    def test_several_matches_ask_which(self, client, token, game_recipes):
        answer = ok(client, token, "find_recipes", item="Name of gregtech")
        assert answer["matches_total"] == 4 and "item key" in answer["note"]
        assert all("|" in m["item"] for m in answer["matches"])

    def test_nothing_makes_it(self, client, token, game_recipes):
        found = ok(client, token, "find_recipes", item=f"Name of {DUST}")
        assert found["recipes"] == [] and "crafting table" in found["note"]
        assert found["used_in_machines"] == ["Electric Blast Furnace"]

    def test_without_game_data(self, client, token, monkeypatch):
        monkeypatch.setattr(gamedata, "selected_version", lambda: None)
        answer, is_error = call(client, token, "find_recipes", item=HOT_KEY)
        assert is_error and "No GTNH version" in answer
        monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
        monkeypatch.setattr(recipes, "index", lambda version: None)
        answer, is_error = call(client, token, "find_recipes", item=HOT_KEY)
        assert is_error and "has no recipes" in answer

    def test_unknown_name(self, client, token, game_recipes):
        answer, is_error = call(client, token, "find_recipes", item="Unobtainium")
        assert is_error and "Unobtainium" in answer


class TestGameItem:
    def test_a_multiblocks_structure(self, client, token, tooltips, monkeypatch):
        monkeypatch.setattr(gamedata, "selected_version", lambda: None)
        found = ok(client, token, "game_item", item=EBF_CONTROLLER)
        assert found["tooltip"] == "Controller block for the Electric Blast Furnace"
        assert "Heating Coils" in found["shift_tooltip"] and found["mod"] == "GregTech"
        assert found["in_network"] is None

    def test_by_name_from_the_network(self, client, token, tooltips, api_headers, monkeypatch):
        monkeypatch.setattr(gamedata, "selected_version", lambda: None)
        run_scan(client, api_headers, [[{"name": "Electric Blast Furnace", "mod": "gregtech",
                                         "internal": "gt.blockmachines", "damage": 1000, "size": 2}]])
        found = ok(client, token, "game_item", item="electric blast furnace")
        assert found["item"] == EBF_CONTROLLER and found["in_network"] == 2 and found["shift_tooltip"]

    def test_no_tooltip(self, client, token, tooltips, monkeypatch):
        monkeypatch.setattr(gamedata, "selected_version", lambda: None)
        found = ok(client, token, "game_item", item="minecraft|stone|0|item")
        assert "no tooltip" in found["note"]


class TestBasePatterns:
    def test_lists_the_machines_that_make_it(self, client, token, api_headers):
        run_scan(client, api_headers, [[pattern_item("log", "Oak Wood", size=3, item_id=17)]], interface_terminal=True)
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])
        found = ok(client, token, "base_patterns", item="minecraft|planks|0|item")
        assert found["patterns_total"] == 1
        row = found["patterns"][0]
        assert row["machine"] == "Molecular Assembler" and row["at"] == {"x": -1413, "y": 57, "z": -123, "dim": 0}
        assert row["kind"] == "crafting" and row["takes"][0]["name"] == "Oak Wood"
        none = ok(client, token, "base_patterns", item="minecraft|stone|0|item")
        assert none["patterns"] == [] and "No pattern" in none["note"]

    def test_before_patterns_are_read(self, client, token):
        assert call(client, token, "base_patterns", item="minecraft|planks|0|item")[1] is True


class TestPatternCheck:
    def test_operators_only(self, client):
        viewer = make_token("vera")
        answer, is_error = call(client, viewer, "pattern_check")
        assert is_error and "Only operators" in answer

    def test_no_update_to_check(self, client, token, monkeypatch):
        monkeypatch.setattr(recipes, "default_versions", lambda: ("2.9.0", None))
        answer, is_error = call(client, token, "pattern_check")
        assert is_error and "no GTNH update" in answer

    def test_preparing_then_ready(self, client, token, monkeypatch):
        monkeypatch.setattr(recipes, "default_versions", lambda: ("2.8.4", "2.9.0"))
        monkeypatch.setattr(recipes, "result", lambda a, b: ("preparing", None))
        assert ok(client, token, "pattern_check")["state"] == "preparing"
        provider = {"name": "Electric Blast Furnace", "x": 1, "y": 2, "z": 3, "dim": 0}
        body = {"from": "2.8.4", "to": "2.9.0", "counts": {"ok": 1, "broken": 1, "changed": 1},
                "patterns": [
                    {"status": "ok", "provider": provider, "outputs": [{"name": "A"}]},
                    {"status": "changed", "provider": provider, "outputs": [{"name": "B"}],
                     "changes": ["needs 2700 K coils, not 1700 K"]},
                    {"status": "broken", "provider": provider, "outputs": [{"name": "C", "icon": "x.png"}]},
                ]}
        monkeypatch.setattr(recipes, "result", lambda a, b: ("ready", body))
        found = ok(client, token, "pattern_check")
        assert [r["status"] for r in found["patterns"]] == ["broken", "changed"]
        assert found["patterns"][0]["makes"] == ["C"] and found["patterns"][1]["changes"]
        assert found["patterns_needing_attention"] == 2
