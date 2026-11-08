"""Build the local recipe fonts (requires fonttools).

Uses Idrees Hassan's OFL-1.1 Minecraft-Font at a pinned revision. Remove
its half-pixel side padding and align the em to Minecraft's eight-pixel
character grid, retaining the original glyph outlines and descenders.
Runtime needs only the prebuilt WOFF files, with no network dependency.
"""
import hashlib
import urllib.request
from io import BytesIO
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

REVISION = "261ac77fbf28796ca09c22eb83ecdfe386c4b838"
BASE_URL = f"https://raw.githubusercontent.com/IdreesInc/Minecraft-Font/{REVISION}"
SOURCES = {
    "Minecraft.otf": "ebc7a7be9f69479f02875af1fe79a2b93cc868b83be4f6369ecceb45fabe5ee6",
    "Minecraft-Bold.otf": "2a7216d047fd2991aa8a2fcbc002e0782a903d68de3cc248092ffa88aa941b4f",
    "LICENSE": "5bf787b21c1a2cdee6050d5a676b5a4e5ebe9ca630a43e5ade8e8b05fe584a58",
}


def download(name):
    with urllib.request.urlopen(f"{BASE_URL}/{name}", timeout=30) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SOURCES[name]:
        raise ValueError(f"Unexpected upstream content for {name}")
    return data


def build(data, path, bold):
    source = TTFont(BytesIO(data))
    cmap, outlines = source.getBestCmap(), source.getGlyphSet()
    chars = [chr(code) for code in range(32, 127) if code in cmap]
    names = {char: f"uni{ord(char):04X}" for char in chars}
    builder = FontBuilder(16, isTTF=True)
    builder.setupGlyphOrder([".notdef", *names.values()])
    builder.setupCharacterMap({ord(char): name for char, name in names.items()})
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    metrics = {".notdef": (12, 0)}
    for char in chars:
        original = cmap[ord(char)]
        advance, bearing = source["hmtx"][original]
        # Each source pixel is two font units. Its one-unit left padding
        # otherwise lands glyphs between pixels at normal GUI scales.
        shift = -1 if bearing == 1 else 0
        pen = TTGlyphPen(None)
        outlines[original].draw(TransformPen(pen, (1, 0, 0, 1, shift, 0)))
        glyphs[names[char]] = pen.glyph()
        metrics[names[char]] = (advance + shift, bearing + shift)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=16, descent=-2)
    style = "Bold" if bold else "Regular"
    builder.setupNameTable({
        "copyright": "Copyright (c) 2022, Idrees Hassan. Pixel alignment by GTNH Craft Monitor.",
        "familyName": "GCM Recipe", "styleName": style,
        "uniqueFontIdentifier": f"GCMRecipe-{style}-2", "fullName": f"GCM Recipe {style}",
        "psName": f"GCMRecipe-{style}", "version": "Version 2.0",
        "licenseDescription": "SIL Open Font License, Version 1.1. See OFL-Minecraft-Font.txt.",
        "licenseInfoURL": "https://openfontlicense.org/",
    })
    builder.setupOS2(sTypoAscender=16, sTypoDescender=-2, usWinAscent=16, usWinDescent=2,
                     usWeightClass=700 if bold else 400, fsSelection=0x20 if bold else 0x40)
    builder.setupPost()
    builder.font["head"].macStyle = 1 if bold else 0
    builder.font["head"].created = builder.font["head"].modified = 2082844800
    builder.font.recalcTimestamp = False
    builder.font.flavor = "woff"
    builder.save(path)
    if not bold:
        print("Non-default advances (em):", {char: metrics[name][0] / 16
              for char, name in names.items() if metrics[name][0] != 12})


def main():
    directory = Path(__file__).resolve().parents[1] / "server/static/fonts"
    directory.mkdir(exist_ok=True)
    (directory / "OFL-Minecraft-Font.txt").write_bytes(download("LICENSE"))
    build(download("Minecraft.otf"), directory / "recipe-count.woff", False)
    build(download("Minecraft-Bold.otf"), directory / "recipe-bold.woff", True)


if __name__ == "__main__":
    main()
