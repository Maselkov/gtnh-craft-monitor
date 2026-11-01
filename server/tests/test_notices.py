from conftest import login_as
from gcm import notices


def test_a_dismissed_notice_holds_for_every_admin(flask_app):
    first = login_as(flask_app.test_client(), "usr_a", "admin")
    response = first.put("/api/admin/notices/recipe_check", json={"value": "2.9.0-RC-1>2.9.0"})
    assert response.status_code == 200
    assert response.get_json() == {"dismissed": {"recipe_check": "2.9.0-RC-1>2.9.0"}}
    other = login_as(flask_app.test_client(), "usr_b", "admin")
    assert other.get("/api/admin/notices").get_json() == {"dismissed": {"recipe_check": "2.9.0-RC-1>2.9.0"}}


def test_dismissing_again_replaces_the_value(client):
    login_as(client, "usr_a", "admin")
    client.put("/api/admin/notices/scripts_update", json={"value": "7.1.0"})
    client.put("/api/admin/notices/scripts_update", json={"value": "7.2.0"})
    client.put("/api/admin/notices/terminal", json={"value": "1"})
    assert notices.dismissed() == {"scripts_update": "7.2.0", "terminal": "1"}


def test_bad_requests_are_refused(client):
    login_as(client, "usr_a", "admin")
    assert client.put("/api/admin/notices/nonsense", json={"value": "1"}).status_code == 400
    for body in ({}, {"value": ""}, {"value": 3}, {"value": "x" * (notices.MAX_VALUE_LENGTH + 1)}):
        assert client.put("/api/admin/notices/gamedata", json=body).status_code == 400
    assert notices.dismissed() == {}


def test_only_admins(client):
    assert client.get("/api/admin/notices").status_code in (401, 403)
    login_as(client, "usr_op", "operator")
    assert client.get("/api/admin/notices").status_code == 403
    assert client.put("/api/admin/notices/terminal", json={"value": "1"}).status_code == 403
