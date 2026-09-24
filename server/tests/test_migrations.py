import os
import sqlite3

import pytest

from gcm import config, db


@pytest.fixture()
def open_db(tmp_path):
    path = str(tmp_path / "test.db")
    return lambda: sqlite3.connect(path)


def version(open_db):
    conn = open_db()
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def tables(open_db):
    conn = open_db()
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_fresh_file_runs_every_step_once(open_db):
    calls = []
    steps = [
        lambda conn: calls.append(1) or conn.execute("CREATE TABLE a (x)"),
        lambda conn: calls.append(2) or conn.execute("CREATE TABLE b (x)"),
    ]
    db.migrate(open_db, steps)
    db.migrate(open_db, steps)
    assert calls == [1, 2]
    assert version(open_db) == 2
    assert tables(open_db) == {"a", "b"}


def test_new_steps_run_on_top_of_an_older_version(open_db):
    db.migrate(open_db, [lambda conn: conn.execute("CREATE TABLE a (x)")])
    db.migrate(
        open_db,
        [
            lambda conn: pytest.fail("step 1 already ran"),
            lambda conn: conn.execute("ALTER TABLE a ADD COLUMN y"),
        ],
    )
    assert version(open_db) == 2


def test_failing_step_rolls_back_its_changes_and_version(open_db):
    def broken(conn):
        conn.execute("CREATE TABLE half_done (x)")
        raise ValueError("boom")

    steps = [lambda conn: conn.execute("CREATE TABLE a (x)"), broken]
    with pytest.raises(ValueError):
        db.migrate(open_db, steps)
    assert version(open_db) == 1
    assert tables(open_db) == {"a"}


def test_file_from_a_newer_server_is_refused(open_db):
    db.migrate(open_db, [lambda conn: None, lambda conn: None])
    with pytest.raises(RuntimeError, match="schema version 2"):
        db.migrate(open_db, [lambda conn: None])


def test_app_databases_are_at_their_latest_version(flask_app):
    for path, steps in (
        (config.APP_DB_PATH, db.APP_MIGRATIONS),
        (config.POWER_DB_PATH, db.POWER_MIGRATIONS),
        (config.ITEM_HISTORY_DB_PATH, db.ITEM_HISTORY_MIGRATIONS),
    ):
        assert version(lambda: sqlite3.connect(path)) == len(steps), path


def version_2_app_db(path):
    """An app.db as the last release left it (steps 1-2), holding a user
    with a row in every user-owned table, one orphan pin and an audit row."""
    db.migrate(lambda: sqlite3.connect(path), db.APP_MIGRATIONS[:2])
    conn = sqlite3.connect(path)  # foreign keys off, as before step 3
    try:
        conn.executescript("""
            INSERT INTO users (id, display_name, role, created_at)
                VALUES ('usr_alice', 'Alice', 'operator', 0);
            INSERT INTO access_tokens (id, user_id, secret_hash, created_at, is_bootstrap)
                VALUES ('tok-a', 'usr_alice', 'h', 0, 1);
            INSERT INTO sessions (id, user_id, token_hash, access_token_id,
                                  must_rotate_bootstrap, created_at, expires_at)
                VALUES ('ses-a', 'usr_alice', 'th', 'tok-a', 1, 0, 9e9);
            INSERT INTO craft_events (id, cpu_name, status, occurred_at)
                VALUES (1, 'W01', 'finished', 0);
            INSERT INTO user_completions (user_id, craft_event_id, created_at)
                VALUES ('usr_alice', 1, 0);
            INSERT INTO user_pins (user_id, cpu_name, pinned_at)
                VALUES ('usr_alice', 'W01', 0), ('usr_gone', 'W02', 0);
            INSERT INTO user_item_pins (user_id, item_key, internal, kind, pinned_at)
                VALUES ('usr_alice', '|water||fluid', 'water', 'fluid', 0);
            INSERT INTO craft_request_history
                (request_id, user_id, label, internal, amount, kind, status, created_at)
                VALUES (1, 'usr_alice', 'Iron', 'iron', 1, 'item', 'failed', 0);
        """)
        conn.commit()
    finally:
        conn.close()


USER_OWNED = ("user_pins", "user_completions", "user_item_pins", "access_tokens", "sessions")


def test_step_3_keeps_rows_and_makes_user_deletion_cascade(tmp_path):
    path = str(tmp_path / "app.db")
    version_2_app_db(path)

    def open_db():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    db.migrate(open_db, db.APP_MIGRATIONS)
    conn = open_db()
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in USER_OWNED}
        # Every row survives the rebuild, except the orphan pin.
        assert counts == dict.fromkeys(USER_OWNED, 1)
        assert conn.execute(
            "SELECT is_bootstrap, must_rotate_bootstrap, access_token_id "
            "FROM access_tokens JOIN sessions ON sessions.access_token_id = access_tokens.id"
        ).fetchone() == (1, 1, "tok-a")
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_access_tokens_user", "idx_sessions_user", "idx_user_completions_user"} <= indexes

        conn.execute("DELETE FROM users WHERE id = 'usr_alice'")
        conn.commit()
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in USER_OWNED}
        assert counts == dict.fromkeys(USER_OWNED, 0)
        # The audit trail outlives the user.
        assert conn.execute("SELECT COUNT(*) FROM craft_request_history").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_legacy_craft_history_db_is_adopted(tmp_path, monkeypatch):
    legacy, current = tmp_path / "craft_history.db", tmp_path / "app.db"
    legacy.write_bytes(b"old db")
    (tmp_path / "craft_history.db-wal").write_bytes(b"old wal")
    monkeypatch.setattr(config, "LEGACY_CRAFT_DB_PATH", str(legacy))
    monkeypatch.setattr(config, "APP_DB_PATH", str(current))

    db.adopt_legacy_app_db()

    assert current.read_bytes() == b"old db"
    assert (tmp_path / "app.db-wal").read_bytes() == b"old wal"
    assert not legacy.exists()


def test_legacy_and_current_app_db_together_are_refused(tmp_path, monkeypatch):
    legacy, current = tmp_path / "craft_history.db", tmp_path / "app.db"
    legacy.write_bytes(b"old")
    current.write_bytes(b"new")
    monkeypatch.setattr(config, "LEGACY_CRAFT_DB_PATH", str(legacy))
    monkeypatch.setattr(config, "APP_DB_PATH", str(current))

    with pytest.raises(RuntimeError, match="Both"):
        db.adopt_legacy_app_db()
    assert legacy.read_bytes() == b"old" and current.read_bytes() == b"new"


def test_every_database_uses_wal(flask_app):
    for open_db in (db.app_db, db.power_db, db.item_history_db):
        conn = open_db()
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            conn.close()


def test_backup_copies_every_database_and_never_overwrites(flask_app, tmp_path):
    with db.transaction(db.power_db) as conn:
        conn.execute("INSERT INTO power_readings (ts, stored, capacity) VALUES (1, 2, 3)")

    target = str(tmp_path / "backup")
    written = db.backup_all(target)

    assert sorted(os.path.basename(p) for p in written) == ["app.db", "item_history.db", "power.db"]
    for path, steps in zip(written, (db.APP_MIGRATIONS, db.POWER_MIGRATIONS, db.ITEM_HISTORY_MIGRATIONS)):
        assert version(lambda: sqlite3.connect(path)) == len(steps)
    copy = sqlite3.connect(written[1])
    try:
        assert copy.execute("SELECT ts, stored, capacity FROM power_readings").fetchall() == [(1, 2, 3)]
    finally:
        copy.close()

    with pytest.raises(RuntimeError, match="already exists"):
        db.backup_all(target)


def test_app_db_enforces_foreign_keys(flask_app):
    conn = db.app_db()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO access_tokens (id, user_id, secret_hash, created_at) "
                "VALUES ('tok', 'usr_nobody', 'x', 0)"
            )
    finally:
        conn.close()
