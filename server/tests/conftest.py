"""
Shared fixtures for the backend test suite.

Isolation strategy, and why it looks the way it does:

- Importing gcm has no side effects; gcm.create_app() does all startup
  work (SQLite init/migrations, icons_lookup.json loading, snapshot
  reload). It is called once here with a fresh temp directory as
  data_dir, so real production data is never touched by running tests.

- The app is created exactly ONCE for the whole test session and
  shared through the flask_app fixture. In-memory state lives at module
  level in gcm/state.py regardless of how many apps exist, so one app
  plus an explicit reset between tests is simpler than a fresh app
  per test.

- reset_state (autouse) runs before every single test: calls
  gcm.reset_runtime_state() for in-memory state and empties every
  table in every SQLite file (discovered from sqlite_master, so a new
  table needs no change here). New module-level state still needs
  adding to state.reset() in gcm/state.py.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import time

import pytest

TEST_API_KEY = "test-key-for-pytest"
_test_data_dir = tempfile.mkdtemp(prefix="gtnh_test_data_")

# server/ itself (one level up from server/tests/) needs to be on the
# import path so "import gcm" finds server/gcm/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gcm  # noqa: E402  (must come after the path setup above)
from gcm import config, db, gamedata_index, store  # noqa: E402

_app = gcm.create_app(data_dir=_test_data_dir, api_key=TEST_API_KEY)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_test_data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def flask_app():
    _app.config["TESTING"] = True
    return _app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture()
def api_key():
    return TEST_API_KEY


@pytest.fixture()
def api_headers(api_key):
    return {"X-API-Key": api_key}


def login_as(client, user_id, role="viewer"):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, user_id, role, time.time()),
        )
        token = store.users.create_access_token(conn, user_id)
        conn.commit()
    finally:
        conn.close()
    response = client.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200
    return client


@pytest.fixture(autouse=True)
def reset_state():
    gcm.reset_runtime_state()

    # SQLite - wiped, not dropped/recreated (the schema/migration logic
    # already ran once in create_app(); DELETE FROM is enough for a clean
    # slate). Tables are read from sqlite_master so new ones are covered
    # automatically.
    for db_path in (
        config.APP_DB_PATH,
        config.POWER_DB_PATH,
        config.ITEM_HISTORY_DB_PATH,
    ):
        conn = sqlite3.connect(db_path)
        try:
            tables = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for (table,) in tables:
                conn.execute(f'DELETE FROM "{table}"')
            conn.commit()
        finally:
            conn.close()

    yield


@pytest.fixture()
def webhook_events(monkeypatch):
    """One webhook with every event on; records the events it would be
    sent instead of posting them."""
    from gcm import webhooks

    events = []
    monkeypatch.setattr(config, "WEBHOOK_URLS", ["https://hooks.example.test/x"])
    monkeypatch.setattr(config, "WEBHOOK_EVENTS", set(webhooks.EVENTS))
    monkeypatch.setattr(config, "PUBLIC_URL", "https://gtnh.example.test")
    monkeypatch.setattr(webhooks, "_put", events.append)
    return events


def bundle_index(bundle):
    """What oredict.load() and item_info.load() take: the index
    (gcm/gamedata_index.py) of the bundle files in directory `bundle`
    (ore_dict.json, item_info.json, ...), whichever are there."""
    bundle = str(bundle)
    data_json = os.path.join(bundle, "data.json")
    if not os.path.exists(data_json):
        with open(data_json, "w") as f:
            f.write("{}")
    index_path = os.path.join(bundle, gamedata_index.FILE)
    gamedata_index.build(bundle, index_path)
    return gamedata_index.Index(index_path)


def ore_dict_index(path):
    """bundle_index() for an ore_dict.json at path."""
    if os.path.basename(str(path)) != "ore_dict.json":
        raise ValueError("the bundle's ore dictionary is ore_dict.json")
    return bundle_index(os.path.dirname(str(path)))
