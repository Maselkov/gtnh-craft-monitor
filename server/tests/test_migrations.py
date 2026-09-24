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
        (config.CRAFT_HISTORY_DB_PATH, db.CRAFT_MIGRATIONS),
        (config.POWER_DB_PATH, db.POWER_MIGRATIONS),
        (config.ITEM_HISTORY_DB_PATH, db.ITEM_HISTORY_MIGRATIONS),
    ):
        assert version(lambda: sqlite3.connect(path)) == len(steps), path


def test_rows_left_by_deleted_users_are_dropped(flask_app):
    conn = db.craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES ('usr_alice', 'Alice', 'viewer', 0)"
        )
        for user_id in ("usr_alice", "usr_gone"):
            conn.execute(
                "INSERT INTO user_pins (user_id, cpu_name, pinned_at) VALUES (?, 'W01', 0)",
                (user_id,),
            )
        conn.commit()
        db._craft_drop_orphaned_user_rows(conn)
        pins = conn.execute("SELECT user_id FROM user_pins").fetchall()
    finally:
        conn.close()
    assert pins == [("usr_alice",)]


def test_craft_db_enforces_foreign_keys(flask_app):
    conn = db.craft_db()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO access_tokens (id, user_id, secret_hash, created_at) "
                "VALUES ('tok', 'usr_nobody', 'x', 0)"
            )
    finally:
        conn.close()
