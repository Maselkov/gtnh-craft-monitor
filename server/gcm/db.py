"""SQLite connections and schemas. Three files, split by kind of data:
app.db holds everything relational (users, credentials, pins,
completions, craft events, request audit), linked by foreign keys;
power.db and item_history.db hold bulk time series. Each connection is
opened fresh per call - sqlite3 connections aren't safe to share across
the server's threads, and at this traffic level the cost is irrelevant.

Every file runs in WAL mode, so readers never wait on a writer (a
network scan's snapshot rewrite, say). That also means a plain file
copy of a live database can miss recent commits - use backup_all().

Schemas are versioned; see "Schema migrations" at the bottom."""

import os
import sqlite3
from contextlib import contextmanager

from gcm import config


@contextmanager
def transaction(open_db):
    """`with db.transaction(db.app_db) as conn:` - one connection,
    committed if the block finishes, rolled back if it raises, and
    closed either way."""
    conn = open_db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _connect(path):
    conn = sqlite3.connect(path, timeout=10)
    # In WAL mode this can't corrupt the file; a power cut can only lose
    # the last few commits. It saves an fsync on every write.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def app_db():
    conn = _connect(config.APP_DB_PATH)
    # Off by default in SQLite, per connection - without it the
    # REFERENCES clauses below are never checked.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# A database file's companions: its rollback journal, or its WAL and
# shared-memory index.
_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


def adopt_legacy_app_db():
    """Renames craft_history.db - app.db's name from before it held more
    than craft history - to app.db, along with any journal/WAL files
    that belong to it. Refuses to guess when both exist."""
    legacy, current = config.LEGACY_CRAFT_DB_PATH, config.APP_DB_PATH
    if not os.path.exists(legacy):
        return
    if os.path.exists(current):
        raise RuntimeError(
            f"Both {legacy} and {current} exist. {current} replaced {legacy}; "
            "move whichever one is out of date out of the data directory."
        )
    for suffix in _SIDECAR_SUFFIXES:
        if os.path.exists(legacy + suffix):
            os.replace(legacy + suffix, current + suffix)
    os.replace(legacy, current)


def _app_baseline(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
            created_at REAL NOT NULL,
            disabled_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS access_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            secret_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            last_used_at REAL,
            revoked_at REAL,
            is_bootstrap INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_access_tokens_user ON access_tokens (user_id)"
    )
    token_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(access_tokens)")
    }
    if "is_bootstrap" not in token_columns:
        conn.execute(
            "ALTER TABLE access_tokens ADD COLUMN is_bootstrap INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            token_hash TEXT NOT NULL UNIQUE,
            access_token_id TEXT,
            must_rotate_bootstrap INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked_at REAL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)"
    )
    session_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(sessions)")
    }
    if "access_token_id" not in session_columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN access_token_id TEXT")
    if "must_rotate_bootstrap" not in session_columns:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN must_rotate_bootstrap INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS craft_request_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            label TEXT NOT NULL,
            mod TEXT,
            internal TEXT NOT NULL,
            damage INTEGER,
            amount REAL NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            cpu_name TEXT,
            created_at REAL NOT NULL,
            resolved_at REAL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_craft_request_history_user "
        "ON craft_request_history (user_id, created_at DESC)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS craft_cancel_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            cpu_name TEXT NOT NULL,
            status TEXT NOT NULL,
            success INTEGER,
            reason TEXT,
            created_at REAL NOT NULL,
            resolved_at REAL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_craft_cancel_history_user "
        "ON craft_cancel_history (user_id, created_at DESC)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS craft_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cpu_name TEXT NOT NULL,
            item_label TEXT,
            item_icon TEXT,
            status TEXT NOT NULL,
            progress_at_end INTEGER,
            occurred_at REAL NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_craft_events_cpu ON craft_events (cpu_name)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_pins (
            user_id TEXT NOT NULL,
            cpu_name TEXT NOT NULL,
            pinned_at REAL NOT NULL,
            PRIMARY KEY (user_id, cpu_name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            craft_event_id INTEGER NOT NULL REFERENCES craft_events(id),
            created_at REAL NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_completions_user ON user_completions (user_id)"
    )
    # Leftover from the retired craft_keys.txt sync; request access is
    # now decided by account roles.
    conn.execute("DROP TABLE IF EXISTS craft_keys")
    # Item pins (Network tab "favorite this item" feature) - a
    # genuinely different concept from user_pins above (which tracks
    # CPUs being watched for craft completion), so kept as its own
    # table rather than overloading that one. item_key reuses the
    # exact same mod|internal|damage|kind format store.items.item_key()
    # builds for item history, rather than a composite primary key
    # over individually-nullable columns (mod and damage can both be
    # NULL for a fluid - standard SQL NULL semantics treat NULL as
    # never equal to itself even in a primary key, so a plain
    # composite key over nullable columns wouldn't reliably prevent
    # duplicate rows for the same fluid).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_item_pins (
            user_id TEXT NOT NULL,
            item_key TEXT NOT NULL,
            mod TEXT,
            internal TEXT NOT NULL,
            damage INTEGER,
            kind TEXT NOT NULL,
            pinned_at REAL NOT NULL,
            PRIMARY KEY (user_id, item_key)
        )
    """)


def power_db():
    # A fresh connection per call rather than one shared connection -
    # sqlite3 connections aren't safe to share across Flask's threads,
    # and at this traffic level (one insert a minute, occasional reads)
    # the cost of opening a new one each time is irrelevant.
    return _connect(config.POWER_DB_PATH)


def _power_baseline(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS power_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            stored REAL NOT NULL,
            capacity REAL NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_power_readings_ts ON power_readings (ts)"
    )
    # Migration for existing installs - these columns didn't exist
    # before this feature (avg EU in/out over a few windows, plus a
    # time-to-empty estimate - all confirmed available for free from
    # the SAME getSensorInformation() call power_monitor.lua already
    # makes, from a real captured dump off a Lapotronic Super
    # Capacitor). SQLite has no "ADD COLUMN IF NOT EXISTS", so check
    # the existing column list first rather than trying the ALTER
    # and swallowing a "duplicate column" error - more explicit
    # about what's actually happening, and safe to run on every
    # startup either way (only adds what's genuinely missing).
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(power_readings)")
    }
    new_cols = [
        ("avg_eu_in_5s", "REAL"),
        ("avg_eu_out_5s", "REAL"),
        ("avg_eu_in_5m", "REAL"),
        ("avg_eu_out_5m", "REAL"),
        ("avg_eu_in_1h", "REAL"),
        ("avg_eu_out_1h", "REAL"),
        ("time_to_empty_minutes", "REAL"),
    ]
    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            conn.execute(
                f"ALTER TABLE power_readings ADD COLUMN {col_name} {col_type}"
            )


def _power_machines(conn):
    # Several machines (power_monitor.lua's MACHINES), each reading
    # tagged with its name. '' is the one reported without a name, which
    # every reading from before this was.
    conn.execute("ALTER TABLE power_readings ADD COLUMN machine TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX idx_power_readings_machine_ts ON power_readings (machine, ts)")


# 2026-01-01: before this project's earliest data (its history predates
# the repository by a few days), so no real reading is older.
POWER_EARLIEST_REAL_TS = 1767225600


def _power_drop_impossible_times(conn):
    # Readings posted with a bogus "timestamp" before the server checked
    # it (a game clock, a test value): years before this project existed
    # or more than a day ahead. One stretched the Lifetime chart back
    # nine years.
    conn.execute(
        "DELETE FROM power_readings WHERE ts < ? OR ts > strftime('%s', 'now') + 86400",
        (POWER_EARLIEST_REAL_TS,),
    )


# ---------------------------------------------------------------------
# Item/fluid quantity history (SQLite-backed, own db file - separate
# from power.db and app.db, matching the established "one db
# per major concern" pattern rather than growing an existing file with
# an unrelated table).
#
# Change-only storage, NOT one row per scan: with ~6,000+ items scanned
# every 20 minutes, storing every scan unconditionally would mean ~470k
# rows/day - several GB/year and a table that keeps getting slower to
# query. Item quantity is fundamentally a step function (constant, then
# jumps), not a continuously-sampled signal like power draw, so a row is
# only written when a value actually CHANGES from what was last
# recorded for that item - most items (stockpiled materials, anything
# not currently being produced/consumed) simply don't change between
# most scans, which is what keeps this genuinely small in practice.
# (store/items.py writes it.)
def item_history_db():
    return _connect(config.ITEM_HISTORY_DB_PATH)


def _item_history_baseline(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS item_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_key TEXT NOT NULL,
            label TEXT,
            size REAL NOT NULL,
            recorded_at REAL NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_history_key_time ON item_history (item_key, recorded_at)"
    )
    # A true MIRROR of the live network snapshot (unlike item_history
    # above, which is change-only) - wiped and fully rewritten on
    # every scan/finish, so it always reflects exactly what the last
    # completed scan saw. Purpose: surviving a SERVER restart without
    # the Network tab sitting empty until network_browser.lua's next
    # scan completes (which could be most of 20 minutes away). Loaded
    # back into memory once at server startup - see
    # load_snapshot() in gcm/inventory.py.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS network_snapshot (
            item_key TEXT PRIMARY KEY,
            mod TEXT,
            internal TEXT NOT NULL,
            damage INTEGER,
            kind TEXT NOT NULL,
            name TEXT,
            size REAL NOT NULL,
            is_craftable INTEGER NOT NULL,
            updated_at REAL NOT NULL
        )
    """)


# ---------------------------------------------------------------------
# Schema migrations. A database's PRAGMA user_version is how many steps
# of its list below it has had applied; startup applies the rest in
# order, each in one transaction together with its version bump, so a
# failing step leaves the file at the last version that worked.
#
# Step 1 of each list is the baseline: the schema as it stood before
# versioning, written to bring ANY earlier install up to date (CREATE
# ... IF NOT EXISTS, columns added only when missing), because files
# from before versioning all report version 0 whatever state they're
# in. Every later step runs exactly once, so it can be a plain change.
# Never edit a step that has shipped - append a new one.
def _app_drop_orphaned_user_rows(conn):
    # Deleting a user used to leave their pins and pending completions
    # behind, and those pins kept producing completions nobody could read.
    for table in ("user_pins", "user_completions", "user_item_pins", "access_tokens", "sessions"):
        conn.execute(f"DELETE FROM {table} WHERE user_id NOT IN (SELECT id FROM users)")


# The tables whose rows exist only for their user, rebuilt below with
# user_id ... ON DELETE CASCADE: deleting the users row removes them.
# The request/cancel audit tables deliberately have no key - their rows
# outlive the user.
_USER_OWNED_SCHEMAS = {
    "user_pins": """
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        cpu_name TEXT NOT NULL,
        pinned_at REAL NOT NULL,
        PRIMARY KEY (user_id, cpu_name)
    """,
    "user_completions": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        craft_event_id INTEGER NOT NULL REFERENCES craft_events(id),
        created_at REAL NOT NULL
    """,
    "user_item_pins": """
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        item_key TEXT NOT NULL,
        mod TEXT,
        internal TEXT NOT NULL,
        damage INTEGER,
        kind TEXT NOT NULL,
        pinned_at REAL NOT NULL,
        PRIMARY KEY (user_id, item_key)
    """,
    "access_tokens": """
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        secret_hash TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_used_at REAL,
        revoked_at REAL,
        is_bootstrap INTEGER NOT NULL DEFAULT 0
    """,
    "sessions": """
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        access_token_id TEXT,
        must_rotate_bootstrap INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        revoked_at REAL
    """,
}


def _app_cascade_user_rows(conn):
    # SQLite can't add a foreign key to an existing table, so each one is
    # rebuilt. Safe with foreign_keys on: none of them is another table's
    # parent, so dropping the old copy checks nothing.
    for table, columns in _USER_OWNED_SCHEMAS.items():
        conn.execute(f"DELETE FROM {table} WHERE user_id NOT IN (SELECT id FROM users)")
        names = ", ".join(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
        conn.execute(f"CREATE TABLE {table}_new ({columns})")
        conn.execute(f"INSERT INTO {table}_new ({names}) SELECT {names} FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    # Indexes went with the old tables.
    conn.execute("CREATE INDEX idx_access_tokens_user ON access_tokens (user_id)")
    conn.execute("CREATE INDEX idx_sessions_user ON sessions (user_id)")
    conn.execute("CREATE INDEX idx_user_completions_user ON user_completions (user_id)")
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(f"foreign key violations after rebuilding user tables: {problems}")


def _app_item_pin_variants(conn):
    # NBT variants of one item id (gcm/inventory.py) are pinned separately;
    # item_key already includes the variant, this keeps it retrievable.
    conn.execute("ALTER TABLE user_item_pins ADD COLUMN variant TEXT")


def _item_history_variants(conn):
    conn.execute("ALTER TABLE network_snapshot ADD COLUMN variant TEXT")
    conn.execute("ALTER TABLE network_snapshot ADD COLUMN variant_name TEXT")
    # Until now every NBT variant of an item was recorded under one key,
    # interleaving their counts. Which keys that hit is only known once a
    # scan reports variants, so the first scan clears them - see
    # store.items.clear_legacy_variant_history().
    conn.execute("CREATE TABLE pending_nbt_cleanup (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO pending_nbt_cleanup (id) VALUES (1)")


def _item_history_pattern_snapshot(conn):
    # The last complete pattern scan (gcm/patterns.py) as one JSON list,
    # so a restart doesn't leave the server without patterns until the
    # next one, up to an hour away. Only ever read and written whole.
    conn.execute("""
        CREATE TABLE pattern_snapshot (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            patterns TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)


def _item_history_compact(conn):
    # Each row repeated the item's full key and its name, and the index
    # repeated the key again: ~145 bytes a row, ~1 GB a year on a big
    # base. Now the key and the latest name are stored once in
    # history_items, and a row is (item_id, recorded_at, size) in a
    # WITHOUT ROWID table whose primary key is the index the queries
    # use. Rows at the same time for one item keep the later one.
    conn.execute("""
        CREATE TABLE history_items (
            id INTEGER PRIMARY KEY,
            item_key TEXT NOT NULL UNIQUE,
            label TEXT
        )
    """)
    conn.execute("INSERT INTO history_items (item_key) SELECT DISTINCT item_key FROM item_history")
    conn.execute("""
        UPDATE history_items SET label = (
            SELECT label FROM item_history h
            WHERE h.item_key = history_items.item_key AND h.label IS NOT NULL
            ORDER BY h.recorded_at DESC LIMIT 1
        )
    """)
    conn.execute("""
        CREATE TABLE item_history_compact (
            item_id INTEGER NOT NULL,
            recorded_at REAL NOT NULL,
            size REAL NOT NULL,
            PRIMARY KEY (item_id, recorded_at)
        ) WITHOUT ROWID
    """)
    conn.execute("""
        INSERT OR REPLACE INTO item_history_compact (item_id, recorded_at, size)
        SELECT i.id, h.recorded_at, h.size FROM item_history h
        JOIN history_items i ON i.item_key = h.item_key
        ORDER BY h.id
    """)
    conn.execute("DROP TABLE item_history")
    conn.execute("ALTER TABLE item_history_compact RENAME TO item_history")
    # How far gcm/retention.py has thinned each tier, so a run only
    # looks at days it hasn't done yet.
    conn.execute("CREATE TABLE history_thinned (tier TEXT PRIMARY KEY, until REAL NOT NULL)")


def _app_push_subscriptions(conn):
    # One row per browser that turned notifications on (gcm/push.py).
    # A browser has one endpoint whoever is signed in, so it's the key.
    # origin is the page's own URL, the default VAPID contact.
    conn.execute("""
        CREATE TABLE push_subscriptions (
            endpoint TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            origin TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX idx_push_subscriptions_user ON push_subscriptions (user_id)")


def _app_craft_event_details(conn):
    # For the Crafts tab's history: how long a job ran (started_at, NULL
    # when it was already running as the server first saw its CPU) and
    # which item it made, so a row can open that item's stock history.
    for column in ("started_at REAL", "item_mod TEXT", "item_internal TEXT", "item_damage INTEGER"):
        conn.execute(f"ALTER TABLE craft_events ADD COLUMN {column}")


def _app_stock_rules(conn):
    # Stock rules (gcm/stock.py), checked after every network scan.
    # stock_alerts: a user's "tell me when this drops below N". armed is
    # 0 once it has fired, until the item is back at or above N.
    conn.execute("""
        CREATE TABLE stock_alerts (
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            item_key TEXT NOT NULL,
            label TEXT NOT NULL,
            mod TEXT,
            internal TEXT NOT NULL,
            damage INTEGER,
            kind TEXT NOT NULL,
            variant TEXT,
            below REAL NOT NULL,
            armed INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, item_key)
        )
    """)
    # stock_targets: the base's "keep at least N, refill to M" rules, one
    # per item whoever set it. updated_by is who auto requests are filed
    # under; last_* is the latest auto request and how it went.
    conn.execute("""
        CREATE TABLE stock_targets (
            item_key TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            mod TEXT,
            internal TEXT NOT NULL,
            damage INTEGER,
            kind TEXT NOT NULL,
            variant TEXT,
            keep_at_least REAL NOT NULL,
            refill_to REAL NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_by TEXT NOT NULL,
            updated_at REAL NOT NULL,
            last_request_id INTEGER,
            last_requested_at REAL,
            last_status TEXT,
            last_reason TEXT
        )
    """)
    # Whether a request came from the page or a stock target.
    conn.execute("ALTER TABLE craft_request_history ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")


def _app_craft_event_auto(conn):
    # Whether a keep-in-stock target (gcm/stock.py) started the job, for
    # the Auto badge in the Crafts tab's history. Rows from before are 0:
    # nothing recorded which of them were.
    conn.execute("ALTER TABLE craft_events ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")


def _app_settings(conn):
    # Server-wide switches set on the page (gcm/agents.py: the Status
    # dialog's checkboxes), one JSON value per key.
    conn.execute("CREATE TABLE settings (k TEXT PRIMARY KEY, v TEXT NOT NULL)")


APP_MIGRATIONS = [
    _app_baseline,
    _app_drop_orphaned_user_rows,
    _app_cascade_user_rows,
    _app_item_pin_variants,
    _app_push_subscriptions,
    _app_craft_event_details,
    _app_stock_rules,
    _app_craft_event_auto,
    _app_settings,
]
POWER_MIGRATIONS = [_power_baseline, _power_machines, _power_drop_impossible_times]
ITEM_HISTORY_MIGRATIONS = [
    _item_history_baseline,
    _item_history_variants,
    _item_history_pattern_snapshot,
    _item_history_compact,
]


def migrate(open_db, steps):
    conn = open_db()
    # Transactions are managed explicitly below; sqlite3's implicit
    # ones would commit DDL statements one at a time.
    conn.isolation_level = None
    try:
        while True:
            conn.execute("BEGIN IMMEDIATE")
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > len(steps):
                    path = conn.execute("PRAGMA database_list").fetchone()[2]
                    raise RuntimeError(
                        f"{path} is at schema version {version}, but this server "
                        f"only knows {len(steps)} - it was written by a newer "
                        "version. Upgrade the server instead of running this one."
                    )
                if version == len(steps):
                    conn.execute("COMMIT")
                    return
                steps[version](conn)
                conn.execute(f"PRAGMA user_version = {version + 1}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


def _use_wal(open_db):
    # A setting of the file itself, so once is enough; it can't change
    # inside a transaction, so it runs before migrate().
    conn = open_db()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    finally:
        conn.close()


def init_app_db():
    _use_wal(app_db)
    migrate(app_db, APP_MIGRATIONS)


def init_power_db():
    _use_wal(power_db)
    migrate(power_db, POWER_MIGRATIONS)


def init_item_history_db():
    _use_wal(item_history_db)
    migrate(item_history_db, ITEM_HISTORY_MIGRATIONS)
    vacuum_if_mostly_free(item_history_db)


def vacuum_if_mostly_free(open_db, fraction=0.25):
    """Shrinks the file when over `fraction` of it is free pages: after
    the compact migration, or thinning a long backlog. Day to day, freed
    pages are reused by new rows and this does nothing. Returns whether
    it ran."""
    conn = open_db()
    try:
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        total = conn.execute("PRAGMA page_count").fetchone()[0]
        if not total or free / total <= fraction:
            return False
        conn.execute("VACUUM")
        return True
    finally:
        conn.close()


def backup_all(target_dir):
    """Copies every database into target_dir with SQLite's online backup
    API - a consistent snapshot even while the server is writing, which
    copying the files (and their WAL) by hand can't promise. Returns the
    paths written. Refuses to overwrite an existing file."""
    sources = (
        (config.APP_DB_PATH, app_db),
        (config.POWER_DB_PATH, power_db),
        (config.ITEM_HISTORY_DB_PATH, item_history_db),
    )
    targets = [os.path.join(target_dir, os.path.basename(path)) for path, _ in sources]
    existing = [path for path in targets if os.path.exists(path)]
    if existing:
        raise RuntimeError(f"Backup target already exists: {', '.join(existing)}")
    os.makedirs(target_dir, exist_ok=True)
    for (_, open_db), target in zip(sources, targets, strict=True):
        source = open_db()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    return targets


def _restorable():
    """Each database by its file name, as backup_all() writes it:
    (live path, open_db, init, migrations)."""
    return {
        os.path.basename(config.APP_DB_PATH): (config.APP_DB_PATH, app_db, init_app_db, APP_MIGRATIONS),
        os.path.basename(config.POWER_DB_PATH): (config.POWER_DB_PATH, power_db, init_power_db, POWER_MIGRATIONS),
        os.path.basename(config.ITEM_HISTORY_DB_PATH): (
            config.ITEM_HISTORY_DB_PATH, item_history_db, init_item_history_db, ITEM_HISTORY_MIGRATIONS,
        ),
    }


def database_names():
    return set(_restorable())


def _read_pragma(path, name):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(f"PRAGMA {name}").fetchone()[0]
    finally:
        conn.close()


def why_not_restorable(name, path):
    """None if the file at path, a copy of database `name` from
    backup_all(), can be restored with restore_copy(); else why not."""
    live_path, _, _, migrations = _restorable()[name]
    try:
        ok = _read_pragma(path, "quick_check")
        version = _read_pragma(path, "user_version")
        page_size = _read_pragma(path, "page_size")
    except sqlite3.DatabaseError:
        return "isn't a database"
    if ok != "ok":
        return "is damaged"
    if version > len(migrations):
        return "was made by a newer version of the server. Update this one first"
    if page_size != _read_pragma(live_path, "page_size"):
        # Copying into a WAL database can't change its page size.
        return "can't be restored while the server runs. Restore it by hand (see Backups in docs/server.md)"
    return None


def restore_copy(name, path):
    """Copies the file at path over the live database `name` with the
    online backup API - the reverse of backup_all(), safe while the server
    runs - then migrates it, for a copy made by an older version."""
    _, open_db, init, _ = _restorable()[name]
    source = sqlite3.connect(path)
    destination = open_db()
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    init()
