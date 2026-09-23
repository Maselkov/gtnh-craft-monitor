import app as app_module
from conftest import user_headers


def sync_keys(client, api_headers, keys):
    return client.post("/api/craft/keys", json={"keys": keys}, headers=api_headers)


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


class TestCraftKeySync:
    def test_unauthenticated_sync_rejected(self, client):
        res = client.post("/api/craft/keys", json={"keys": ["alice-key"]})
        assert res.status_code == 401

    def test_sync_replaces_wholesale_not_appends(self, client, api_headers):
        sync_keys(client, api_headers, ["alice-key"])
        res = sync_keys(client, api_headers, ["bob-key"])
        assert res.get_json()["key_count"] == 1
        # alice-key should no longer work after being replaced by the sync
        res2 = client.post(
            "/api/craft/request", json=valid_request_payload(), headers=user_headers("alice-key"))
        assert res2.status_code == 403


class TestCraftRequestCreation:
    def test_fails_closed_with_no_keys_ever_synced(self, client):
        res = client.post(
            "/api/craft/request", json=valid_request_payload(), headers=user_headers("anything"))
        assert res.status_code == 403

    def test_invalid_key_rejected_even_after_real_keys_synced(self, client, api_headers):
        sync_keys(client, api_headers, ["real-key"])
        res = client.post(
            "/api/craft/request", json=valid_request_payload(), headers=user_headers("wrong-key"))
        assert res.status_code == 403

    def test_valid_key_succeeds(self, client, api_headers):
        sync_keys(client, api_headers, ["real-key"])
        res = client.post(
            "/api/craft/request", json=valid_request_payload(), headers=user_headers("real-key"))
        assert res.status_code == 200
        assert "id" in res.get_json()

    def test_missing_x_user_id_rejected_before_key_check(self, client, api_headers):
        sync_keys(client, api_headers, ["real-key"])
        res = client.post("/api/craft/request", json=valid_request_payload())
        assert res.status_code == 400

    def test_missing_label_rejected(self, client, api_headers):
        sync_keys(client, api_headers, ["real-key"])
        res = client.post(
            "/api/craft/request", json=valid_request_payload(label=None), headers=user_headers("real-key"))
        assert res.status_code == 400

    def test_non_positive_amount_rejected(self, client, api_headers):
        sync_keys(client, api_headers, ["real-key"])
        res = client.post(
            "/api/craft/request", json=valid_request_payload(amount=0), headers=user_headers("real-key"))
        assert res.status_code == 400

    def test_invalid_kind_rejected(self, client, api_headers):
        sync_keys(client, api_headers, ["real-key"])
        res = client.post(
            "/api/craft/request", json=valid_request_payload(kind="gas"), headers=user_headers("real-key"))
        assert res.status_code == 400

    def test_fluid_kind_accepted(self, client, api_headers):
        sync_keys(client, api_headers, ["real-key"])
        res = client.post(
            "/api/craft/request",
            json=valid_request_payload(kind="fluid", mod=None, internal="cryotheum", damage=None),
            headers=user_headers("real-key"))
        assert res.status_code == 200


class TestCraftRequestLifecycle:
    def _create_pending_request(self, client, api_headers, user_id="real-key"):
        sync_keys(client, api_headers, [user_id])
        res = client.post("/api/craft/request", json=valid_request_payload(), headers=user_headers(user_id))
        return res.get_json()["id"]

    def test_appears_in_lua_pending_poll(self, client, api_headers):
        req_id = self._create_pending_request(client, api_headers)
        res = client.get("/api/craft/requests/pending", headers=api_headers)
        ids = [r["id"] for r in res.get_json()["requests"]]
        assert req_id in ids

    def test_appears_in_users_own_request_list(self, client, api_headers):
        self._create_pending_request(client, api_headers, user_id="real-key")
        res = client.get("/api/craft/requests", headers=user_headers("real-key"))
        assert len(res.get_json()["requests"]) == 1

    def test_not_visible_to_a_different_user(self, client, api_headers):
        self._create_pending_request(client, api_headers, user_id="real-key")
        sync_keys(client, api_headers, ["real-key", "other-key"])
        res = client.get("/api/craft/requests", headers=user_headers("other-key"))
        assert res.get_json()["requests"] == []

    def test_accepted_result_creates_a_real_pin_and_removes_the_request(self, client, api_headers):
        req_id = self._create_pending_request(client, api_headers)
        # The CPU doesn't need to show busy in _state["jobs"] for this to
        # work - that's the whole point of _create_pin_bypassing_busy_check,
        # confirmed directly here rather than just trusting the comment.
        res = client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers)
        assert res.status_code == 200

        # Request is gone (existing pin infra takes over)...
        res = client.get("/api/craft/requests", headers=user_headers("real-key"))
        assert res.get_json()["requests"] == []
        # ...but a real pin now exists for it.
        res = client.get("/api/pins", headers=user_headers("real-key"))
        assert res.get_json()["pins"] == ["W01"]

    def test_accepted_seeds_transition_tracking_state(self, client, api_headers):
        # Confirmed root-cause fix from the real "pin stuck forever"
        # investigation - without this, a craft finishing faster than the
        # main poll interval is invisible to completion detection.
        req_id = self._create_pending_request(client, api_headers)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers)
        assert app_module._cpu_last_busy.get("W01") is True
        assert app_module._cpu_last_known.get("W01", {}).get("label") == "Neutronium Ingot"

    def test_failed_result_keeps_request_visible_with_reason(self, client, api_headers):
        req_id = self._create_pending_request(client, api_headers)
        res = client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "failed", "reason": "no pattern found"},
            headers=api_headers)
        assert res.status_code == 200

        res = client.get("/api/craft/requests", headers=user_headers("real-key"))
        requests = res.get_json()["requests"]
        assert len(requests) == 1
        assert requests[0]["status"] == "failed"
        assert requests[0]["reason"] == "no pattern found"

    def test_result_for_unknown_id_returns_404(self, client, api_headers):
        res = client.post(
            "/api/craft/requests/99999/result", json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers)
        assert res.status_code == 404

    def test_dismiss_removes_a_failed_request(self, client, api_headers):
        req_id = self._create_pending_request(client, api_headers)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "failed", "reason": "x"}, headers=api_headers)
        client.post(f"/api/craft/requests/{req_id}/dismiss", headers=user_headers("real-key"))
        res = client.get("/api/craft/requests", headers=user_headers("real-key"))
        assert res.get_json()["requests"] == []

    def test_dismiss_by_a_different_user_is_a_silent_no_op(self, client, api_headers):
        req_id = self._create_pending_request(client, api_headers)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "failed", "reason": "x"}, headers=api_headers)
        sync_keys(client, api_headers, ["real-key", "someone-else"])
        client.post(f"/api/craft/requests/{req_id}/dismiss", headers=user_headers("someone-else"))
        # Still visible to the real owner - someone else's dismiss didn't touch it.
        res = client.get("/api/craft/requests", headers=user_headers("real-key"))
        assert len(res.get_json()["requests"]) == 1
