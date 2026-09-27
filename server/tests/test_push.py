import base64
import os
import stat

import pytest
from pywebpush import WebPushException

from gcm import config, push, store
from conftest import login_as

FCM = "https://fcm.googleapis.com/fcm/send/abc123"


def subscription(endpoint=FCM):
    return {"endpoint": endpoint, "keys": {"p256dh": "BPkey", "auth": "authsecret"}}


@pytest.fixture()
def sent(monkeypatch):
    """Records pushes instead of sending them."""
    calls = []
    monkeypatch.setattr(push, "_send", lambda sub, data: calls.append((sub["endpoint"], data)))
    return calls


def post_job(client, api_headers, busy, cpu_name="W01", label="Iron Ingot"):
    job = {"name": cpu_name, "busy": busy}
    if not busy:
        job["final_output"] = label
    client.post("/api/crafts", json={"jobs": [job]}, headers=api_headers)


def test_key_file_is_private_and_public_key_is_a_raw_p256_point():
    mode = stat.S_IMODE(os.stat(config.VAPID_KEY_PATH).st_mode)
    assert mode == 0o600
    key = push.public_key()
    raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    assert len(raw) == 65 and raw[0] == 4


def test_key_survives_reload():
    before = push.public_key()
    push.load_keys()
    assert push.public_key() == before


@pytest.mark.parametrize(
    "endpoint,allowed",
    [
        (FCM, True),
        ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
        ("https://web.push.apple.com/abc", True),
        ("https://wns2-par02p.notify.windows.com/w/?token=x", True),
        ("http://fcm.googleapis.com/fcm/send/x", False),
        ("https://fcm.googleapis.com:8443/x", False),
        ("https://evilfcm.googleapis.com.attacker.test/x", False),
        ("https://notify.windows.com.attacker.test/x", False),
        ("https://127.0.0.1/x", False),
        ("not a url", False),
    ],
)
def test_endpoint_allowlist(endpoint, allowed):
    assert push.endpoint_allowed(endpoint) is allowed


def test_routes_require_sign_in(client):
    assert client.get("/api/push/key").status_code == 401
    assert client.post("/api/push/subscribe", json=subscription()).status_code == 401
    assert client.post("/api/push/unsubscribe", json={"endpoint": FCM}).status_code == 401


def test_subscribe_validates(client):
    login_as(client, "alice")
    assert client.get("/api/push/key").get_json()["publicKey"] == push.public_key()
    assert client.post("/api/push/subscribe", json={"endpoint": FCM}).status_code == 400
    response = client.post("/api/push/subscribe", json=subscription("https://internal.lan/hook"))
    assert response.status_code == 400
    assert store.push.subscriptions_for(["alice"]) == []


def test_subscribe_then_unsubscribe(client):
    login_as(client, "alice")
    assert client.post("/api/push/subscribe", json=subscription()).status_code == 200
    [saved] = store.push.subscriptions_for(["alice"])
    assert saved["endpoint"] == FCM
    assert saved["origin"] == "http://localhost"

    # Only the owner can remove it.
    login_as(client, "bob")
    client.post("/api/push/unsubscribe", json={"endpoint": FCM})
    assert len(store.push.subscriptions_for(["alice"])) == 1

    login_as(client, "alice")
    client.post("/api/push/unsubscribe", json={"endpoint": FCM})
    assert store.push.subscriptions_for(["alice"]) == []


def test_same_browser_moves_to_whoever_signs_in(client):
    login_as(client, "alice")
    client.post("/api/push/subscribe", json=subscription())
    login_as(client, "bob")
    client.post("/api/push/subscribe", json=subscription())
    assert store.push.subscriptions_for(["alice"]) == []
    assert len(store.push.subscriptions_for(["bob"])) == 1


def test_finished_pinned_craft_is_pushed_to_its_pinner_only(client, api_headers, sent):
    post_job(client, api_headers, busy=True)
    login_as(client, "bob")
    client.post("/api/push/subscribe", json=subscription("https://fcm.googleapis.com/fcm/send/bob"))
    login_as(client, "alice")
    client.post("/api/push/subscribe", json=subscription())
    client.post("/api/pins", json={"cpu_name": "W01"})

    post_job(client, api_headers, busy=False)
    push.wait_idle()

    [completion] = client.get("/api/completions").get_json()["completions"]
    assert sent == [(FCM, (
        '{"title": "Craft finished", "body": "Iron Ingot is done crafting.", '
        f'"tag": "craft-completion-{completion["id"]}"}}'
    ))]


def test_gone_subscription_is_deleted(client, monkeypatch):
    login_as(client, "alice")
    client.post("/api/push/subscribe", json=subscription())

    class Gone:
        status_code = 410

    def refuse(sub, data):
        raise WebPushException("gone", response=Gone())

    monkeypatch.setattr(push, "_send", refuse)
    push.deliver([("alice", 1)], "Iron Ingot", "finished")
    assert store.push.subscriptions_for(["alice"]) == []


def test_other_failures_keep_the_subscription(client, monkeypatch):
    login_as(client, "alice")
    client.post("/api/push/subscribe", json=subscription())

    def fail(sub, data):
        raise OSError("network unreachable")

    monkeypatch.setattr(push, "_send", fail)
    push.deliver([("alice", 1)], "Iron Ingot", "finished")
    assert len(store.push.subscriptions_for(["alice"])) == 1


def test_stopped_craft_message():
    assert push.message(7, None, "incomplete") == {
        "title": "Craft stopped",
        "body": "A pinned craft stopped before finishing (cancelled or interrupted).",
        "tag": "craft-completion-7",
    }


def test_service_worker_served_from_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert "javascript" in response.content_type
    assert response.headers["Cache-Control"] == "no-cache"
    assert b"addEventListener('push'" in response.data
