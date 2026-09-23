#!/usr/bin/env python3
"""
generate_icons_lookup.py - the whole icon-export pipeline in one command.

Previously a manual, multi-step, undocumented process: run NESQL-Exporter
in-game, run ExportItems.java/ExportFluids.java by hand to get CSVs, then
hand-transform those CSVs into icons_lookup.json's specific three-table
shape - that last step was never preserved as a script anywhere, only as
a paragraph in CONTEXT.md saying it was done manually. This script is a
reconstruction of that transformation, built and verified against real
ground truth rather than guessed:
  - The real Item.java/Fluid.java entity field names, from nesql-exporter's
    own source (github.com/ShadowTheAge/nesql-exporter - see CONTEXT.md's
    "Gotchas" section for why this fork specifically, and what it took to
    build it).
  - nesql-exporter's own persistence.xml, which pins
    hibernate.physical_naming_strategy = CamelCaseToUnderscoresNamingStrategy
    - confirming the ACTUAL SQL column names (modId -> mod_id,
    internalName -> internal_name, itemDamage -> item_damage, etc.),
    not just the Java field names, which is a genuinely different thing
    ExportItems.java's own comment already flags as uncertain without
    checking (Hibernate's naming strategy could have left them as-is
    instead).
  - The real, existing server/data/icons_lookup.json this project already
    ships, whose exact structure (by_key keyed "mod:internal:damage",
    fluids_by_key keyed by bare internal name, by_label keyed by display
    name) this script's output is designed to match.

USAGE:
    python3 generate_icons_lookup.py --nesql-db /path/to/nesql-repository/nesql-db

    (--nesql-db is the path to your nesql-db files WITHOUT any extension -
    same argument ExportItems.java/ExportFluids.java themselves take.
    Find it under .minecraft/nesql/<repo-name>/ after running /nesql
    in-game with NESQL-Exporter installed.)

Requires a JDK (javac + java) on PATH. Everything else - locating or
downloading the hsqldb jar, compiling the exporters, running them, and
building the final JSON - happens automatically. Safe to re-run: nothing
here modifies your nesql-db export (both Java tools connect read-only),
and a cached hsqldb jar is reused rather than re-downloaded.
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HSQLDB_VERSION = "2.7.2"
HSQLDB_JAR_NAME = f"hsqldb-{HSQLDB_VERSION}-jdk8.jar"
HSQLDB_DOWNLOAD_URL = (
    f"https://repo1.maven.org/maven2/org/hsqldb/hsqldb/{HSQLDB_VERSION}/{HSQLDB_JAR_NAME}"
)

TOOLS_DIR = Path(__file__).resolve().parent


def find_or_fetch_hsqldb_jar():
    """Checks a couple of likely locations before trying to download -
    the gradle cache path is worth checking first since anyone who's
    already built nesql-exporter (see CONTEXT.md) already has this jar
    sitting there for free."""
    cached = TOOLS_DIR / HSQLDB_JAR_NAME
    if cached.exists():
        print(f"[icons] Using cached {cached}")
        return cached

    gradle_cache = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1" / "org.hsqldb" / "hsqldb"
    if gradle_cache.exists():
        matches = list(gradle_cache.rglob(HSQLDB_JAR_NAME))
        if matches:
            print(f"[icons] Found {HSQLDB_JAR_NAME} in the gradle cache: {matches[0]}")
            return matches[0]

    print(f"[icons] {HSQLDB_JAR_NAME} not found locally - attempting to download from Maven Central...")
    try:
        urllib.request.urlretrieve(HSQLDB_DOWNLOAD_URL, cached)
        print(f"[icons] Downloaded to {cached} (cached here for next time)")
        return cached
    except Exception as e:
        print(f"[icons] Download failed: {e}")
        print(f"[icons] Grab it manually from:\n  {HSQLDB_DOWNLOAD_URL}")
        print(f"[icons] and place it at:\n  {cached}")
        sys.exit(1)


def check_jdk():
    missing = [tool for tool in ("javac", "java") if shutil.which(tool) is None]
    if missing:
        print(f"[icons] Missing from PATH: {', '.join(missing)} - a JDK is required.")
        print("[icons] Install one (e.g. Temurin/OpenJDK 17+) and make sure javac/java are on PATH.")
        sys.exit(1)


def compile_exporters():
    print("[icons] Compiling ExportItems.java / ExportFluids.java...")
    for name in ("ExportItems.java", "ExportFluids.java"):
        result = subprocess.run(
            ["javac", name], cwd=TOOLS_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[icons] Failed to compile {name}:\n{result.stderr}")
            sys.exit(1)


def run_exporter(class_name, hsqldb_jar, nesql_db_path, out_csv):
    classpath = f".{':' if sys.platform != 'win32' else ';'}{hsqldb_jar}"
    result = subprocess.run(
        ["java", "-cp", classpath, class_name, str(nesql_db_path), str(out_csv)],
        cwd=TOOLS_DIR, capture_output=True, text=True)
    print(result.stdout, end="")
    if result.returncode != 0:
        print(f"[icons] {class_name} failed:\n{result.stderr}")
        sys.exit(1)


# Confirmed real SQL column names - see this file's own docstring for
# where these came from (nesql-exporter's real entity source PLUS its
# persistence.xml's CamelCaseToUnderscoresNamingStrategy setting, not
# assumed from the Java field names alone).
ITEM_MOD_ID = "mod_id"
ITEM_INTERNAL_NAME = "internal_name"
ITEM_DAMAGE = "item_damage"
ITEM_IMAGE_PATH = "image_file_path"
ITEM_LOCALIZED_NAME = "localized_name"
ITEM_NBT = "nbt"

FLUID_MOD_ID = "mod_id"
FLUID_INTERNAL_NAME = "internal_name"
FLUID_IMAGE_PATH = "image_file_path"
FLUID_LOCALIZED_NAME = "localized_name"
FLUID_NBT = "nbt"


def _check_columns(fieldnames, required, csv_path):
    missing = [c for c in required if c not in fieldnames]
    if missing:
        print(f"[icons] {csv_path} is missing expected column(s): {missing}")
        print(f"[icons] Columns actually present: {fieldnames}")
        print("[icons] nesql-exporter's schema may have changed - check")
        print("[icons] Item.java/Fluid.java and persistence.xml against the")
        print("[icons] assumptions documented at the top of this script.")
        sys.exit(1)


def _prefer_no_nbt(existing_row, new_row, nbt_col):
    """True if new_row should replace existing_row (or there is no
    existing_row yet). Multiple rows can genuinely share the same
    mod:internal:damage key (or the same bare fluid internal name) when
    they differ only by NBT - our OC-side data never carries NBT to
    disambiguate by, so the plain, no-NBT variant is the best default
    representative when one exists. If every variant has NBT, later
    rows win (arbitrary but deterministic), same as a plain dict update."""
    if existing_row is None:
        return True
    existing_has_nbt = bool(existing_row.get(nbt_col, "").strip())
    new_has_nbt = bool(new_row.get(nbt_col, "").strip())
    if existing_has_nbt and not new_has_nbt:
        return True
    if not existing_has_nbt and new_has_nbt:
        return False
    return True  # both (or neither) have NBT - last one wins


def build_lookup(items_csv, fluids_csv):
    by_key = {}
    by_key_rows = {}  # key -> the raw row currently backing by_key[key], for collision comparisons
    by_label = {}
    by_label_rows = {}

    with open(items_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        _check_columns(
            reader.fieldnames,
            [ITEM_MOD_ID, ITEM_INTERNAL_NAME, ITEM_DAMAGE, ITEM_IMAGE_PATH, ITEM_LOCALIZED_NAME, ITEM_NBT],
            items_csv)
        item_count = 0
        for row in reader:
            item_count += 1
            key = f"{row[ITEM_MOD_ID]}:{row[ITEM_INTERNAL_NAME]}:{row[ITEM_DAMAGE]}"
            if _prefer_no_nbt(by_key_rows.get(key), row, ITEM_NBT):
                by_key[key] = row[ITEM_IMAGE_PATH]
                by_key_rows[key] = row

            label = row[ITEM_LOCALIZED_NAME]
            if label and _prefer_no_nbt(by_label_rows.get(label), row, ITEM_NBT):
                by_label[label] = row[ITEM_IMAGE_PATH]
                by_label_rows[label] = row

    fluids_by_key = {}
    fluids_by_key_rows = {}
    with open(fluids_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        _check_columns(
            reader.fieldnames,
            [FLUID_MOD_ID, FLUID_INTERNAL_NAME, FLUID_IMAGE_PATH, FLUID_LOCALIZED_NAME, FLUID_NBT],
            fluids_csv)
        fluid_count = 0
        for row in reader:
            fluid_count += 1
            # Bare internal name, deliberately no mod prefix - matches
            # how AE2 itself reports a fluid's identity (see CONTEXT.md's
            # "AE2 item name fields are inconsistently formatted" gotcha:
            # a fluid's OC-side name has no colon/mod prefix at all).
            key = row[FLUID_INTERNAL_NAME]
            if _prefer_no_nbt(fluids_by_key_rows.get(key), row, FLUID_NBT):
                fluids_by_key[key] = row[FLUID_IMAGE_PATH]
                fluids_by_key_rows[key] = row

            label = row[FLUID_LOCALIZED_NAME]
            if label and _prefer_no_nbt(by_label_rows.get(label), row, FLUID_NBT):
                by_label[label] = row[FLUID_IMAGE_PATH]
                by_label_rows[label] = row

    print(f"[icons] {item_count} item rows -> {len(by_key)} by_key entries, {len(by_label)} by_label entries so far")
    print(f"[icons] {fluid_count} fluid rows -> {len(fluids_by_key)} fluids_by_key entries")

    return {
        "by_key": by_key,
        "fluids_by_key": fluids_by_key,
        "by_label": by_label,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--nesql-db", required=True,
        help="Path to your nesql-db files, WITHOUT any extension "
             "(e.g. .../nesql-repository/nesql-db)")
    parser.add_argument(
        "--output", default=str(TOOLS_DIR.parent / "server" / "data" / "icons_lookup.json"),
        help="Where to write icons_lookup.json (default: server/data/icons_lookup.json)")
    parser.add_argument(
        "--items-csv", default=None,
        help="Skip re-running the Java exporter and use an existing items CSV instead")
    parser.add_argument(
        "--fluids-csv", default=None,
        help="Skip re-running the Java exporter and use an existing fluids CSV instead")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="icons_export_") as tmpdir:
        items_csv = Path(args.items_csv) if args.items_csv else Path(tmpdir) / "items.csv"
        fluids_csv = Path(args.fluids_csv) if args.fluids_csv else Path(tmpdir) / "fluids.csv"

        if not args.items_csv or not args.fluids_csv:
            check_jdk()
            hsqldb_jar = find_or_fetch_hsqldb_jar()
            compile_exporters()

        if not args.items_csv:
            print("[icons] Exporting items...")
            run_exporter("ExportItems", hsqldb_jar, args.nesql_db, items_csv)
        if not args.fluids_csv:
            print("[icons] Exporting fluids...")
            run_exporter("ExportFluids", hsqldb_jar, args.nesql_db, fluids_csv)

        print("[icons] Building icons_lookup.json...")
        lookup = build_lookup(items_csv, fluids_csv)

        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(lookup, f)

        print(f"[icons] Done. Wrote {out_path}")
        print(f"[icons]   by_key: {len(lookup['by_key'])}")
        print(f"[icons]   fluids_by_key: {len(lookup['fluids_by_key'])}")
        print(f"[icons]   by_label: {len(lookup['by_label'])}")
        print("[icons] Remember: image.zip (the actual rendered icons) still needs to")
        print("[icons] be copied to server/data/images.zip separately - this script only")
        print("[icons] builds the lookup table, not the image archive itself.")


if __name__ == "__main__":
    main()
