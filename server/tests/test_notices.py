from conftest import login_as
from gcm import notices


def test_a_dismissed_notice_holds_for_every_admin(flask_app):
    first = login_as(flask_app.test_client(), "usr_a", "admin")
    response = first.put("/api/notices/scripts_update", json={"value": "7.2.0"})
    assert response.status_code == 200
    assert response.get_json() == {"dismissed": {"scripts_update": "7.2.0"}}
    other = login_as(flask_app.test_client(), "usr_b", "admin")
    assert other.get("/api/notices").get_json() == {"dismissed": {"scripts_update": "7.2.0"}}


def test_dismissing_again_replaces_the_value(client):
    login_as(client, "usr_a", "admin")
    client.put("/api/notices/scripts_update", json={"value": "7.1.0"})
    client.put("/api/notices/scripts_update", json={"value": "7.2.0"})
    client.put("/api/notices/terminal", json={"value": "1"})
    assert client.get("/api/notices").get_json() == {"dismissed": {"scripts_update": "7.2.0", "terminal": "1"}}


def test_bad_requests_are_refused(client):
    login_as(client, "usr_a", "admin")
    assert client.put("/api/notices/nonsense", json={"value": "1"}).status_code == 400
    for body in ({}, {"value": ""}, {"value": 3}, {"value": "x" * (notices.MAX_VALUE_LENGTH + 1)}):
        assert client.put("/api/notices/gamedata", json=body).status_code == 400
    assert client.get("/api/notices").get_json() == {"dismissed": {}}


def test_operators_share_the_pattern_check_notice_only(flask_app):
    admin = login_as(flask_app.test_client(), "usr_a", "admin")
    admin.put("/api/notices/terminal", json={"value": "1"})
    operator = login_as(flask_app.test_client(), "usr_op", "operator")
    # They fix patterns in game, so the pattern check is theirs too.
    assert operator.put("/api/notices/recipe_check", json={"value": "2.9.0-RC-1>2.9.0"}).status_code == 200
    assert operator.put("/api/notices/terminal", json={"value": "1"}).status_code == 403
    assert operator.get("/api/notices").get_json() == {"dismissed": {"recipe_check": "2.9.0-RC-1>2.9.0"}}
    assert admin.get("/api/notices").get_json() == {
        "dismissed": {"recipe_check": "2.9.0-RC-1>2.9.0", "terminal": "1"},
    }


def test_not_viewers(client):
    assert client.get("/api/notices").status_code == 401
    login_as(client, "usr_v", "viewer")
    assert client.get("/api/notices").status_code == 403
    assert client.put("/api/notices/recipe_check", json={"value": "1"}).status_code == 403
