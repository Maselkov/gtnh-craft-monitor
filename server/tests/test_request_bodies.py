"""Request bodies that aren't what a route expects: a JSON body that
isn't an object, and fields of the wrong type, are refused with a 4xx
rather than a 500 (gcm/request_body.py)."""

import json
import re

import pytest

from gcm import commands
from conftest import login_as
from test_craft_requests_api import valid_request_payload


def write_routes(app):
    """(method, url) of every route that takes a body, with any URL
    parameter filled in."""
    routes = []
    for rule in app.url_map.iter_rules():
        for method in sorted(rule.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            routes.append((method, re.sub(r"<(?:\w+:)?(\w+)>", "1", rule.rule)))
    return sorted(set(routes))


@pytest.mark.parametrize("body", ["[1]", '"text"', "5", "null"])
def test_no_route_fails_on_a_body_that_isnt_an_object(flask_app, client, api_headers, body):
    failures = []
    for method, url in write_routes(flask_app):
        # Signed in afresh each time: the sign-out route ends the session.
        login_as(client, "usr_admin", role="admin")
        res = client.open(url, method=method, data=body, content_type="application/json", headers=api_headers)
        if res.status_code >= 500:
            failures.append(f"{method} {url}: {res.status_code}")
    assert failures == []


class TestWrongTypes:
    def test_sign_in_with_a_list(self, client):
        assert client.post("/api/auth/login", json=[1]).status_code == 401

    def test_user_display_name_that_isnt_text(self, client):
        login_as(client, "usr_admin", role="admin")
        res = client.post("/api/admin/users", json={"display_name": 5, "role": "viewer"})
        assert res.status_code == 400

    def test_craft_request_internal_that_isnt_text(self, client):
        login_as(client, "usr_operator", role="operator")
        assert client.post("/api/craft/request", json=valid_request_payload(internal=["a"])).status_code == 400
        assert client.post("/api/craft/request", json=valid_request_payload(label=5)).status_code == 400
        assert client.post("/api/craft/request", json=valid_request_payload(mod={"a": 1})).status_code == 400

    def test_item_pin_with_a_list(self, client):
        login_as(client, "usr_viewer")
        res = client.post("/api/network/pins", json={"mod": "minecraft", "internal": ["a"], "damage": 0})
        assert res.status_code == 400
        res = client.post("/api/network/pins", json={"mod": ["x"], "internal": "iron_ingot", "damage": 0})
        assert res.status_code == 400
        res = client.post("/api/network/pins", json={"mod": "minecraft", "internal": "iron_ingot", "damage": 0})
        assert res.status_code == 200

    @pytest.mark.parametrize("items", ["x", [1], {"a": 1}])
    def test_scan_batch_items_that_arent_stacks(self, client, api_headers, items):
        res = client.post("/api/network/scan/batch", json={"scan_token": "t", "items": items}, headers=api_headers)
        assert res.status_code == 400


class TestCraftAmounts:
    def post(self, client, amount):
        login_as(client, "usr_operator", role="operator")
        return client.post("/api/craft/request", json=valid_request_payload(amount=amount))

    @pytest.mark.parametrize("amount", [2.5, 0, -3, commands.MAX_CRAFT_AMOUNT + 1, "5", True])
    def test_refused(self, client, amount):
        res = self.post(client, amount)
        assert res.status_code == 400
        assert "whole number" in res.get_json()["error"]

    def test_infinity_is_refused_rather_than_breaking_the_games_poll(self, client, api_headers):
        # JSON's 1e400 reads as infinity, which jsonify would send to the
        # game as Infinity: not JSON, so its whole /pending poll was lost.
        login_as(client, "usr_operator", role="operator")
        payload = json.dumps(valid_request_payload()).replace('"amount": 5', '"amount": 1e400')
        res = client.post("/api/craft/request", data=payload, content_type="application/json")
        assert res.status_code == 400
        assert client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"] == []

    def test_largest_is_taken(self, client):
        assert self.post(client, commands.MAX_CRAFT_AMOUNT).status_code == 200

    def test_a_whole_float_is_sent_on_as_a_whole_number(self, client, api_headers):
        assert self.post(client, 5.0).status_code == 200
        [req] = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
        assert req["amount"] == 5 and isinstance(req["amount"], int)

    def test_batch_rows_are_checked_too(self, client):
        login_as(client, "usr_operator", role="operator")
        rows = [valid_request_payload(amount=1), valid_request_payload(amount=1.5)]
        assert client.post("/api/craft/request-batch", json={"requests": rows}).status_code == 400
