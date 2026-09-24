import time

from gcm import db, history, state
from conftest import login_as


def post_busy_job(client, api_headers, cpu_name="W01"):
    client.post(
        "/api/crafts",
        json={"source": "me_controller", "jobs": [{"name": cpu_name, "busy": True}]},
        headers=api_headers,
    )


def submit_craft_request(client):
    login_as(client, "usr_alice", role="operator")
    res = client.post(
        "/api/craft/request",
        json={"label": "Neutronium Ingot", "internal": "gt.metaitem.01", "amount": 1},
    )
    return res.get_json()["id"]


def submit_cancel(client, api_headers):
    post_busy_job(client, api_headers)
    login_as(client, "usr_alice", role="operator")
    return client.post("/api/craft/cancel", json={"cpu_name": "W01"}).get_json()["id"]


def age(record, seconds, *fields):
    for field in fields:
        record[field] -= seconds


def history_status(table, request_id):
    conn = db.craft_db()
    try:
        return conn.execute(
            f"SELECT status, reason FROM {table} WHERE request_id = ?", (request_id,)
        ).fetchone()
    finally:
        conn.close()


class TestCraftRequestExpiry:
    def test_request_never_picked_up_fails(self, client, api_headers):
        req_id = submit_craft_request(client)
        age(state.craft_requests.requests[req_id], 121, "created_at")

        pending = client.get("/api/craft/requests/pending", headers=api_headers)
        assert pending.get_json()["requests"] == []
        mine = client.get("/api/craft/requests").get_json()["requests"]
        assert mine[0]["status"] == "failed"
        assert "didn't pick up" in mine[0]["reason"]
        assert history_status("craft_request_history", req_id)[0] == "failed"

    def test_picked_up_request_waits_for_planning(self, client, api_headers):
        req_id = submit_craft_request(client)
        client.get("/api/craft/requests/pending", headers=api_headers)
        age(state.craft_requests.requests[req_id], 600, "created_at", "picked_up_at")

        pending = client.get("/api/craft/requests/pending", headers=api_headers)
        assert [r["id"] for r in pending.get_json()["requests"]] == [req_id]

    def test_picked_up_request_without_result_eventually_fails(self, client, api_headers):
        req_id = submit_craft_request(client)
        client.get("/api/craft/requests/pending", headers=api_headers)
        age(state.craft_requests.requests[req_id], 3601, "created_at", "picked_up_at")

        client.get("/api/craft/requests/pending", headers=api_headers)
        assert state.craft_requests.requests[req_id]["status"] == "failed"

    def test_old_failed_requests_are_forgotten(self, client, api_headers):
        req_id = submit_craft_request(client)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "failed", "reason": "missing resources"},
            headers=api_headers,
        )
        age(state.craft_requests.requests[req_id], 86401, "created_at", "failed_at")

        assert client.get("/api/craft/requests").get_json()["requests"] == []
        assert req_id not in state.craft_requests.requests


class TestCancelExpiry:
    def test_cancel_never_picked_up_is_not_run_later(self, client, api_headers):
        req_id = submit_cancel(client, api_headers)
        age(state.cancel_requests.requests[req_id], 21, "created_at")

        pending = client.get("/api/craft/cancel/pending", headers=api_headers)
        assert pending.get_json()["requests"] == []
        data = client.get(f"/api/craft/cancel/{req_id}").get_json()
        assert data["status"] == "resolved"
        assert data["success"] is False
        assert history_status("craft_cancel_history", req_id)[0] == "resolved"

    def test_resolved_cancels_are_forgotten(self, client, api_headers):
        req_id = submit_cancel(client, api_headers)
        client.post(
            f"/api/craft/cancel/{req_id}/result",
            json={"success": True},
            headers=api_headers,
        )
        age(state.cancel_requests.requests[req_id], 601, "created_at", "resolved_at")

        assert client.get(f"/api/craft/cancel/{req_id}").status_code == 404
        assert req_id not in state.cancel_requests.requests


def test_login_prunes_expired_and_revoked_sessions(client, flask_app):
    login_as(client, "usr_alice")
    conn = db.craft_db()
    try:
        conn.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
        conn.commit()
    finally:
        conn.close()

    login_as(flask_app.test_client(), "usr_alice")

    conn = db.craft_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    finally:
        conn.close()


def test_startup_closes_request_history_left_open(client):
    submit_craft_request(client)
    history.close_orphaned_requests()

    conn = db.craft_db()
    try:
        row = conn.execute(
            "SELECT status, reason FROM craft_request_history"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("failed", "server restarted")
