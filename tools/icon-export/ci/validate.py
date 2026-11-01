#!/usr/bin/env python3
"""
Sanity checks on an icon export before CI publishes it.

    python3 validate.py <out-dir>

Exits non-zero (and says why) if the counts in export-report.json are far
below what a GTNH pack produces, or if too many icons are flat single-colour
silhouettes. The latter is how broken GL state shows up: the export still
"succeeds", but every icon after some point is one colour (see CONTEXT.md,
"Rendering icons at the main menu"). A good export has ~1% flat icons.

item_info.json's tooltips have to have a known GregTech multiblock's
abbreviation in them (DTPF, from its machine type line), or they came
out without the lines that make searching them worthwhile; and enough
of them have to change with Shift held, or the export's Shift (see
ExportKeys.java) didn't reach GregTech.

Icons have to be WebP, under .webp paths. Animated ones are checked too:
there have to be plenty of them (fewer means the animation capture broke),
and none may be huge.

images-faithful32.zip, when there is one, gets the same flat check, and
enough of it has to differ from images.zip to show the resource pack was
actually applied.

Needs Pillow.
"""
import json
import random
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageStat

MINIMUMS = {
    "by_keyEntries": 40000,
    "fluids_by_keyEntries": 1000,
    # Thaumcraft has about 50 aspects, and GTNH's addons add more.
    "aspects_by_keyEntries": 40,
    # GTNH 2.9 has about 1,200 names that can stand in for something.
    "oreNames": 500,
    "catalogIds": 8000,
    # Every GregTech material item has its chemical formula.
    "tooltipsByKey": 10000,
    # Every GregTech multiblock shows its structure with Shift.
    "shiftTooltipsByKey": 200,
    "fluidInfo": 1000,
    # GTNH 2.9 has about 26,000 items and 860 fluids with a GregTech
    # formula; far fewer means the reflection into GregTech broke.
    "formulasByKey": 15000,
    "fluidFormulas": 400,
    "modNames": 100,
    # GTNH 2.9 has about 175,000 GregTech recipes in 138 maps, and NEI
    # names machines for every map: far fewer means the reflection into
    # GregTech (Recipes.java) or NEI's catalysts broke.
    "recipes": 100000,
    "recipeMapsWithMachines": 100,
}
# A tooltip line every GTNH version since the DTPF's has.
KNOWN_TOOLTIP = "dtpf"
MAX_FAILED_SHARE = 0.05
FLAT_SAMPLE = 500
MAX_FLAT_SHARE = 0.05
FLAT_STDDEV = 2.0
FAITHFUL_ZIP = "images-faithful32.zip"
# The pack retextures part of GTNH; a failed load changes nothing.
MIN_RETEXTURED_SHARE = 0.2
MAX_FALLBACK_SHARE = 0.05
# A full GTNH export has a few thousand; see CONTEXT.md, "Animated icons".
MIN_ANIMATED_ICONS = 1000
MAX_ICON_BYTES = 2_000_000
# export.py's; every icon's path in them must name a WebP.
LOOKUP_TABLES = ("by_key", "by_key_label", "by_key_material", "fluids_by_key", "aspects_by_key", "by_label")


def is_webp(data):
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def is_flat(png):
    image = Image.open(BytesIO(png)).convert("RGBA")
    alpha = image.getchannel("A").point(lambda a: 255 if a else 0)
    if not alpha.getbbox():
        return False
    stddev = ImageStat.Stat(image.convert("RGB"), mask=alpha).stddev
    return sum(stddev) / 3 < FLAT_STDDEV


def check(out_dir):
    problems = []
    report = json.loads((out_dir / "export-report.json").read_text())
    for key, minimum in MINIMUMS.items():
        if report.get(key, 0) < minimum:
            problems.append(f"{key} is {report.get(key)}, expected >= {minimum}")
    attempted = report.get("itemsRendered", 0) + report.get("itemsFailed", 0)
    if attempted and report.get("itemsFailed", 0) / attempted > MAX_FAILED_SHARE:
        problems.append(f"{report['itemsFailed']} of {attempted} items failed "
                        "to render")

    if report.get("animationTicks", 0) > 0 \
            and report.get("animatedImages", 0) < MIN_ANIMATED_ICONS:
        problems.append(f"only {report.get('animatedImages', 0)} animated icons, "
                        f"expected >= {MIN_ANIMATED_ICONS}")
    with zipfile.ZipFile(out_dir / "images.zip") as zf:
        biggest = max(zf.infolist(), key=lambda info: info.file_size)
    if biggest.file_size > MAX_ICON_BYTES:
        problems.append(f"{biggest.filename} is {biggest.file_size} bytes")

    tooltips = json.loads((out_dir / "item_info.json").read_text())["tooltips"]
    if not any(KNOWN_TOOLTIP in text.lower() for text in tooltips["by_key"].values()):
        problems.append(f"no tooltip mentions {KNOWN_TOOLTIP.upper()}")

    lookup = json.loads((out_dir / "icons_lookup.json").read_text())
    with zipfile.ZipFile(out_dir / "images.zip") as zf:
        names = set(zf.namelist())
    missing = [p for p in lookup.get("bleed", {}) if p not in names]
    if missing:
        problems.append(f"{len(missing)} icons in the lookup's bleed table "
                        f"aren't in images.zip, e.g. {missing[0]}")
    not_webp = [p for table in LOOKUP_TABLES
                for p in lookup.get(table, {}).values() if not p.endswith(".webp")]
    if not_webp:
        problems.append(f"{len(not_webp)} paths in the lookup aren't .webp, "
                        f"e.g. {not_webp[0]}")
    paths = sorted(set(lookup["by_key"].values()))
    sample = random.Random(0).sample(paths, min(FLAT_SAMPLE, len(paths)))
    with zipfile.ZipFile(out_dir / "images.zip") as zf:
        default = {p: zf.read(p) for p in sample}
    not_webp = [p for p, data in default.items() if not is_webp(data)]
    if not_webp:
        problems.append(f"{len(not_webp)} of {len(sample)} sampled icons "
                        f"aren't WebP, e.g. {not_webp[0]}")
    flat = sum(is_flat(png) for png in default.values())
    if sample and flat / len(sample) > MAX_FLAT_SHARE:
        problems.append(f"{flat} of {len(sample)} sampled icons are flat "
                        "single-colour silhouettes")
    if (out_dir / FAITHFUL_ZIP).exists():
        problems += check_faithful(out_dir, default, len(paths))
    return problems, flat, len(sample)


def check_faithful(out_dir, default, total):
    problems = []
    with zipfile.ZipFile(out_dir / FAITHFUL_ZIP) as zf:
        names = set(zf.namelist())
        if "CREDITS.txt" not in names:
            problems.append(f"{FAITHFUL_ZIP} has no CREDITS.txt")
        missing = [p for p in default if p not in names]
        if missing:
            return problems + [f"{FAITHFUL_ZIP} is missing {len(missing)} "
                               f"sampled icons, e.g. {missing[0]}"]
        textured = {p: zf.read(p) for p in default}
    flat = sum(is_flat(png) for png in textured.values())
    changed = sum(textured[p] != default[p] for p in default)
    print(f"Faithful: {flat}/{len(default)} flat, "
          f"{changed}/{len(default)} differ from the default textures")
    if default and flat / len(default) > MAX_FLAT_SHARE:
        problems.append(f"{flat} of {len(default)} sampled Faithful icons "
                        "are flat single-colour silhouettes")
    if default and changed / len(default) < MIN_RETEXTURED_SHARE:
        problems.append(f"only {changed} of {len(default)} sampled Faithful "
                        "icons differ from the default ones; was the "
                        "resource pack applied?")
    manifest = json.loads((out_dir / "data.json").read_text())
    fell_back = manifest["textures"]["faithful32"]["default_fallbacks"]
    if total and fell_back / total > MAX_FALLBACK_SHARE:
        problems.append(f"{fell_back} Faithful icons fell back to the "
                        "default render")
    return problems


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
    problems, flat, sampled = check(out_dir)
    print(f"Flat icons: {flat}/{sampled}")
    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        sys.exit(1)
    print("Export looks sane.")


if __name__ == "__main__":
    main()
