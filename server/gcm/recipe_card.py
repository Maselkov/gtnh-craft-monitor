"""One page of the recipe book as a link preview: the recipe drawn as a
PNG (the book's card, gcm/routes/pages.py's og:image) and as a line of
text (its description).

The drawing follows static/js/recipe-render.js: the same slot layout
(the crafting grid, a bundle's NEI preview, or inputs -> outputs for a
recipe with neither), on the same paper, in the same pixel font, at a
whole number of image pixels per GUI pixel so the pixel art stays sharp.
Discord shows an animated image's first frame in a link preview, so
animated icons are drawn still.

Pillow is imported only to draw: it's matplotlib's dependency, and a
server nobody shares a recipe link from never loads it."""

import functools
import hashlib
import io
import itertools
import math
import os
import unicodedata

from gcm import config, icons, mctext, recipe_viewer, recipes

# app.css's --recipe-* colours (the same in both themes).
PAPER = "#c6c6c6"
INK = "#000000"
NOTE = "#4a4a4a"
SLOT = "#8b8b8b"
SLOT_SHADOW = "#373737"
SLOT_LIGHT = "#ffffff"
ARROW = "#8b8b8b"
COUNT = "#ffffff"
COUNT_SHADOW = "#222222"
NC = "#ffff55"
TREE_LINE = "#c25a5a"
TREE_CHANCE = "#aa0000"
# A slot frame the game data has no texture for: app.css's
# .recipe-preview-fallback-frame, in its light theme's colours.
FALLBACK_FILL = "#dde0e6"
FALLBACK_OUTLINE = "#c3c7cf"

# GTNH 2.9's tier colours as Minecraft formatting codes, and which are
# bold or underlined (recipe-render.js's TIER_STYLE).
TIER_STYLE = {
    "ULV": ("c", False, False), "LV": ("2", False, False), "MV": ("6", False, False),
    "HV": ("e", False, False), "EV": ("8", False, False), "IV": ("9", False, False),
    "LuV": ("d", False, False), "ZPM": ("b", False, False), "UV": ("2", False, True),
    "UHV": ("4", False, True), "UEV": ("5", False, True), "UIV": ("1", True, True),
    "UMV": ("c", True, True), "UXV": ("4", True, True), "MAX": ("f", True, True),
}

# GUI pixels around the card's contents, and the most image pixels per
# GUI pixel: Discord shows a large preview about 400-550 px wide.
PADDING = 7
MAX_SCALE = 4
MAX_PIXELS = 1100
# Inputs and outputs listed in a description before "and N more".
MAX_LISTED = 6

FONT_DIR = os.path.join(config.SERVER_DIR, "static", "fonts")


def game_colour(code):
    """A Minecraft formatting code's colour (recipe-render.js's gameColour)."""
    value = int(code, 16)
    bright = 85 if value & 8 else 0
    return ((170 if value & 4 else 0) + bright + (85 if value == 6 else 0),
            (170 if value & 2 else 0) + bright, (170 if value & 1 else 0) + bright)


def format_qty(n):
    """util.js's formatQty()."""
    a = abs(n)
    if a >= 1e21:
        return f"{n:.2e}"
    for limit, suffix in ((1e18, "E"), (1e15, "P"), (1e12, "T"), (1e9, "G"), (1e6, "M")):
        if a >= limit:
            return f"{n / limit:.2f}{suffix}"
    if a >= 1e3:
        return f"{n / 1e3:.1f}k"
    return str(round(n))


def _three_digits(text):
    """'12.34k' as '12.3k': three significant digits, as in a slot."""
    number = text.rstrip("kMGTPE")
    if number == text or "." not in number:
        return text
    return f"{float(f'{float(number):.3g}'):g}" + text[len(number):]


def slot_amount(e):
    """The amount printed on a slot (recipe-render.js's previewAmountText)."""
    size = e.get("size")
    if size is None:
        return "?"
    if size == 0:
        return "NC"
    if e.get("kind") == "fluid":
        return _three_digits(format_qty(size)) + "L"
    if size == 1:
        return ""
    return _three_digits(format_qty(size)) if size >= 10000 else f"{size:,}"


def amount_text(e):
    """An amount in words (recipe-render.js's amountText)."""
    size = e.get("size")
    if size is None:
        return "?"
    if e.get("kind") == "fluid":
        return f"{size:,} mB"
    if size == 0:
        return "not used up"
    return f"{size:,}x"


def display_first(alternatives):
    """The alternative a slot shows first (recipe-render.js's displayChoices)."""
    for e in alternatives or []:
        return (e.get("display_variants") or [e])[0]
    return None


def crafting_title(row):
    return "Shapeless Crafting" if row["grid"].get("shapeless") else "Shaped Crafting"


# ---------- Layout, in GUI pixels ----------

def _crafting_scene(row):
    """recipe-render.js's craftingHtml()."""
    grid = row["grid"]
    cols, rows_ = max(3, grid["w"]), max(3, grid["h"])
    slots = []
    for y in range(rows_):
        for x in range(cols):
            index = y * grid["w"] + x
            # A shapeless recipe's list can be shorter than its w x h.
            slot = grid["slots"][index] if x < grid["w"] and y < grid["h"] and index < len(grid["slots"]) else None
            slots.append({"x": 25 + x * 18, "y": 12 + y * 18, "w": 18, "h": 18, "crafting": True,
                          "stack": display_first(slot["alternatives"]) if slot else None})
    for i, output in enumerate(row["outputs"]):
        slots.append({"x": 65 + cols * 18 + i * 28, "y": 12 + rows_ * 9 - 13, "w": 26, "h": 26,
                      "crafting": True, "stack": display_first([output])})
    return {"width": max(166, 112 + cols * 18 + (len(row["outputs"]) - 1) * 28),
            "height": max(92, 24 + rows_ * 18), "slots": slots, "layers": [], "lines": [], "labels": [],
            "aspects": [], "arrow": {"x": 33 + cols * 18, "y": 6 + rows_ * 9, "w": 26, "h": 12}}


def _preview_scene(row):
    p = row["preview"]
    slots = [{**{k: s[k] for k in ("x", "y", "w", "h")}, "crafting": bool(s.get("crafting")),
              "frame": s.get("frame"), "outline": bool(s.get("framed")) and not s.get("frame"),
              "stack": display_first(s.get("alternatives"))} for s in p.get("slots", [])]
    aspects = []
    if p.get("family") == "arcane":
        aspects = [{**{k: a[k] for k in ("x", "y", "w", "h")}, "icon": a.get("icon"), "amount": a.get("amount")}
                   for a in p.get("aspects", []) if "x" in a]
    return {"width": p["width"], "height": p["height"], "slots": slots, "layers": p.get("layers", []),
            "lines": p.get("lines", []), "labels": p.get("labels", []), "aspects": aspects, "arrow": None,
            "wires": p.get("family") == "research_station"}


def _plain_scene(row):
    """A recipe with no grid or preview: its inputs, an arrow, its outputs,
    three slots to a row on each side."""
    inputs = [display_first(g.get("alternatives")) for g in row.get("groups", [])] + list(row.get("inputs", []))
    inputs = [e for e in inputs if e]
    outputs = list(row.get("outputs", []))
    cols_in, cols_out = min(3, max(1, len(inputs))), min(3, max(1, len(outputs)))
    rows_in, rows_out = math.ceil(len(inputs) / 3) or 1, math.ceil(len(outputs) / 3) or 1
    height = max(rows_in, rows_out) * 18
    slots = []

    def side(stacks, cols, rows_, left):
        top = (height - rows_ * 18) // 2
        for i, e in enumerate(stacks):
            slots.append({"x": left + (i % cols) * 18, "y": 6 + top + (i // cols) * 18, "w": 18, "h": 18,
                          "crafting": True, "stack": e})

    side(inputs, cols_in, rows_in, 6)
    arrow_x = 6 + cols_in * 18 + 8
    side(outputs, cols_out, rows_out, arrow_x + 26 + 8)
    return {"width": arrow_x + 26 + 8 + cols_out * 18 + 6, "height": height + 12, "slots": slots,
            "layers": [], "lines": [], "labels": [], "aspects": [],
            "arrow": {"x": arrow_x, "y": 6 + height // 2 - 6, "w": 26, "h": 12}}


def scene(row):
    if row.get("grid"):
        return _crafting_scene(row)
    if row.get("preview"):
        return _preview_scene(row)
    return _plain_scene(row)


def _integer(n):
    return f"{int(n):,}"


def _energy(n):
    """GTNHlib's NumberFormatUtil (recipe-render.js's energyText)."""
    n = int(n)
    if abs(n) >= 10 ** 12:
        mantissa, exponent = f"{n:.2e}".split("e")
        return mantissa.rstrip("0").rstrip(".") + "e" + str(int(exponent))
    return f"{n:,}"


def _voltage(eut, tier):
    """A usage line's parts: (text, code, bold, underline) runs."""
    runs = [(f"{_integer(eut)} EU/t", None, False, False)]
    if tier:
        code, bold, underline = TIER_STYLE.get(tier, (None, False, False))
        runs += [(" (", None, False, False), (tier, code, bold, underline), (")", None, False, False)]
    return runs


def stat_lines(row):
    """The card's lines under the recipe (recipe-render.js's energyHtml),
    each a list of (text, colour code or None, bold, underline) runs."""
    lines = []

    def line(text):
        lines.append([(text, None, False, False)])

    scan = row.get("scan")
    if scan:
        if scan.get("max_eu") is not None:
            line(f"Max EU: {_energy(scan['max_eu'])} EU")
        if scan.get("total_eu") is not None:
            line(f"Total: {_energy(scan['total_eu'])} EU")
        if scan.get("usage") is not None:
            lines.append([("Usage: ", None, False, False),
                          *_voltage(scan["usage"], scan.get("tier") if scan.get("method") == "scanner" else None)])
        if scan.get("method") == "research_station" and (scan.get("amperage") or 0) > 1:
            lines.append([("Voltage: ", None, False, False), *_voltage(scan["eut"], scan.get("tier")),
                          (f" at {_integer(scan['amperage'])} A", None, False, False)])
        if scan.get("method") == "scanner" and scan.get("ticks") is not None:
            line(f"Time: {scan['ticks'] / 20:,g} seconds")
        if scan.get("computation") is not None:
            line(f"Computation: {_integer(scan['computation'])}")
        if scan.get("computation_per_second") is not None:
            line(f"Min Computation: {_integer(scan['computation_per_second'])} /s")
    else:
        eut, ticks = row.get("eut"), row.get("ticks")
        if eut and ticks and eut > 0 and ticks > 0:
            line(f"Total: {_energy(eut * ticks)} EU")
        if eut is not None:
            lines.append([("Usage: ", None, False, False), *_voltage(eut, row.get("tier"))])
        if ticks is not None:
            line(f"Time: {ticks / 20:,g} seconds")
    if row.get("special"):
        line(f"Heat: {row['special']:,} K" if row.get("heat") else f"Special: {row['special']}")
    if row.get("breeding"):
        line(f"Chance: {row['breeding']['chance']:,g}%")
    preview = row.get("preview") or {}
    notes = []
    for note in [*(row.get("breeding") or {}).get("conditions", []), *row.get("requirement_notes", [])]:
        lowered = note.lower()
        if lowered in ("cleanroom: no", "cleanroom: false"):
            continue
        if preview.get("research") and lowered.startswith("research:"):
            continue
        if preview.get("family") == "infusion" and lowered.startswith("instability:"):
            continue
        if preview.get("aspects") and lowered.startswith("essentia:"):
            continue
        note = "Needs Cleanroom" if lowered in ("cleanroom: yes", "cleanroom: true") else note
        if note not in notes:
            notes.append(note)
    if row.get("type") == "research":
        # Under the card on the page.
        notes.append("Scan the research item to write this Assembly Line recipe to a Data Stick.")
    return lines, notes


def research_text(preview):
    """The research line under a recipe: what it needs researched."""
    research = preview.get("research")
    if not research:
        return None
    category = research.get("category") if preview.get("family") != "arcane" else None
    return f"Research: {research['name']}" + (f" · {category}" if category else "")


# ---------- Text ----------

def _stack_words(e):
    name = mctext.plain(e.get("name") or "?")
    if e.get("kind") == "fluid":
        return f"{amount_text(e)} {name}"
    size = e.get("size")
    if size == 0:
        return f"{name} (not used up)"
    return f"{size:,}× {name}" if size is not None else name


def _listed(stacks):
    """'2× Plate, 1× Bolt': the same stack in several slots counted once."""
    totals = {}
    for e in stacks:
        if not e:
            continue
        key = (e.get("key") or e.get("name"), e.get("kind"), e.get("size") == 0)
        if key in totals:
            if totals[key].get("size") is not None and e.get("size") is not None:
                totals[key] = {**totals[key], "size": totals[key]["size"] + e["size"]}
        else:
            totals[key] = e
    words = [_stack_words(e) for e in totals.values()]
    if len(words) > MAX_LISTED:
        words = words[:MAX_LISTED - 1] + [f"{len(words) - MAX_LISTED + 1} more"]
    return ", ".join(words)


def recipe_inputs(row):
    if row.get("grid"):
        return [display_first(s["alternatives"]) for s in row["grid"]["slots"] if s]
    return [display_first(g.get("alternatives")) for g in row.get("groups", [])] + list(row.get("inputs", []))


def _research_summary(row):
    """A Scanner or Research Station recipe: what's scanned, for which
    Assembly Line recipe. Its output isn't made from its input."""
    scanned = _listed(row.get("inputs", [])) or "the research item"
    names = ", ".join(dict.fromkeys(mctext.plain(e.get("name") or "?") for e in row.get("outputs", [])))
    text = f"Scan {scanned} to write the Assembly Line recipe for {names} to a Data Stick"
    scan = row.get("scan") or {}
    extras = []
    if scan.get("usage") is not None:
        tier = scan.get("tier") if scan.get("method") == "scanner" else None
        extras.append(f"{_integer(scan['usage'])} EU/t" + (f" ({tier})" if tier else ""))
    if scan.get("method") == "scanner" and scan.get("ticks") is not None:
        extras.append(f"{scan['ticks'] / 20:,g} s")
    if scan.get("computation") is not None:
        extras.append(f"{_integer(scan['computation'])} computation")
    return " · ".join([text, *extras])


def summary(row):
    """The recipe in a line: what goes in, what comes out, and its costs."""
    if row.get("type") == "research":
        return _research_summary(row)
    ins, outs = _listed(recipe_inputs(row)), _listed(row.get("outputs", []))
    text = f"{ins} → {outs}" if ins and outs else outs or ins
    extras = []
    if row.get("eut") is not None:
        extras.append(f"{_integer(row['eut'])} EU/t" + (f" ({row['tier']})" if row.get("tier") else ""))
    if row.get("ticks") is not None:
        extras.append(f"{row['ticks'] / 20:,g} s")
    if row.get("special") and row.get("heat"):
        extras.append(f"{row['special']:,} K")
    if row.get("breeding"):
        extras.append(f"{row['breeding']['chance']:,g}% chance")
    return " · ".join([text, *extras]) if text else " · ".join(extras)


# ---------- A /recipes link ----------

class Page:
    """The recipe a /recipes link shows, picked as the book picks it
    (static/js/recipe-viewer.js's loadFrame): its category, or the
    item's first with recipes that way; its page, or the last one."""

    def __init__(self, args):
        kind, key = recipe_viewer.parse_key(args.get("key"))
        self.direction = "using" if args.get("dir") == "using" else "making"
        self.version, idx = recipes.selected_index()
        self.dataset = f"{self.version}|{idx.fingerprint}|{icons.data_version() or ''}"
        meta = recipe_viewer.item_summary(idx, kind, key)
        self.item = mctext.plain(meta["item"].get("name") or args.get("key"))
        counts = meta[self.direction]
        self.map = args.get("map") or ""
        if not counts.get(self.map):
            self.map = next((m["id"] for m in meta["maps"] if counts.get(m["id"])), "")
        self.map_name = next((m["name"] for m in meta["maps"] if m["id"] == self.map), self.map)
        self.total = counts.get(self.map, 0)
        try:
            page = int(args.get("page") or 1)
        except ValueError:
            page = 1
        self.index = max(0, min(page - 1, self.total - 1))
        self.row = None
        if self.total:
            shown, _ = recipe_viewer.listing(idx, kind, key, self.direction, self.map, limit=1, offset=self.index)
            self.row = shown[0] if shown else None
            if self.row and self.row.get("grid"):
                self.map_name = crafting_title(self.row)

    @property
    def version_tag(self):
        """Changes with the game data, for the image's URL: Discord keeps
        an image it has fetched."""
        return hashlib.sha256(self.dataset.encode()).hexdigest()[:12]

    def heading(self):
        return f"{'Uses' if self.direction == 'using' else 'Recipes'} for {self.item}"

    def position(self):
        return f"{self.map_name} · {self.index + 1} of {self.total}" if self.total else "No recipes"


# ---------- Drawing ----------

# The book's font is an ASCII subset (static/fonts/README.md): the page
# falls back to another font for anything else, an image can't.
_ASCII = str.maketrans({"·": "-", "×": "x", "→": "->", "–": "-", "—": "-", "‘": "'", "’": "'",
                        "“": '"', "”": '"', "…": "...", "°": " deg", "µ": "u", "²": "2", "³": "3"})


def glyphs(text):
    """text in characters the font has: accents dropped, a few symbols
    spelt out, anything else a ?."""
    text = unicodedata.normalize("NFKD", str(text).translate(_ASCII))
    return "".join(c if " " <= c <= "~" else "" if unicodedata.combining(c) else "?" for c in text)

@functools.lru_cache(maxsize=16)
def _font(size, bold=False):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(FONT_DIR, "recipe-bold.woff" if bold else "recipe-count.woff"), size)


@functools.lru_cache(maxsize=512)
def _image(path):
    """An icon or texture as a still RGBA image, or None."""
    from PIL import Image
    data = icons.read_image(path) if path else None
    if data is None:
        return None
    try:
        image = Image.open(io.BytesIO(data))  # an animated one opens at its first frame
        return image.convert("RGBA")
    except OSError:
        return None


def _text_width(runs, scale):
    return sum(_font(8 * scale, bold).getlength(text) for text, _code, bold, _u in runs)


def _draw_runs(draw, x, y, runs, scale, ink=INK):
    for text, code, bold, underline in runs:
        font = _font(8 * scale, bold)
        if code == "f":
            draw.text((x + scale, y + scale), text, font=font, fill=COUNT_SHADOW)
        draw.text((x, y), text, font=font, fill=game_colour(code) if code else ink)
        advance = font.getlength(text)
        if underline:
            draw.rectangle([x, y + 8 * scale, x + advance - 1, y + 9 * scale - 1], fill=game_colour(code) if code else ink)
        x += advance


def _wrap(text, width, scale):
    """Words that fit width image pixels per line (a long condition)."""
    font, lines, current = _font(8 * scale), [], ""
    for word in text.split():
        trial = f"{current} {word}" if current else word
        if current and font.getlength(trial) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current] if current else lines


def _paste_icon(card, path, x, y, size):
    """An item icon in a box of size image pixels at x, y; one drawn past
    its box (a cosmic halo, path ~bleed12/) spills over around it."""
    from PIL import Image
    image = _image(path)
    if image is None:
        return
    if path.split("/", 1)[0].endswith("~bleed12"):
        grown = size * 40 // 16
        x, y, size = x - (grown - size) // 2, y - (grown - size) // 2, grown
    method = Image.Resampling.NEAREST if size >= image.width else Image.Resampling.BOX
    image = image.resize((size, size), method)
    card.alpha_composite(image, (int(x), int(y)))


def _wire_paths():
    """The Research Station's wiring (recipe-render.js's researchWires),
    each path as its points, in its 170 x 82 picture."""
    paths = []
    for i in range(8):
        x, y = 16 + 2 * i, 14 + 2 * i
        paths += [[(x, 8), (x, y), (40 + i, y), (40 + i, 30)],
                  [(124 - x, 8), (124 - x, y), (84 - i, y), (84 - i, 30)],
                  [(x, 76), (x, 84 - y), (40 + i, 84 - y), (40 + i, 54)],
                  [(124 - x, 76), (124 - x, 84 - y), (84 - i, 84 - y), (84 - i, 54)],
                  [(40 + 2 * i, 8), (40 + 2 * i, 20)], [(66 + 2 * i, 8), (66 + 2 * i, 20)],
                  [(40 + 2 * i, 66), (40 + 2 * i, 76)], [(66 + 2 * i, 66), (66 + 2 * i, 76)]]
    return paths


WIRE_PATHS = _wire_paths()
# The three lines from the core to the output, and the bend down to the
# data stick's slot.
WIRE_LEADS = [[(84, 39), (106, 39)], [(84, 42), (106, 42)], [(84, 45), (106, 45)],
              [(115, 51), (115, 58), (133, 58), (133, 62)]]


def _draw_wires(draw, ox, oy, s, width, height):
    """The wiring under a Research Station recipe's slots, fitted to the
    picture as the page's SVG is (its viewBox, centred)."""
    k = min(width / 170, height / 82)
    left, top = ox + (width - 170 * k) / 2 * s, oy + (height - 82 * k) / 2 * s
    unit = k * s

    def stroke(points, thickness, colour):
        # Square ends and corners, as SVG's default miter joins draw them.
        half = max(1, round(thickness * unit)) / 2
        for (x1, y1), (x2, y2) in itertools.pairwise(points):
            ax, bx = sorted((left + x1 * unit, left + x2 * unit))
            ay, by = sorted((top + y1 * unit, top + y2 * unit))
            draw.rectangle([round(ax - half), round(ay - half), round(bx + half) - 1, round(by + half) - 1], fill=colour)

    for path in WIRE_PATHS:
        stroke(path, 2, "#ffffff")
    for path in WIRE_PATHS:
        stroke(path, 1.1, "#111111")
    for path in WIRE_LEADS:
        stroke(path, 1.5, "#ffffff")
    # The core: a fine grid (a 2-pixel tile, darker along its top and left)
    # with a grey edge.
    x0, y0 = round(left + 38 * unit), round(top + 20 * unit)
    x1, y1 = round(left + 86 * unit), round(top + 66 * unit)
    draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill="#aaaaaa")
    tile, line = 2 * unit, max(1, round(unit / 4))
    gx = x0
    while gx < x1:
        draw.rectangle([round(gx), y0, min(x1, round(gx) + line) - 1, y1 - 1], fill="#666666")
        gx += tile
    gy = y0
    while gy < y1:
        draw.rectangle([x0, round(gy), x1 - 1, min(y1, round(gy) + line) - 1], fill="#666666")
        gy += tile
    edge = max(1, round(unit))
    draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline="#555555", width=edge)


def _plan(page):
    """What render() draws and how big: the layout, the text lines, the
    card's size in GUI pixels and the image pixels per GUI pixel."""
    row = page.row
    layout = scene(row)
    lines, notes = stat_lines(row)
    title = [(glyphs(page.map_name), None, False, False)]
    lines = [[(glyphs(text), *rest) for text, *rest in line] for line in lines]
    notes = [glyphs(note) for note in notes]
    # The width in GUI pixels: the recipe's, or a longer line of text's.
    text_width = max([_text_width(title, 1), *(_text_width(line, 1) for line in lines)])
    width = math.ceil(max(layout["width"], text_width + 2 * PADDING))
    scale = max(1, min(MAX_SCALE, MAX_PIXELS // width))

    inner = width - 2 * PADDING
    # The font's advances are whole multiples of its size, so text wrapped
    # at one GUI pixel per pixel wraps the same at any scale.
    wrapped = [w for note in notes for w in _wrap(note, inner, 1)]
    under = _under(row, inner)
    height = (PADDING + 10 + math.ceil(layout["height"]) + 4 + sum(part["height"] for part in under)
              + 9 * (len(lines) + len(wrapped)) + PADDING)
    if height * scale > MAX_PIXELS:
        scale = max(1, MAX_PIXELS // height)
    return {"layout": layout, "title": title, "lines": lines, "wrapped": wrapped, "under": under,
            "width": width, "height": height, "scale": scale}


# GUI pixels across an essentia icon in the row under a recipe, and between them.
ASPECT_ICON = 16
ASPECT_GAP = 6


def _under(row, inner):
    """What the page shows between a recipe's picture and its stats
    (recipe-render.js's previewHtml): an infusion's instability, the
    essentia it takes as a row of icons with their amounts, and the
    research it needs, each {"kind", "height", ...} in GUI pixels."""
    preview = row.get("preview") or {}
    parts = []
    if preview.get("family") == "infusion":
        instability = (row.get("requirements") or {}).get("instability", "?")
        parts.append({"kind": "center", "height": 11, "text": glyphs(f"Instability: {instability}")})
    aspects = preview.get("aspects") or []
    if aspects and preview.get("family") != "arcane":
        rows, current, used = [], [], 0
        for aspect in aspects:
            cell = max(ASPECT_ICON, math.ceil(_font(8).getlength(glyphs(aspect.get("amount", "")))))
            if current and used + ASPECT_GAP + cell > inner:
                rows.append(current)
                current, used = [], 0
            used += (ASPECT_GAP if current else 0) + cell
            current.append({**aspect, "cell": cell})
        rows.append(current)
        parts.append({"kind": "aspects", "height": len(rows) * (ASPECT_ICON + 11), "rows": rows})
    text = research_text(preview)
    if text:
        icon = (preview.get("research") or {}).get("icon")
        indent = 14 if icon else 0
        wrapped = _wrap(glyphs(text), inner - indent, 1)
        parts.append({"kind": "research", "height": max(12, 9 * len(wrapped)) + 3, "icon": icon,
                      "indent": indent, "lines": wrapped})
    return parts


def size(page):
    """The card's (width, height) in image pixels."""
    plan = _plan(page)
    return plan["width"] * plan["scale"], plan["height"] * plan["scale"]


def render(page):
    """The card for page.row as PNG bytes."""
    from PIL import Image, ImageDraw

    plan = _plan(page)
    layout, title, lines, wrapped = plan["layout"], plan["title"], plan["lines"], plan["wrapped"]
    width, height, scale = plan["width"], plan["height"], plan["scale"]
    s = scale
    card = Image.new("RGBA", (width * s, height * s), PAPER)
    draw = ImageDraw.Draw(card)
    draw.fontmode = "1"  # the pixel font's pixels, not smoothed ones
    _draw_runs(draw, PADDING * s, PADDING * s, title, s, ink=NOTE)

    ox, oy = (width - layout["width"]) // 2 * s, (PADDING + 10) * s

    def box(r):
        return (ox + round(r["x"] * s), oy + round(r["y"] * s), round(r["w"] * s), round(r["h"] * s))

    for layer in layout["layers"]:
        texture = _image(layer.get("image"))
        if texture is None:
            continue
        u, v, right, bottom = layer["uv"]
        crop = texture.crop((round(u * texture.width), round(v * texture.height),
                             round(right * texture.width), round(bottom * texture.height)))
        x, y, w, h = box(layer)
        if w <= 0 or h <= 0 or crop.width <= 0 or crop.height <= 0:
            continue
        crop = crop.resize((w, h), Image.Resampling.NEAREST)
        if layer.get("opacity", 1) < 1:
            crop.putalpha(crop.getchannel("A").point(lambda a, o=layer["opacity"]: round(a * o)))
        card.alpha_composite(crop, (x, y))
    if layout.get("wires"):
        _draw_wires(draw, ox, oy, s, layout["width"], layout["height"])
    for line in layout["lines"]:
        draw.line([ox + line["x1"] * s, oy + line["y1"] * s, ox + line["x2"] * s, oy + line["y2"] * s],
                  fill=TREE_LINE, width=s)
    arrow = layout["arrow"]
    if arrow:
        x, y, w, h = box(arrow)
        draw.polygon([(x, y + h * .32), (x + w * .6, y + h * .32), (x + w * .6, y), (x + w, y + h / 2),
                      (x + w * .6, y + h), (x + w * .6, y + h * .68), (x, y + h * .68)], fill=ARROW)
    for slot in layout["slots"]:
        x, y, w, h = box(slot)
        edge = max(1, round(w / slot["w"])) if slot["w"] else s
        if slot.get("crafting"):
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=SLOT)
            draw.rectangle([x, y, x + w - 1, y + edge - 1], fill=SLOT_SHADOW)
            draw.rectangle([x, y, x + edge - 1, y + h - 1], fill=SLOT_SHADOW)
            draw.rectangle([x, y + h - edge, x + w - 1, y + h - 1], fill=SLOT_LIGHT)
            draw.rectangle([x + w - edge, y, x + w - 1, y + h - 1], fill=SLOT_LIGHT)
        elif slot.get("frame"):
            frame = _image(slot["frame"])
            if frame is not None:
                card.alpha_composite(frame.resize((w, h), Image.Resampling.NEAREST), (x, y))
        elif slot.get("outline"):
            draw.rectangle([x, y, x + w - 1, y + h - 1], fill=FALLBACK_FILL, outline=FALLBACK_OUTLINE, width=s)
        e = slot.get("stack")
        if not e:
            continue
        # The icon in the slot less a frame texel each side (app.css's 5.56%).
        inset = math.ceil(w / 18)
        _paste_icon(card, e.get("icon"), x + inset, y + inset, w - 2 * inset)
        count = slot_amount(e)
        if count:
            font = _font(8 * s)
            tw = font.getlength(count)
            tx, ty = x + w - tw, y + h - 8 * s
            draw.text((tx + s, ty + s), count, font=font, fill=COUNT_SHADOW)
            draw.text((tx, ty), count, font=font, fill=NC if count == "NC" else COUNT)
    for aspect in layout["aspects"]:
        x, y, w, h = box(aspect)
        _paste_icon(card, aspect.get("icon"), x, y, w)
        count = glyphs(aspect.get("amount", ""))
        font = _font(8 * s)
        tx, ty = x + w - font.getlength(count), y + h - 8 * s
        draw.text((tx + s, ty + s), count, font=font, fill=COUNT_SHADOW)
        draw.text((tx, ty), count, font=font, fill=COUNT)
    for label in layout["labels"]:
        x, y, w, _h = box(label)
        text = glyphs(label.get("text", ""))
        draw.text((x + (w - _font(8 * s).getlength(text)) / 2, y), text, font=_font(8 * s), fill=TREE_CHANCE)

    y = oy + math.ceil(layout["height"] + 4) * s
    font = _font(8 * s)
    for part in plan["under"]:
        if part["kind"] == "center":
            draw.text(((width * s - font.getlength(part["text"])) / 2, y), part["text"], font=font, fill=INK)
        elif part["kind"] == "aspects":
            for i, cells in enumerate(part["rows"]):
                used = sum(c["cell"] for c in cells) + ASPECT_GAP * (len(cells) - 1)
                x = (width - used) / 2 * s
                top = y + i * (ASPECT_ICON + 11) * s
                for c in cells:
                    _paste_icon(card, c.get("icon"), round(x + (c["cell"] - ASPECT_ICON) / 2 * s), top, ASPECT_ICON * s)
                    amount = glyphs(c.get("amount", ""))
                    draw.text((x + (c["cell"] * s - font.getlength(amount)) / 2, top + (ASPECT_ICON + 1) * s),
                              amount, font=font, fill=INK)
                    x += (c["cell"] + ASPECT_GAP) * s
        else:
            if part["icon"]:
                _paste_icon(card, part["icon"], PADDING * s, y, 12 * s)
            for i, text in enumerate(part["lines"]):
                draw.text(((PADDING + part["indent"]) * s, y + (2 + 9 * i) * s), text, font=font, fill=NOTE)
        y += part["height"] * s
    for line in lines:
        _draw_runs(draw, PADDING * s, y, line, s)
        y += 9 * s
    for line in wrapped:
        _draw_runs(draw, PADDING * s, y, [(line, None, False, False)], s, ink=NOTE)
        y += 9 * s
    out = io.BytesIO()
    card.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
