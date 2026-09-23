import time

import pytest

import app as app_module


def test_access_token_resolves_stable_user_id():
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        token = app_module._create_access_token(conn, "usr_alice")
        conn.commit()

        identity = app_module._find_access_token(conn, token)
    finally:
        conn.close()

    assert identity == {
        "id": identity["id"],
        "user_id": "usr_alice",
        "is_bootstrap": False,
    }
    assert identity["id"].startswith("tok-")


def test_invalid_access_token_is_rejected():
    conn = app_module._craft_db()
    try:
        token = "gcm_tok_unknown_not-a-real-secret"
        assert app_module._find_access_token(conn, token) is None
    finally:
        conn.close()


def test_login_exchanges_access_token_for_session_cookie(client):
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "operator", time.time()),
        )
        token = app_module._create_access_token(conn, "usr_alice")
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

    assert response.status_code == 400


def test_disabling_a_user_invalidates_existing_sessions(client):
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "viewer", time.time()),
        )
        token = app_module._create_access_token(conn, "usr_alice")
        conn.commit()
    finally:
        conn.close()

    assert client.post("/api/auth/login", json={"token": token}).status_code == 200
    conn = app_module._craft_db()
    try:
        conn.execute(
            "UPDATE users SET disabled_at = ? WHERE id = ?", (time.time(), "usr_alice")
        )
        conn.commit()
    finally:
        conn.close()

    assert client.get("/api/auth/session").get_json() == {"authenticated": False}


def test_only_admins_can_list_or_create_users(client):
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_viewer", "Viewer", "viewer", time.time()),
        )
        viewer_token = app_module._create_access_token(conn, "usr_viewer")
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

    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        admin_token = app_module._create_access_token(conn, "usr_admin")
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


def test_initial_admin_is_required_for_direct_server_startup():
    with pytest.raises(RuntimeError, match="No users exist"):
        app_module._require_initial_admin()


def test_bootstrap_token_must_be_rotated_before_admin_actions(client):
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        bootstrap_token = app_module._create_access_token(
            conn, "usr_admin", is_bootstrap=True
        )
        conn.commit()
    finally:
        conn.close()

    login = client.post("/api/auth/login", json={"token": bootstrap_token})
    assert login.get_json()["must_rotate_bootstrap"] is True
    assert client.get("/api/admin/users").status_code == 403

    rotation = client.post("/api/auth/rotate-bootstrap")
    assert rotation.status_code == 200
    replacement_token = rotation.get_json()["token"]
    assert replacement_token.startswith("gcm_tok-")
    assert client.get("/api/admin/users").status_code == 200

    other_client = app_module.app.test_client()
    assert (
        other_client.post(
            "/api/auth/login", json={"token": bootstrap_token}
        ).status_code
        == 401
    )


def test_existing_bootstrap_session_is_revoked_during_startup_recognition(monkeypatch):
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        bootstrap_token = app_module._create_access_token(conn, "usr_admin")
        access_token = app_module._find_access_token(conn, bootstrap_token)
        app_module._create_session(conn, access_token)
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv("GCM_BOOTSTRAP_ADMIN_TOKEN", bootstrap_token)
    app_module._bootstrap_admin()

    conn = app_module._craft_db()
    try:
        token_row = conn.execute(
            "SELECT is_bootstrap FROM access_tokens WHERE id = ?", (access_token["id"],)
        ).fetchone()
        session_row = conn.execute(
            "SELECT revoked_at FROM sessions WHERE access_token_id = ?",
            (access_token["id"],),
        ).fetchone()
    finally:
        conn.close()

    assert token_row == (1,)
    assert session_row[0] is not None


def test_default_or_short_service_key_is_rejected(monkeypatch):
    monkeypatch.setattr(app_module, "API_KEY", "change-me")
    with pytest.raises(RuntimeError, match="API_KEY must be a unique secret"):
        app_module._require_runtime_secrets()

    monkeypatch.setattr(app_module, "API_KEY", "too-short")
    with pytest.raises(RuntimeError, match="API_KEY must be a unique secret"):
        app_module._require_runtime_secrets()


def test_debug_dumps_require_an_operator_session(client):
    assert client.get("/api/debug").status_code == 403

    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_operator", "Operator", "operator", time.time()),
        )
        token = app_module._create_access_token(conn, "usr_operator")
        conn.commit()
    finally:
        conn.close()

    assert client.post("/api/auth/login", json={"token": token}).status_code == 200
    assert client.get("/api/debug").status_code == 200


def test_cross_origin_session_writes_are_rejected(client):
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_alice", "Alice", "viewer", time.time()),
        )
        token = app_module._create_access_token(conn, "usr_alice")
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
        app_module,
        "TRUSTED_PROXY_NETWORKS",
        [app_module.ipaddress.ip_network("10.0.0.0/8")],
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


def test_admin_can_revoke_a_token_and_its_sessions(client):
    conn = app_module._craft_db()
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
        admin_token = app_module._create_access_token(conn, "usr_admin")
        operator_token = app_module._create_access_token(conn, "usr_operator")
        operator_token_id = app_module._find_access_token(conn, operator_token)["id"]
        conn.commit()
    finally:
        conn.close()

    operator_client = app_module.app.test_client()
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
        "authenticated": False
    }
    assert (
        operator_client.post(
            "/api/auth/login", json={"token": operator_token}
        ).status_code
        == 401
    )


def test_admin_can_view_user_action_history(client):
    conn = app_module._craft_db()
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
        admin_token = app_module._create_access_token(conn, "usr_admin")
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
    conn = app_module._craft_db()
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
        admin_token = app_module._create_access_token(conn, "usr_admin")
        alice_token = app_module._create_access_token(conn, "usr_alice")
        alice_token_id = app_module._find_access_token(conn, alice_token)["id"]
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


def test_admin_can_delete_a_user_and_their_credentials(client):
    conn = app_module._craft_db()
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
        admin_token = app_module._create_access_token(conn, "usr_admin")
        alice_token = app_module._create_access_token(conn, "usr_alice")
        conn.commit()
    finally:
        conn.close()

    alice_client = app_module.app.test_client()
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
    assert alice_client.get("/api/auth/session").get_json() == {"authenticated": False}
    assert (
        alice_client.post("/api/auth/login", json={"token": alice_token}).status_code
        == 401
    )


def test_admin_cannot_delete_their_own_account(client):
    conn = app_module._craft_db()
    try:
        conn.execute(
            "INSERT INTO users (id, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("usr_admin", "Admin", "admin", time.time()),
        )
        admin_token = app_module._create_access_token(conn, "usr_admin")
        conn.commit()
    finally:
        conn.close()

    assert (
        client.post("/api/auth/login", json={"token": admin_token}).status_code == 200
    )
    response = client.delete("/api/admin/users/usr_admin")
    assert response.status_code == 400


def test_admin_can_regenerate_a_users_token(client):
    conn = app_module._craft_db()
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
        admin_token = app_module._create_access_token(conn, "usr_admin")
        old_token = app_module._create_access_token(conn, "usr_alice")
        conn.commit()
    finally:
        conn.close()

    alice_client = app_module.app.test_client()
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
    assert alice_client.get("/api/auth/session").get_json() == {"authenticated": False}
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
