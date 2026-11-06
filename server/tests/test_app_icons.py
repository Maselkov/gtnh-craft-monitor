import io

import pytest
from gcm import app_icons, config
from PIL import Image

RED = (0xFF, 0x6B, 0x6B)
PNGS = ("favicon-32.png", "icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png")


def colours(png):
    return {c[:3] for _, c in Image.open(io.BytesIO(png)).convert("RGBA").getcolors(1 << 16)}


def test_icons_are_blue_without_icon_color(client):
    assert config.ICON_COLOR is None
    assert "#5fb3ff" in client.get("/static/icons/favicon.svg").get_data(as_text=True)
    assert app_icons.BLUE in colours(client.get("/static/icons/icon-192.png").data)


def test_icon_color_recolours_every_icon(client, monkeypatch):
    monkeypatch.setattr(config, "ICON_COLOR", RED)
    svg = client.get("/static/icons/favicon.svg")
    assert svg.mimetype == "image/svg+xml"
    assert "#ff6b6b" in svg.get_data(as_text=True) and "#5fb3ff" not in svg.get_data(as_text=True)
    # Revalidated like any static file, so a changed colour shows.
    assert svg.headers["Cache-Control"] == "no-cache"
    for name in PNGS:
        response = client.get(f"/static/icons/{name}")
        assert response.mimetype == "image/png", name
        assert RED in colours(response.data) and app_icons.BLUE not in colours(response.data), name
    # Only the icons: the rest of static/ is the files as they are.
    assert client.get("/static/manifest.json").get_json()["name"] == "GTNH Monitor"
    assert client.get("/static/icons/nothing.png").status_code == 404


@pytest.mark.parametrize("value, parsed", [
    ("", None), ("  ", None), ("#ff6b6b", RED), ("FF6B6B", RED), (" #Ff6b6B ", RED),
])
def test_icon_color_setting(value, parsed):
    assert config._parse_icon_color(value) == parsed


@pytest.mark.parametrize("value", ["red", "#ff6b6", "#ff6b6bff", "rgb(255,0,0)"])
def test_an_icon_color_that_isnt_one_stops_the_server(value):
    with pytest.raises(RuntimeError, match="ICON_COLOR"):
        config._parse_icon_color(value)


@pytest.mark.parametrize("color", [None, RED])
def test_favicon_with_a_dot(client, monkeypatch, color):
    monkeypatch.setattr(config, "ICON_COLOR", color)
    maze = "#ff6b6b" if color else "#5fb3ff"
    for dot, rgb in app_icons.DOTS.items():
        svg = client.get(f"/static/icons/favicon.svg?v=1&dot={dot}")
        assert svg.mimetype == "image/svg+xml"
        text = svg.get_data(as_text=True)
        assert 'fill="#%02x%02x%02x"' % rgb in text and "<circle" in text and maze in text
        png = client.get(f"/static/icons/favicon-32.png?dot={dot}")
        assert png.mimetype == "image/png"
        assert rgb in colours(png.data) and app_icons.RING in colours(png.data)
        assert (RED if color else app_icons.BLUE) in colours(png.data)
    plain = client.get("/static/icons/favicon.svg").get_data(as_text=True)
    assert "<circle" not in plain
    # A dot that isn't one, or on an icon that has none: the plain icon.
    assert client.get("/static/icons/favicon.svg?dot=nope").get_data(as_text=True) == plain
    assert client.get("/static/icons/icon-192.png?dot=alert").data == client.get("/static/icons/icon-192.png").data
