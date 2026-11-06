"""The page's own icons (static/icons/) with their blue maze in another
colour, for config.ICON_COLOR: so a test server's tab, bookmark or
installed app doesn't pass for the real one's. The icons are flat
colours, so the blue is one exact colour in the SVG and every PNG."""

import io
import os
import threading

from gcm import config

BLUE = (0x5F, 0xB3, 0xFF)

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
