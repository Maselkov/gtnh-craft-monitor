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
import gzip
import json
import random
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageStat

from audit_previews import audit

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
    "craftingRecipes": 40000,
    "furnaceRecipes": 5000,
    # GTNH 2.9 has 334 GregTech multiblocks, and every one builds: far
    # fewer means the build in StructureBuilder.java broke.
    "multiblocks": 250,
    # The blocks they're built from, with textures for 3D (Layouts.java):
    # about 600 kinds in 2.9, nearly all with textures.
    "layoutBlocks": 300,
    "layoutTextures": 300,
    # And their geometry as the game draws it (BlockMeshes.java): all but
    # a handful in 2.9; far fewer means the capture broke.
    "layoutMeshes": 300,
    # The connected textures for their blocks (CtmDefinitions.java): 121
    # in 2.9.
    "ctmUsed": 60,
}
# The EBF: 16 coils, the first coil channel tier's (Cupronickel).
KNOWN_STRUCTURE = ("gregtech:gt.blockmachines:1000", "gregtech:gt.blockcasings5:0", 16)
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
LOOKUP_TABLES = ("by_key", "by_key_label", "by_key_material", "fluids_by_key", "aspects_by_key", "by_label", "by_variant", "ui_assets")


def is_webp(data):
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def is_flat(png):
    image = Image.open(BytesIO(png)).convert("RGBA")
    alpha = image.getchannel("A").point(lambda a: 255 if a else 0)
    if not alpha.getbbox():
        return False
    stddev = ImageStat.Stat(image.convert("RGB"), mask=alpha).stddev
    return sum(stddev) / 3 < FLAT_STDDEV


def validate_recipes(data):
    """Validate supported typed recipe contracts before publishing. Empty mandatory
    alternatives and missing intern references must never become free inputs.
    Unsupported/dynamic handlers are expected; adapter failures are not."""
    def require(ok, why):
        if not ok:
            raise ValueError(why)

    require(isinstance(data, dict) and type(data.get("format")) is int and data["format"] in (2, 3, 4),
            "expected recipe format 2, 3 or 4")
    for key in ("items", "fluids"):
        require(isinstance(data.get(key), list) and all(isinstance(k, str) and k for k in data[key]),
                f"invalid {key} intern table")
        names = data.get("item_names" if key == "items" else "fluid_names")
        require(isinstance(names, list) and len(names) == len(data[key]), f"missing {key} display names")
    # NEI's order and item groups for the recipe book (CatalogOrder.java): optional, but whole.
    count = len(data["items"])
    if "item_rank" in data:
        ranks = data["item_rank"]
        require(isinstance(ranks, list) and len(ranks) == count and all(type(r) is int for r in ranks)
                and sorted(ranks) == list(range(count)), "item_rank isn't a position for each item")
    if "item_group" in data or "item_groups" in data:
        groups, of = data.get("item_groups"), data.get("item_group")
        require(isinstance(groups, list) and all(isinstance(g, dict) and isinstance(g.get("name"), str)
                                                 and isinstance(g.get("expanded"), bool) for g in groups),
                "invalid item_groups")
        require(isinstance(of, list) and len(of) == count and all(type(g) is int and -1 <= g < len(groups) for g in of),
                "item_group isn't a group (or -1) for each item")

    def stacks(raw, table, positive=False):
        require(isinstance(raw, list), "stacks must be a list")
        for s in raw:
            require(isinstance(s, list) and len(s) in ((2, 3, 4) if data["format"] >= 4 else (2, 3)), "invalid stack")
            require(type(s[0]) is int and 0 <= s[0] < len(data[table]), "invalid intern reference")
            require(type(s[1]) is int and s[1] >= (1 if positive else 0), "invalid stack amount")
            if len(s) >= 3 and (len(s) != 4 or s[2] is not None):
                require(type(s[2]) is int and 0 < s[2] <= 10000, "invalid output chance")

            if len(s) == 4:
                require(isinstance(s[3], list) and len(s[3]) <= 16 and all(
                    isinstance(ref, str) and ref in data.get("display_stacks", {}) for ref in s[3]),
                    "unknown display stack reference")

    if data["format"] >= 4:
        for field in ("display_stacks", "ui_assets", "research_info", "aspects"):
            require(isinstance(data.get(field), dict), f"missing {field}")
        for value in data["display_stacks"].values():
            require(isinstance(value, dict) and isinstance(value.get("key"), str)
                    and isinstance(value.get("name"), str), "invalid display stack")

    def layout(scene):
        require(isinstance(scene, dict) and isinstance(scene.get("family"), str), "invalid preview layout")
        require(all(type(scene.get(k)) in (int, float) and 0 < scene[k] <= 4096
                    for k in ("width", "height")), "invalid preview dimensions")
        require(isinstance(scene.get("slots"), list) and len(scene["slots"]) <= 256
                and isinstance(scene.get("layers"), list) and len(scene["layers"]) <= 64, "invalid preview contents")
        for slot in scene["slots"]:
            require(isinstance(slot, dict) and slot.get("role") in ("i", "o", "f", "g", "groups", "aux", "extras")
                    and type(slot.get("index")) is int and 0 <= slot["index"] < 256, "invalid preview slot")
        for part in scene["slots"] + scene["layers"]:
            require(all(type(part.get(k)) in (int, float) and -4096 <= part[k] <= 4096
                        for k in ("x", "y", "w", "h")), "invalid preview geometry")
            if part.get("asset") is not None:
                require(part["asset"] in data.get("ui_assets", {}), "unknown preview asset")
        for layer in scene["layers"]:
            uv = layer.get("uv")
            require(isinstance(uv, list) and len(uv) == 4 and all(type(v) in (int, float) and 0 <= v <= 1 for v in uv)
                    and uv[0] < uv[2] and uv[1] < uv[3], "invalid preview texture crop")

    maps = data.get("maps")
    require(isinstance(maps, dict), "missing recipe maps")
    for name, entry in maps.items():
        require(isinstance(entry, dict) and isinstance(entry.get("recipes"), list), "invalid recipe map")
        if entry.get("preview") is not None:
            layout(entry["preview"])
        for r in entry["recipes"]:
            if "preview" in r:
                layout(r["preview"])
            require(isinstance(r, dict), "invalid recipe")
            kind = r.get("type")
            allowed = {"shaped", "shapeless"} if name == "crafting" else {"furnace"} if name == "furnace" else {"gt", "assembly_line", "nei"} if data["format"] >= 3 else {"gt"}
            require(kind in allowed, f"invalid recipe type in {name}")
            for side, table in (("i", "items"), ("o", "items"), ("f", "fluids"), ("g", "fluids")):
                stacks(r.get(side, []), table)
            require(r.get("o") or r.get("g"), "recipe has no output")
            if "approximate" in r:
                require(isinstance(r["approximate"], list) and all(isinstance(f, str) and f for f in r["approximate"]),
                        "invalid approximation flags")
            if kind in ("gt", "assembly_line"):
                require(all(type(r.get(k)) is int for k in ("e", "d")), "GT recipe needs EU/t and duration")
            elif kind == "furnace":
                require(len(r.get("i", [])) == len(r.get("o", [])) == 1 and not r.get("f") and not r.get("g"),
                        "furnace recipe needs one item input and output")
                stacks(r["i"] + r["o"], "items", positive=True)
                if "d" in r:
                    require(type(r["d"]) is int and r["d"] > 0, "invalid furnace duration")
            elif kind in ("shaped", "shapeless"):
                slots = r.get("slots")
                require(isinstance(slots, list) and 1 <= len(slots) <= 9, "invalid crafting slots")
                require(not r.get("i") and not r.get("f") and not r.get("g") and len(r.get("o", [])) == 1,
                        "invalid crafting inputs/output")
                stacks(r["o"], "items", positive=True)
                if kind == "shaped":
                    w, h = r.get("w"), r.get("h")
                    require(type(w) is int and type(h) is int and 1 <= w <= 3 and 1 <= h <= 3
                            and len(slots) == w * h, "invalid crafting dimensions")
                    require(type(r.get("mirrored", False)) is bool, "invalid mirrored flag")
                require(any(s is not None for s in slots), "empty crafting grid")
                for slot in slots:
                    if slot is None and kind == "shaped":
                        continue
                    require(isinstance(slot, dict) and isinstance(slot.get("alts"), list) and slot["alts"],
                            "unresolved crafting ingredient")
                    stacks(slot["alts"], "items", positive=True)
                    if "od" in slot:
                        require(isinstance(slot["od"], str) and slot["od"], "invalid ore name")

            if kind in ("assembly_line", "nei"):
                groups = r.get("groups")
                require(isinstance(groups, list) and (kind != "assembly_line" or groups), "missing processing groups")
                for group in groups:
                    require(isinstance(group, dict) and group.get("kind", "item") in ("item", "fluid"), "invalid processing group")
                    require(group.get("alts"), "unresolved processing group")
                    stacks(group["alts"], "fluids" if group.get("kind") == "fluid" else "items")
            for group in r.get("extras", []):
                require(isinstance(group, dict) and group.get("kind", "item") in ("item", "fluid"), "invalid extra display group")
                stacks(group.get("alts"), "fluids" if group.get("kind") == "fluid" else "items")
            if "aux" in r:
                stacks(r["aux"], "items")
            if "preview_stacks" in r:
                require(isinstance(r["preview_stacks"], dict) and not r["preview_stacks"].keys() - {"i", "o", "f", "g"},
                        "invalid preview stacks")
                for side, values in r["preview_stacks"].items():
                    stacks(values, "fluids" if side in ("f", "g") else "items")
            if "research" in r:
                research = r["research"]
                require(kind == "assembly_line" and isinstance(research, dict)
                        and research.get("method") in ("scanner", "research_station"), "invalid research")
                stacks([research.get("item")], "items", positive=True)
                require(all(type(research.get(k, 0)) is int and research.get(k, 0) >= 0
                            for k in ("ticks", "eut", "computation", "amperage", "computation_per_second")), "invalid research cost")
            for key in ("requirements", "snapshot"):
                if key in r:
                    require(isinstance(r[key], dict), f"invalid {key}")

    coverage = data.get("coverage")
    require(isinstance(coverage, dict) and coverage.get("identity") == "item_id_damage", "missing identity coverage")
    require(isinstance(coverage.get("handlers"), list) and {"gregtech", "crafting", "furnace"}.issubset(coverage["handlers"]), "missing handler coverage")

    def counts(report):
        require(isinstance(report, dict), "missing export counts")
        registered, exported, skipped = report.get("registered"), report.get("exported"), report.get("skipped")
        require(type(registered) is int and registered >= 0 and type(exported) is int and exported >= 0
                and isinstance(skipped, dict), "invalid export counts")
        require(all(type(v) is int and v >= 0 for v in skipped.values()), "invalid skip counts")
        require(registered == exported + sum(skipped.values()), "export counts do not balance")
        require(not skipped.get("adapter_error"), "recipe adapter failed")
        return registered, exported

    for handler in ("crafting", "furnace"):
        require(handler in maps and maps[handler]["recipes"], f"empty {handler} map")
        _, exported = counts(coverage.get(handler))
        require(exported == len(maps[handler]["recipes"]), f"wrong {handler} exported count")
    if data["format"] >= 3:
        _, exported = counts(coverage.get("assembly_line"))
        require(exported > 0 and exported == len(maps.get("assembly_line", {}).get("recipes", [])), "empty or wrong assembly-line count")
        require({"assembly_line", "nei"}.issubset(coverage["handlers"]), "missing extended handler coverage")
        require(isinstance(coverage.get("gt_maps"), dict) and coverage["gt_maps"]
                and isinstance(coverage.get("nei_handlers"), dict) and coverage["nei_handlers"], "missing recipe audit")
        for name, report in coverage["gt_maps"].items():
            saved = [report.get(k, 0) for k in ("exported", "display_exported", "archived")]
            require(all(type(n) is int and n >= 0 for n in saved), "invalid GT map audit count")
            require(report.get("registered", 0) == sum(saved), f"unaccounted GT records in {name}")
            require(saved[0] == len(maps.get(name, {}).get("recipes", []))
                    and saved[1] == len(maps.get("nei.gt." + name, {}).get("recipes", []))
                    and saved[2] == len(maps.get(name, {}).get("archive", [])), f"wrong GT archive counts in {name}")
        for report in coverage["nei_handlers"].values():
            require(report.get("status") in ("native", "display", "not_enumerable", "informational_or_unsupported", "unavailable"), "missing handler status")
            if "registered" in report:
                counts(report)
    classes = coverage["crafting"].get("classes")
    require(isinstance(classes, dict) and classes, "missing crafting class coverage")
    totals = [counts(report) for report in classes.values()]
    require(sum(t[0] for t in totals) == coverage["crafting"]["registered"]
            and sum(t[1] for t in totals) == coverage["crafting"]["exported"], "crafting class counts do not balance")


def check_infusion_icon(data, lookup, image_paths):
    """A failed camera-dependent inventory render used to silently lose this tab icon."""
    infusion = data.get("maps", {}).get("nei.thaumcraft.infusion")
    if infusion is None:
        return []
    key = "Thaumcraft:blockStoneDevice:2"
    if not any(isinstance(m, dict) and m.get("key") == key for m in infusion.get("machines", [])):
        return ["Arcane Infusion is missing its Runic Matrix catalyst identity"]
    path = lookup.get("by_key", {}).get(key)
    if not path or path not in image_paths:
        return ["Arcane Infusion is missing its Runic Matrix icon"]
    return []


def check(out_dir):
    problems = []
    recipe_data = {}
    report = json.loads((out_dir / "export-report.json").read_text())
    try:
        with gzip.open(out_dir / "recipes.json.gz", "rt") as f:
            recipe_data = json.load(f)
            validate_recipes(recipe_data)
            preview_audit = audit(recipe_data)
            (out_dir / "recipe-preview-audit.json").write_text(json.dumps(preview_audit, indent=2) + "\n")
            print(f"Recipe preview audit (advisory): {json.dumps(preview_audit['totals'], sort_keys=True)}")
    except (OSError, ValueError, TypeError, KeyError) as e:
        problems.append(f"recipes.json.gz: {e}")
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
        lookup = json.loads((out_dir / "icons_lookup.json").read_text())
        problems.extend(check_infusion_icon(recipe_data, lookup, set(zf.namelist())))
    if biggest.file_size > MAX_ICON_BYTES:
        problems.append(f"{biggest.filename} is {biggest.file_size} bytes")

    tooltips = json.loads((out_dir / "item_info.json").read_text())["tooltips"]
    if not any(KNOWN_TOOLTIP in text.lower() for text in tooltips["by_key"].values()):
        problems.append(f"no tooltip mentions {KNOWN_TOOLTIP.upper()}")

    with gzip.open(out_dir / "structures.json.gz") as f:
        structures = json.load(f)["multiblocks"]
    controller, block, count = KNOWN_STRUCTURE
    built = structures.get(controller, {}).get("base", {}).get(block)
    if built != count:
        problems.append(f"{controller} has {built} of {block}, expected {count}")
    # Its layout places the same coils (Cupronickel is gt.blockcasings5
    # with meta 0, as its item is).
    with gzip.open(out_dir / "layouts.json.gz") as f:
        layout = json.load(f)["multiblocks"].get(controller, {}).get("base", {})
    placed = len(layout.get(block, []))
    if placed != count:
        problems.append(f"{controller}'s layout places {placed} of {block}, expected {count}")
    if not (out_dir / "layouts.png").is_file():
        problems.append("no layouts.png")

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
    for table in ("by_variant", "ui_assets"):
        missing_paths = set(lookup.get(table, {}).values()) - names
        if missing_paths:
            problems.append(f"{len(missing_paths)} {table} images are missing from images.zip")
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
