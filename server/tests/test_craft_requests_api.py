from gcm import db, state
from conftest import login_as


def valid_request_payload(**overrides):
    payload = {
        "label": "Neutronium Ingot",
        "mod": "gregtech",
        "internal": "gt.metaitem.01",
        "damage": 11129,
        "amount": 5,
        "kind": "item",
    }
    payload.update(overrides)
    return payload


class TestCraftRequestCreation:
    def test_unauthenticated_user_is_rejected(self, client):
        res = client.post("/api/craft/request", json=valid_request_payload())
        assert res.status_code == 401

    def test_viewer_is_rejected(self, client):
        login_as(client, "usr_viewer", role="viewer")
        res = client.post("/api/craft/request", json=valid_request_payload())
        assert res.status_code == 403

    def test_operator_succeeds(self, client):
        login_as(client, "usr_operator", role="operator")
        res = client.post("/api/craft/request", json=valid_request_payload())
        assert res.status_code == 200
        assert "id" in res.get_json()

    def test_missing_label_rejected(self, client):
        login_as(client, "usr_operator", role="operator")
        res = client.post("/api/craft/request", json=valid_request_payload(label=None))
        assert res.status_code == 400

    def test_non_positive_amount_rejected(self, client):
        login_as(client, "usr_operator", role="operator")
        res = client.post("/api/craft/request", json=valid_request_payload(amount=0))
        assert res.status_code == 400

    def test_invalid_kind_rejected(self, client):
        login_as(client, "usr_operator", role="operator")
        res = client.post("/api/craft/request", json=valid_request_payload(kind="gas"))
        assert res.status_code == 400

    def test_fluid_kind_accepted(self, client):
        login_as(client, "usr_operator", role="operator")
        res = client.post(
            "/api/craft/request",
            json=valid_request_payload(
                kind="fluid", mod=None, internal="cryotheum", damage=None
            ),
        )
        assert res.status_code == 200


class TestCraftRequestLifecycle:
    def _create_pending_request(self, client, user_id="usr_operator"):
        login_as(client, user_id, role="operator")
        res = client.post("/api/craft/request", json=valid_request_payload())
        return res.get_json()["id"]

    def test_appears_in_lua_pending_poll(self, client, api_headers):
        req_id = self._create_pending_request(client)
        res = client.get("/api/craft/requests/pending", headers=api_headers)
        ids = [r["id"] for r in res.get_json()["requests"]]
        assert req_id in ids

    def test_appears_in_users_own_request_list(self, client, api_headers):
        self._create_pending_request(client)
        res = client.get("/api/craft/requests")
        assert len(res.get_json()["requests"]) == 1

    def test_not_visible_to_a_different_user(self, client, api_headers):
        self._create_pending_request(client)
        login_as(client, "usr_other", role="viewer")
        res = client.get("/api/craft/requests")
        assert res.get_json()["requests"] == []

    def test_accepted_result_creates_a_real_pin_and_removes_the_request(
        self, client, api_headers
    ):
        req_id = self._create_pending_request(client)
        # The CPU doesn't need to show busy in _state["jobs"] for this to
        # work - that's the whole point of _create_pin_bypassing_busy_check,
        # confirmed directly here rather than just trusting the comment.
        res = client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers,
        )
        assert res.status_code == 200

        # Request is gone (existing pin infra takes over)...
        res = client.get("/api/craft/requests")
        assert res.get_json()["requests"] == []
        # ...but a real pin now exists for it.
        res = client.get("/api/pins")
        assert res.get_json()["pins"] == ["W01"]

    def test_accepted_seeds_transition_tracking_state(self, client, api_headers):
        # Confirmed root-cause fix from the real "pin stuck forever"
        # investigation - without this, a craft finishing faster than the
        # main poll interval is invisible to completion detection.
        req_id = self._create_pending_request(client)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers,
        )
        assert state.cpu_last_busy.get("W01") is True
        assert (
            state.cpu_last_known.get("W01", {}).get("label") == "Neutronium Ingot"
        )

    def test_failed_result_keeps_request_visible_with_reason(self, client, api_headers):
        req_id = self._create_pending_request(client)
        res = client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "failed", "reason": "no pattern found"},
            headers=api_headers,
        )
        assert res.status_code == 200

        res = client.get("/api/craft/requests")
        requests = res.get_json()["requests"]
        assert len(requests) == 1
        assert requests[0]["status"] == "failed"
        assert requests[0]["reason"] == "no pattern found"

    def test_request_history_records_lua_result(self, client, api_headers):
        req_id = self._create_pending_request(client)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers,
        )
        conn = db.craft_db()
        try:
            row = conn.execute(
                "SELECT status, cpu_name FROM craft_request_history "
                "WHERE request_id = ?",
                (req_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row == ("accepted", "W01")

    def test_result_for_unknown_id_returns_404(self, client, api_headers):
        res = client.post(
            "/api/craft/requests/99999/result",
            json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers,
        )
        assert res.status_code == 404

    def test_dismiss_removes_a_failed_request(self, client, api_headers):
        req_id = self._create_pending_request(client)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "failed", "reason": "x"},
            headers=api_headers,
        )
        client.post(f"/api/craft/requests/{req_id}/dismiss")
        res = client.get("/api/craft/requests")
        assert res.get_json()["requests"] == []

    def test_dismiss_by_a_different_user_is_a_silent_no_op(self, client, api_headers):
        req_id = self._create_pending_request(client)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "failed", "reason": "x"},
            headers=api_headers,
        )
        login_as(client, "usr_other", role="viewer")
        client.post(f"/api/craft/requests/{req_id}/dismiss")
        # Still visible to the real owner - someone else's dismiss didn't touch it.
        login_as(client, "usr_operator", role="operator")
        res = client.get("/api/craft/requests")
        assert len(res.get_json()["requests"]) == 1
