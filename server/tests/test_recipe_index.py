"""The format-2 data contract, pagination, and processing-check isolation."""
import gzip
import json

import pytest

from gcm import recipe_index, recipes
from test_recipes import DUST, EBF, EBF_PATTERN, HOT, ebf, write_recipes

LOG = "minecraft:log:0"
ANY_LOG = "minecraft:log:*"
PLANKS = "minecraft:planks:0"


def format2():
    return {
        "format": 2,
        "items": [LOG, ANY_LOG, PLANKS, *[f"example:part:{n}" for n in range(20)]],
        "item_names": ["Oak Log", "Any Log", "Planks", *[f"Part {n}" for n in range(20)]],
        "fluids": ["water", "mod:fluid"], "fluid_names": ["Water", "Fluid"],
        "coverage": {"handlers": ["gregtech", "crafting", "furnace"], "identity": "item_id_damage",
                     "limitations": ["NBT variants are not indexed"],
                     "crafting": {"registered": 3, "exported": 2, "skipped": {"unsupported_class": 1},
                                  "classes": {"DynamicRecipe": {"registered": 1, "exported": 0}}}},
        "maps": {
            "crafting": {"machines": [{"name": "Crafting Table", "key": "minecraft:crafting_table:0"}],
                         "recipes": [
                             {"type": "shaped", "w": 2, "h": 2, "mirrored": True,
                              "slots": [{"alts": [[0, 1], [1, 1]], "od": "logWood"}, None,
                                        {"alts": [[n, 1] for n in range(3, 23)]}, None],
                              "o": [[2, 4]], "approximate": ["nbt"]},
                             {"type": "shapeless", "slots": [{"alts": [[1, 1]]}], "o": [[2, 4]]},
                         ]},
            "furnace": {"machines": [{"name": "Furnace", "key": "minecraft:furnace:0"}],
                        "recipes": [{"type": "furnace", "i": [[1, 1]], "o": [[2, 1]], "d": 200}]},
            EBF: {"machines": [{"name": "Electric Blast Furnace", "key": "gregtech:gt.blockmachines:1000"}],
                  "recipes": [{"type": "gt", "i": [[0, 1], [1, 1]], "o": [[2, 1, 5000]],
                               "f": [[0, 1000]], "g": [[1, 500]], "e": -120, "d": 20},
                              {"type": "gt", "i": [[0, 1]], "o": [[2, 1]], "e": 30, "d": 40}]},
        },
    }


def build_index(tmp_path, data, name="recipes"):
    source = str(tmp_path / f"{name}.json.gz")
    with gzip.open(source, "wt") as f:
        json.dump(data, f)
    path = str(tmp_path / f"{name}.sqlite")
    recipe_index.build(source, path)
    return recipe_index.RecipeIndex(path)


@pytest.fixture()
def typed_index(tmp_path):
    idx = build_index(tmp_path, format2())
    yield idx
    idx.close()


def test_slots_keep_holes_full_alternatives_names_and_wildcards(typed_index):
    rows = typed_index.making(recipe_index.ITEM, PLANKS)
    shaped = rows[0][2]
    assert (shaped["w"], shaped["h"], shaped["mirrored"]) == (2, 2, True)
    assert shaped["slots"][1] is None
    assert shaped["slots"][0] == {"alts": [[LOG, 1], [ANY_LOG, 1]], "od": "logWood"}
    assert len(shaped["slots"][2]["alts"]) == 20
    assert shaped["approximate"] == ["nbt"]
    assert typed_index.counts_using(recipe_index.ITEM, "example:part:19") == {"crafting": 1}
    assert typed_index.names(recipe_index.ITEM, ["example:part:19"]) == {"example:part:19": "Part 19"}
    assert typed_index.source_format == 2
    assert typed_index.coverage == format2()["coverage"]
    assert json.loads(typed_index.fingerprint)["format"] == recipe_index.FORMAT


def test_exact_and_wildcard_matches_are_deduplicated_before_paging_and_counts(typed_index):
    expected = {"crafting": 2, "furnace": 1, EBF: 2}
    assert typed_index.counts_using(recipe_index.ITEM, LOG) == expected
    assert typed_index.counts_making(recipe_index.ITEM, PLANKS) == expected
    all_rows, total = typed_index.used_by(recipe_index.ITEM, LOG, limit=100)
    assert total == 5
    paged = [typed_index.used_by(recipe_index.ITEM, LOG, limit=1, offset=i)[0][0] for i in range(total)]
    assert paged == all_rows
    assert len({r[0] for r in paged}) == total
    assert [r[2]["type"] for r in all_rows] == ["shaped", "shapeless", "furnace", "gt", "gt"]
    assert [r[2]["e"] for r in all_rows[-2:]] == [30, -120]
    assert typed_index.used_by(recipe_index.ITEM, LOG, limit=1, offset=5) == ([], 5)
    assert typed_index.used_by(recipe_index.ITEM, LOG, "unknown") == ([], 0)
    rows, total = typed_index.used_by(recipe_index.ITEM, LOG, "crafting", limit=1, offset=1)
    assert total == 2 and rows[0][2]["type"] == "shapeless"
    assert typed_index.using(recipe_index.ITEM, "minecraft:log:7") == ["crafting", "furnace", EBF]
    # A wildcard query is the wildcard identity, without expanding concrete variants.
    assert typed_index.counts_using(recipe_index.ITEM, ANY_LOG) == {"crafting": 2, "furnace": 1, EBF: 1}


def test_making_pages_and_fluids_have_the_same_contract(typed_index):
    rows, total = typed_index.made_by(recipe_index.ITEM, PLANKS, limit=2)
    assert total == 5 and rows == typed_index.making(recipe_index.ITEM, PLANKS)[:2]
    assert typed_index.made_by(recipe_index.ITEM, PLANKS, "furnace")[0][0][2]["d"] == 200
    rows, total = typed_index.made_by(recipe_index.FLUID, "mod:fluid")
    assert total == 1 and rows[0][2]["g"] == [["mod:fluid", 500]]
    assert typed_index.counts_using(recipe_index.FLUID, "water") == {EBF: 1}
    assert typed_index.used_by(recipe_index.FLUID, "mod:fluid") == ([], 0)


@pytest.mark.parametrize("limit,offset", [(0, 0), (101, 0), (True, 0), (1, -1), (1, 0.5)])
def test_page_bounds(typed_index, limit, offset):
    with pytest.raises(ValueError, match="recipe page"):
        typed_index.used_by(recipe_index.ITEM, LOG, limit=limit, offset=offset)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(w=3),
    lambda r: r["slots"][0].update(alts=[]),
    lambda r: r["slots"][0].update(alts=[[999, 1]]),
    lambda r: r["slots"][0].update(alts=[[True, 1]]),
    lambda r: r["slots"][0].update(alts=[[0, 0]]),
    lambda r: r.update(type="mystery"),
    lambda r: r.update(type="gt"),
    lambda r: r.update(o=[]),
    lambda r: r.update(approximate="nbt"),
])
def test_malformed_format2_is_rejected_without_replacing_a_valid_index(tmp_path, mutate):
    data = format2()
    idx = build_index(tmp_path, data)
    idx.close()
    original = (tmp_path / "recipes.sqlite").read_bytes()
    mutate(data["maps"]["crafting"]["recipes"][0])
    with pytest.raises(ValueError):
        build_index(tmp_path, data)
    assert (tmp_path / "recipes.sqlite").read_bytes() == original


def test_old_bundles_keep_gt_behavior_and_expose_their_coverage(tmp_path):
    source = write_recipes(str(tmp_path / "v1.gz"), {EBF: [ebf()]})
    path = str(tmp_path / "v1.sqlite")
    recipe_index.build(source, path)
    idx = recipe_index.RecipeIndex(path)
    try:
        assert idx.source_format == 1 and idx.coverage["handlers"] == ["gregtech"]
        assert idx.making(recipe_index.ITEM, HOT)[0][2]["type"] == "gt"
        assert recipe_index.is_current(path, source)
    finally:
        idx.close()


def test_processing_checker_ignores_pseudo_maps_in_matches_fallback_and_uses(tmp_path):
    old_source = write_recipes(str(tmp_path / "old.gz"), {EBF: [ebf()]})
    old_path = str(tmp_path / "old.sqlite")
    recipe_index.build(old_source, old_path)
    old = recipe_index.RecipeIndex(old_path)
    data = format2()
    data["items"] = [DUST, "example:unused:0", HOT, *data["items"][3:]]
    # Only crafting and furnace make/use what the old GT pattern made.
    del data["maps"][EBF]
    data["maps"]["furnace"]["recipes"][0]["i"] = [[2, 1]]
    new = build_index(tmp_path, data, "new")
    try:
        result = recipes.compare([EBF_PATTERN], old, new)
        row = result["patterns"][0]
        assert row["status"] == "broken" and row["new"] == []
        assert row["removed"]["used"] is False
        assert recipes.compare([EBF_PATTERN], new, new)["counts"]["unchecked"] == 1
        assert recipes._maps_named("Crafting Table", new) == set()
    finally:
        old.close()
        new.close()


@pytest.mark.parametrize("chunk", [1, 7, 64])
def test_streaming_supports_any_field_order_and_split_strings_numbers_and_escapes(tmp_path, monkeypatch, chunk):
    monkeypatch.setattr(recipe_index._JSONStream, "CHUNK", chunk)
    data = format2()
    data["item_names"][0] = 'Log "quoted" \\ newline\n中文'
    # Metadata can follow maps; map recipes can precede their machines.
    data["maps"] = {k: dict(reversed(list(v.items()))) for k, v in data["maps"].items()}
    data = dict(reversed(list(data.items())))
    idx = build_index(tmp_path, data)
    try:
        assert idx.names(recipe_index.ITEM, [LOG]) == {LOG: data["item_names"][0]}
        assert idx.machines("crafting")[0]["name"] == "Crafting Table"
        assert idx.made_by(recipe_index.ITEM, PLANKS)[1] == 5
        assert idx.made_by(recipe_index.ITEM, PLANKS, EBF)[0][-1][2]["e"] == -120
    finally:
        idx.close()


@pytest.mark.parametrize("text", [
    '{"format":2,"maps":{',
    '{"format":2,"maps":{"crafting":{"recipes":[{},]}}}',
    '{"format":2,"maps":{}} trailing',
    '{"format":2 "maps":{}}',
])
def test_streaming_rejects_truncation_and_invalid_separators(tmp_path, text):
    source = tmp_path / "invalid.gz"
    with gzip.open(source, "wt") as f:
        f.write(text)
    with pytest.raises(ValueError):
        recipe_index.build(str(source), str(tmp_path / "index.sqlite"))
    assert not (tmp_path / "index.sqlite").exists()
    assert not (tmp_path / "index.sqlite.tmp").exists()


def test_atomic_source_replacement_does_not_mix_fingerprint_and_recipes(tmp_path, monkeypatch):
    data = format2()
    with gzip.open(tmp_path / "source.gz", "wt") as f:
        json.dump(data, f)
    before = recipe_index.fingerprint(str(tmp_path / "source.gz"))
    data["maps"]["crafting"]["recipes"][0]["o"] = [[2, 64]]
    with gzip.open(tmp_path / "replacement.gz", "wt") as f:
        json.dump(data, f)
    source_events = recipe_index._source_events
    calls = 0

    def replace_after_metadata(stream):
        nonlocal calls
        calls += 1
        yield from source_events(stream)
        if calls == 1:
            (tmp_path / "replacement.gz").replace(tmp_path / "source.gz")

    monkeypatch.setattr(recipe_index, "_source_events", replace_after_metadata)
    path = str(tmp_path / "index.sqlite")
    recipe_index.build(str(tmp_path / "source.gz"), path)
    idx = recipe_index.RecipeIndex(path)
    try:
        assert idx.fingerprint == before
        assert idx.making(recipe_index.ITEM, PLANKS)[0][2]["o"] == [[PLANKS, 4]]
        assert not recipe_index.is_current(path, str(tmp_path / "source.gz"))
    finally:
        idx.close()
