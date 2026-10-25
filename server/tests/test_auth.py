import ipaddress
import time

import pytest

import app as app_module
from gcm import auth, config, db, store


def test_access_token_resolves_stable_user_id():
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        token = store.users.create_access_token(conn, "usr_alice")
        conn.commit()

        identity = store.users.find_access_token(conn, token)
    finally:
        conn.close()

    assert identity == {"id": identity["id"], "user_id": "usr_alice"}
    assert identity["id"].startswith("tok-")


def test_invalid_access_token_is_rejected():
    conn = db.app_db()
    try:
        token = "gcm_tok_unknown_not-a-real-secret"
        assert store.users.find_access_token(conn, token) is None
    finally:
        conn.close()


def test_login_exchanges_access_token_for_session_cookie(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        token = store.users.create_access_token(conn, "usr_alice")
        conn.commit()
    finally:
        conn.close()

    response = client.post("/api/auth/login", json={"token": token})

    assert response.status_code == 200
    assert response.get_json()["user"] == {
        "id": "usr_alice",
        "display_name": "Alice",
        "role": "operator",
    }
    assert "gcm_session=" in response.headers["Set-Cookie"]
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/auth/session").get_json()["user"]["id"] == "usr_alice"


def test_spoofed_user_id_header_does_not_authenticate(client):
    response = client.get("/api/pins", headers={"X-User-Id": "usr_alice"})

    assert response.status_code == 401


def test_disabling_a_user_invalidates_existing_sessions(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "viewer", time.time()),
        )
        token = store.users.create_access_token(conn, "usr_alice")
        conn.commit()
    finally:
        conn.close()

    assert client.post("/api/auth/login", json={"token": token}).status_code == 200
    conn = db.app_db()
    try:
        conn.execute(
            "UPDATE users SET disabled_at = ? WHERE id = ?", (time.time(), "usr_alice")
        )
        conn.commit()
    finally:
        conn.close()

    assert client.get("/api/auth/session").get_json() == {"authenticated": False, "setup_required": False, "private": False}


def test_only_admins_can_list_or_create_users(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_viewer", "Viewer", "viewer", time.time()),
        )
        viewer_token = store.users.create_access_token(conn, "usr_viewer")
        conn.commit()
    finally:
        conn.close()

    assert (
        client.post("/api/auth/login", json={"token": viewer_token}).status_code == 200
    )
    assert client.get("/api/admin/users").status_code == 403
    assert (
        client.post(
            "/api/admin/users", json={"display_name": "Alice", "role": "operator"}
        ).status_code
        == 403
    )

    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        admin_token = store.users.create_access_token(conn, "usr_admin")
        conn.commit()
    finally:
        conn.close()

    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )
    response = client.post(
        "/api/admin/users", json={"display_name": "Alice", "role": "operator"}
    )
    assert response.status_code == 201
    assert response.get_json()["user"]["role"] == "operator"
    assert response.get_json()["token"].startswith("gcm_tok-")

    users = client.get("/api/admin/users").get_json()["users"]
    assert [(user["display_name"], user["role"]) for user in users] == [
        ("Admin", "admin"),
        ("Alice", "operator"),
        ("Viewer", "viewer"),
    ]
    assert all("secret_hash" not in user for user in users)


def test_setup_code_exists_only_while_there_are_no_users(client):
    auth.start_setup()
    assert auth.setup_code()
    assert client.get("/api/auth/session").get_json() == {
        "authenticated": False,
        "setup_required": True,
        "private": False,
    }

    store.users.create("Admin", "admin")
    assert auth.setup_code() is None
    auth.start_setup()
    assert auth.setup_code() is None


def test_setup_creates_the_first_admin_and_signs_them_in(client, flask_app):
    auth.start_setup()
    code = auth.setup_code()

    wrong = client.post("/api/auth/setup", json={"code": "nope", "display_name": "Ann"})
    assert wrong.status_code == 403
    assert store.users.count() == 0
    assert client.post("/api/auth/setup", json={"code": code}).status_code == 400

    response = client.post("/api/auth/setup", json={"code": code, "display_name": " Ann "})
    assert response.status_code == 200
    body = response.get_json()
    assert body["user"]["display_name"] == "Ann"
    assert body["user"]["role"] == "admin"
    assert response.headers["Cache-Control"] == "no-store"
    # Signed in by the same response, as an admin.
    assert client.get("/api/admin/users").status_code == 200
    # The token it shows signs in elsewhere.
    other = flask_app.test_client()
    assert other.post("/api/auth/login", json={"token": body["token"]}).status_code == 200

    again = flask_app.test_client().post(
        "/api/auth/setup", json={"code": code, "display_name": "Mallory"}
    )
    assert again.status_code == 409
    assert store.users.count() == 1


def test_setup_is_closed_once_an_admin_is_made_from_the_command_line(client, capsys):
    auth.start_setup()
    code = auth.setup_code()
    app_module._cli_create_admin("Admin")
    token = capsys.readouterr().out.strip()

    response = client.post("/api/auth/setup", json={"code": code, "display_name": "Ann"})
    assert response.status_code == 409
    assert client.post("/api/auth/login", json={"token": token}).status_code == 200
    with pytest.raises(SystemExit):
        app_module._cli_create_admin("Admin")


def test_startup_logs_the_setup_link_or_an_unrotated_bootstrap_token(caplog):
    import logging

    log = logging.getLogger("gcm-test")
    auth.start_setup()
    with caplog.at_level(logging.WARNING, logger="gcm-test"):
        app_module._log_setup_status(log)
    assert f"/#setup={auth.setup_code()}" in caplog.text

    caplog.clear()
    user_id, _ = store.users.create("Admin", "admin")
    conn = db.app_db()
    try:
        conn.execute("UPDATE access_tokens SET is_bootstrap = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    with caplog.at_level(logging.WARNING, logger="gcm-test"):
        app_module._log_setup_status(log)
    assert "GCM_BOOTSTRAP_ADMIN_TOKEN" in caplog.text


def test_api_key_is_generated_once_when_the_environment_has_none(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "API_KEY_PATH", str(tmp_path / "data" / "api_key"))
    monkeypatch.setattr(config, "API_KEY", "")
    config.load_api_key()
    generated = config.API_KEY
    assert len(generated) == 64
    assert (tmp_path / "data" / "api_key").stat().st_mode & 0o777 == 0o600
    config.require_runtime_secrets()

    monkeypatch.setattr(config, "API_KEY", "")
    config.load_api_key()
    assert config.API_KEY == generated

    monkeypatch.setattr(config, "API_KEY", "from-the-environment-" + "x" * 32)
    config.load_api_key()
    assert config.API_KEY == "from-the-environment-" + "x" * 32


def test_default_or_short_service_key_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "change-me")
    with pytest.raises(RuntimeError, match="API_KEY must be a unique secret"):
        config.require_runtime_secrets()

    monkeypatch.setattr(config, "API_KEY", "too-short")
    with pytest.raises(RuntimeError, match="API_KEY must be a unique secret"):
        config.require_runtime_secrets()


def test_debug_dumps_require_an_operator_session(client):
    assert client.get("/api/debug").status_code == 403

    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_operator", "Operator", "operator", time.time()),
        )
        token = store.users.create_access_token(conn, "usr_operator")
        conn.commit()
    finally:
        conn.close()

    assert client.post("/api/auth/login", json={"token": token}).status_code == 200
    assert client.get("/api/debug").status_code == 200


def test_cross_origin_session_writes_are_rejected(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "viewer", time.time()),
        )
        token = store.users.create_access_token(conn, "usr_alice")
        conn.commit()
    finally:
        conn.close()

    assert client.post("/api/auth/login", json={"token": token}).status_code == 200
    response = client.post(
        "/api/completions/ack-all", headers={"Origin": "https://attacker.example"}
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "cross-origin request rejected"


def test_browser_security_headers_are_returned(client):
    response = client.get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_untrusted_peer_cannot_spoof_forwarded_host(client):
    response = client.get(
        "/",
        environ_overrides={
            "REMOTE_ADDR": "203.0.113.5",
            "HTTP_X_FORWARDED_HOST": "attacker.example",
            "HTTP_X_FORWARDED_PROTO": "https",
        },
    )

    assert "attacker.example" not in response.get_data(as_text=True)


def test_trusted_proxy_can_supply_forwarded_host(client, monkeypatch):
    monkeypatch.setattr(
        config,
        "TRUSTED_PROXY_NETWORKS",
        [ipaddress.ip_network("10.0.0.0/8")],
    )
    response = client.get(
        "/",
        environ_overrides={
            "REMOTE_ADDR": "10.1.2.3",
            "HTTP_X_FORWARDED_HOST": "monitor.example",
            "HTTP_X_FORWARDED_PROTO": "https",
        },
    )

    assert "https://monitor.example/" in response.get_data(as_text=True)


def test_admin_can_revoke_a_token_and_its_sessions(client, flask_app):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_operator", "Operator", "operator", time.time()),
        )
        admin_token = store.users.create_access_token(conn, "usr_admin")
        operator_token = store.users.create_access_token(conn, "usr_operator")
        operator_token_id = store.users.find_access_token(conn, operator_token)["id"]
        conn.commit()
    finally:
        conn.close()

    operator_client = flask_app.test_client()
    assert (
        operator_client.post(
            "/api/auth/login", json={"token": operator_token}
        ).status_code
        == 200
    )
    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )

    response = client.post(f"/api/admin/tokens/{operator_token_id}/revoke")
    assert response.status_code == 200
    assert operator_client.get("/api/auth/session").get_json() == {
        "authenticated": False,
        "setup_required": False,
        "private": False,
    }
    assert (
        operator_client.post(
            "/api/auth/login", json={"token": operator_token}
        ).status_code
        == 401
    )


def test_admin_can_view_user_action_history(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        conn.execute(
            "INSERT INTO craft_request_history "
            "(request_id, user_id, label, internal, amount, kind, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "usr_alice",
                "Iron Ingot",
                "iron_ingot",
                64,
                "item",
                "accepted",
                time.time(),
            ),
        )
        admin_token = store.users.create_access_token(conn, "usr_admin")
        conn.commit()
    finally:
        conn.close()

    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )
    response = client.get("/api/admin/users/usr_alice/history")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json()["events"][0]["target"] == "Iron Ingot"


def test_revoked_tokens_are_not_listed(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        admin_token = store.users.create_access_token(conn, "usr_admin")
        alice_token = store.users.create_access_token(conn, "usr_alice")
        alice_token_id = store.users.find_access_token(conn, alice_token)["id"]
        conn.commit()
    finally:
        conn.close()

    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )
    client.post(f"/api/admin/tokens/{alice_token_id}/revoke")

    users = client.get("/api/admin/users").get_json()["users"]
    alice = next(user for user in users if user["id"] == "usr_alice")
    assert alice["tokens"] == []


def test_admin_can_delete_a_user_and_their_credentials(client, flask_app):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        admin_token = store.users.create_access_token(conn, "usr_admin")
        alice_token = store.users.create_access_token(conn, "usr_alice")
        conn.execute(
            "INSERT INTO user_pins (user_id, cpu_name, pinned_at) VALUES ('usr_alice', 'W01', 0)"
        )
        conn.execute(
            "INSERT INTO user_item_pins (user_id, item_key, internal, kind, pinned_at) "
            "VALUES ('usr_alice', '|x||item', 'x', 'item', 0)"
        )
        event_id = conn.execute(
            "INSERT INTO craft_events (cpu_name, status, occurred_at) VALUES ('W01', 'finished', 0)"
        ).lastrowid
        conn.execute(
            "INSERT INTO user_completions (user_id, craft_event_id, created_at) "
            "VALUES ('usr_alice', ?, 0)",
            (event_id,),
        )
        conn.commit()
    finally:
        conn.close()

    alice_client = flask_app.test_client()
    assert (
        alice_client.post("/api/auth/login", json={"token": alice_token}).status_code
        == 200
    )
    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )

    response = client.delete("/api/admin/users/usr_alice")
    assert response.status_code == 200

    users = client.get("/api/admin/users").get_json()["users"]
    assert all(user["id"] != "usr_alice" for user in users)
    assert alice_client.get("/api/auth/session").get_json() == {"authenticated": False, "setup_required": False, "private": False}
    assert (
        alice_client.post("/api/auth/login", json={"token": alice_token}).status_code
        == 401
    )
    conn = db.app_db()
    try:
        for table in ("user_pins", "user_item_pins", "user_completions"):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = 'usr_alice'"
            ).fetchone() == (0,), table
    finally:
        conn.close()


def test_admin_cannot_delete_their_own_account(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        admin_token = store.users.create_access_token(conn, "usr_admin")
        conn.commit()
    finally:
        conn.close()

    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )
    response = client.delete("/api/admin/users/usr_admin")
    assert response.status_code == 400


def test_admin_can_regenerate_a_users_token(client, flask_app):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        admin_token = store.users.create_access_token(conn, "usr_admin")
        old_token = store.users.create_access_token(conn, "usr_alice")
        conn.commit()
    finally:
        conn.close()

    alice_client = flask_app.test_client()
    assert (
        alice_client.post("/api/auth/login", json={"token": old_token}).status_code
        == 200
    )
    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )

    response = client.post("/api/admin/users/usr_alice/tokens")
    assert response.status_code == 200
    new_token = response.get_json()["token"]
    assert new_token.startswith("gcm_tok-")
    assert new_token != old_token

    # Old token and its session are dead...
    assert alice_client.get("/api/auth/session").get_json() == {"authenticated": False, "setup_required": False, "private": False}
    assert (
        alice_client.post("/api/auth/login", json={"token": old_token}).status_code
        == 401
    )
    # ...but the new one works and only one active token remains.
    assert (
        alice_client.post("/api/auth/login", json={"token": new_token}).status_code
        == 200
    )
    users = client.get("/api/admin/users").get_json()["users"]
    alice = next(user for user in users if user["id"] == "usr_alice")
    assert len(alice["tokens"]) == 1


def _login_new_admin(client):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        token = store.users.create_access_token(conn, "usr_admin")
        conn.commit()
    finally:
        conn.close()
    assert client.post("/api/auth/login", json={"token": token}).status_code == 200


def test_admin_can_create_another_admin(client):
    _login_new_admin(client)
    response = client.post(
        "/api/admin/users", json={"display_name": "Backup", "role": "admin"}
    )
    assert response.status_code == 201
    assert response.get_json()["user"]["role"] == "admin"


def test_cross_origin_token_revoke_is_rejected(client):
    _login_new_admin(client)
    response = client.post(
        "/api/admin/tokens/tok-anything/revoke",
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"] == "cross-origin request rejected"


def test_cli_new_token_replaces_a_users_tokens(client, capsys):
    conn = db.app_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        old_token = store.users.create_access_token(conn, "usr_admin")
        conn.commit()
    finally:
        conn.close()

    app_module._cli_new_token("Admin")
    new_token = capsys.readouterr().out.strip()

    assert client.post("/api/auth/login", json={"token": old_token}).status_code == 401
    assert client.post("/api/auth/login", json={"token": new_token}).status_code == 200
    with pytest.raises(SystemExit):
        app_module._cli_new_token("Nobody")
