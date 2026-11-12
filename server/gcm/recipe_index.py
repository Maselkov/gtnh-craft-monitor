"""One GTNH version's recipes (a game data bundle's
recipes.json.gz, written by tools/icon-export/'s Recipes.java) as an
SQLite file, read in place: a version has 100k+ recipes, and the pattern
check (gcm/recipes.py) only ever looks at the few that make what the
network's patterns make.

gcm/recipes.py builds one per version by running this file as a script,
as gamedata_index.py is built. It streams recipes in two passes, retaining
only intern tables and one recipe rather than the full parsed JSON (which
used ~511 MiB with format 2). Standard library only, so it also runs in the
isolated build subprocess. A host serving several servers the
same bundles (gcm-hosting) can build each one beside its bundle's recipes
instead, as FILE, so they don't each build their own.

A recipe is kept as its JSON object with the file's item and fluid
indices swapped for their keys: {"i": [[item key, n], ...], "f":
[[fluid, mB], ...], "o": [[item key, n(, chance)], ...], "g": [[fluid,
mB], ...], "e": EU/t, "d": ticks, "s": special value}. Crafting has typed
slots with full alternatives and null holes; furnace has item input/output
and optional duration. Coverage and the source fingerprint are in meta. Item keys are
"mod:internal:damage" (damage "*" for any), fluids their registry
name.

Forestry genome items (bees, saplings, butterflies, pollen...) share one
item id across every species; the recipe keeps the bare key, and the index
also files it under "mod:internal:damage#<species>" (species_key()), from
the species in its display stacks' NBT, with that stack's name. A species
key matches only that species; the bare key still matches all of them."""

import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from array import array
from collections import OrderedDict
from contextlib import closing
from typing import NamedTuple
from urllib.parse import quote

# Bumped when the schema or what goes in it changes: an index of another
# format is built again.
FORMAT = 9
# An index built beside a bundle's recipes.json.gz is called this.
FILE = "recipes.sqlite"
ITEM, FLUID = 0, 1
NON_GT_MAPS = frozenset({"crafting", "furnace", "assembly_line"})
# These NEI diagrams show the researched target as a fake output. Treating
# them as production would claim that scanning an item manufactures it.
REFERENCE_ONLY_MAPS = frozenset({"nei.gt.gt.recipe.fakeAssemblylineProcess",
                                "nei.gt.gt.recipe.researchStation", "nei.gt.gt.recipe.scanner"})
PAGE_MAX = 100
# How many sets of open groups an index keeps the shown rows of, for
# browsing without a search: an array of positions, ~64 KB for GTNH 2.9.0
# with its groups shut.
BROWSE_CACHE = 16


class _Catalog(NamedTuple):
    """The whole catalog in order, for browsing (RecipeIndex._catalog):
    each row's kind, key and group (-1 for none); each group's first row,
    size and last row; and (count, loose rows, groups, lowest group)."""
    kinds: bytearray
    keys: list
    grps: array
    first: dict
    size: dict
    last: dict
    stats: tuple

SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE display_stack (id TEXT PRIMARY KEY, body TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE preview_layout (map INTEGER PRIMARY KEY, body TEXT NOT NULL);
-- The item NEI draws on a map's tab (its handler's), as an item key.
CREATE TABLE map_icon (map INTEGER PRIMARY KEY, k TEXT NOT NULL);
CREATE TABLE map (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
-- What NEI lists as the map's machines, in its order: v is {"name", "key"}.
CREATE TABLE machine (map INTEGER NOT NULL, seq INTEGER NOT NULL, v TEXT NOT NULL, PRIMARY KEY (map, seq)) WITHOUT ROWID;
CREATE TABLE recipe (id INTEGER PRIMARY KEY, map INTEGER NOT NULL,
    priority INTEGER NOT NULL, eut INTEGER NOT NULL, ticks INTEGER NOT NULL, body TEXT NOT NULL);
-- What each recipe makes, by kind (ITEM, FLUID) and key.
CREATE TABLE output (kind INTEGER NOT NULL, k TEXT NOT NULL, recipe INTEGER NOT NULL,
    PRIMARY KEY (kind, k, recipe)) WITHOUT ROWID;
-- What each recipe takes, the same way.
CREATE TABLE input (kind INTEGER NOT NULL, k TEXT NOT NULL, recipe INTEGER NOT NULL,
    PRIMARY KEY (kind, k, recipe)) WITHOUT ROWID;
-- Display names, where the export had them; for items, where NEI's sort
-- puts them (rank, 0 first) and their NEI collapsible group (grp), where
-- the export said (CatalogOrder.java).
CREATE TABLE name (kind INTEGER NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL, rank INTEGER, grp INTEGER,
    PRIMARY KEY (kind, k)) WITHOUT ROWID;
-- NEI's collapsible item groups: a name ("" for none), and whether NEI opens it by default.
CREATE TABLE item_group (id INTEGER PRIMARY KEY, name TEXT NOT NULL, expanded INTEGER NOT NULL);
"""


# The active allele of a Forestry genome's species chromosome (chromosome 0),
# as the export writes NBT (NBTTagCompound.toString()).
_SPECIES_SNBT = re.compile(r'Genome:\{Chromosomes:\[0:\{Slot:0b,UID0:"([^"\\]{1,128})"')
SPECIES_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


def species_id(uid):
    """A species allele UID as the species part of a key: a few GT++ UIDs
    have spaces ("gtpp.bee.speciesDragon Blood"), which keys can't."""
    if not isinstance(uid, str):
        return None
    value = re.sub(r"\s", "_", uid.strip())
    return value if SPECIES_RE.fullmatch(value) else None


def species_from_snbt(text):
    """The species id of an exported stack's NBT string, or None."""
    match = _SPECIES_SNBT.search(text) if isinstance(text, str) else None
    return species_id(match[1]) if match else None


def species_key(key, species):
    return f"{key}#{species}"


def split_species(key):
    """(bare key, species or None)."""
    base, sep, species = key.partition("#")
    return (base, species) if sep else (key, None)


def fingerprint(source):
    """What the index has to match: the recipes file's own checksum."""
    with open(source, "rb") as f:
        return _fingerprint_file(f)


def _fingerprint_file(f):
    digest = hashlib.sha256()
    for chunk in iter(lambda: f.read(1 << 20), b""):
        digest.update(chunk)
    return json.dumps({"format": FORMAT, "source": digest.hexdigest()}, sort_keys=True)


def is_current(index_path, source):
    try:
        with closing(sqlite3.connect(f"file:{quote(index_path)}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT v FROM meta WHERE k = 'fingerprint'").fetchone()
        return row is not None and row[0] == fingerprint(source)
    except (OSError, sqlite3.Error):
        return False


def _stacks(raw, table):
    """A recipe's [[index, n(, chance)], ...] with keys for indices; entries
    naming nothing in the table are dropped."""
    out = []
    for entry in raw if isinstance(raw, list) else ():
        if not (isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[0], int)):
            continue
        if 0 <= entry[0] < len(table):
            out.append([table[entry[0]], *entry[1:]])
    return out


def _checked_stacks(raw, table, *, positive=False, variants=False):
    """Format 2 must not silently lose an unknown or malformed ingredient."""
    if not isinstance(raw, list):
        raise ValueError("recipe stacks must be a list")
    for entry in raw:
        if (not isinstance(entry, list) or len(entry) not in ((2, 3, 4) if variants else (2, 3))
                or type(entry[0]) is not int or not 0 <= entry[0] < len(table)
                or type(entry[1]) is not int or entry[1] < (1 if positive else 0)
                or (len(entry) >= 3 and not (len(entry) == 4 and entry[2] is None) and (type(entry[2]) is not int or not 0 < entry[2] <= 10000))
                or (len(entry) == 4 and (not isinstance(entry[3], list) or len(entry[3]) > 16
                                         or not all(isinstance(ref, str) and ref for ref in entry[3])))):
            raise ValueError("invalid recipe stack or intern-table reference")
    return _stacks(raw, table)


def _body(recipe, map_name, items, fluids, source_format):
    kind = recipe.get("type", "gt" if source_format == 1 else None)
    allowed = {"shaped", "shapeless"} if map_name == "crafting" else {"furnace"} if map_name == "furnace" else {"gt", "assembly_line", "nei"} if source_format >= 3 else {"gt"}
    if kind not in allowed:
        raise ValueError(f"invalid recipe type {kind!r} for map {map_name}")
    body = {"type": kind}
    for side, table in (("i", items), ("o", items), ("f", fluids), ("g", fluids)):
        body[side] = (_checked_stacks(recipe.get(side, []), table, variants=source_format >= 4) if source_format >= 2
                      else _stacks(recipe.get(side), table))
    if source_format >= 2 and not (body["o"] or body["g"]):
        raise ValueError("recipe has no output")
    if kind in ("gt", "assembly_line"):
        if source_format >= 2 and not all(type(recipe.get(k)) is int for k in ("e", "d")):
            raise ValueError("GT recipe needs EU/t and duration")
        for k in ("e", "d", "s"):
            value = recipe.get(k) or 0
            if type(value) is not int:
                raise ValueError(f"invalid recipe {k}")
            body[k] = value
    elif kind == "furnace":
        if len(body["i"]) != 1 or len(body["o"]) != 1 or body["f"] or body["g"]:
            raise ValueError("furnace recipe must have one item input and output")
        if any(s[1] <= 0 for s in body["i"] + body["o"]):
            raise ValueError("furnace amounts must be positive")
        if "d" in recipe:
            if type(recipe["d"]) is not int or recipe["d"] <= 0:
                raise ValueError("invalid furnace duration")
            body["d"] = recipe["d"]
    elif kind in ("shaped", "shapeless"):
        slots = recipe.get("slots")
        if not isinstance(slots, list) or not 1 <= len(slots) <= 9:
            raise ValueError("crafting recipe must have 1-9 slots")
        if kind == "shaped":
            w, h = recipe.get("w"), recipe.get("h")
            if type(w) is not int or type(h) is not int or not 1 <= w <= 3 or not 1 <= h <= 3 or w * h != len(slots):
                raise ValueError("invalid shaped crafting dimensions")
            if type(recipe.get("mirrored", False)) is not bool:
                raise ValueError("invalid mirrored flag")
            body.update(w=w, h=h, mirrored=recipe.get("mirrored", False))
        body["slots"] = []
        for slot in slots:
            if slot is None and kind == "shaped":
                body["slots"].append(None)
                continue
            if not isinstance(slot, dict):
                raise ValueError("invalid crafting ingredient")
            alts = _checked_stacks(slot.get("alts"), items, positive=True, variants=source_format >= 4)
            if not alts:
                raise ValueError("unresolved crafting ingredient")
            normalized = {"alts": alts}
            if "od" in slot:
                if not isinstance(slot["od"], str) or not slot["od"]:
                    raise ValueError("invalid ore-dictionary name")
                normalized["od"] = slot["od"]
            body["slots"].append(normalized)
        if not any(body["slots"]) or body["i"] or body["f"] or body["g"] or len(body["o"]) != 1 or body["o"][0][1] <= 0:
            raise ValueError("invalid crafting inputs or output")
    if source_format >= 3:
        if kind in ("assembly_line", "nei"):
            groups = recipe.get("groups")
            if not isinstance(groups, list) or (kind == "assembly_line" and not groups):
                raise ValueError("processing recipe needs ingredient groups")
            body["groups"] = []
            for group in groups:
                if not isinstance(group, dict) or group.get("kind", "item") not in ("item", "fluid"):
                    raise ValueError("invalid ingredient group kind")
                group_kind = group.get("kind", "item")
                alts = _checked_stacks(group.get("alts"), fluids if group_kind == "fluid" else items, variants=source_format >= 4)
                if not alts:
                    raise ValueError("unresolved processing ingredient")
                body["groups"].append({"kind": group_kind, "alts": alts})
        if "research" in recipe:
            research = recipe["research"]
            if kind != "assembly_line" or not isinstance(research, dict) or research.get("method") not in ("scanner", "research_station"):
                raise ValueError("invalid assembly-line research")
            stack = _checked_stacks([research.get("item")], items, positive=True, variants=source_format >= 4)[0]
            body["research"] = {"item": stack, "method": research["method"]}
            for key in ("eut", "ticks", "computation", "amperage", "computation_per_second"):
                if key in research:
                    if type(research[key]) is not int or research[key] < 0:
                        raise ValueError("invalid research requirement")
                    body["research"][key] = research[key]
        if "requirements" in recipe:
            if not isinstance(recipe["requirements"], dict):
                raise ValueError("invalid recipe requirements")
            body["requirements"] = {k: v for k, v in recipe["requirements"].items() if k not in ("nbt_inputs", "nbt_outputs")}
        # NEI's loss-aware raw snapshot stays in the source bundle, not the
        # query index. It can be reinterpreted without launching the pack again.
    if source_format >= 4:
        if "preview_stacks" in recipe:
            overrides = recipe["preview_stacks"]
            if not isinstance(overrides, dict) or overrides.keys() - {"i", "o", "f", "g"}:
                raise ValueError("invalid preview stacks")
            body["preview_stacks"] = {side: _checked_stacks(stacks, fluids if side in ("f", "g") else items, variants=True)
                                      for side, stacks in overrides.items()}
        body["aux"] = _checked_stacks(recipe.get("aux", []), items, variants=True)
        body["extras"] = []
        for group in recipe.get("extras", []):
            if not isinstance(group, dict) or group.get("kind", "item") not in ("item", "fluid"):
                raise ValueError("invalid extra display group")
            group_kind = group.get("kind", "item")
            body["extras"].append({"kind": group_kind, "alts": _checked_stacks(
                group.get("alts"), fluids if group_kind == "fluid" else items, variants=True)})
        for field in ("preview", "preview_indices"):
            if field in recipe:
                if not isinstance(recipe[field], dict):
                    raise ValueError(f"invalid recipe {field}")
                body[field] = recipe[field]
    if "approximate" in recipe:
        flags = recipe["approximate"]
        if not isinstance(flags, list) or not all(isinstance(f, str) and f for f in flags):
            raise ValueError("invalid recipe approximation flags")
        body["approximate"] = flags
    return body


class _JSONStream:
    """Small standard-library JSON reader: decode individual values, not
    the recipes array or a whole map. The JSON decoder still handles strings,
    escapes, numbers and nested bodies. Chunk boundaries can fall anywhere."""

    CHUNK = 1 << 16

    def __init__(self, stream):
        self.stream = stream
        self.buffer = ""
        self.pos = 0
        self.eof = False
        self.decoder = json.JSONDecoder()

    def _more(self):
        chunk = self.stream.read(self.CHUNK)
        self.buffer = self.buffer[self.pos:] + chunk
        self.pos = 0
        self.eof = not chunk

    def peek(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if self.eof:
                return ""
            self._more()

    def expect(self, char):
        if self.peek() != char:
            raise ValueError(f"invalid recipes JSON: expected {char!r}")
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                # A number might end exactly at the chunk edge, or before an
                # incomplete exponent. Read its delimiter before accepting it.
                if end < len(self.buffer) and self.buffer[end] in " \t\r\n,:]}":
                    self.pos = end
                    return value
                if self.eof:
                    if end != len(self.buffer):
                        raise ValueError("invalid recipes JSON after value")
                    self.pos = end
                    return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("invalid or truncated recipes JSON") from None
            self._more()

    def keys(self):
        self.expect("{")
        if self.peek() == "}":
            self.pos += 1
            return
        while True:
            key = self.value()
            if not isinstance(key, str):
                raise ValueError("recipes JSON object keys must be strings")
            self.expect(":")
            yield key  # caller consumes this key's value
            if self.peek() == "}":
                self.pos += 1
                return
            self.expect(",")

    def values(self):
        self.expect("[")
        if self.peek() == "]":
            self.pos += 1
            return
        while True:
            yield self.value()
            if self.peek() == "]":
                self.pos += 1
                return
            self.expect(",")


def _source_events(source_file):
    with gzip.open(source_file, "rt", encoding="utf-8") as f:
        reader = _JSONStream(f)
        for key in reader.keys():
            if key == "display_stacks":
                for variant in reader.keys():
                    yield "variant", variant, reader.value()
                continue
            if key != "maps":
                yield "meta", key, reader.value()
                continue
            for map_name in reader.keys():
                yield "map", map_name, None
                for field in reader.keys():
                    if field == "recipes":
                        for body in reader.values():
                            yield "recipe", map_name, body
                    elif field == "archive":
                        for _ in reader.values():
                            pass
                    elif field == "preview":
                        yield "layout", map_name, reader.value()
                    elif field == "machines":
                        yield "machines", map_name, reader.value()
                    elif field == "icon":
                        yield "icon", map_name, reader.value()
                    else:
                        reader.value()
        if reader.peek():
            raise ValueError("unexpected data after recipes JSON")


def build(source, out_path):
    """Build atomically, retaining intern tables and at most one recipe.
    Two passes allow JSON fields in any order (including maps before items).
    Keep one source descriptor so an atomic bundle replacement cannot mix
    one generation's bodies with another generation's fingerprint."""
    with open(source, "rb") as f:
        signature = _fingerprint_file(f)
        f.seek(0)
        raw, map_names, species = {}, set(), {}
        for event, key, value in _source_events(f):
            if event == "meta":
                raw[key] = value
            elif event == "variant":
                # Small: only genome stacks (a few thousand of 100k+).
                found = species_from_snbt(value.get("nbt")) if isinstance(value, dict) else None
                if found:
                    species[key] = (found, value.get("name"))
            elif event == "map":
                if key in map_names:
                    raise ValueError("duplicate recipe map")
                map_names.add(key)
        if type(raw.get("format")) is not int or raw["format"] not in (1, 2, 3, 4):
            raise ValueError(f"{source} isn't a recipes file this server knows")
        if raw["format"] >= 2 and (not isinstance(raw.get("coverage"), dict)
                                   or not isinstance(raw["coverage"].get("handlers"), list)):
            raise ValueError("typed recipes need coverage metadata")
        f.seek(0)
        _build(f, out_path, raw, {name: i for i, name in enumerate(sorted(map_names))}, signature, species)


def _build(source_file, out_path, raw, map_ids, signature, species=None):
    species = species or {}
    species_names = {}
    items = [str(k) for k in raw.get("items") or []]
    fluids = [str(k) for k in raw.get("fluids") or []]
    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    complete = False
    try:
        conn.executescript(SCHEMA)
        conn.execute("CREATE TEMP TABLE display_ref (id TEXT PRIMARY KEY)")
        recipe_id = 0
        conn.executemany("INSERT INTO map VALUES (?, ?)", ((i, name) for name, i in map_ids.items()))
        for event, map_name, value in _source_events(source_file):
            if event == "variant":
                if not isinstance(value, dict) or not isinstance(value.get("key"), str):
                    raise ValueError("invalid display stack")
                conn.execute("INSERT INTO display_stack VALUES (?, ?)", (map_name, json.dumps(value)))
                continue
            if event == "layout":
                if value is not None:
                    if not isinstance(value, dict):
                        raise ValueError("invalid preview layout")
                    conn.execute("INSERT INTO preview_layout VALUES (?, ?)", (map_ids[map_name], json.dumps(value)))
                continue
            if event == "icon":
                # Display only: an unusable one is left out, not an error.
                if isinstance(value, str) and 0 < len(value) <= 512:
                    conn.execute("INSERT OR REPLACE INTO map_icon VALUES (?, ?)", (map_ids[map_name], value))
                continue
            if event not in ("machines", "recipe"):
                continue
            map_id = map_ids[map_name]
            if event == "machines":
                machines = [m for m in value or [] if isinstance(m, dict) and m.get("name")]
                conn.executemany(
                    "INSERT INTO machine VALUES (?, ?, ?)",
                    ((map_id, seq, json.dumps({"name": m["name"], "key": m.get("key")})) for seq, m in enumerate(machines)),
                )
                continue
            if not isinstance(value, dict):
                if raw["format"] >= 2:
                    raise ValueError("invalid recipe body")
                continue
            body = _body(value, map_name, items, fluids, raw["format"])
            if raw["format"] >= 3 and map_name in REFERENCE_ONLY_MAPS:
                continue
            if raw["format"] >= 4:
                stacks = [s for side in ("i", "o", "f", "g", "aux") for s in body.get(side, [])]
                stacks.extend(s for values in body.get("preview_stacks", {}).values() for s in values)
                stacks.extend(s for field in ("groups", "extras") for group in body.get(field, []) for s in group["alts"])
                stacks.extend(s for slot in body.get("slots", []) if slot for s in slot["alts"])
                if body.get("research"):
                    stacks.append(body["research"]["item"])
                conn.executemany("INSERT OR IGNORE INTO display_ref VALUES (?)",
                                 ((ref,) for stack in stacks if len(stack) == 4 for ref in stack[3]))
            priority = {"shaped": 0, "shapeless": 0, "furnace": 1, "gt": 2, "assembly_line": 2, "nei": 3}[body["type"]]
            conn.execute("INSERT INTO recipe VALUES (?, ?, ?, ?, ?, ?)",
                         (recipe_id, map_id, priority, abs(body.get("e", 0)), body.get("d", 0), json.dumps(body)))
            outputs = {(ITEM, o[0]) for o in body["o"]} | {(FLUID, g[0]) for g in body["g"]}
            outputs.update(_species_keys(body["o"], species, species_names))
            conn.executemany("INSERT INTO output VALUES (?, ?, ?)", ((kind, k, recipe_id) for kind, k in outputs))
            inputs = {(ITEM, i[0]) for i in body["i"]} | {(FLUID, f[0]) for f in body["f"]}
            inputs.update(_species_keys(body["i"], species, species_names))
            for slot in body.get("slots", []):
                if slot:
                    inputs.update((ITEM, alt[0]) for alt in slot["alts"])
                    inputs.update(_species_keys(slot["alts"], species, species_names))
            for group in body.get("groups", []):
                inputs.update((FLUID if group["kind"] == "fluid" else ITEM, alt[0]) for alt in group["alts"])
                if group["kind"] != "fluid":
                    inputs.update(_species_keys(group["alts"], species, species_names))
            if body.get("research"):
                inputs.add((ITEM, body["research"]["item"][0]))
            conn.executemany("INSERT INTO input VALUES (?, ?, ?)", ((kind, k, recipe_id) for kind, k in inputs))
            recipe_id += 1
        if conn.execute("SELECT 1 FROM display_ref r LEFT JOIN display_stack s ON s.id = r.id WHERE s.id IS NULL LIMIT 1").fetchone():
            raise ValueError("unknown display stack reference")
        for kind, keys, names in ((ITEM, items, raw.get("item_names")), (FLUID, fluids, raw.get("fluid_names"))):
            if isinstance(names, list):
                conn.executemany(
                    "INSERT OR IGNORE INTO name VALUES (?, ?, ?, NULL, NULL)",
                    ((kind, k, str(v)) for k, v in zip(keys, names, strict=False) if v),
                )
        conn.executemany("INSERT OR IGNORE INTO name VALUES (?, ?, ?, NULL, NULL)",
                         ((ITEM, k, v) for k, v in species_names.items()))
        _catalog_order(conn, raw, items)
        conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (signature,))
        conn.execute("INSERT INTO meta VALUES ('recipes', ?)", (str(recipe_id),))
        conn.execute("INSERT INTO meta VALUES ('source_format', ?)", (str(raw["format"]),))
        coverage = raw.get("coverage") if raw["format"] >= 2 else None
        if not isinstance(coverage, dict):
            coverage = {"handlers": ["gregtech"], "identity": "item_id_damage",
                        "limitations": ["This bundle contains only GregTech recipes; NBT variants are not indexed"]}
        conn.execute("INSERT INTO meta VALUES ('coverage', ?)", (json.dumps(coverage),))
        for section in ("ui_assets", "research_info", "aspects"):
            conn.execute("INSERT INTO meta VALUES (?, ?)", (section, json.dumps(raw.get(section, {}))))
        conn.commit()
        complete = True
    finally:
        conn.close()
        if not complete:
            os.remove(tmp)
    os.replace(tmp, out_path)


def _catalog_order(conn, raw, items):
    """Each named item's NEI sort position and collapsible group, where
    the export has them (item_rank, item_group and item_groups, aligned
    with items). A species takes its item's.

    As NEI's item list does (ItemListLoader.updateOrdering), a group's
    items all go where its first one sorts, keeping their own order
    among themselves: Chisel's variants of a block group with the block
    itself, and sort far from it on their own."""
    def ints(value, low, high):
        ok = isinstance(value, list) and len(value) == len(items)
        return ok and all(type(x) is int and low <= x < high for x in value)
    ranks = raw.get("item_rank")
    if ints(ranks, 0, 1 << 31):
        conn.executemany("UPDATE name SET rank = ? WHERE kind = ? AND k = ?",
                         ((r, ITEM, k) for k, r in zip(items, ranks, strict=True)))
    groups, described = raw.get("item_group"), raw.get("item_groups")
    if isinstance(described, list) and described and ints(groups, -1, len(described)):
        conn.executemany("INSERT INTO item_group VALUES (?, ?, ?)", (
            (i, str(g.get("name") or "") if isinstance(g, dict) else "",
             int(isinstance(g, dict) and g.get("expanded") is True)) for i, g in enumerate(described)))
        conn.executemany("UPDATE name SET grp = ? WHERE kind = ? AND k = ?",
                         ((g, ITEM, k) for k, g in zip(items, groups, strict=True) if g >= 0))
        placed = conn.execute("SELECT k, rank, grp FROM name WHERE kind = ? AND rank IS NOT NULL", (ITEM,)).fetchall()
        first = {}
        for _, rank, grp in placed:
            if grp is not None:
                first[grp] = min(rank, first.get(grp, rank))
        placed.sort(key=lambda row: (row[1] if row[2] is None else first[row[2]], row[1]))
        conn.executemany("UPDATE name SET rank = ? WHERE kind = ? AND k = ?",
                         ((n, ITEM, k) for n, (k, _, _) in enumerate(placed)))
    conn.execute("""UPDATE name SET (rank, grp) = (SELECT b.rank, b.grp FROM name b
        WHERE b.kind = name.kind AND b.k = substr(name.k, 1, instr(name.k, '#') - 1))
        WHERE kind = ? AND instr(k, '#') > 0""", (ITEM,))


def _species_keys(stacks, species, names):
    """(ITEM, species key) for each genome species among stacks' display
    variants, noting each one's name."""
    out = set()
    for stack in stacks:
        if len(stack) < 4 or "#" in stack[0]:
            continue
        for ref in stack[3]:
            found = species.get(ref)
            if found:
                key = species_key(stack[0], found[0])
                out.add((ITEM, key))
                if found[1] and key not in names:
                    names[key] = str(found[1])
    return out


class RecipeIndex:
    """A built index, read-only, shared by every thread. Page queries decode
    only the requested recipes, including for high-cardinality ingredients."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(f"file:{quote(path)}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self._maps = dict(self._conn.execute("SELECT id, name FROM map"))
        self._map_ids = {name: map_id for map_id, name in self._maps.items()}
        meta = dict(self._conn.execute("SELECT k, v FROM meta"))
        self.source_format = int(meta["source_format"])
        self.fingerprint = meta["fingerprint"]
        self.coverage = json.loads(meta["coverage"])
        self.presentation = {k: json.loads(meta.get(k, "{}")) for k in ("ui_assets", "research_info", "aspects")}
        self._layouts = {self._maps[m]: json.loads(body) for m, body in self._conn.execute("SELECT map, body FROM preview_layout")}
        self._memo = {}
        self._icons = {self._maps[m]: k for m, k in self._conn.execute("SELECT map, k FROM map_icon")}
        self._machines = {}
        for map_id, v in self._conn.execute("SELECT map, v FROM machine ORDER BY map, seq"):
            self._machines.setdefault(self._maps[map_id], []).append(json.loads(v))

    def close(self):
        with self._lock:
            self._conn.close()
            self._conn = None

    def _all(self, sql, args=()):
        with self._lock:
            # Raised rather than nothing found: a check reading a closed
            # index would call every pattern unchecked.
            if self._conn is None:
                raise RuntimeError(f"{self.path} is closed")
            return self._conn.execute(sql, args).fetchall()

    def maps(self):
        return list(self._maps.values())

    def map_icon(self, map_name):
        """The item key NEI draws on the map's tab, or None."""
        return self._icons.get(map_name)

    def memo(self, name, build):
        """build(), once per index: a table derived from its recipes."""
        with self._lock:
            if name in self._memo:
                return self._memo[name]
        value = build()
        with self._lock:
            return self._memo.setdefault(name, value)

    def map_recipes(self, map_name):
        """[(id, body), ...]: every recipe of one map, in index order."""
        map_id = self._map_ids.get(map_name)
        if map_id is None:
            return []
        return [(rid, json.loads(body)) for rid, body in
                self._all("SELECT id, body FROM recipe WHERE map = ? ORDER BY id", (map_id,))]

    def machines(self, map_name):
        """[{"name", "key"}, ...]: what NEI lists as the map's machines."""
        return self._machines.get(map_name, [])

    def layout(self, map_name):
        return self._layouts.get(map_name)

    def display_stacks(self, refs):
        refs = list(set(refs))
        out = {}
        for start in range(0, len(refs), 500):
            batch = refs[start:start + 500]
            rows = self._all(f"SELECT id, body FROM display_stack WHERE id IN ({','.join('?' * len(batch))})", batch)
            out.update((key, json.loads(body)) for key, body in rows)
        return out

    def _where(self, kind, key, map_name):
        if kind not in (ITEM, FLUID) or not isinstance(key, str) or not key:
            raise ValueError("recipe lookup needs an item or fluid key")
        # A concrete damage matches a wildcard ingredient/output as well. A
        # wildcard query means the wildcard identity itself, not every variant.
        keys = [key]
        if kind == ITEM and not key.endswith(":*") and "#" not in key:
            keys.append(key.rpartition(":")[0] + ":*")
        where = f"x.kind = ? AND x.k IN ({','.join('?' for _ in keys)})"
        args = [kind, *keys]
        if map_name is not None:
            where += " AND r.map = ?"
            args.append(self._map_ids.get(map_name, -1))
        return where, args

    def _rows(self, table, kind, key, map_name=None, limit=None, offset=0):
        where, args = self._where(kind, key, map_name)
        # DISTINCT precedes LIMIT: the same recipe can take both a concrete
        # stack and a wildcard, or several alternatives with the same key.
        sql = (f"SELECT DISTINCT r.id, r.map, r.body, r.priority, r.eut, r.ticks "
               f"FROM {table} x JOIN recipe r ON r.id = x.recipe WHERE {where} "
               "ORDER BY r.priority, r.eut, r.ticks, r.id")
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args.extend((limit, offset))
        return [(rid, self._maps[map_id], json.loads(body))
                for rid, map_id, body, *_ in self._all(sql, args)]

    def making(self, kind, key):
        """Every recipe making key, in the same stable order as made_by()."""
        return self._rows("output", kind, key)

    def _counts(self, table, kind, key, map_name=None):
        where, args = self._where(kind, key, map_name)
        rows = self._all(
            f"SELECT r.map, COUNT(DISTINCT r.id) FROM {table} x JOIN recipe r ON r.id = x.recipe "
            f"WHERE {where} GROUP BY r.map ORDER BY r.map", args,
        )
        return {self._maps[map_id]: n for map_id, n in rows}

    def counts_making(self, kind, key):
        return self._counts("output", kind, key)

    def counts_using(self, kind, key):
        return self._counts("input", kind, key)

    def using(self, kind, key):
        """Map names with a recipe taking key, using the same wildcard rules."""
        return sorted(self.counts_using(kind, key))

    def _page(self, table, kind, key, map_name, limit, offset):
        if type(limit) is not int or not 1 <= limit <= PAGE_MAX or type(offset) is not int or offset < 0:
            raise ValueError(f"recipe page needs limit 1-{PAGE_MAX} and a nonnegative offset")
        total = sum(self._counts(table, kind, key, map_name).values())
        return self._rows(table, kind, key, map_name, limit, offset), total

    def made_by(self, kind, key, map_name=None, *, limit=20, offset=0):
        """(rows, total) making key. Crafting first, then furnace, then GT
        by absolute EU/t and ticks, with recipe ID breaking all ties."""
        return self._page("output", kind, key, map_name, limit, offset)

    def used_by(self, kind, key, map_name=None, *, limit=20, offset=0):
        """(rows, total) taking key; all slot alternatives are indexed."""
        return self._page("input", kind, key, map_name, limit, offset)

    def _research_where(self, kind, key, direction, method=None):
        where, args = self._where(kind, key, "assembly_line")
        where += " AND x.kind = 0 AND json_extract(r.body, '$.research.method') IS NOT NULL"
        if direction == "using":
            where += " AND json_extract(r.body, '$.research.item[0]') = x.k"
        elif direction != "making":
            raise ValueError("invalid research direction")
        if method is not None:
            where += " AND json_extract(r.body, '$.research.method') = ?"
            args.append(method)
        return where, args

    def research_counts(self, kind, key, direction):
        """Viewer references to native research; never manufacturing recipes."""
        where, args = self._research_where(kind, key, direction)
        table = "output" if direction == "making" else "input"
        return dict(self._all(
            f"SELECT json_extract(r.body, '$.research.method'), COUNT(DISTINCT r.id) "
            f"FROM {table} x JOIN recipe r ON r.id = x.recipe WHERE {where} GROUP BY 1", args))

    def research_page(self, kind, key, direction, method=None, *, limit=20, offset=0):
        if type(limit) is not int or not 1 <= limit <= PAGE_MAX or type(offset) is not int or offset < 0:
            raise ValueError("invalid research page")
        where, args = self._research_where(kind, key, direction, method)
        table = "output" if direction == "making" else "input"
        rows = self._all(
            f"SELECT DISTINCT r.id, r.body FROM {table} x JOIN recipe r ON r.id = x.recipe "
            f"WHERE {where} ORDER BY r.eut, r.ticks, r.id LIMIT ? OFFSET ?", [*args, limit, offset])
        return [(rid, "assembly_line", json.loads(body)) for rid, body in rows]

    def find_names(self, query, limit=200):
        """[(kind, key, name), ...] for every named item and fluid whose
        name contains query, ignoring case (ASCII), up to limit."""
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return self._all("SELECT kind, k, v FROM name WHERE v LIKE ? ESCAPE '\\' LIMIT ?", (pattern, limit))

    def has_names(self):
        return bool(self._all("SELECT 1 FROM name LIMIT 1"))

    def search_names(self, query, kind=None, *, limit=40, offset=0, groups=None):
        """Paged catalog lookup, independent of network stock. Words are
        ANDed. Items come in NEI's order where the export gave it (after
        exact and prefix name matches), fluids after them, by name.

        Returns (rows, total, shown): rows (kind, key, name). As NEI does,
        an item group's matches collapse into its first match, unless the
        group is open, or every match is in that one group (NEI opens it
        then). shown has, for each row in a group, {"id", "name", "size"
        (its matches), "open", "forced" (open for being the only group
        matched), "back" (its last match's key, collapsed)}, else None.
        groups: {"all": True/False/None (every group open/shut,
        or NEI's defaults), "open": ids, "shut": ids}, those last two
        overriding the first."""
        if kind not in (None, ITEM, FLUID) or not 1 <= limit <= 1024 or not 0 <= offset <= 1000000:
            raise ValueError("Invalid catalog page.")
        terms = query.strip().split()
        if len(query) > 160 or len(terms) > 12:
            raise ValueError("Use a shorter search (up to 160 characters and 12 words).")
        def escaped(value):
            return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses, args = [], []
        if kind is not None:
            clauses.append("kind = ?")
            args.append(kind)
        for term in terms:
            if term.startswith("@") and len(term) > 1:
                clauses.append("substr(k, 1, instr(k, ':')-1) LIKE ? ESCAPE '\\'")
                args.append("%" + escaped(term[1:]) + "%")
            else:
                clauses.append("(v LIKE ? ESCAPE '\\' OR k LIKE ? ESCAPE '\\')")
                args.extend(["%" + escaped(term) + "%"] * 2)
        where = " AND ".join(clauses) or "1"
        order, order_args = "rank IS NULL, rank, v COLLATE NOCASE, kind, k", []
        if terms:
            order = "CASE WHEN v = ? COLLATE NOCASE THEN 0 WHEN v LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END, " + order
            order_args = [query.strip(), escaped(query.strip()) + "%"]
        info = self._item_groups()
        if not info:
            total = self._all(f"SELECT COUNT(*) FROM name WHERE {where}", args)[0][0]
            rows = self._all(f"SELECT kind, k, v FROM name WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                             [*args, *order_args, limit, offset])
            return rows, total, [None] * len(rows)
        catalog = None if terms else self._catalog(kind, where, args, order)
        if catalog:
            count, loose, distinct, only = catalog.stats
        else:
            count, loose, distinct, only = self._all(
                f"SELECT COUNT(*), COUNT(*) - COUNT(grp), COUNT(DISTINCT grp), MIN(grp) FROM name WHERE {where}", args)[0]
        groups = groups or {}
        forced = bool(count and not loose and distinct == 1)
        if forced:
            opened = {only}
        else:
            base = groups.get("all")
            opened = set(info) if base is True else set() if base is False else {i for i, g in info.items() if g[1]}
            opened = (opened | {int(i) for i in groups.get("open", ()) if int(i) in info}) - {int(i) for i in groups.get("shut", ())}
        if catalog:
            return self._browse(catalog, info, opened, forced, limit, offset)
        if opened >= set(info):
            # Nothing collapses: the plain query, and the page's groups counted.
            total = self._all(f"SELECT COUNT(*) FROM name WHERE {where}", args)[0][0]
            page = self._all(f"SELECT kind, k, v, grp FROM name WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                             [*args, *order_args, limit, offset])
            ids = sorted({g for *_, g in page if g is not None})
            sizes = dict(self._all(f"""SELECT grp, COUNT(*) FROM name WHERE {where} AND grp IN
                (SELECT value FROM json_each(?)) GROUP BY grp""", [*args, json.dumps(ids)])) if ids else {}
            return [r[:3] for r in page], total, [None if g is None else {
                "id": g, "name": info[g][0], "size": sizes[g], "open": True, "forced": forced, "back": None}
                for *_, g in page]
        found = self._all(f"""WITH f AS (SELECT kind, k, v, grp, ROW_NUMBER() OVER (ORDER BY {order}) AS pos
                FROM name WHERE {where}),
            g AS (SELECT *, COUNT(*) OVER w AS size, ROW_NUMBER() OVER (w ORDER BY pos) AS nth,
                LAST_VALUE(k) OVER (w ORDER BY pos ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS back
                FROM f WINDOW w AS (PARTITION BY grp))
            SELECT kind, k, v, grp, size, back, COUNT(*) OVER () FROM g
            WHERE grp IS NULL OR nth = 1 OR grp IN (SELECT value FROM json_each(?))
            ORDER BY pos LIMIT ? OFFSET ?""", [*order_args, *args, json.dumps(sorted(opened)), limit, offset])
        total = found[0][6] if found else self._all(
            f"""SELECT COUNT(*) FROM (SELECT grp, ROW_NUMBER() OVER (PARTITION BY grp) AS nth
            FROM name WHERE {where}) WHERE grp IS NULL OR nth = 1 OR grp IN (SELECT value FROM json_each(?))""",
            [*args, json.dumps(sorted(opened))])[0][0]
        rows, shown = [], []
        for kind_, k, v, grp, size, back, _ in found:
            rows.append((kind_, k, v))
            open_ = grp in opened
            shown.append(None if grp is None else {"id": grp, "name": info[grp][0], "size": size, "open": open_,
                                                   "forced": forced, "back": None if open_ or size < 2 else back})
        return rows, total, shown

    def _catalog(self, kind, where, args, order):
        """Browsing without a search: the whole catalog in order (for one
        kind filter), read once, with where each group starts and ends.
        Opening or shutting a group then only filters it (_browse), rather
        than collapsing every name again in SQL (~150 ms for GTNH)."""
        def build():
            rows = self._all(f"SELECT kind, k, grp FROM name WHERE {where} ORDER BY {order}", args)
            kinds, keys, grps = bytearray(len(rows)), [None] * len(rows), array("i", bytes(4 * len(rows)))
            first, size, last = {}, {}, {}
            for i, (kind_, k, grp) in enumerate(rows):
                kinds[i], keys[i], grps[i] = kind_, k, -1 if grp is None else grp
                if grp is not None:
                    first.setdefault(grp, i)
                    size[grp] = size.get(grp, 0) + 1
                    last[grp] = i
            loose = sum(1 for g in grps if g < 0)
            return _Catalog(kinds, keys, grps, first, size, last,
                            (len(rows), loose, len(first), min(first) if first else None))
        return self.memo(("catalog", kind), build)

    def _browse(self, catalog, info, opened, forced, limit, offset):
        """A page of the catalog with these groups open; which rows show is
        kept for the last few sets of open groups (a few dozen KB each)."""
        cache_key = (id(catalog), frozenset(opened))
        with self._lock:
            cache = self._memo.setdefault("browse", OrderedDict())
            visible = cache.get(cache_key)
            if visible is not None:
                cache.move_to_end(cache_key)
        if visible is None:
            first, grps = catalog.first, catalog.grps
            visible = array("i", (i for i, g in enumerate(grps) if g < 0 or g in opened or first[g] == i))
            with self._lock:
                cache[cache_key] = visible
                while len(cache) > BROWSE_CACHE:
                    cache.popitem(last=False)
        page = visible[offset:offset + limit]
        names = {}
        for kind in {catalog.kinds[i] for i in page}:
            names[kind] = self.names(kind, [catalog.keys[i] for i in page if catalog.kinds[i] == kind])
        rows, shown = [], []
        for i in page:
            kind, k, grp = catalog.kinds[i], catalog.keys[i], catalog.grps[i]
            rows.append((kind, k, names[kind].get(k)))
            if grp < 0:
                shown.append(None)
                continue
            open_, size = grp in opened, catalog.size[grp]
            shown.append({"id": grp, "name": info[grp][0], "size": size, "open": open_, "forced": forced,
                          "back": None if open_ or size < 2 else catalog.keys[catalog.last[grp]]})
        return rows, len(visible), shown

    def _item_groups(self):
        """{id: (name, open by default)}; empty for exports without NEI's groups."""
        return self.memo("item_groups", lambda: {
            i: (n, bool(e)) for i, n, e in self._all("SELECT id, name, expanded FROM item_group")})

    def names(self, kind, keys):
        """{key: display name} for those of keys the export named."""
        keys = list(keys)
        if not keys:
            return {}
        rows = self._all(
            f"SELECT k, v FROM name WHERE kind = ? AND k IN ({','.join('?' * len(keys))})", (kind, *keys)
        )
        return dict(rows)


if __name__ == "__main__":
    # How gcm/recipes.py runs it: <recipes.json.gz> <index path>.
    if len(sys.argv) != 3:
        sys.exit("usage: recipe_index.py <recipes.json.gz> <index path>")
    build(sys.argv[1], sys.argv[2])
