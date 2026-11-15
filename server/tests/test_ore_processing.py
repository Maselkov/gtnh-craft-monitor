"""GregTech ore processing diagrams, composed into the recipe index from
its own GT recipes (gcm/ore_processing.py)."""
import pytest

from gcm import ore_processing, recipe_card, recipe_index, recipe_viewer
from gcm.mcp import tools
from test_recipe_index import build_index

ORE, NETHER_ORE, RAW = "gregtech:gt.blockores2:69", "gregtech:gt.blockores2:1069", "gregtech:gt.metaitem.03:5069"
CRUSHED, PURIFIED, CENTRIFUGED = "gregtech:gt.metaitem.01:5069", "gregtech:gt.metaitem.01:6069", "gregtech:gt.metaitem.01:7069"
IMPURE, PURE_DUST, DUST = "gregtech:gt.metaitem.01:3069", "gregtech:gt.metaitem.01:4069", "gregtech:gt.metaitem.01:2069"
BYPRODUCT, STONE_DUST, GEM = "gregtech:gt.metaitem.01:2070", ore_processing.STONE_DUST, "gregtech:gt.metaitem.01:8069"
OTHER_ORE = "GalacticraftAmunRa:tile.basaltMultiOre:6"
MACERATOR, WASHER, THERMAL = "gregtech:gt.blockmachines:201", "gregtech:gt.blockmachines:211", "gregtech:gt.blockmachines:221"
STEAM_MACERATOR, CENTRIFUGE, BATH = "gregtech:gt.blockmachines:106", "gregtech:gt.blockmachines:231", "gregtech:gt.blockmachines:241"
SIFTER = "gregtech:gt.blockmachines:251"
ITEMS = {ORE: "Samarium Ore", NETHER_ORE: "Nether Samarium Ore", RAW: "Raw Samarium Ore", CRUSHED: "Crushed Samarium Ore",
         PURIFIED: "Purified Samarium Ore", CENTRIFUGED: "Centrifuged Samarium Ore", IMPURE: "Impure Pile of Samarium Dust",
         PURE_DUST: "Purified Pile of Samarium Dust", DUST: "Samarium Dust", BYPRODUCT: "Byproduct Dust",
         STONE_DUST: "Stone Dust", GEM: "Samarium Gem", OTHER_ORE: "Samarium Ore"}


def bundle(same_thermal=True, sifter=True):
    keys = list(ITEMS)
    i = keys.index

    def gt(inputs, outputs, fluids=()):
        return {"type": "gt", "i": [[i(k), 1] for k in inputs], "o": [[i(k), n, *([c] if c else [])] for k, n, c in outputs],
                "f": [[f, 1000] for f in fluids], "e": 16, "d": 200}
    maps = {
        ore_processing.MACERATOR: ([{"name": "Steam Macerator", "key": STEAM_MACERATOR}, {"name": "Basic Macerator", "key": MACERATOR}], [
            gt([NETHER_ORE], [(CRUSHED, 4, None), (BYPRODUCT, 2, 2000)]),
            gt([ORE], [(CRUSHED, 2, None), (BYPRODUCT, 2, 1000), (STONE_DUST, 1, 5000)]),
            gt([OTHER_ORE], [(CRUSHED, 2, None)]),
            gt([RAW], [(CRUSHED, 2, None), (BYPRODUCT, 2, 500)]),
            gt([CRUSHED], [(IMPURE, 1, None), (BYPRODUCT, 1, 1000)]),
            gt([PURIFIED], [(PURE_DUST, 1, None), (BYPRODUCT, 1, 1000)]),
            gt([CENTRIFUGED], [(DUST, 1, None), (BYPRODUCT, 1, 1000)])]),
        ore_processing.ORE_WASHER: ([{"name": "Basic Ore Washing Plant", "key": WASHER}], [
            gt([CRUSHED], [(PURIFIED, 1, None), (BYPRODUCT, 1, 1111), (STONE_DUST, 1, None)], fluids=[0])]),
        ore_processing.THERMAL_CENTRIFUGE: ([{"name": "Basic Thermal Centrifuge", "key": THERMAL}], [
            gt([CRUSHED], [(CENTRIFUGED, 1, None), (BYPRODUCT, 1, 1111)]),
            gt([PURIFIED], [(CENTRIFUGED, 1, None), (BYPRODUCT, 1, 1111 if same_thermal else 2222)])]),
        ore_processing.CENTRIFUGE: ([{"name": "Basic Centrifuge", "key": CENTRIFUGE}], [
            gt([IMPURE], [(DUST, 1, None), (BYPRODUCT, 1, 1111)]),
            gt([PURE_DUST], [(DUST, 1, None), (BYPRODUCT, 1, 1111)])]),
        ore_processing.CHEMICAL_BATH: ([{"name": "Basic Chemical Bath", "key": BATH}], [
            gt([CRUSHED], [(PURIFIED, 1, None), (BYPRODUCT, 1, 7000), (STONE_DUST, 1, 4000)], fluids=[1])]),
    }
    if sifter:
        maps[ore_processing.SIFTER] = ([{"name": "Basic Sifting Machine", "key": SIFTER}], [
            gt([PURIFIED], [(GEM, 1, 100 * n) for n in range(1, 10)])])
    return {
        "format": 2, "items": keys, "item_names": [ITEMS[k] for k in keys],
        "fluids": ["water", "mercury"], "fluid_names": ["Water", "Mercury"],
        "coverage": {"handlers": ["gregtech"], "identity": "item_id_damage", "limitations": []},
        "maps": {name: {"machines": machines, "recipes": rs} for name, (machines, rs) in maps.items()},
    }


@pytest.fixture()
def idx(tmp_path):
    index = build_index(tmp_path, bundle())
    yield index
    index.close()


def diagram(index):
    rows = index.making(recipe_index.ITEM, DUST)
    found = [body for _, map_name, body in rows if map_name == recipe_index.ORE_PROCESSING_MAP]
    assert len(found) == 1
    return found[0]


def slot_at(body, x, y):
    """The slot centred on grid point (x, y), and its stacks."""
    cx, cy = ore_processing.grid(x, y)
    for slot, group in zip(body["preview"]["slots"], body["groups"], strict=True):
        if (slot["x"] + 9, slot["y"] + 9) == (cx, cy):
            return slot, group["alts"]
    raise AssertionError(f"no slot at {(x, y)}")


def test_one_diagram_per_crushed_ore_shows_from_every_stage(idx):
    body = diagram(idx)
    assert body["type"] == "ore_tree" and body["crushed"] == CRUSHED
    counts = {k: idx.counts_using(recipe_index.ITEM, k).get(recipe_index.ORE_PROCESSING_MAP) for k in
              (ORE, RAW, CRUSHED, PURIFIED, IMPURE, PURE_DUST, CENTRIFUGED, DUST)}
    assert counts == {ORE: 1, RAW: 1, CRUSHED: 1, PURIFIED: 1, IMPURE: 1, PURE_DUST: 1, CENTRIFUGED: 1, DUST: None}
    assert idx.counts_making(recipe_index.ITEM, BYPRODUCT)[recipe_index.ORE_PROCESSING_MAP] == 1
    # Stone dust is left out, as NEI Custom Diagram does; machines aren't products.
    assert not idx.counts_making(recipe_index.ITEM, STONE_DUST).get(recipe_index.ORE_PROCESSING_MAP)
    assert not idx.counts_making(recipe_index.ITEM, MACERATOR).get(recipe_index.ORE_PROCESSING_MAP)
    assert idx.machines(recipe_index.ORE_PROCESSING_MAP) == [{"name": "GregTech Ore Processing", "key": None}]
    assert idx.map_icon(recipe_index.ORE_PROCESSING_MAP) == ORE
    # It isn't a recipe the pattern check can match.
    assert recipe_index.ORE_PROCESSING_MAP in recipe_index.NON_GT_MAPS


def test_layout_follows_nei_custom_diagram(idx):
    body = diagram(idx)
    # GT's ores first, the plain stone one before the nether one; the raw ore beside them.
    assert [a[0] for a in slot_at(body, 4, 2)[1]] == [ORE, NETHER_ORE, OTHER_ORE]
    assert slot_at(body, 6, 2)[1] == [[RAW, 1, None]]
    # The ore's macerator, drawn as its first electric machine, unframed.
    machine, stacks = slot_at(body, 4, 5)
    assert stacks == [[MACERATOR, 1, None]] and machine["framed"] is False and machine["label"] == "Basic Macerator"
    assert slot_at(body, 4, 7)[1] == [[CRUSHED, 2, None]]
    assert slot_at(body, 4, 8.5)[1] == [[BYPRODUCT, 2, 1000]]
    assert slot_at(body, 6, 8.5)[1] == [[BYPRODUCT, 2, 500]]
    assert slot_at(body, 2, 14)[1] == [[IMPURE, 1, None]]
    assert slot_at(body, 8, 10)[1] == [[PURIFIED, 1, None]]
    assert slot_at(body, 10, 14)[1] == [[PURE_DUST, 1, None]]
    assert slot_at(body, 0, 16)[1] == [[DUST, 1, None]]
    assert slot_at(body, 8, 16)[1] == [[DUST, 1, None]]
    # Both feed one thermal centrifuge when they give the same.
    assert slot_at(body, 6, 12)[1] == [[THERMAL, 1, None]]
    assert slot_at(body, 6, 14)[1] == [[CENTRIFUGED, 1, None]]
    assert slot_at(body, 4, 16)[1] == [[DUST, 1, None]]
    # The mercury bath, its outputs stacked up from the slot over the machine.
    bath, _ = slot_at(body, 0, 8)
    assert bath["label"] == "Chemical Bath (Mercury)"
    assert [slot_at(body, 0, y)[1][0] for y in (6, 4.5)] == [[PURIFIED, 1, None], [BYPRODUCT, 1, 7000]]
    # The sifter's gems, three by three.
    assert slot_at(body, 8.5, 5)[1] == [[GEM, 1, 100]]
    assert slot_at(body, 11.5, 2)[1] == [[GEM, 1, 900]]
    # No electromagnetic separator recipe, no branch.
    with pytest.raises(AssertionError):
        slot_at(body, 12, 14)
    assert {"x": 57, "y": 84, "dir": "S"} in body["preview"]["arrows"]
    assert {"x1": 57, "y1": 93, "x2": 81, "y2": 93} in body["preview"]["lines"]
    assert body["preview"]["width"] == 166
    assert body["preview"]["height"] == max(s["y"] for s in body["preview"]["slots"]) + 20


def test_different_thermal_centrifuge_outputs_get_one_machine_each(tmp_path):
    index = build_index(tmp_path, bundle(same_thermal=False, sifter=False))
    try:
        body = diagram(index)
        assert slot_at(body, 4, 10)[1] == [[THERMAL, 1, None]]
        assert slot_at(body, 6, 10)[1] == [[THERMAL, 1, None]]
        assert slot_at(body, 4, 13.5)[1] == [[BYPRODUCT, 1, 1111]]
        assert slot_at(body, 6, 13.5)[1] == [[BYPRODUCT, 1, 2222]]
        assert slot_at(body, 6, 16)[1] == [[MACERATOR, 1, None]]
        assert slot_at(body, 4, 16)[1] == [[DUST, 1, None]]
        assert {"x": 66, "y": 201, "dir": "W"} in body["preview"]["arrows"]
    finally:
        index.close()


def test_bundles_without_ore_processing_maps_get_no_diagrams(tmp_path):
    data = bundle()
    del data["maps"][ore_processing.THERMAL_CENTRIFUGE]
    index = build_index(tmp_path, data)
    try:
        assert recipe_index.ORE_PROCESSING_MAP not in index.maps()
    finally:
        index.close()


def test_raw_ores_are_told_from_ore_blocks():
    assert ore_processing.is_raw_ore("gregtech:gt.metaitem.03:5069", "Raw Samarium Ore")
    assert ore_processing.is_raw_ore("bartworks:gt.bwMetaGeneratedrawOre:11128", None)
    assert ore_processing.is_raw_ore("miscutils:oreRawCerite:0", None)
    assert ore_processing.is_raw_ore("othermod:thing:0", "Raw Thing Ore")
    assert not ore_processing.is_raw_ore("gregtech:gt.blockores2:69", "Samarium Ore")
    assert not ore_processing.is_raw_ore("miscutils:oreCerite:0", "Cerite Ore")


def test_viewer_card_and_mcp_present_the_diagram(idx, monkeypatch):
    rid, map_name, body = next(r for r in idx.making(recipe_index.ITEM, DUST) if r[1] == recipe_index.ORE_PROCESSING_MAP)
    row = recipe_viewer.present_recipes([(rid, map_name, body)], idx)[0]
    preview = row["preview"]
    assert preview["family"] == "ore_tree"
    machine = next(s for s in preview["slots"] if s.get("note") == "Basic Macerator")
    assert machine["framed"] is False and machine["frame"] is None
    assert all(s["framed"] for s in preview["slots"] if "note" not in s)
    assert preview["lines"] == body["preview"]["lines"] and preview["arrows"] == body["preview"]["arrows"]
    assert recipe_viewer.map_info(idx, map_name)["name"] == "GregTech Ore Processing"
    scene = recipe_card.scene(row)
    assert scene["ore"] and scene["arrows"] == preview["arrows"]

    monkeypatch.setattr(tools, "_recipe_index", lambda: ("2.9.0", idx))
    monkeypatch.setattr(tools, "_pick", lambda query, index: (f"item:{DUST}", None))
    monkeypatch.setattr(tools, "_recipe_key", lambda key: (recipe_index.ITEM, DUST))
    out = tools.find_recipes(None, {"item": "samarium dust", "machine": "GregTech Ore Processing"})
    tree = out["recipes"][0]
    assert tree["machine"] == "GregTech Ore Processing" and tree["outputs"] == []
    assert "inputs" not in tree and "ingredient_groups" not in tree
    wash = next(s for s in tree["steps"] if s["input"]["name"] == "Crushed Samarium Ore" and s["outputs"][0]["name"] == "Purified Samarium Ore"
                and "fluid" not in s)
    assert wash["outputs"][1] == {"name": "Byproduct Dust", "amount": 1, "unit": "items", "chance_percent": 11.11}
    assert any(s.get("fluid") == "mercury" for s in tree["steps"])
