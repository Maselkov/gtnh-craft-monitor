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
from test_recipe_index import typed_index  # noqa: F401
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
    def test_typed_recipes_preserve_slots_and_bound_presentation(self, client, token, typed_index, monkeypatch):
        monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
        monkeypatch.setattr(recipes, "index", lambda version: typed_index)
        found = ok(client, token, "find_recipes", item="minecraft|planks|0|item", limit=4)
        assert found["recipes_total"] == 5 and len(found["recipes"]) == 4
        shaped, shapeless, furnace, gt = found["recipes"]
        assert (shaped["type"], shaped["width"], shaped["height"]) == ("shaped", 2, 2)
        assert shaped["slots"][1] is None
        assert shaped["slots"][0]["ore"] == "logWood"
        assert shaped["slots"][0]["alternatives"][1]["item"] == "minecraft|log||item"
        assert shaped["slots"][2]["alternatives_total"] == 20
        assert len(shaped["slots"][2]["alternatives"]) == 16
        assert shaped["slots"][2]["alternatives"][15]["name"] == "Part 15"
        assert shaped["approximate"] == ["nbt"] and "inputs" not in shaped
        assert "eu_per_tick" not in shaped and "seconds" not in shaped
        assert shapeless["type"] == "shapeless" and "width" not in shapeless
        assert furnace["seconds"] == 10 and "eu_per_tick" not in furnace
        assert gt["eu_per_tick"] == 30
        assert found["coverage"]["handlers"] == ["gregtech", "crafting", "furnace"]
        assert "classes" not in found["coverage"]["crafting"]
        assert "approximate" in found["note"]

    def test_typed_recipes_report_absent_results_and_wildcard_uses(self, client, token, typed_index, monkeypatch):
        monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
        monkeypatch.setattr(recipes, "index", lambda version: typed_index)
        found = ok(client, token, "find_recipes", item="minecraft|log|7|item")
        assert found["recipes_total"] == 0 and "No supported recipe" in found["note"]
        assert found["used_in_machines"] == ["Crafting Table", "Electric Blast Furnace", "Furnace"]

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

    def test_uses(self, client, token, game_recipes):
        found = ok(client, token, "find_recipes", item=f"Name of {DUST}", direction="using")
        assert found["direction"] == "using" and found["recipes_total"] == 2
        assert all(r["machine"] == "Electric Blast Furnace" for r in found["recipes"])
        assert {"name": f"Name of {DUST}", "amount": 2, "unit": "items"} in found["recipes"][0]["inputs"]
        assert found["machines"] == [{"machine": "Electric Blast Furnace", "recipes": 2}]
        assert "used_in_machines" not in found and found["note"].startswith("Recipes that take it")
        assert found["recipe_book"].endswith("/recipes?key=gregtech%3Agt.metaitem.01%3A2028&dir=using")

    def test_nothing_takes_it(self, client, token, game_recipes):
        found = ok(client, token, "find_recipes", item=INGOT_KEY, direction="using")
        assert found["recipes"] == [] and found["machines"] == []
        assert "No supported recipe in this bundle takes it" in found["note"]

    def test_one_machine_and_pages(self, client, token, typed_index, monkeypatch):
        monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
        monkeypatch.setattr(recipes, "index", lambda version: typed_index)
        found = ok(client, token, "find_recipes", item="minecraft|planks|0|item", limit=1)
        assert found["next_offset"] == 1 and found["offset"] == 0
        assert {"machine": "Crafting Table", "recipes": 2} in found["machines"]
        page = ok(client, token, "find_recipes", item="minecraft|planks|0|item", limit=1, offset=found["next_offset"])
        assert page["recipes"][0]["type"] == "shapeless" and page["next_offset"] == 2
        crafting = ok(client, token, "find_recipes", item="minecraft|planks|0|item", machine="crafting table")
        assert crafting["machine"] == "Crafting Table" and crafting["recipes_total"] == 2
        assert {r["type"] for r in crafting["recipes"]} == {"shaped", "shapeless"} and "next_offset" not in crafting
        assert crafting["recipe_book"].endswith("/recipes?key=minecraft%3Aplanks%3A0&map=crafting")
        partial = ok(client, token, "find_recipes", item="minecraft|planks|0|item", machine="blast")
        assert partial["machine"] == "Electric Blast Furnace" and partial["recipes_total"] == 2
        answer, is_error = call(client, token, "find_recipes", item="minecraft|planks|0|item", machine="Centrifuge")
        assert is_error and "Centrifuge" in answer and "Crafting Table" in answer

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


def test_find_recipes_includes_research_and_display_costs(tmp_path, monkeypatch):
    from gcm.mcp import tools
    from test_recipe_index import build_index, format3
    idx = build_index(tmp_path, format3())
    monkeypatch.setattr(tools, '_recipe_index', lambda: ('2.9.0', idx))
    try:
        result = tools.find_recipes(None, {'item': 'minecraft|planks|0|item', 'limit': 10})
        assembly = next(r for r in result['recipes'] if r['type'] == 'assembly_line')
        assert assembly['eu_per_tick'] == 32768 and assembly['research']['eut'] == 8192
        assert assembly['research']['item']['name'] == 'Oak Log'
        assert assembly['ingredient_groups'][0]['alternatives_total'] == 20
        assert len(assembly['ingredient_groups'][0]['alternatives']) == 16
        display = next(r for r in result['recipes'] if r['type'] == 'nei')
        assert display['requirements'] == {'mana': 2000}
        assert display['ingredient_groups'][0]['alternatives'][0]['unit'] == 'mB'
        assert 'eu_per_tick' not in display and 'seconds' not in display
    finally:
        idx.close()
