"""Where ores generate: gtneioreplugin's Vein Stats and Small Ore Stats,
from a map's ore_gen records (NeiRecipes.oreGen)."""
import copy

import pytest

from gcm import recipe_card, recipe_index, recipe_viewer
from gcm.mcp import tools
from test_recipe_index import build_index

VEINS = "nei.gtneioreplugin.plugin.gregtech5.PluginGT5VeinStat::"
SMALL = "nei.gtneioreplugin.plugin.gregtech5.PluginGT5SmallOreStat::"
SAMARIUM, SAMARIUM_MOON = "gregtech:gt.blockores3:4098", "gregtech:gt.blockores3:5098"
NEODYMIUM, TARTARITE = "gregtech:gt.blockores3:4100", "gregtech:gt.blockores3:4096"
CALLISTO, CERES = "gtneioreplugin:blockDimensionDisplay_Ca:0", "gtneioreplugin:blockDimensionDisplay_Ce:0"
SMALL_ORE, DUST, GEM = "gregtech:gt.blockores2:17035", "gregtech:gt.metaitem.01:2035", "gregtech:gt.metaitem.01:8035"
NAMES = {SAMARIUM: "Callisto Samarium Ore", SAMARIUM_MOON: "Ceres Samarium Ore", NEODYMIUM: "Callisto Neodymium Ore",
         TARTARITE: "Callisto Tartarite Ore", CALLISTO: "Callisto", CERES: "Ceres",
         SMALL_ORE: "Small Copper Ore", DUST: "Copper Dust", GEM: "Copper Nugget"}


def bundle():
    keys = list(NAMES)
    i = keys.index
    vein = {"type": "ore_vein", "name": "§lSamarium Vein", "height": "10-40", "weight": 10, "size": 16, "density": 2,
            "dim_heights": {"Ca": "10-40"},
            "layers": [{"role": "primary", "alts": [[i(SAMARIUM), 1, None, []], [i(SAMARIUM_MOON), 1, None, []]]},
                       {"role": "secondary", "alts": [[i(NEODYMIUM), 1, None, []]]},
                       {"role": "between", "alts": [[i(TARTARITE), 1, None, []]]},
                       {"role": "sporadic", "alts": [[i(TARTARITE), 1, None, []]]}],
            "dims": [[i(CALLISTO), 1, None, []], [i(CERES), 1, None, []]]}
    small = {"type": "small_ore", "name": "Copper", "height": "5-60", "amount": 32,
             "ores": [[i(SMALL_ORE), 1, None, []]], "dust": [[i(DUST), 1, None, []]],
             "drops": [[i(GEM), 1, 2500, []]], "dims": [[i(CALLISTO), 1, None, []]]}
    return {
        "format": 4, "items": keys, "item_names": [NAMES[k] for k in keys], "fluids": [], "fluid_names": [],
        "display_stacks": {}, "ui_assets": {}, "research_info": {}, "aspects": {},
        "coverage": {"handlers": ["gregtech", "nei"], "identity": "item_id_damage", "limitations": []},
        "maps": {
            VEINS: {"machines": [{"name": "Vein Stats"}], "recipes": [], "archive": [{"raw": True}], "ore_gen": [vein]},
            SMALL: {"machines": [{"name": "Small Ore Stats"}], "recipes": [], "ore_gen": [small]},
        },
    }


@pytest.fixture()
def idx(tmp_path):
    index = build_index(tmp_path, bundle())
    yield index
    index.close()


def test_an_ores_recipes_show_its_veins_and_a_dimensions_uses_list_them(idx):
    for ore in (SAMARIUM, SAMARIUM_MOON, NEODYMIUM, TARTARITE):
        assert idx.counts_making(recipe_index.ITEM, ore) == {VEINS: 1}
    assert idx.counts_using(recipe_index.ITEM, CALLISTO) == {VEINS: 1, SMALL: 1}
    assert idx.counts_using(recipe_index.ITEM, CERES) == {VEINS: 1}
    for item in (SMALL_ORE, DUST, GEM):
        assert idx.counts_making(recipe_index.ITEM, item) == {SMALL: 1}
    (_, _, vein), = idx.making(recipe_index.ITEM, NEODYMIUM)
    assert vein["type"] == "ore_vein" and (vein["height"], vein["weight"], vein["size"], vein["density"]) == ("10-40", 10, 16, 2)
    assert [layer["role"] for layer in vein["layers"]] == ["primary", "secondary", "between", "sporadic"]
    assert vein["o"] == [[SAMARIUM, 1], [SAMARIUM_MOON, 1], [NEODYMIUM, 1], [TARTARITE, 1]]


@pytest.mark.parametrize("change", [
    lambda r: r.update(type="vein"),
    lambda r: r.update(layers=[]),
    lambda r: r["layers"][0].update(role="core"),
    lambda r: r["layers"][0].update(alts=[[99, 1]]),
    lambda r: r.update(weight="10"),
])
def test_malformed_ore_records_are_rejected(tmp_path, change):
    data = bundle()
    change(data["maps"][VEINS]["ore_gen"][0])
    with pytest.raises(ValueError):
        build_index(tmp_path, data)


def test_older_servers_skip_ore_gen_as_they_skip_archives(tmp_path):
    # The field is new; the format isn't: a reader that ignores unknown map
    # fields still reads the bundle. Simulate one by dropping the field.
    data = copy.deepcopy(bundle())
    for entry in data["maps"].values():
        entry.pop("ore_gen")
    index = build_index(tmp_path, data)
    try:
        assert index.counts_making(recipe_index.ITEM, SAMARIUM) == {}
    finally:
        index.close()


def texts(row):
    return [t["text"] for t in row["preview"]["texts"]]


def test_vein_page_reads_like_neis(idx):
    rows = idx.making(recipe_index.ITEM, SAMARIUM)
    row = recipe_viewer.present_recipes(rows, idx)[0]
    preview = row["preview"]
    assert preview["family"] == "ore_gen"
    assert texts(row) == ["Samarium Vein", "Primary", "Callisto Samarium Ore", "Secondary", "Callisto Neodymium Ore",
                          "Between", "Callisto Tartarite Ore", "Sporadic", "Callisto Tartarite Ore",
                          "Height: 10-40", "Weight: 10", "Generated world:"]
    title = preview["texts"][0]
    assert title["align"] == "center" and title["title"] is True
    # The primary layer cycles its stone types; the dimensions sit in a row.
    first = preview["slots"][0]
    assert (first["x"], first["y"]) == (2, 16)
    assert [a["key"] for a in first["alternatives"]] == [SAMARIUM, SAMARIUM_MOON]
    dims = preview["slots"][4:]
    assert [(s["x"], s["alternatives"][0]["key"]) for s in dims] == [(2, CALLISTO), (20, CERES)]
    assert dims[0]["y"] == preview["texts"][-1]["y"] + 10
    assert preview["height"] == dims[0]["y"] + 20
    assert row["ore_gen"] == {"kind": "vein", "name": "Samarium Vein", "height": "10-40", "weight": 10, "size": 16,
                              "density": 2, "dim_heights": {"Ca": "10-40"}}
    scene = recipe_card.scene(row)
    assert [t["text"] for t in scene["texts"]] == texts(row)


def test_small_ore_page(idx):
    row = recipe_viewer.present_recipes(idx.making(recipe_index.ITEM, SMALL_ORE), idx)[0]
    assert texts(row) == ["Copper", "Small Copper Ore", "Height: 5-60", "Amount per chunk: 32", "Drops:", "Generated world:"]
    slots = row["preview"]["slots"]
    assert [s["alternatives"][0]["key"] for s in slots] == [SMALL_ORE, DUST, GEM, CALLISTO]
    assert [(s["x"], s["y"]) for s in slots[:2]] == [(2, 16), (20, 16)]
    assert row["preview"]["texts"][1]["x"] == 42
    assert slots[2]["alternatives"][0]["chance"] == 25


def test_find_recipes_says_where_an_ore_generates(idx, monkeypatch):
    monkeypatch.setattr(tools, "_recipe_index", lambda: ("2.9.0", idx))
    monkeypatch.setattr(tools, "_pick", lambda query, index: (f"item:{NEODYMIUM}", None))
    monkeypatch.setattr(tools, "_recipe_key", lambda key: (recipe_index.ITEM, NEODYMIUM))
    out = tools.find_recipes(None, {"item": "neodymium ore"})
    (vein,) = out["recipes"]
    assert vein["outputs"] == [] and "inputs" not in vein
    assert vein["ore_generation"] == {
        "kind": "vein", "name": "Samarium Vein", "height": "10-40", "weight": 10, "size": 16, "density": 2,
        "dim_heights": {"Ca": "10-40"}, "dimensions": ["Callisto", "Ceres"],
        "layers": [{"layer": "primary", "ore": "Callisto Samarium Ore"}, {"layer": "secondary", "ore": "Callisto Neodymium Ore"},
                   {"layer": "between", "ore": "Callisto Tartarite Ore"}, {"layer": "sporadic", "ore": "Callisto Tartarite Ore"}]}

    monkeypatch.setattr(tools, "_pick", lambda query, index: (f"item:{GEM}", None))
    monkeypatch.setattr(tools, "_recipe_key", lambda key: (recipe_index.ITEM, GEM))
    (small,) = tools.find_recipes(None, {"item": "copper nugget"})["recipes"]
    assert small["ore_generation"]["ore"] == "Small Copper Ore"
    assert small["ore_generation"]["drops"] == [{"name": "Copper Nugget", "amount": 1, "unit": "items", "chance_percent": 25.0}]
    assert small["ore_generation"]["drops"][0] == {"name": "Copper Nugget", "amount": 1, "unit": "items", "chance_percent": 25.0}

