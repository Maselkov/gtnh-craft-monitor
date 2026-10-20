import pytest

from gcm import search

# The same items and queries as tests/js/search.test.js: the server's
# port must match exactly what the page's search does.
ITEMS = [
    ("Molten Neutronium", "gregtech"),
    ("Neutronium Ingot", "gregtech"),
    ("Copper Ingot", "minecraft"),
    ("Tin Ingot", "ic2"),
    ("Small Air Shard", "thaumcraft"),
    ("Air Crystal Shard", "thaumcraft"),
]


@pytest.mark.parametrize("query, expected", [
    ("neutronium molten", ["Molten Neutronium"]),
    ("copper|tin", ["Copper Ingot", "Tin Ingot"]),
    ("@thaum", ["Small Air Shard", "Air Crystal Shard"]),
    ("ingot -@gregtech", ["Copper Ingot", "Tin Ingot"]),
    ('shard -"air shard"', ["Air Crystal Shard"]),
    ('"air shard"', ["Small Air Shard"]),
    ('"air sh', ["Small Air Shard"]),
    ("", [name for name, _ in ITEMS]),
    ("  ", [name for name, _ in ITEMS]),
])
def test_matches_like_the_page(query, expected):
    terms = search.parse(query)
    assert [name for name, mod in ITEMS if search.matches(name, mod, terms)] == expected


def test_lone_dash_is_a_plain_term():
    assert search.parse("-") == [("text", False, "-")]


def test_missing_name_and_mod():
    assert search.matches(None, None, search.parse(""))
    assert not search.matches(None, None, search.parse("gear"))


# tests/js/search.test.js's DESCRIBED items and #, $ and @ cases:
# (name, mod, description, ore names, mod name, formula).
DESCRIBED = [
    ("Dimensionally Transcendent Plasma Forge", "gregtech", "Machine Type: Plasma Forge, DTPF", [], "GregTech", None),
    ("Electric Blast Furnace", "gregtech", "Machine Type: Blast Furnace, EBF", [], "GregTech", None),
    ("Plasma Cell", "gregtech", "", ["cellPlasma"], "GregTech", "SiO2"),
    ("Iron Ingot", "minecraft", "", ["ingotIron", "ingotAnyIron"], "Minecraft", None),
    ("Industrial Diamond", "IC2", "", ["gemDiamond"], "IndustrialCraft 2", None),
]


@pytest.mark.parametrize("query, expected", [
    ("#dtpf", ["Dimensionally Transcendent Plasma Forge"]),
    ("#plasma", ["Dimensionally Transcendent Plasma Forge"]),
    ("#cell", []),
    ("dtpf", ["Dimensionally Transcendent Plasma Forge"]),
    ("plasma", ["Dimensionally Transcendent Plasma Forge", "Plasma Cell"]),
    ("anyiron", ["Iron Ingot"]),
    ("ebf|gemdiamond", ["Electric Blast Furnace", "Industrial Diamond"]),
    ('"type: blast"', ["Electric Blast Furnace"]),
    ("-machine @gregtech", ["Plasma Cell"]),
    ("#ebf|dtpf", ["Dimensionally Transcendent Plasma Forge", "Electric Blast Furnace"]),
    ('#"blast furnace"', ["Electric Blast Furnace"]),
    ("-#plasma @gregtech", ["Electric Blast Furnace", "Plasma Cell"]),
    ('#"type: plasma', ["Dimensionally Transcendent Plasma Forge"]),
    ("$ingotiron", ["Iron Ingot"]),
    ("$gem|cell", ["Plasma Cell", "Industrial Diamond"]),
    ("-$iron", ["Dimensionally Transcendent Plasma Forge", "Electric Blast Furnace", "Plasma Cell",
                "Industrial Diamond"]),
    ("@industrialcraft", ["Industrial Diamond"]),
    ("@ic2", ["Industrial Diamond"]),
    ("=sio2", ["Plasma Cell"]),
    ("=si|fe", ["Plasma Cell"]),
    ("-=sio @gregtech", ["Dimensionally Transcendent Plasma Forge", "Electric Blast Furnace"]),
])
def test_game_data_terms_match_like_the_page(query, expected):
    terms = search.parse(query)
    assert [name for name, mod, desc, ore, mod_name, formula in DESCRIBED
            if search.matches(name, mod, terms, desc, ore, mod_name, formula)] == expected


def test_without_game_data_tooltip_and_ore_terms_match_nothing():
    for query in ("#ingot", "$ingot", "=ingot"):
        assert [name for name, mod in ITEMS if search.matches(name, mod, search.parse(query))] == []


def test_lone_prefixes_are_plain_terms():
    assert search.parse("#") == [("text", False, "#")]
    assert search.parse("$") == [("text", False, "$")]
