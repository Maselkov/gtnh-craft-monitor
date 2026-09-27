import gzip

import pytest

from gcm import nbt
from nbt_fixtures import compound_body, seed_tag, tag_hex


def test_parses_nested_values():
    root = nbt.parse_hex(tag_hex([
        ("name", nbt.STRING, "x"),
        ("f", nbt.FLOAT, 1.5),
        ("inner", nbt.COMPOUND, [("n", nbt.INT, 7)]),
        ("list", nbt.LIST, (nbt.SHORT, [1, 2])),
    ]))
    assert nbt.plain(nbt.COMPOUND, root) == {
        "name": "x", "f": 1.5, "inner": {"n": 7}, "list": [1, 2],
    }


def test_hash_ignores_key_order_but_not_values():
    a = nbt.parse_hex(tag_hex([("a", nbt.INT, 1), ("b", nbt.INT, 2)]))
    b = nbt.parse_hex(tag_hex([("b", nbt.INT, 2), ("a", nbt.INT, 1)]))
    c = nbt.parse_hex(tag_hex([("a", nbt.INT, 1), ("b", nbt.INT, 3)]))
    assert nbt.canonical_hash(a) == nbt.canonical_hash(b)
    assert nbt.canonical_hash(a) != nbt.canonical_hash(c)
    # Same number, different NBT type: a different item to Minecraft.
    d = nbt.parse_hex(tag_hex([("a", nbt.BYTE, 1), ("b", nbt.INT, 2)]))
    assert nbt.canonical_hash(a) != nbt.canonical_hash(d)


def test_uncompressed_tags_are_accepted_too():
    raw = (b"\x0a\x00\x00" + compound_body([("a", nbt.INT, 1)])).hex()
    assert nbt.plain(nbt.COMPOUND, nbt.parse_hex(raw)) == {"a": 1}


@pytest.mark.parametrize("bad", ["zz", "", "1f8b00", gzip.compress(b"\x08\x00\x00").hex(),
                                 (b"\x0a\x00\x00" + compound_body([("a", nbt.INT, 1)])[:-3]).hex()])
def test_malformed_tags_raise_nbt_error(bad):
    with pytest.raises(nbt.NbtError):
        nbt.parse_hex(bad)


def test_decompression_is_capped():
    bomb = gzip.compress(b"\x0a\x00\x00" + b"\x00" * (nbt.MAX_DECOMPRESSED + 10)).hex()
    with pytest.raises(nbt.NbtError):
        nbt.parse_hex(bomb)


def test_describes_crop_seed_stats():
    assert nbt.describe(nbt.parse_hex(seed_tag("sugarbeet", 12, 8, 1))) == "Gr 12 · Ga 8 · Re 1"


def test_describes_crop_stats_nested_and_capitalized():
    root = nbt.parse_hex(tag_hex([("genes", nbt.COMPOUND, [
        ("Growth", nbt.INT, 3), ("Gain", nbt.INT, 2), ("Resistance", nbt.INT, 1)])]))
    assert nbt.describe(root) == "Gr 3 · Ga 2 · Re 1"


def test_describes_bees():
    def bee(active, inactive, analyzed):
        return nbt.parse_hex(tag_hex([
            ("Genome", nbt.COMPOUND, [("Chromosomes", nbt.LIST, (nbt.COMPOUND, [
                [("UID0", nbt.STRING, active), ("UID1", nbt.STRING, inactive)]]))]),
            ("IsAnalyzed", nbt.BYTE, analyzed),
        ]))

    assert nbt.describe(bee("forestry.speciesForest", "forestry.speciesForest", 1)) == "Analyzed"
    assert nbt.describe(bee("forestry.speciesForest", "forestry.speciesForest", 0)) == "Unanalyzed"
    assert nbt.describe(bee("forestry.speciesForest", "forestry.speciesMeadows", 1)) == (
        "Hybrid Meadows · Analyzed")


def test_unrecognized_nbt_has_no_description():
    assert nbt.describe(nbt.parse_hex(tag_hex([("Energy", nbt.INT, 5)]))) is None


def test_describes_gregtech_tool_material():
    root = nbt.parse_hex(tag_hex([("GT.ToolStats", nbt.COMPOUND, [
        ("PrimaryMaterial", nbt.STRING, "Neutronium"), ("SecondaryMaterial", nbt.STRING, "Magnalium"),
        ("MaxDamage", nbt.INT, 5)])]))
    assert nbt.describe(root) == "Neutronium"
