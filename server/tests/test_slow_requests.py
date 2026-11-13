import logging

from gcm import slow_requests


def test_a_slow_request_is_logged_without_its_query(client, monkeypatch, caplog):
    monkeypatch.setattr(slow_requests, "SLOW_SECONDS", 0)
    with caplog.at_level(logging.INFO, logger="gcm.slow_requests"):
        assert client.get("/api/crafts?secret=abc").status_code == 200
    [message] = [r.getMessage() for r in caplog.records if r.name == "gcm.slow_requests"]
    assert message.startswith("slow request: GET /api/crafts took ")
    assert "(200), 1 running when it started" in message
    assert "secret" not in message
    assert slow_requests.running() == 0


def test_a_quick_request_is_not_logged(client, caplog):
    with caplog.at_level(logging.INFO, logger="gcm.slow_requests"):
        client.get("/api/crafts")
    assert not [r for r in caplog.records if r.name == "gcm.slow_requests"]
    assert slow_requests.running() == 0


def test_a_turned_away_request_is_still_counted(client, monkeypatch, caplog):
    monkeypatch.setattr(slow_requests, "SLOW_SECONDS", 0)
    with caplog.at_level(logging.INFO, logger="gcm.slow_requests"):
        status = client.get("/api/admin/health").status_code
    assert status in (401, 403)
    [message] = [r.getMessage() for r in caplog.records if r.name == "gcm.slow_requests"]
    assert f"({status})" in message
    assert slow_requests.running() == 0
