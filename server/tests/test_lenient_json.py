import json

from test_network_api import ITEM


def post_batch(client, api_headers, body):
    token = client.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
    body = body.replace(b"TOKEN", token.encode())
    return client.post(
        "/api/network/scan/batch",
        data=body,
        headers={**api_headers, "Content-Type": "application/json"},
    )


def batch_with_name(name):
    return json.dumps({"items": [{**ITEM, "name": "NAME"}], "scan_token": "TOKEN"}).encode().replace(
        b"NAME", name
    )


def test_a_raw_control_character_in_a_string_is_read(client, api_headers, caplog):
    res = post_batch(client, api_headers, batch_with_name(b"Odd\x07 Ingot"))
    assert res.status_code == 200 and res.get_json()["ok"] is True
    assert "/api/network/scan/batch leniently" in caplog.text
    assert "Odd\\x07 Ingot" in caplog.text


def test_invalid_utf8_in_a_string_is_replaced(client, api_headers):
    res = post_batch(client, api_headers, batch_with_name(b"Odd\xff Ingot"))
    assert res.status_code == 200 and res.get_json()["ok"] is True


def test_valid_json_logs_nothing(client, api_headers, caplog):
    res = post_batch(client, api_headers, batch_with_name(b"Iron Ingot"))
    assert res.get_json()["ok"] is True
    assert "JSON" not in caplog.text


def test_broken_json_is_still_refused_and_logged(client, api_headers, caplog):
    res = post_batch(client, api_headers, b'{"items": [{"name": "Iron Ingot",, "scan_token": "TOKEN"}')
    assert res.status_code == 400
    assert "Unreadable JSON sent to /api/network/scan/batch" in caplog.text
