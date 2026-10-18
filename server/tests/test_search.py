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
