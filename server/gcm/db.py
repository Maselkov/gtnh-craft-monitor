"""SQLite connections and schemas. One file per major concern
(craft_history.db, power.db, item_history.db), each opened fresh per
call - sqlite3 connections aren't safe to share across the server's
threads, and at this traffic level the cost is irrelevant.

Schemas are versioned; see "Schema migrations" at the bottom."""

import sqlite3
from contextlib import contextmanager

from gcm import config


@contextmanager
def transaction(open_db):
    """`with db.transaction(db.craft_db) as conn:` - one connection,
    committed if the block finishes, rolled back if it raises, and
    closed either way."""
    conn = open_db()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def craft_db():
    return sqlite3.connect(config.CRAFT_HISTORY_DB_PATH, timeout=10)


def _craft_baseline(conn):
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
    conn = sqlite3.connect(config.POWER_DB_PATH, timeout=10)
    return conn


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


# ---------------------------------------------------------------------
# Item/fluid quantity history (SQLite-backed, own db file - separate
# from power.db and craft_history.db, matching the established "one db
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
    conn = sqlite3.connect(config.ITEM_HISTORY_DB_PATH, timeout=10)
    return conn


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
    # load_network_snapshot() in routes/network.py.
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
CRAFT_MIGRATIONS = [_craft_baseline]
POWER_MIGRATIONS = [_power_baseline]
ITEM_HISTORY_MIGRATIONS = [_item_history_baseline]


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


def init_craft_db():
    migrate(craft_db, CRAFT_MIGRATIONS)


def init_power_db():
    migrate(power_db, POWER_MIGRATIONS)


def init_item_history_db():
    migrate(item_history_db, ITEM_HISTORY_MIGRATIONS)
