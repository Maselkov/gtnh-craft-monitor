"""One GTNH version's GregTech recipes (a game data bundle's
recipes.json.gz, written by tools/icon-export/'s Recipes.java) as an
SQLite file, read in place: a version has 100k+ recipes, and the pattern
check (gcm/recipes.py) only ever looks at the few that make what the
network's patterns make.

gcm/recipes.py builds one per version by running this file as a script,
as gamedata_index.py is built, so the memory parsing the JSON takes
(~200 MB for GTNH 2.9) goes back to the OS when it exits. Standard
library only, for the same reason. A host serving several servers the
same bundles (gcm-hosting) can build each one beside its bundle's recipes
instead, as FILE, so they don't each build their own.

A recipe is kept as its JSON object with the file's item and fluid
indices swapped for their keys: {"i": [[item key, n], ...], "f":
[[fluid, mB], ...], "o": [[item key, n(, chance)], ...], "g": [[fluid,
mB], ...], "e": EU/t, "d": ticks, "s": special value}. Item keys are
"mod:internal:damage" (damage "*" for any), fluids their registry
name."""

import gzip
import hashlib
import json
import os
import sqlite3
import sys
import threading
from contextlib import closing
from urllib.parse import quote

# Bumped when the schema or what goes in it changes: an index of another
# format is built again.
FORMAT = 2
# An index built beside a bundle's recipes.json.gz is called this.
FILE = "recipes.sqlite"
ITEM, FLUID = 0, 1

SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE map (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
-- What NEI lists as the map's machines, in its order: v is {"name", "key"}.
CREATE TABLE machine (map INTEGER NOT NULL, seq INTEGER NOT NULL, v TEXT NOT NULL, PRIMARY KEY (map, seq)) WITHOUT ROWID;
CREATE TABLE recipe (id INTEGER PRIMARY KEY, map INTEGER NOT NULL, body TEXT NOT NULL);
-- What each recipe makes, by kind (ITEM, FLUID) and key.
CREATE TABLE output (kind INTEGER NOT NULL, k TEXT NOT NULL, recipe INTEGER NOT NULL,
    PRIMARY KEY (kind, k, recipe)) WITHOUT ROWID;
-- What each recipe takes, the same way.
CREATE TABLE input (kind INTEGER NOT NULL, k TEXT NOT NULL, recipe INTEGER NOT NULL,
    PRIMARY KEY (kind, k, recipe)) WITHOUT ROWID;
-- Display names, where the export had them.
CREATE TABLE name (kind INTEGER NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL, PRIMARY KEY (kind, k)) WITHOUT ROWID;
"""


def fingerprint(source):
    """What the index has to match: the recipes file's own checksum."""
    digest = hashlib.sha256()
    with open(source, "rb") as f:
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


def build(source, out_path):
    """Builds the index of the recipes file `source` at out_path, through a
    temporary file so a reader never sees half of one."""
    with gzip.open(source, "rt", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict) or raw.get("format") != 1:
        raise ValueError(f"{source} isn't a recipes file this server knows")
    items = [str(k) for k in raw.get("items") or []]
    fluids = [str(k) for k in raw.get("fluids") or []]
    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        recipe_id = 0
        for map_id, (map_name, entry) in enumerate(sorted((raw.get("maps") or {}).items())):
            conn.execute("INSERT INTO map VALUES (?, ?)", (map_id, map_name))
            machines = [m for m in entry.get("machines") or [] if isinstance(m, dict) and m.get("name")]
            conn.executemany(
                "INSERT INTO machine VALUES (?, ?, ?)",
                ((map_id, seq, json.dumps({"name": m["name"], "key": m.get("key")})) for seq, m in enumerate(machines)),
            )
            for recipe in entry.get("recipes") or []:
                if not isinstance(recipe, dict):
                    continue
                body = {
                    "i": _stacks(recipe.get("i"), items),
                    "f": _stacks(recipe.get("f"), fluids),
                    "o": _stacks(recipe.get("o"), items),
                    "g": _stacks(recipe.get("g"), fluids),
                    "e": recipe.get("e") or 0,
                    "d": recipe.get("d") or 0,
                    "s": recipe.get("s") or 0,
                }
                conn.execute("INSERT INTO recipe VALUES (?, ?, ?)", (recipe_id, map_id, json.dumps(body)))
                outputs = {(ITEM, o[0]) for o in body["o"]} | {(FLUID, g[0]) for g in body["g"]}
                conn.executemany("INSERT INTO output VALUES (?, ?, ?)", ((kind, k, recipe_id) for kind, k in outputs))
                inputs = {(ITEM, i[0]) for i in body["i"]} | {(FLUID, f[0]) for f in body["f"]}
                conn.executemany("INSERT INTO input VALUES (?, ?, ?)", ((kind, k, recipe_id) for kind, k in inputs))
                recipe_id += 1
        for kind, keys, names in ((ITEM, items, raw.get("item_names")), (FLUID, fluids, raw.get("fluid_names"))):
            if isinstance(names, list):
                conn.executemany(
                    "INSERT OR IGNORE INTO name VALUES (?, ?, ?)",
                    ((kind, k, str(v)) for k, v in zip(keys, names, strict=False) if v),
                )
        conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint(source),))
        conn.execute("INSERT INTO meta VALUES ('recipes', ?)", (str(recipe_id),))
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, out_path)


class RecipeIndex:
    """A built index, read-only, shared by every thread (each query takes
    microseconds)."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(f"file:{quote(path)}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self._maps = dict(self._conn.execute("SELECT id, name FROM map"))
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

    def machines(self, map_name):
        """[{"name", "key"}, ...]: what NEI lists as the map's machines."""
        return self._machines.get(map_name, [])

    def making(self, kind, key):
        """[(recipe id, map name, body), ...] for every recipe making key
        (an ITEM key or a FLUID name)."""
        rows = self._all(
            "SELECT r.id, r.map, r.body FROM output o JOIN recipe r ON r.id = o.recipe WHERE o.kind = ? AND o.k = ?",
            (kind, key),
        )
        return [(rid, self._maps[map_id], json.loads(body)) for rid, map_id, body in rows]

    def using(self, kind, key):
        """The names of the maps with a recipe taking key, an ITEM key or a
        FLUID name."""
        rows = self._all(
            "SELECT DISTINCT r.map FROM input i JOIN recipe r ON r.id = i.recipe WHERE i.kind = ? AND i.k = ?",
            (kind, key),
        )
        return sorted(self._maps[map_id] for (map_id,) in rows)

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
