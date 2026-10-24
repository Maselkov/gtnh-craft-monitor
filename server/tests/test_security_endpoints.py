from gcm import security


def test_every_no_store_endpoint_exists(flask_app):
    # The no-store header matches on request.endpoint by name; a typo or a
    # route moved to another blueprint would silently drop it.
    missing = security.NO_STORE_ENDPOINTS - set(flask_app.view_functions)
    assert missing == set()


def every_route(flask_app):
    """(method, url, policies) for every route, with placeholder values
    filled in for URL arguments."""
    adapter = flask_app.url_map.bind("localhost")
    for rule in flask_app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        values = {
            arg: 1 if rule._converters[arg].__class__.__name__ == "IntegerConverter" else "x"
            for arg in rule.arguments
        }
        view = flask_app.view_functions[rule.endpoint]
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            url = adapter.build(rule.endpoint, values, method=method)
            yield method, url, getattr(view, "auth_policies", ())


def test_cross_origin_write_with_a_session_is_rejected_on_every_route(
    client, flask_app
):
    from conftest import login_as

    login_as(client, "usr_admin", role="admin")
    checked = 0
    for method, url, _ in every_route(flask_app):
        if method in security.SAFE_METHODS:
            continue
        response = client.open(
            url, method=method, headers={"Origin": "https://attacker.example"}
        )
        assert response.status_code == 403, (method, url)
        assert response.get_json() == {"error": "cross-origin request rejected"}, (
            method,
            url,
        )
        checked += 1
    assert checked > 0
