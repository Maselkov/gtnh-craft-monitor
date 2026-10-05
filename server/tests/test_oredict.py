import json

import pytest

from gcm import oredict, store


def key(mod, internal, damage, variant=None):
    return store.items.item_key(mod, internal, damage, "item", variant)


DIAMOND = key("minecraft", "diamond", 0)
INDUSTRIAL = key("IC2", "itemPartIndustrialDiamond", 0)
GT_DIAMOND = key("gregtech", "gt.metaitem.01", 2500)
GT_DIAMOND_TAGGED = key("gregtech", "gt.metaitem.01", 2500, "Labc")
OTHER_GT = key("gregtech", "gt.metaitem.01", 2032)


@pytest.fixture
def load(tmp_path):
    def write(data):
        path = tmp_path / "ore_dict.json"
        path.write_text(json.dumps(data) if not isinstance(data, str) else data)
        oredict.load(str(path))
    yield write
    oredict.load(None)


def test_items_sharing_a_name_stand_in_for_each_other(load):
    load({"gemDiamond": ["minecraft:diamond:0", "IC2:itemPartIndustrialDiamond:0", "gregtech:gt.metaitem.01:2500"]})
    known = oredict.Known([DIAMOND, INDUSTRIAL, GT_DIAMOND, GT_DIAMOND_TAGGED, OTHER_GT])
    # Every NBT variant of a named item and damage, nothing else of its id.
    assert oredict.equivalents(DIAMOND, known) == [INDUSTRIAL, GT_DIAMOND, GT_DIAMOND_TAGGED]
    assert oredict.equivalents(GT_DIAMOND_TAGGED, known) == [DIAMOND, INDUSTRIAL, GT_DIAMOND]
    assert oredict.equivalents(OTHER_GT, known) == []


def test_a_wildcard_damage_takes_every_damage(load):
    load({"plankWood": ["minecraft:planks:*", "Natura:planks:3"]})
    oak, birch, natura = key("minecraft", "planks", 0), key("minecraft", "planks", 2), key("Natura", "planks", 3)
    known = oredict.Known([oak, birch, natura])
    assert oredict.equivalents(natura, known) == [oak, birch]
    assert oredict.equivalents(oak, known) == [birch, natura]


def test_only_what_the_plan_could_have_is_offered(load):
    load({"gemDiamond": ["minecraft:diamond:0", "IC2:itemPartIndustrialDiamond:0"]})
    assert oredict.equivalents(DIAMOND, oredict.Known([DIAMOND])) == []


def test_fluids_and_junk_are_ignored(load):
    load({"gemDiamond": ["minecraft:diamond:0", "nonsense", "a:b:c", "IC2:itemPartIndustrialDiamond:0"], "x": 5})
    known = oredict.Known([DIAMOND, INDUSTRIAL, "|water||fluid"])
    assert oredict.equivalents(DIAMOND, known) == [INDUSTRIAL]
    assert oredict.equivalents("|water||fluid", known) == []


def test_missing_or_unreadable_means_none(load, tmp_path):
    load("{not json")
    assert not oredict.loaded()
    oredict.load(str(tmp_path / "nope.json"))
    assert not oredict.loaded()
    before = oredict.version()
    oredict.load(None)
    assert oredict.version() == before + 1
