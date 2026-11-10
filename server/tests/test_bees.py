"""Forestry bees in the recipe book: species keys in the recipe index, the
breeding cards' chance and conditions, Bee Better At Bees' tree, the
network's bees opening their species, and find_recipes by species."""

import pytest

from gcm import gamedata, inventory, nbt, recipe_index, recipe_viewer, recipes
from nbt_fixtures import tag_hex
from test_mcp import make_token, ok
from test_network_api import run_scan
from test_recipe_index import build_index

PRINCESS, DRONE, QUEEN = "Forestry:beePrincessGE:0", "Forestry:beeDroneGE:0", "Forestry:beeQueenGE:0"
BREEDING = recipe_viewer.BEE_BREEDING_MAP
TREE = recipe_viewer.BBAB_TREE_MAP
UIDS = {"Forest": "forestry.speciesForest", "Meadows": "forestry.speciesMeadows",
        "Common": "forestry.speciesCommon", "Cultivated": "forestry.speciesCultivated",
        "Dragon Blood": "gtpp.bee.speciesDragon Blood"}
KINDS = {PRINCESS: "Princess", DRONE: "Drone", QUEEN: "Queen"}
ITEMS = [PRINCESS, DRONE, QUEEN, "minecraft:diamond_block:0"]


def snbt(species):
    uid = UIDS[species]
    return '{IsAnalyzed:1b,Genome:{Chromosomes:[0:{Slot:0b,UID0:"%s",UID1:"%s"},1:{Slot:1b,UID0:"x",UID1:"x"}]}}' % (uid, uid)


def ref(species, key):
    return f"{species}-{KINDS[key]}"


def bee(species, key):
    return [ITEMS.index(key), 1, None, [ref(species, key)]]


def slot(role, index, x, y):
    return {"role": role, "index": index, "x": x, "y": y, "w": 18, "h": 18}


def mutation(p1, p2, child, chance, conditions=()):
    return {"type": "nei", "groups": [{"alts": [bee(p1, PRINCESS)]}, {"alts": [bee(p2, DRONE)]}],
            "o": [bee(child, QUEEN)], "requirements": {"chance": chance, "requirements": list(conditions)},
            "approximate": ["nei_display"]}


def tree():
    """Cultivated, from Common and Forest; Common from Forest and Meadows;
    Forest a base species. Rows as BBAB lays them out."""
    groups = [{"alts": [[3, 1]]}, {"alts": [bee("Common", DRONE)]}, {"alts": [bee("Forest", DRONE)]},
              {"alts": [bee("Forest", DRONE)]}, {"alts": [bee("Meadows", DRONE)]}]
    slots = [slot("o", 0, 73, 0), slot("groups", 0, 73, 23), slot("groups", 1, 33, 49), slot("groups", 2, 113, 49),
             slot("groups", 3, 13, 99), slot("groups", 4, 53, 99)]
    return {"type": "nei", "groups": groups, "o": [bee("Cultivated", PRINCESS)], "approximate": ["nei_display"],
            "preview": {"family": "positioned", "width": 166, "height": 120, "layers": [], "slots": slots}}


def source():
    return {
        "format": 4,
        "display_stacks": {ref(s, k): {"key": k, "name": f"{s} {KINDS[k]}", "nbt": snbt(s), "icon": f"item/{s}.webp"}
                           for s in UIDS for k in KINDS},
        "coverage": {"handlers": ["nei"], "identity": "item_id_damage", "limitations": []},
        "items": ITEMS, "item_names": ["Scanned Princess", "Scanned Drone", "Scanned Queen", "Block of Diamond"],
        "fluids": [], "fluid_names": [],
        "maps": {
            BREEDING: {"machines": [{"name": "Bee Breeding"}], "recipes": [
                mutation("Forest", "Meadows", "Common", 15),
                mutation("Common", "Forest", "Cultivated", 12, ["Requires Warm temperature."]),
                mutation("Forest", "Dragon Blood", "Common", 5)]},
            TREE: {"machines": [{"name": "Bee Breeding Tree"}], "recipes": [tree()]},
        },
    }


@pytest.fixture()
def idx(tmp_path, monkeypatch):
    index = build_index(tmp_path, source())
    monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
    monkeypatch.setattr(recipes, "index", lambda version: index)
    yield index
    index.close()


def test_species_ids_come_from_the_genome_and_fit_in_a_key():
    assert recipe_index.species_from_snbt(snbt("Forest")) == "forestry.speciesForest"
    # GT++ has species with spaces.
    assert recipe_index.species_from_snbt(snbt("Dragon Blood")) == "gtpp.bee.speciesDragon_Blood"
    assert recipe_index.species_from_snbt("{charge:10}") is None
    assert recipe_index.species_id("bad\"uid" * 40) is None
    root = nbt.parse_hex(tag_hex([("Genome", nbt.COMPOUND, [("Chromosomes", nbt.LIST, (nbt.COMPOUND, [
        [("Slot", nbt.BYTE, 0), ("UID0", nbt.STRING, "forestry.speciesForest"), ("UID1", nbt.STRING, "forestry.speciesMeadows")],
    ]))])]))
    assert nbt.genome_species(root) == "forestry.speciesForest"
    assert nbt.describe(root) == "Hybrid Meadows"


def test_species_keys_find_only_that_species_and_bare_keys_all_of_them(idx):
    common = recipe_index.species_key(QUEEN, "forestry.speciesCommon")
    assert idx.counts_making(recipe_index.ITEM, common) == {BREEDING: 2}
    assert idx.counts_making(recipe_index.ITEM, recipe_index.species_key(QUEEN, "forestry.speciesCultivated")) == {BREEDING: 1}
    assert idx.counts_making(recipe_index.ITEM, QUEEN) == {BREEDING: 3}
    forest = recipe_index.species_key(DRONE, "forestry.speciesForest")
    assert idx.counts_using(recipe_index.ITEM, forest) == {BREEDING: 1, TREE: 1}
    assert idx.names(recipe_index.ITEM, [common]) == {common: "Common Queen"}
    rows, total = idx.search_names("Common")
    assert {k for _, k, _ in rows} == {recipe_index.species_key(k, "forestry.speciesCommon") for k in KINDS}
    assert total == 3


def test_species_key_parsing():
    assert recipe_viewer.parse_key(PRINCESS + "#forestry.speciesForest") == (recipe_index.ITEM, PRINCESS + "#forestry.speciesForest")
    for bad in (PRINCESS + "#", PRINCESS + "#a b", "Forestry:beePrincessGE:*#forestry.speciesForest"):
        with pytest.raises(ValueError):
            recipe_viewer.parse_key(bad)


def test_breeding_cards_show_species_chance_and_conditions(idx):
    key = recipe_index.species_key(QUEEN, "forestry.speciesCultivated")
    (row,) = recipe_viewer.present_recipes(idx.making(recipe_index.ITEM, key), idx)
    assert row["breeding"] == {"chance": 12, "conditions": ["Requires Warm temperature."]}
    assert "requirement_notes" not in row
    princess, drone = (g["alternatives"][0] for g in row["groups"])
    assert (princess["name"], princess["key"]) == ("Common Princess", PRINCESS + "#forestry.speciesCommon")
    assert drone["species"] == "forestry.speciesForest"
    assert row["outputs"][0]["key"] == key


def test_bbab_tree_gets_lines_to_parents_and_chances(idx):
    rows = idx.making(recipe_index.ITEM, recipe_index.species_key(PRINCESS, "forestry.speciesCultivated"))
    (row,) = recipe_viewer.present_recipes([r for r in rows if r[1] == TREE], idx)
    preview = row["preview"]
    assert preview["family"] == "breeding_tree" and not any(s["framed"] for s in preview["slots"])
    # Cultivated to Common and Forest; Common to Forest and Meadows. The
    # Forest drone on the second row is a base species: no lines below it.
    assert preview["lines"] == [{"x1": 82, "y1": 9, "x2": 42, "y2": 58}, {"x1": 82, "y1": 9, "x2": 122, "y2": 58},
                                {"x1": 42, "y1": 58, "x2": 22, "y2": 108}, {"x1": 42, "y1": 58, "x2": 62, "y2": 108}]
    assert [(label["text"], label["x"], label["conditions"]) for label in preview["labels"]] == [
        ("12%", 73, ["Requires Warm temperature."]), ("15%", 33, [])]


def test_mutation_table_keeps_the_best_chance_per_pair(idx):
    table = recipe_viewer.bee_mutations(idx)
    assert table[(("forestry.speciesForest", "forestry.speciesMeadows"), "forestry.speciesCommon")]["chance"] == 15
    assert table[(("forestry.speciesForest", "gtpp.bee.speciesDragon_Blood"), "forestry.speciesCommon")]["chance"] == 5
    assert recipe_viewer.bee_mutations(idx) is table


def test_recipe_api_pages_one_species(client, idx):
    key = PRINCESS + "#forestry.speciesCultivated"
    item = client.get("/api/recipes/item", query_string={"key": key}).get_json()
    assert item["item"]["name"] == "Cultivated Princess" and item["item"]["species"] == "forestry.speciesCultivated"
    assert item["making"] == {TREE: 1}
    found = client.get("/api/recipes/search", query_string={"q": "cultivated"}).get_json()
    assert [i["key"] for i in found["items"]] == [PRINCESS + "#forestry.speciesCultivated", QUEEN + "#forestry.speciesCultivated"]


def test_network_bees_know_their_species(client, api_headers):
    tag = tag_hex([("IsAnalyzed", nbt.BYTE, 1), ("Genome", nbt.COMPOUND, [("Chromosomes", nbt.LIST, (nbt.COMPOUND, [
        [("Slot", nbt.BYTE, 0), ("UID0", nbt.STRING, "forestry.speciesForest"), ("UID1", nbt.STRING, "forestry.speciesForest")],
    ]))])])
    run_scan(client, api_headers, [[{"name": "Forest Drone", "mod": "Forestry", "internal": "beeDroneGE", "damage": 0,
                                     "size": 3, "hasTag": True, "tag": tag}]])
    (bee,) = client.get("/api/network").get_json()["items"]
    assert bee["species"] == "forestry.speciesForest" and bee["variant"]
    # And after a restart, from the saved snapshot.
    items, _ = inventory.store.items.load_snapshot()
    assert items[0]["species"] == "forestry.speciesForest"


def test_find_recipes_by_species_name_and_key(client, idx):
    token = make_token("mia", role="operator")
    found = ok(client, token, "find_recipes", item="Cultivated Queen")
    assert found["item"] == "Forestry|beeQueenGE|0|item|species:forestry.speciesCultivated"
    assert found["name"] == "Cultivated Queen" and found["recipes_total"] == 1
    (recipe,) = found["recipes"]
    assert [g["alternatives"][0]["name"] for g in recipe["ingredient_groups"]] == ["Common Princess", "Forest Drone"]
    assert recipe["requirements"]["chance"] == 12
    assert recipe["outputs"][0]["item"] == found["item"]
    again = ok(client, token, "find_recipes", item=found["item"])
    assert again["recipes_total"] == 1
