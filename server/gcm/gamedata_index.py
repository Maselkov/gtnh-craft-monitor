"""A game data bundle's lookups as one SQLite file, index.sqlite, read in
place instead of loaded into memory: the icon lookup, where each icon is
in each images*.zip, and the ore dictionary. As Python dicts those took
~175 MB in every server (zipfile's index of a 131k-icon zip alone ~90
MB); read from the file they take next to nothing, and servers on one
host reading a shared bundle (GAMEDATA_SHARED_DIR) share one copy of it
in the OS's page cache.

gcm/gamedata.py builds it next to a bundle, or in the server's own
directory for a shared bundle it can't write to, by running this file as
a script - so the memory building takes goes back to the OS when it
exits. Standard library only, for the same reason: running it doesn't
import the rest of gcm. A host can build it into a shared bundle once
(it takes a couple of seconds), so its servers don't each build one:

    python app.py index-gamedata <bundle directory>

Readers: icons.py (Index.icon(), Index.zip_entry()) and oredict.py."""

import hashlib
import json
import os
import sqlite3
import sys
import threading
import zipfile
from contextlib import closing
from urllib.parse import quote

FILE = "index.sqlite"
# Bumped when the schema or what goes in it changes: an index of another
# format is built again.
FORMAT = 1
# The icon lookup's tables (icons_lookup.json's keys), numbered for the
# icon table's ns column.
ICON_TABLES = ("by_key", "by_key_label", "by_key_material", "by_label", "fluids_by_key", "aspects_by_key", "bleed")

SCHEMA = """
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE icon (ns INTEGER NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL, PRIMARY KEY (ns, k)) WITHOUT ROWID;
CREATE TABLE zip_entry (
    zip TEXT NOT NULL, name TEXT NOT NULL,
    offset INTEGER NOT NULL, size INTEGER NOT NULL, method INTEGER NOT NULL, crc INTEGER NOT NULL,
    PRIMARY KEY (zip, name)
) WITHOUT ROWID;
-- One row per (ore name, member), seq keeping the file's order of names
-- and of each name's members. damage NULL: every damage ("*").
CREATE TABLE ore (seq INTEGER PRIMARY KEY, name TEXT NOT NULL, mod TEXT NOT NULL, internal TEXT NOT NULL, damage INTEGER);
CREATE INDEX ore_item ON ore (mod, internal, damage);
CREATE INDEX ore_name ON ore (name, seq);
"""


def _zip_names(bundle_dir):
    return sorted(n for n in os.listdir(bundle_dir) if n.startswith("images") and n.endswith(".zip"))


def fingerprint(bundle_dir):
    """What the index has to match: the bundle's data.json and the size of
    each icon zip (a texture set downloaded later adds one)."""
    with open(os.path.join(bundle_dir, "data.json"), "rb") as f:
        data = hashlib.sha256(f.read()).hexdigest()
    zips = {n: os.path.getsize(os.path.join(bundle_dir, n)) for n in _zip_names(bundle_dir)}
    return json.dumps({"format": FORMAT, "data": data, "zips": zips}, sort_keys=True)


def is_current(index_path, bundle_dir):
    try:
        with closing(sqlite3.connect(f"file:{quote(index_path)}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT v FROM meta WHERE k = 'fingerprint'").fetchone()
        return row is not None and row[0] == fingerprint(bundle_dir)
    except (OSError, sqlite3.Error):
        return False


def _ore_rows(path):
    """(name, mod, internal, damage) for each member in ore_dict.json, in
    its order. Entries are "mod:internal:damage", damage "*" for any."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return
    for name, entries in raw.items() if isinstance(raw, dict) else ():
        for entry in entries if isinstance(entries, list) else ():
            mod, sep, rest = str(entry).partition(":")
            internal, sep2, damage = rest.rpartition(":")
            if not (sep and sep2 and mod and internal):
                continue
            if damage == "*":
                yield name, mod, internal, None
            elif damage.lstrip("-").isdigit():
                yield name, mod, internal, int(damage)


def build(bundle_dir, out_path):
    """Writes bundle_dir's index to out_path, replacing it in one step."""
    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        lookup_path = os.path.join(bundle_dir, "icons_lookup.json")
        if os.path.exists(lookup_path):
            with open(lookup_path, encoding="utf-8") as f:
                lookup = json.load(f)
            for ns, table in enumerate(ICON_TABLES):
                rows = lookup.get(table) or {}
                conn.executemany("INSERT INTO icon VALUES (?, ?, ?)", ((ns, k, str(v)) for k, v in rows.items()))
            del lookup
        for zip_name in _zip_names(bundle_dir):
            with zipfile.ZipFile(os.path.join(bundle_dir, zip_name)) as zf:
                conn.executemany(
                    "INSERT OR REPLACE INTO zip_entry VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        (zip_name, i.filename, i.header_offset, i.compress_size, i.compress_type, i.CRC)
                        for i in zf.infolist()
                    ),
                )
        conn.executemany(
            "INSERT INTO ore (name, mod, internal, damage) VALUES (?, ?, ?, ?)",
            _ore_rows(os.path.join(bundle_dir, "ore_dict.json")),
        )
        conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)", (fingerprint(bundle_dir),))
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, out_path)


class Index:
    """A built index, read-only. One connection, shared by every thread:
    each query takes microseconds. Memory-mapped, so its pages are the
    OS's file cache, shared with any other server reading the same file."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            f"file:{quote(path)}?mode=ro&immutable=1", uri=True, check_same_thread=False
        )
        self._conn.execute("PRAGMA mmap_size = 1073741824")
        self._conn.execute("PRAGMA cache_size = -256")

    def close(self):
        """Closes it. A thread still holding it (a request that started
        before another bundle went live) then finds nothing, rather than
        an error."""
        with self._lock:
            self._conn.close()
            self._conn = None

    def one(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).fetchone() if self._conn else None

    def all(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).fetchall() if self._conn else []

    def icon(self, table, key):
        row = self.one("SELECT v FROM icon WHERE ns = ? AND k = ?", (ICON_TABLES.index(table), key))
        return row[0] if row else None

    def has_ore_dict(self):
        return self.one("SELECT 1 FROM ore LIMIT 1") is not None

    def ore_names(self, mod, internal, damage):
        """An item's ore names for that damage, then for every damage of
        it, in ore dictionary order (repeats left in)."""
        exact = self.all(
            "SELECT name FROM ore WHERE mod = ? AND internal = ? AND damage = ? ORDER BY seq", (mod, internal, damage)
        )
        any_damage = self.all(
            "SELECT name FROM ore WHERE mod = ? AND internal = ? AND damage IS NULL ORDER BY seq", (mod, internal)
        )
        return [r[0] for r in exact + any_damage]

    def ore_members(self, name):
        """(mod, internal, damage or None) of each item under an ore name."""
        return self.all("SELECT mod, internal, damage FROM ore WHERE name = ? ORDER BY seq", (name,))

    def zip_entry(self, zip_name, name):
        """(offset, size, method, crc) of name in zip_name, or None."""
        return self.one("SELECT offset, size, method, crc FROM zip_entry WHERE zip = ? AND name = ?", (zip_name, name))


if __name__ == "__main__":
    # How gcm/gamedata.py runs it: <bundle directory> <index path>.
    if len(sys.argv) != 3:
        raise SystemExit("usage: gamedata_index.py <bundle directory> <index path>")
    build(sys.argv[1], sys.argv[2])
