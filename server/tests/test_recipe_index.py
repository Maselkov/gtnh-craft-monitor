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


def format3():
    data = format2()
    data['format'] = 3
    data['coverage']['handlers'] += ['assembly_line', 'nei']
    data['maps']['assembly_line'] = {'machines': [{'name': 'Assembly Line', 'key': 'gregtech:gt.blockmachines:1170'}],
        'recipes': [{'type': 'assembly_line', 'groups': [{'alts': [[n, 2] for n in range(3, 23)]}],
                     'f': [[0, 144]], 'o': [[2, 1]], 'e': 32768, 'd': 400,
                     'research': {'method': 'research_station', 'item': [0, 1], 'eut': 8192,
                                  'ticks': 4000, 'computation': 1000000, 'amperage': 4, 'computation_per_second': 128},
                     'snapshot': {'output': {'nbt': '{example:1}'}},
                     'requirements': {'nbt_sensitive': True, 'nbt_inputs': [{'item': 'example:part:0', 'nbt': '{large:1}'}]}}]}
    data['maps']['nei.example'] = {'machines': [{'name': 'Special Machine'}], 'recipes': [
        {'type': 'nei', 'groups': [{'kind': 'fluid', 'alts': [[0, 1000], [1, 500]]}], 'o': [[2, 1]],
         'requirements': {'mana': 2000}, 'approximate': ['nei_display'],
         'snapshot': {'other': [{'item': 'example:catalyst:0'}]}}],
        'archive': [{'dynamic': True, 'unindexed_output': 'example:archive:0'}]}
    return data


def test_assembly_research_and_nei_alternatives_are_indexed_without_raw_archives(tmp_path):
    idx = build_index(tmp_path, format3())
    try:
        assert idx.source_format == 3
        assert idx.counts_using(recipe_index.ITEM, 'example:part:19')['assembly_line'] == 1
        assert idx.counts_using(recipe_index.ITEM, LOG)['assembly_line'] == 1
        assert idx.counts_using(recipe_index.FLUID, 'mod:fluid')['nei.example'] == 1
        rows, _ = idx.made_by(recipe_index.ITEM, PLANKS)
        assembly = next(r[2] for r in rows if r[2]['type'] == 'assembly_line')
        display = next(r[2] for r in rows if r[2]['type'] == 'nei')
        assert assembly['e'] == 32768 and assembly['research']['eut'] == 8192
        assert display['requirements'] == {'mana': 2000} and rows[-1][2]['type'] == 'nei'
        assert 'snapshot' not in assembly and 'snapshot' not in display
        assert assembly['requirements'] == {'nbt_sensitive': True}
        assert idx.counts_making(recipe_index.ITEM, 'example:archive:0') == {}
        assert recipes._maps_named('Assembly Line', idx) == recipes._maps_named('Special Machine', idx) == set()
        assert all(row[2]['type'] == 'gt' for row in recipes._Matcher().making(idx, recipe_index.ITEM, PLANKS))
    finally:
        idx.close()


@pytest.mark.parametrize('change', [
    lambda r: r.update(groups=[{'alts': []}]),
    lambda r: r.update(groups=[{'kind': 'essentia', 'alts': [[0, 1]]}]),
    lambda r: r['research'].update(item=[999, 1]),
    lambda r: r['research'].update(computation=-1),
    lambda r: r['research'].update(method='unknown'),
])
def test_invalid_assembly_requirements_are_rejected(tmp_path, change):
    data = format3()
    change(data['maps']['assembly_line']['recipes'][0])
    with pytest.raises(ValueError):
        build_index(tmp_path, data)


def test_research_diagrams_do_not_claim_to_manufacture_the_research_target(tmp_path):
    data = format3()
    for name in recipe_index.REFERENCE_ONLY_MAPS:
        data['maps'][name] = data['maps']['nei.example']
    idx = build_index(tmp_path, data)
    try:
        rows, total = idx.made_by(recipe_index.ITEM, PLANKS)
        assert total == 7 and all(name not in recipe_index.REFERENCE_ONLY_MAPS for _, name, _ in rows)
    finally:
        idx.close()


def format4():
    data = format3()
    data['format'] = 4
    data['display_stacks'] = {
        'charged': {'key': 'minecraft:log:7', 'name': 'Charged Log', 'nbt': '{charge:10}', 'icon': 'item/charged.webp'},
        'empty': {'key': 'minecraft:log:8', 'name': 'Empty Log', 'nbt': None, 'icon': 'item/empty.webp'},
    }
    data['ui_assets'] = {'frame': {'path': 'ui/frame.webp', 'width': 18, 'height': 18}}
    data['research_info'] = {'INFUSION': {'name': 'Infusion Research', 'category_name': 'Thaumaturgy', 'icon': 'frame'}}
    data['aspects'] = {'ordo': {'name': 'Order', 'icon': 'aspect/ordo.webp'}}
    r = data['maps'][EBF]['recipes'][0]
    r['i'] = [[1, 2, None, ['charged', 'empty']]]
    r['aux'] = [[0, 0, None, ['empty']]]
    r['extras'] = [{'alts': [[3, 0, None, ['empty']]]}]
    r['requirements'] = {'research': 'INFUSION', 'instability': 0, 'essentia': {'ordo': 25}}
    r['preview_indices'] = {'i': [2]}
    data['maps'][EBF]['preview'] = {'family': 'gt', 'width': 166, 'height': 84,
        'slots': [{'role': 'i', 'index': 2, 'x': 8, 'y': 8, 'w': 18, 'h': 18, 'asset': 'frame'},
                  {'role': 'i', 'index': 0, 'x': 26, 'y': 8, 'asset': 'frame'},
                  {'role': 'aux', 'index': 0, 'x': 80, 'y': 8, 'asset': 'frame'}],
        'layers': [{'asset': 'frame', 'x': 44, 'y': 8, 'w': 18, 'h': 18, 'uv': [0, 0, 1, .5]}]}
    return data


def test_display_pool_can_follow_maps_and_keeps_semantic_identity(tmp_path):
    raw = format4()
    raw['display_stacks'] = raw.pop('display_stacks')
    idx = build_index(tmp_path, raw)
    try:
        body = next(b for _, m, b in idx.making(recipe_index.ITEM, PLANKS) if m == EBF and b.get('aux'))
        assert body['i'][0] == [ANY_LOG, 2, None, ['charged', 'empty']]
        assert idx.display_stacks(['charged'])['charged']['nbt'] == '{charge:10}'
        assert idx.layout(EBF)['slots'][0]['index'] == 2
        assert EBF not in idx.counts_using(recipe_index.ITEM, 'example:part:0')  # display extras are not consumed
        assert idx.presentation['research_info']['INFUSION']['name'] == 'Infusion Research'
    finally:
        idx.close()


def test_missing_display_reference_fails_instead_of_losing_variant(tmp_path):
    raw = format4()
    del raw['display_stacks']['charged']
    with pytest.raises(ValueError, match='unknown display stack reference'):
        build_index(tmp_path, raw)


def test_legacy_source_without_names_has_no_search_catalog(tmp_path):
    raw = format4()
    raw.pop('item_names')
    raw.pop('fluid_names')
    idx = build_index(tmp_path, raw)
    try:
        assert not idx.has_names()
        assert idx.making(recipe_index.ITEM, PLANKS)  # Production lookup still works.
    finally:
        idx.close()


def test_nei_stack_overrides_keep_production_inputs_and_validate_display_refs(tmp_path):
    raw = format4()
    raw['maps'][EBF]['recipes'][0]['preview_stacks'] = {'i': [[0, 64, None, ['charged']]], 'f': [[0, 10000]]}
    idx = build_index(tmp_path, raw)
    try:
        body = next(b for _, m, b in idx.making(recipe_index.ITEM, PLANKS) if m == EBF and b.get('aux'))
        assert body['i'][0][0] == ANY_LOG
        assert body['preview_stacks']['i'] == [['minecraft:log:0', 64, None, ['charged']]]
        assert body['preview_stacks']['f'][0][1] == 10000
    finally:
        idx.close()
    raw['maps'][EBF]['recipes'][0]['preview_stacks']['i'][0][3] = ['missing']
    with pytest.raises(ValueError, match='unknown display stack reference'):
        build_index(tmp_path, raw)


def ordered():
    """format2, with NEI's order (parts first, highest number first, then
    planks and the logs) and groups: the parts, and the logs, open by default."""
    data = format2()
    parts = [f"example:part:{n}" for n in range(20)]
    rank = {k: i for i, k in enumerate([*reversed(parts), PLANKS, LOG, ANY_LOG])}
    data["item_rank"] = [rank[k] for k in data["items"]]
    data["item_group"] = [1, 1, -1, *[0] * 20]
    data["item_groups"] = [{"name": "Parts", "expanded": False}, {"name": "", "expanded": True}]
    return data


def keys(rows):
    return [k for _, k, _ in rows]


def test_catalog_follows_neis_order_with_fluids_after_items(tmp_path):
    data = ordered()
    data.pop("item_group")
    idx = build_index(tmp_path, data)
    rows, total, shown = idx.search_names("", limit=100)
    assert keys(rows) == [*(f"example:part:{n}" for n in range(19, -1, -1)), PLANKS, LOG, ANY_LOG, "mod:fluid", "water"]
    assert total == 25 and shown == [None] * 25
    # A name match still comes first.
    assert keys(idx.search_names("planks", limit=3)[0])[0] == PLANKS
    idx.close()


def test_without_neis_order_the_catalog_is_alphabetical(tmp_path):
    data = ordered()
    data["item_rank"] = data["item_rank"][:-1]  # Not one per item: ignored.
    idx = build_index(tmp_path, data)
    assert keys(idx.search_names("", recipe_index.ITEM, limit=3)[0]) == [ANY_LOG, LOG, "example:part:0"]
    idx.close()


def test_a_group_collapses_into_its_first_match_unless_open(tmp_path):
    idx = build_index(tmp_path, ordered())
    rows, total, shown = idx.search_names("", recipe_index.ITEM, limit=100)
    assert keys(rows) == ["example:part:19", PLANKS, LOG, ANY_LOG] and total == 4
    assert shown[0] == {"id": 0, "name": "Parts", "size": 20, "open": False, "forced": False,
                        "back": "example:part:0"}
    assert shown[1] is None
    assert shown[2] == {"id": 1, "name": "", "size": 2, "open": True, "forced": False, "back": None}
    # A group counts its matches, not its members.
    rows, total, shown = idx.search_names("o", recipe_index.ITEM, limit=100)
    assert keys(rows) == [LOG, ANY_LOG] and shown[0]["size"] == 2
    # Opened, it lists every match; shut, the default-open one collapses.
    rows, total, _ = idx.search_names("", recipe_index.ITEM, limit=100, groups={"open": {0}, "shut": {1}})
    assert total == 22 and keys(rows)[-1] == LOG
    rows, total, _ = idx.search_names("", recipe_index.ITEM, limit=100, groups={"all": False})
    assert total == 3
    # Every group open: nothing collapses, and each row still counts its group's matches.
    rows, total, shown = idx.search_names("", recipe_index.ITEM, limit=5, offset=19, groups={"all": True})
    assert total == 23 and keys(rows) == ["example:part:0", PLANKS, LOG, ANY_LOG]
    assert shown[0] == {"id": 0, "name": "Parts", "size": 20, "open": True, "forced": False, "back": None}
    assert shown[1] is None and shown[2]["size"] == 2
    # Browsing pages through one kept list, for each set of open groups.
    whole = idx.search_names("", recipe_index.ITEM, limit=100)
    assert idx.search_names("", recipe_index.ITEM, limit=2, offset=2)[0] == whole[0][2:4]
    assert idx.search_names("", recipe_index.ITEM, limit=2, offset=2, groups={"open": {0}})[0][0][1] == "example:part:17"
    rows, total, _ = idx.search_names("", recipe_index.ITEM, limit=100, groups={"all": True, "shut": {0}})
    assert total == 4
    # A page past the end still counts what's shown.
    assert idx.search_names("", recipe_index.ITEM, limit=10, offset=50)[:2] == ([], 4)
    idx.close()


def test_a_groups_items_sort_together_where_its_first_one_does(tmp_path):
    # Planks sorts between the logs, but their group keeps them together,
    # where the first log sorts (as Chisel's variants stay with the block).
    data = ordered()
    data["item_rank"][:3] = [20, 22, 21]  # Log, any log, planks.
    idx = build_index(tmp_path, data)
    rows, _, _ = idx.search_names("", recipe_index.ITEM, limit=100, groups={"all": True})
    assert keys(rows)[20:] == [LOG, ANY_LOG, PLANKS]
    idx.close()


def test_a_search_that_only_matches_one_group_opens_it(tmp_path):
    idx = build_index(tmp_path, ordered())
    rows, total, shown = idx.search_names("part", limit=100, groups={"all": False})
    assert total == 20 and len(rows) == 20
    assert all(s["open"] and s["forced"] and s["back"] is None for s in shown)
    # With a match outside it, it collapses again.
    rows, total, shown = idx.search_names("l", recipe_index.ITEM, limit=100)
    assert keys(rows) == ["example:part:19", PLANKS, LOG, ANY_LOG] and not shown[0]["open"]
    idx.close()
