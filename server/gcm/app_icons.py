"""The page's own icons (static/icons/) with their blue maze in another
colour, for config.ICON_COLOR: so a test server's tab, bookmark or
installed app doesn't pass for the real one's. The icons are flat
colours, so the blue is one exact colour in the SVG and every PNG.

The favicon also comes with a dot in its corner (badged()), which the
page shows while something wants attention (static/js/page-title.js)."""

import io
import os
import threading

from gcm import config

BLUE = (0x5F, 0xB3, 0xFF)

# The favicon's dots: app.css's --idle and --stale (dark theme), the tab
# badges' colours. One value suits both themes: it's on the icon's own
# dark background.
DOTS = {"done": (0x4C, 0xAF, 0x7D), "alert": (0xFF, 0x6B, 0x6B)}
DOT_ICONS = ("favicon.svg", "favicon-32.png")
RING = (0xF4, 0xF5, 0xF7)
# In the SVG's 64x64 units: inside the icon's own edge.
DOT_CENTER, DOT_RADIUS, RING_WIDTH = 47, 12, 4

_made = {}
_lock = threading.Lock()


def recolored(name, color):
    """static/icons/<name> with BLUE as color, as (bytes, mimetype), or
    None for a file that isn't there. Made once per name and colour."""
    key = (name, color)
    with _lock:
        if key not in _made:
            _made[key] = _make(name, color)
        return _made[key]


def badged(name, color, dot):
    """static/icons/<name> (a DOT_ICONS one), in color (None for its own
    blue), with the dot named by dot (a DOTS key) in its bottom-right
    corner, as (bytes, mimetype); None for an icon or dot that isn't
    one. Made once per name, colour and dot."""
    if name not in DOT_ICONS or dot not in DOTS:
        return None
    key = (name, color, dot)
    with _lock:
        if key not in _made:
            _made[key] = _add_dot(*_make(name, color or BLUE), DOTS[dot])
        return _made[key]


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _add_dot(data, mimetype, dot):
    # A third of the icon across, so it still shows at 16px, in a light
    # ring that keeps it apart from the maze, whatever ICON_COLOR made it.
    if mimetype == "image/svg+xml":
        circle = (f'<circle cx="{DOT_CENTER}" cy="{DOT_CENTER}" r="{DOT_RADIUS}" fill="{_hex(dot)}" '
                  f'stroke="{_hex(RING)}" stroke-width="{RING_WIDTH}" shape-rendering="auto"/>')
        return data.decode().replace("</svg>", circle + "</svg>").encode(), mimetype
    from PIL import Image, ImageDraw

    image = Image.open(io.BytesIO(data)).convert("RGBA")
    # Drawn 4x over and scaled down: PIL's circles have no anti-aliasing.
    big = 4 * image.width
    scale = big / 64
    overlay = Image.new("RGBA", (big, big))
    draw = ImageDraw.Draw(overlay)
    for radius, fill in ((DOT_RADIUS + RING_WIDTH / 2, RING), (DOT_RADIUS - RING_WIDTH / 2, dot)):
        c, r = DOT_CENTER * scale, radius * scale
        draw.ellipse((c - r, c - r, c + r, c + r), fill=fill + (255,))
    image.alpha_composite(overlay.resize(image.size, Image.LANCZOS))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue(), mimetype


def _make(name, color):
    path = os.path.join(config.SERVER_DIR, "static", "icons", os.path.basename(name))
    if not os.path.isfile(path):
        return None
    if path.endswith(".svg"):
        with open(path, encoding="utf-8") as f:
            svg = f.read()
        return svg.replace("#%02x%02x%02x" % BLUE, "#%02x%02x%02x" % color).encode(), "image/svg+xml"
    if not path.endswith(".png"):
        return None
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    pixels = bytearray(image.tobytes())
    blue, new = bytes(BLUE), bytes(color)
    for i in range(0, len(pixels), 4):
        if pixels[i:i + 3] == blue:
            pixels[i:i + 3] = new
    out = io.BytesIO()
    Image.frombytes("RGBA", image.size, bytes(pixels)).save(out, format="PNG")
    return out.getvalue(), "image/png"
