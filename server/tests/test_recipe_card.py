"""A recipe book link's preview: its OpenGraph tags and Discord embed,
and the card image (gcm/recipe_card.py)."""
import html
import io
import json
import re

import pytest
from PIL import Image

from conftest import login_as
from gcm import auth, gamedata, recipe_card, recipe_viewer, recipes
from test_recipe_index import typed_index  # noqa: F401


@pytest.fixture()
def data(typed_index, monkeypatch):  # noqa: F811
    monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
    monkeypatch.setattr(recipes, "index", lambda version: typed_index)
    return typed_index


def meta(page, prop):
    found = re.search(rf'<meta property="{prop}" content="([^"]*)"', page)
    return html.unescape(found.group(1)) if found else None


def embed(page):
    found = re.search(r'<script id="discord:component-embed" type="application/json">(.*?)</script>', page)
    return json.loads(found.group(1)) if found else None


def test_a_recipe_link_previews_its_recipe_in_words_and_as_its_card(client, data):
    page = client.get("/recipes?key=minecraft:planks:0").get_data(as_text=True)
    assert meta(page, "og:title") == "Planks"
    desc = meta(page, "og:description")
    assert desc.startswith("Shaped Crafting · 1 of ") and " → " in desc and "Planks" in desc
    image = meta(page, "og:image")
    assert "/api/recipes/card.png?key=minecraft%3Aplanks%3A0&dir=making&map=crafting&page=1&v=" in image
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    card = client.get(image.split("localhost", 1)[1])
    assert card.status_code == 200 and card.mimetype == "image/png"
    # The size the page announces is the image's, so Discord lays it out
    # before fetching it.
    assert Image.open(io.BytesIO(card.data)).size == (int(meta(page, "og:image:width")),
                                                      int(meta(page, "og:image:height")))
    text = json.dumps(embed(page))
    assert "## Planks" in text and "card.png" in text and '"label": "Open"' in text


def test_the_link_picks_the_recipe_the_book_would(client, data):
    # No category: the item's first with recipes that way.
    first = recipe_card.Page({"key": "minecraft:planks:0"})
    assert first.map == "crafting" and first.index == 0 and first.row is not None
    # A page past the end is the last one, as in the book.
    last = recipe_card.Page({"key": "minecraft:planks:0", "map": first.map, "page": "999"})
    assert last.index == last.total - 1
    for bad in ("0", "-2", "x", ""):
        assert recipe_card.Page({"key": "minecraft:planks:0", "page": bad}).index == 0
    using = recipe_card.Page({"key": "minecraft:log:0", "dir": "using", "map": "crafting"})
    assert using.direction == "using" and using.heading() == "Uses for " + using.item
    page = client.get("/recipes?key=minecraft:log:0&dir=using&map=crafting").get_data(as_text=True)
    assert "Uses for" in json.dumps(embed(page))


def test_a_link_without_recipes_or_with_a_bad_key_still_opens(client, data):
    page = client.get("/recipes?key=not-a-key").get_data(as_text=True)
    assert meta(page, "og:title") == "Recipes" and meta(page, "og:image") is None
    assert client.get("/api/recipes/card.png?key=not-a-key").status_code == 404
    page = client.get("/recipes?key=example:unknown:0").get_data(as_text=True)
    assert meta(page, "og:image") is None and "No recipes" in meta(page, "og:description")
    assert client.get("/api/recipes/card.png?key=example:unknown:0").status_code == 404


def test_a_private_server_previews_no_recipes(client, data):
    auth.set_private(True)
    page = client.get("/recipes?key=minecraft:planks:0").get_data(as_text=True)
    assert meta(page, "og:image") is None and embed(page) is None
    assert client.get("/api/recipes/card.png?key=minecraft:planks:0").status_code == 401
    login_as(client, "reader", "viewer")
    assert client.get("/api/recipes/card.png?key=minecraft:planks:0").status_code == 200


def stack(name, size=1, kind="item", icon=None):
    return {"name": name, "size": size, "kind": kind, "key": f"mod:{name}:0", "icon": icon}


def test_a_recipe_in_words_counts_each_stack_once():
    row = {"grid": {"w": 3, "h": 1, "shapeless": False, "slots": [
        {"alternatives": [stack("Plate")]}, None, {"alternatives": [stack("Plate")]}]},
        "outputs": [stack("Gear")]}
    assert recipe_card.summary(row) == "2× Plate → 1× Gear"
    gt = {"inputs": [stack("Circuit"), stack("Tin", 144, "fluid"), stack("Mold", 0)],
          "outputs": [stack("Board")], "eut": 30, "tier": "LV", "ticks": 200}
    assert recipe_card.summary(gt) == "1× Circuit, 144 mB Tin, Mold (not used up) → 1× Board · 30 EU/t (LV) · 10 s"
    many = {"inputs": [stack(f"Part {i}") for i in range(9)], "outputs": [stack("Thing")]}
    assert recipe_card.summary(many).count(",") == recipe_card.MAX_LISTED - 1
    assert "4 more" in recipe_card.summary(many)


def test_slot_amounts_match_the_book():
    assert recipe_card.slot_amount({"size": 1}) == ""
    assert recipe_card.slot_amount({"size": 0}) == "NC"
    assert recipe_card.slot_amount({"size": 2500}) == "2,500"
    assert recipe_card.slot_amount({"size": 12345}) == "12.3k"
    assert recipe_card.slot_amount({"size": 144, "kind": "fluid"}) == "144L"
    assert recipe_card.slot_amount({"size": 1_234_567, "kind": "fluid"}) == "1.23ML"
    assert recipe_card.format_qty(2e21) == "2.00e+21"


class FakePage:
    map_name = "Electric Blast Furnace"

    def __init__(self, row):
        self.row = row


def test_cards_draw_every_layout_at_a_whole_pixel_scale(data):
    rows = [
        {"inputs": [stack("Circuit"), stack("Tin", 144, "fluid")], "outputs": [stack("Board")],
         "eut": 30, "tier": "UIV", "ticks": 200, "requirement_notes": ["Cleanroom: Yes", "A very long condition " * 8]},
        {"grid": {"w": 2, "h": 2, "shapeless": True, "slots": [{"alternatives": [stack("Plate")]}]},
         "outputs": [stack("Gear")]},
        {"preview": {"family": "arcane", "width": 120, "height": 90,
                     "slots": [{"x": 4, "y": 4, "w": 18, "h": 18, "framed": True, "frame": None,
                                "alternatives": [stack("Rod", 0)]}],
                     "layers": [{"x": 0, "y": 0, "w": 120, "h": 90, "image": "ui/missing.webp", "uv": [0, 0, 1, 1]}],
                     "lines": [{"x1": 0, "y1": 0, "x2": 50, "y2": 50}],
                     "labels": [{"x": 10, "y": 60, "w": 30, "h": 8, "text": "15%"}],
                     "aspects": [{"x": 60, "y": 60, "w": 16, "h": 16, "amount": 25, "name": "Order", "icon": None}]},
         "outputs": [stack("Wand")], "breeding": {"chance": 15, "conditions": ["Biome: Forest"]}},
    ]
    for row in rows:
        page = FakePage(row)
        width, height = recipe_card.size(page)
        image = Image.open(io.BytesIO(recipe_card.render(page)))
        assert image.size == (width, height)
        assert max(image.size) <= recipe_card.MAX_PIXELS
        plan = recipe_card._plan(page)
        assert width == plan["width"] * plan["scale"] and plan["scale"] >= 1
    lines, notes = recipe_card.stat_lines(rows[0])
    assert ["".join(run[0] for run in line) for line in lines] == [
        "Total: 6,000 EU", "Usage: 30 EU/t (UIV)", "Time: 10 seconds"]
    tier = lines[1][3]
    assert tier[0] == "UIV" and tier[2] and tier[3]  # bold and underlined, as in the game
    assert notes[0] == "Needs Cleanroom"


def test_recipe_view_row_shapes_are_what_the_card_reads(data):
    """The card reads rows as the book's API serves them."""
    from gcm import recipe_index
    shown, _ = recipe_viewer.listing(data, recipe_index.ITEM, "minecraft:planks:0", "making", limit=20)
    for row in shown:
        recipe_card.scene(row)
        recipe_card.summary(row)
        recipe_card.stat_lines(row)


def test_card_text_keeps_to_the_fonts_ascii():
    assert recipe_card.glyphs("Infusion Research · Thaumaturgy") == "Infusion Research - Thaumaturgy"
    assert recipe_card.glyphs("Électrique 2× → 3°") == "Electrique 2x -> 3 deg"
    assert recipe_card.glyphs("液体") == "??"


def test_research_rows_say_what_is_scanned_for_which_recipe():
    row = {"type": "research", "inputs": [stack("Neutronium Dust")], "outputs": [stack("Assembly Result")],
           "scan": {"method": "scanner", "usage": 120, "tier": "MV", "ticks": 100}}
    assert recipe_card.summary(row) == ("Scan 1× Neutronium Dust to write the Assembly Line recipe for "
                                        "Assembly Result to a Data Stick · 120 EU/t (MV) · 5 s")
    _, notes = recipe_card.stat_lines(row)
    assert "Data Stick" in notes[-1]


def test_infusion_essentia_and_research_sit_under_the_recipe():
    preview = {"family": "infusion", "width": 100, "height": 60, "slots": [], "layers": [], "lines": [], "labels": [],
               "aspects": [{"tag": t, "name": t, "amount": n, "icon": None} for t, n in
                           [("ordo", 25), ("aer", 16), ("terra", 8), ("ignis", 4), ("aqua", 2), ("perditio", 1)]],
               "research": {"name": "Infusion", "category": "Thaumaturgy", "icon": None}}
    row = {"preview": preview, "outputs": [stack("Thing")], "requirements": {"instability": 3},
           "requirement_notes": ["Essentia: 25 Order", "Instability: 3", "Research: INFUSION"]}
    page = FakePage(row)
    plan = recipe_card._plan(page)
    kinds = [part["kind"] for part in plan["under"]]
    assert kinds == ["center", "aspects", "research"]
    assert plan["under"][0]["text"] == "Instability: 3"
    # Wrapped to the card's width, every aspect kept.
    assert sum(len(r) for r in plan["under"][1]["rows"]) == 6
    assert " ".join(plan["under"][2]["lines"]) == "Research: Infusion - Thaumaturgy"
    # Said once, not again as notes.
    assert plan["wrapped"] == []
    assert Image.open(io.BytesIO(recipe_card.render(page))).size == recipe_card.size(page)


def test_research_station_cards_draw_the_wiring():
    preview = {"family": "research_station", "width": 170, "height": 82, "slots": [], "layers": [],
               "lines": [], "labels": [], "aspects": []}
    page = FakePage({"type": "research", "preview": preview, "inputs": [], "outputs": [stack("Result")],
                     "scan": {"method": "research_station", "usage": 32768}})
    image = Image.open(io.BytesIO(recipe_card.render(page))).convert("RGB")
    plan = recipe_card._plan(page)
    s, top = plan["scale"], (recipe_card.PADDING + 10) * plan["scale"]
    left = (plan["width"] - 170) // 2 * s
    # The core's grey edge, where the page's SVG draws it.
    assert image.getpixel((left + 38 * s, top + 40 * s)) == (0x55, 0x55, 0x55)
