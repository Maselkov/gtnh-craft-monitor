from conftest import login_as

# Changing this set should be a deliberate, reviewed decision - a route
# that serves or changes anything user-specific must never end up here.
PUBLIC_ENDPOINTS = {
    "crafts.crafts_get",
    "power.power_get",
    "power.power_chart_png",
    "network.network_get",
    "network.network_history_get",
    "network.network_history_chart_png",
    "pages.icon",
    "pages.index",
    "users.auth_session_get",
    "users.auth_login_post",
    "users.auth_logout_post",
}


def policies(flask_app):
    return {
        endpoint: getattr(view, "auth_policies", ())
        for endpoint, view in flask_app.view_functions.items()
        if endpoint != "static"
    }


def test_every_route_declares_an_auth_policy(flask_app):
    # A new route without one of the gcm.auth decorators fails here
    # instead of silently shipping unauthenticated.
    missing = sorted(e for e, p in policies(flask_app).items() if not p)
    assert missing == []


def test_public_routes_are_exactly_the_reviewed_list(flask_app):
    public = {e for e, p in policies(flask_app).items() if "public" in p}
    assert public == PUBLIC_ENDPOINTS


def test_api_key_routes_reject_missing_and_wrong_keys(client, api_headers):
    assert client.post("/api/crafts", json={}).status_code == 401
    assert client.post("/api/crafts", json={}, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/api/crafts", json={"jobs": []}, headers=api_headers).status_code == 200


def test_login_then_role_order(client):
    # login_required stacked over operator_required: anonymous is 401,
    # a signed-in viewer is 403.
    body = {"label": "Iron Ingot", "internal": "iron_ingot", "amount": 1}
    response = client.post("/api/craft/request", json=body)
    assert response.status_code == 401
    assert response.get_json() == {"error": "authentication required"}

    login_as(client, "usr_viewer", role="viewer")
    response = client.post("/api/craft/request", json=body)
    assert response.status_code == 403
    assert response.get_json() == {"error": "operator access required"}


def test_admin_routes_refuse_non_admins(client):
    assert client.get("/api/admin/users").status_code == 403
    login_as(client, "usr_operator", role="operator")
    response = client.get("/api/admin/users")
    assert response.status_code == 403
    assert response.get_json() == {"error": "administrator access required"}
    login_as(client, "usr_admin", role="admin")
    assert client.get("/api/admin/users").status_code == 200
