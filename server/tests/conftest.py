"""
Shared fixtures for the backend test suite.

Isolation strategy, and why it looks the way it does:

- app.py runs real module-level side effects on import (SQLite init,
  icons_lookup.json loading, etc.) - not something a test suite can
  avoid, so instead it's pointed somewhere safe. API_KEY and DATA_DIR
  are both set from environment variables BEFORE app is ever imported
  (this file does it at module load time, which pytest guarantees runs
  before any test file that imports from here), into a fresh temp
  directory - real production data is never touched by running tests.

- app is imported exactly ONCE for the whole test session, not
  reloaded per test. Re-importing a Flask app module mid-session is a
  real source of "view function mapping is overwriting an existing
  endpoint" errors (Flask tracks registered routes on the app object
  itself) - safer to import once and reset IN-MEMORY state between
  tests explicitly instead.

- reset_state (autouse) runs before every single test: clears every
  known in-memory global (the crafts/network/craft-request/cancel-
  request state, the valid-keys set) and wipes every SQLite table
  (DELETE FROM, not dropping/recreating - the schema/migration logic
  already ran once at import and doesn't need re-running). This list is
  maintained by hand against the real CREATE TABLE statements in app.py
  (confirmed via grep, not assumed) - if a future feature adds new
  module-level state or a new table, this fixture needs a matching
  update, the same way it would for any hand-maintained test harness.
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
os.environ["API_KEY"] = TEST_API_KEY
os.environ["DATA_DIR"] = _test_data_dir

# server/ itself (one level up from server/tests/) needs to be on the
# import path so "import app" finds server/app.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402  (must come after the env/path setup above)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_test_data_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def flask_app():
    app_module.app.config["TESTING"] = True
    return app_module.app


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
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, user_id, role, time.time()),
        )
        token = app_module._create_access_token(conn, user_id)
        conn.commit()
    finally:
        conn.close()
    response = client.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200
    return client


@pytest.fixture(autouse=True)
def reset_state():
    # In-memory state
    app_module._state["jobs"] = []
    app_module._state["source"] = None
    app_module._state["received_at"] = None

    app_module._network_buffer.clear()
    app_module._network_state["items"] = []
    app_module._network_state["item_count"] = 0
    app_module._network_state["updated_at"] = None
    app_module._network_state["in_progress"] = False
    app_module._network_state["scan_started_at"] = None
    app_module._network_state["is_reconstructed"] = False

    app_module._craft_requests.clear()
    app_module._craft_request_next_id = 1
    app_module._cancel_requests.clear()
    app_module._cancel_request_next_id = 1
    app_module._chart_cache.clear()
    app_module._chart_request_times.clear()

    # SQLite - wiped, not dropped/recreated (CREATE TABLE IF NOT EXISTS
    # already ran once at import; DELETE FROM is enough for a clean slate
    # and avoids re-running migration logic per test).
    db_tables = {
        app_module.CRAFT_HISTORY_DB_PATH: [
            "users",
            "access_tokens",
            "sessions",
            "craft_events",
            "user_pins",
            "user_completions",
            "user_item_pins",
            "craft_request_history",
            "craft_cancel_history",
        ],
        app_module.POWER_DB_PATH: ["power_readings"],
        app_module.ITEM_HISTORY_DB_PATH: ["item_history", "network_snapshot"],
    }
    for db_path, tables in db_tables.items():
        conn = sqlite3.connect(db_path)
        try:
            for table in tables:
                try:
                    conn.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    pass  # table doesn't exist yet on this DB - fine
            conn.commit()
        finally:
            conn.close()

    yield
