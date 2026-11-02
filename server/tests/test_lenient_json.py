import json

from test_network_api import ITEM


def post_batch(client, api_headers, body):
    """Posts body as one scan's only batch and finishes the scan; the
    batch's response."""
    token = client.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
    res = client.post(
        "/api/network/scan/batch",
        data=body.replace(b"TOKEN", token.encode()),
        headers={**api_headers, "Content-Type": "application/json"},
    )
    client.post(
        "/api/network/scan/finish",
        json={"scan_token": token, "chunks_sent": 1, "total_errors": 0},
        headers=api_headers,
    )
    return res


def batch_with_name(name):
    return json.dumps({"items": [{**ITEM, "name": "NAME"}], "scan_token": "TOKEN"}).encode().replace(
        b"NAME", name
    )


def scanned_names(client):
    return [i["name"] for i in client.get("/api/network").get_json()["items"]]


def test_a_raw_control_character_in_a_string_is_read(client, api_headers, caplog):
    res = post_batch(client, api_headers, batch_with_name(b"Odd\x07 Ingot"))
    assert res.status_code == 200 and res.get_json()["ok"] is True
    assert scanned_names(client) == ["Odd\x07 Ingot"]
    assert "/api/network/scan/batch leniently" in caplog.text
    assert "Odd\\x07 Ingot" in caplog.text


def test_a_formatting_sign_sent_as_one_byte_is_read_as_cp1252(client, api_headers):
    # As one player's game sent "§a16k§r ME Storage Cell".
    res = post_batch(client, api_headers, batch_with_name(b"\xa7a16k\xa7r ME Storage Cell"))
    assert res.get_json()["ok"] is True
    assert scanned_names(client) == ["§a16k§r ME Storage Cell"]


def test_valid_utf8_in_the_same_body_stays_utf8(client, api_headers):
    res = post_batch(client, api_headers, batch_with_name("Café ".encode() + b"\xa7r\x81"))
    assert res.get_json()["ok"] is True
    # \x81 is one of the bytes cp1252 leaves undefined: Latin-1 then.
    assert scanned_names(client) == ["Café §r\x81"]


def test_a_lenient_read_is_logged_once_per_path(client, api_headers, caplog):
    post_batch(client, api_headers, batch_with_name(b"\xa7fPlatinum Frying Pan"))
    post_batch(client, api_headers, batch_with_name(b"\xa7fPlatinum Frying Pan"))
    assert caplog.text.count("leniently") == 1
    assert "Platinum Frying Pan" in caplog.text


def test_valid_json_logs_nothing(client, api_headers, caplog):
    res = post_batch(client, api_headers, batch_with_name(b"Iron Ingot"))
    assert res.get_json()["ok"] is True
    assert "JSON" not in caplog.text


def test_broken_json_is_still_refused_and_logged_every_time(client, api_headers, caplog):
    body = b'{"items": [{"name": "Iron Ingot",, "scan_token": "TOKEN"}'
    assert post_batch(client, api_headers, body).status_code == 400
    assert post_batch(client, api_headers, body).status_code == 400
    assert caplog.text.count("Unreadable JSON sent to /api/network/scan/batch") == 2
