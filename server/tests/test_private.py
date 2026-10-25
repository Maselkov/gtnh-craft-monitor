"""A private server (gcm.auth.is_private): its crafts, network and power
only for signed-in users."""

import json
import os
import sqlite3
import subprocess
import sys

from conftest import login_as
from test_security_endpoints import every_route

from gcm import auth

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def viewer_urls(flask_app):
    return [url for method, url, p in every_route(flask_app) if method == "GET" and "viewer" in p]


def test_a_server_is_public_until_made_private(client, flask_app):
    assert client.get("/api/auth/session").get_json()["private"] is False
    for url in viewer_urls(flask_app):
        assert client.get(url).status_code != 401, url


def test_a_private_server_shows_its_data_only_to_signed_in_users(client, flask_app):
    auth.set_private(True)
    urls = viewer_urls(flask_app)
    assert len(urls) == 13
    for url in urls:
        response = client.get(url)
        assert response.status_code == 401, url
        assert response.get_json() == {"error": "authentication required"}

    login_as(client, "usr_viewer", role="viewer")
    assert client.get("/api/auth/session").get_json()["private"] is True
    for url in urls:
        assert client.get(url).status_code != 401, url


def test_the_page_icons_and_signing_in_stay_open(client):
    auth.set_private(True)
    session = client.get("/api/auth/session").get_json()
    assert session["authenticated"] is False and session["private"] is True
    assert client.get("/").status_code == 200
    assert client.get("/static/js/main.js").status_code == 200


def test_the_game_still_reports_to_a_private_server(client, api_headers):
    auth.set_private(True)
    assert client.post("/api/crafts", json={"jobs": []}, headers=api_headers).status_code == 200


def test_link_previews_say_nothing_on_a_private_server(client, api_headers):
    client.post("/api/power", json={"stored": 5e9, "capacity": 1e10}, headers=api_headers)
    assert "EU stored" in client.get("/power").get_data(as_text=True)

    auth.set_private(True)
    page = client.get("/power").get_data(as_text=True)
    assert "EU stored" not in page
    assert "og:image" not in page
    assert '<meta property="og:title" content="GTNH Monitor">' in page


def test_shared_caches_may_not_keep_a_private_servers_data(client):
    assert "private" not in client.get("/api/crafts").headers.get("Cache-Control", "")

    auth.set_private(True)
    login_as(client, "usr_viewer", role="viewer")
    assert "private" in client.get("/api/crafts").headers["Cache-Control"]
    # A chart, otherwise "public, max-age=60".
    chart = client.get("/api/power/chart.png?range=day")
    assert "public" not in chart.headers.get("Cache-Control", "")
    assert "private" in chart.headers["Cache-Control"]


def test_only_admins_change_it(client):
    assert client.put("/api/admin/visibility", json={"private": True}).status_code == 403
    login_as(client, "usr_operator", role="operator")
    assert client.put("/api/admin/visibility", json={"private": True}).status_code == 403
    assert auth.is_private() is False

    login_as(client, "usr_admin", role="admin")
    assert client.put("/api/admin/visibility", json={"private": "yes"}).status_code == 400
    response = client.put("/api/admin/visibility", json={"private": True})
    assert response.get_json() == {"private": True}
    assert auth.is_private() is True
    client.put("/api/admin/visibility", json={"private": False})
    assert auth.is_private() is False


def test_the_command_line_sets_it(tmp_path):
    # As a host does for a base it makes (gcm-hosting).
    def visibility(value):
        return subprocess.run(
            [sys.executable, "app.py", "visibility", value],
            cwd=SERVER_DIR,
            env={**os.environ, "DATA_DIR": str(tmp_path)},
            capture_output=True,
            text=True,
        )

    def stored():
        conn = sqlite3.connect(tmp_path / "app.db")
        try:
            row = conn.execute("SELECT v FROM settings WHERE k = 'private'").fetchone()
        finally:
            conn.close()
        return json.loads(row[0])

    assert visibility("private").returncode == 0
    assert stored() is True
    assert visibility("public").returncode == 0
    assert stored() is False
    assert "usage" in visibility("Private").stderr
