"""OAuth for MCP clients (gcm/oauth.py, routes/oauth.py,
store/oauth.py): discovery, registration, consent, tokens, refresh and
revocation, as claude.ai and Claude Code go through them."""

import base64
import hashlib
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import login_as
from gcm import store
from gcm.store import oauth as oauth_store
from test_mcp import make_token

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
LOOPBACK = "http://localhost/callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
BASE = "http://localhost"


def register(client, redirect_uris=(CALLBACK,), name="Claude"):
    res = client.post("/oauth/register", json={"client_name": name, "redirect_uris": list(redirect_uris)})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["client_id"]


def authorize_params(client_id, redirect_uri=CALLBACK, **extra):
    return {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code_challenge": CHALLENGE, "code_challenge_method": "S256", "state": "xyz",
            "resource": BASE + "/mcp", **extra}


def form_value(page):
    return re.search(r'name="form" value="([0-9a-f]+)"', page).group(1)


def consent(client, params, action="allow"):
    """Opens the consent page as the signed-in user and answers it."""
    page = client.get("/oauth/authorize", query_string=params)
    assert page.status_code == 200
    return client.post("/oauth/authorize", data={**params, "form": form_value(page.text), "action": action})


def query_of(response):
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["Location"]).query).items()}


def get_code(client, client_id, redirect_uri=CALLBACK, user="olive", role="operator"):
    login_as(client, user, role=role)
    res = consent(client, authorize_params(client_id, redirect_uri))
    assert res.status_code == 302
    assert res.headers["Location"].startswith(redirect_uri.split("/callback")[0])
    return query_of(res)["code"]


def exchange(client, client_id, code, redirect_uri=CALLBACK, verifier=VERIFIER):
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": redirect_uri, "code_verifier": verifier, "resource": BASE + "/mcp",
    })


def tokens_for(client, **kwargs):
    client_id = register(client)
    res = exchange(client, client_id, get_code(client, client_id, **kwargs))
    assert res.status_code == 200, res.get_json()
    return client_id, res.get_json()


def mcp_status(client, access_token):
    client.delete_cookie("gcm_session")
    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                      headers={"Authorization": f"Bearer {access_token}"})
    return res.status_code


class TestDiscovery:
    def test_a_401_points_at_the_resource_metadata(self, client):
        res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert res.status_code == 401
        assert res.headers["WWW-Authenticate"] == (
            f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource"'
        )
        bad = client.post("/mcp", json={}, headers={"Authorization": "Bearer gcmat_nope"})
        assert 'error="invalid_token"' in bad.headers["WWW-Authenticate"]

    def test_resource_metadata(self, client):
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            data = client.get(path).get_json()
            assert data["resource"] == BASE + "/mcp"
            assert data["authorization_servers"] == [BASE]

    def test_authorization_server_metadata(self, client):
        res = client.get("/.well-known/oauth-authorization-server")
        data = res.get_json()
        assert data["issuer"] == BASE
        assert data["registration_endpoint"] == BASE + "/oauth/register"
        assert data["code_challenge_methods_supported"] == ["S256"]
        assert data["token_endpoint_auth_methods_supported"] == ["none"]
        assert res.headers["Access-Control-Allow-Origin"] == "*"

    def test_public_url_wins(self, client, monkeypatch):
        from gcm import config

        monkeypatch.setattr(config, "PUBLIC_URL", "https://monitor.example.com")
        assert client.get("/.well-known/oauth-authorization-server").get_json()["issuer"] == (
            "https://monitor.example.com"
        )


class TestRegistration:
    def test_registers_a_public_client(self, client):
        res = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CALLBACK]})
        assert res.status_code == 201
        data = res.get_json()
        assert data["client_id"].startswith("gcmc_") and data["token_endpoint_auth_method"] == "none"
        assert store.oauth.client(data["client_id"])["redirect_uris"] == [CALLBACK]

    @pytest.mark.parametrize("uris", [
        [], ["http://example.com/cb"], ["ftp://x/cb"], ["https://x/cb#frag"], ["not a url"], "https://x/cb",
    ])
    def test_refuses_bad_redirect_uris(self, client, uris):
        res = client.post("/oauth/register", json={"redirect_uris": uris})
        assert res.status_code == 400 and res.get_json()["error"] == "invalid_redirect_uri"

    def test_loopback_http_is_fine(self, client):
        for uri in ("http://localhost/callback", "http://127.0.0.1:3118/callback", "http://[::1]/cb"):
            assert client.post("/oauth/register", json={"redirect_uris": [uri]}).status_code == 201

    def test_refuses_confidential_clients(self, client):
        res = client.post("/oauth/register", json={"redirect_uris": [CALLBACK],
                                                   "token_endpoint_auth_method": "client_secret_basic"})
        assert res.status_code == 400

    def test_too_many_in_an_hour(self, client, monkeypatch):
        monkeypatch.setattr(oauth_store, "CLIENTS_PER_HOUR", 2)
        register(client)
        register(client)
        assert client.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 429

    def test_unused_old_clients_are_dropped(self, client, monkeypatch):
        old = register(client)
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 2 * 86400)
        register(client)
        assert store.oauth.client(old) is None


class TestConsent:
    def test_signed_out_it_asks_for_a_token_then_shows_the_consent(self, client):
        client_id = register(client)
        params = authorize_params(client_id)
        page = client.get("/oauth/authorize", query_string=params)
        assert page.status_code == 200 and 'name="token"' in page.text
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]

        bad = client.post("/oauth/authorize", data={**params, "action": "signin", "token": "gcm_tok-x_y"})
        assert bad.status_code == 401 and "That token didn&#x27;t work" in bad.text

        token = make_token("olive", role="operator")
        res = client.post("/oauth/authorize", data={**params, "action": "signin", "token": token})
        assert res.status_code == 303 and res.headers["Location"].startswith("/oauth/authorize?")
        page = client.get(res.headers["Location"])
        assert "Connect Claude?" in page.text and "olive" in page.text and "read the base" in page.text
        # The form's POST redirects to the app: form-action has to allow it.
        assert "form-action 'self' https://claude.ai" in page.headers["Content-Security-Policy"]

    def test_allow_sends_a_code_and_the_state_back(self, client):
        client_id = register(client)
        login_as(client, "olive", role="viewer")
        res = consent(client, authorize_params(client_id))
        assert res.status_code == 302
        back = query_of(res)
        assert res.headers["Location"].startswith(CALLBACK + "?")
        assert back["state"] == "xyz" and back["iss"] == BASE and back["code"]

    def test_deny(self, client):
        client_id = register(client)
        login_as(client, "olive")
        back = query_of(consent(client, authorize_params(client_id), action="deny"))
        assert back["error"] == "access_denied" and "code" not in back

    def test_a_forged_form_is_refused(self, client):
        client_id = register(client)
        login_as(client, "olive")
        params = authorize_params(client_id)
        res = client.post("/oauth/authorize", data={**params, "form": "0" * 64, "action": "allow"})
        assert res.status_code == 403

    def test_another_sites_post_is_refused(self, client):
        client_id = register(client)
        login_as(client, "olive")
        params = authorize_params(client_id)
        page = client.get("/oauth/authorize", query_string=params)
        res = client.post("/oauth/authorize", data={**params, "form": form_value(page.text), "action": "allow"},
                          headers={"Origin": "https://evil.example"})
        assert res.status_code == 403

    def test_unknown_client_or_redirect_is_an_error_page_not_a_redirect(self, client):
        client_id = register(client)
        res = client.get("/oauth/authorize", query_string=authorize_params("gcmc_nope"))
        assert res.status_code == 400 and "Location" not in res.headers
        res = client.get("/oauth/authorize", query_string=authorize_params(client_id, "https://evil.example/cb"))
        assert res.status_code == 400 and "Location" not in res.headers

    @pytest.mark.parametrize("change,error", [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge": ""}, "invalid_request"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
    ])
    def test_bad_requests_are_sent_back_with_an_error(self, client, change, error):
        client_id = register(client)
        res = client.get("/oauth/authorize", query_string=authorize_params(client_id, **change))
        assert res.status_code == 302 and query_of(res)["error"] == error

    def test_loopback_redirects_match_any_port_and_warn(self, client):
        client_id = register(client, [LOOPBACK])
        login_as(client, "olive")
        params = authorize_params(client_id, "http://localhost:3118/callback")
        page = client.get("/oauth/authorize", query_string=params)
        assert "an app on this computer" in page.text
        assert "form-action 'self' http://localhost:3118" in page.headers["Content-Security-Policy"]
        res = client.post("/oauth/authorize", data={**params, "form": form_value(page.text), "action": "allow"})
        assert res.headers["Location"].startswith("http://localhost:3118/callback?")
        other_path = authorize_params(client_id, "http://localhost:3118/other")
        assert client.get("/oauth/authorize", query_string=other_path).status_code == 400


class TestTokens:
    def test_the_full_flow_signs_mcp_in_as_the_user(self, client):
        _, tokens = tokens_for(client, role="viewer")
        assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
        assert tokens["access_token"].startswith("gcmat_") and tokens["refresh_token"].startswith("gcmrt_")
        assert mcp_status(client, tokens["access_token"]) == 200

    def test_a_code_works_once(self, client):
        client_id = register(client)
        code = get_code(client, client_id)
        assert exchange(client, client_id, code).status_code == 200
        res = exchange(client, client_id, code)
        assert res.status_code == 400 and res.get_json()["error"] == "invalid_grant"

    def test_the_verifier_must_match(self, client):
        client_id = register(client)
        res = exchange(client, client_id, get_code(client, client_id), verifier="w" * 64)
        assert res.status_code == 400 and res.get_json()["error"] == "invalid_grant"

    def test_the_redirect_uri_and_client_must_match(self, client):
        client_id = register(client)
        other = register(client)
        code = get_code(client, client_id)
        assert exchange(client, client_id, code, redirect_uri="https://claude.ai/other").status_code == 400
        code = get_code(client, client_id)
        assert exchange(client, other, code).status_code == 400
        assert exchange(client, "gcmc_nope", code).status_code == 401

    def test_an_expired_code(self, client, monkeypatch):
        client_id = register(client)
        code = get_code(client, client_id)
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + oauth_store.CODE_SECONDS + 1)
        assert exchange(client, client_id, code).status_code == 400

    def test_an_access_token_expires(self, client, monkeypatch):
        _, tokens = tokens_for(client)
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + oauth_store.ACCESS_SECONDS + 1)
        assert mcp_status(client, tokens["access_token"]) == 401

    def test_refresh_rotates_and_a_reused_refresh_token_revokes_the_grant(self, client):
        client_id, tokens = tokens_for(client)
        refresh = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": tokens["refresh_token"]}
        res = client.post("/oauth/token", data=refresh)
        assert res.status_code == 200
        new = res.get_json()
        assert new["refresh_token"] != tokens["refresh_token"]
        assert mcp_status(client, new["access_token"]) == 200

        replay = client.post("/oauth/token", data=refresh)
        assert replay.status_code == 400 and replay.get_json()["error"] == "invalid_grant"
        assert mcp_status(client, new["access_token"]) == 401
        again = {**refresh, "refresh_token": new["refresh_token"]}
        assert client.post("/oauth/token", data=again).status_code == 400

    def test_refresh_needs_the_same_client(self, client):
        _, tokens = tokens_for(client)
        other = register(client)
        res = client.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": other,
                                                "refresh_token": tokens["refresh_token"]})
        assert res.status_code == 400

    def test_unsupported_grant_type(self, client):
        client_id = register(client)
        res = client.post("/oauth/token", data={"grant_type": "client_credentials", "client_id": client_id})
        assert res.get_json()["error"] == "unsupported_grant_type"


class TestRevocation:
    def test_revoking_the_login_token_disconnects_the_app(self, client):
        _, tokens = tokens_for(client)
        user = store.users.all_with_tokens()[0]
        for token in user["tokens"]:
            store.users.revoke_token(token["id"])
        assert mcp_status(client, tokens["access_token"]) == 401

    def test_new_token_disconnects_every_app(self, client):
        _, tokens = tokens_for(client)
        store.users.replace_credentials("olive")
        assert mcp_status(client, tokens["access_token"]) == 401

    def test_deleting_the_user(self, client):
        _, tokens = tokens_for(client)
        store.users.delete("olive")
        assert mcp_status(client, tokens["access_token"]) == 401

    def test_admins_see_and_disconnect_apps(self, client):
        _, tokens = tokens_for(client)
        login_as(client, "ada", role="admin")
        users = {u["id"]: u for u in client.get("/api/admin/users").get_json()["users"]}
        apps = users["olive"]["apps"]
        assert [a["client_name"] for a in apps] == ["Claude"] and users["ada"]["apps"] == []
        assert client.post(f"/api/admin/apps/{apps[0]['id']}/revoke").status_code == 200
        assert client.post(f"/api/admin/apps/{apps[0]['id']}/revoke").status_code == 404
        assert mcp_status(client, tokens["access_token"]) == 401

    def test_only_admins_disconnect(self, client):
        login_as(client, "olive", role="operator")
        assert client.post("/api/admin/apps/grant_x/revoke").status_code == 403
