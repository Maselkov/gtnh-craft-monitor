from conftest import user_headers


def sync_keys(client, api_headers, keys):
    client.post("/api/craft/keys", json={"keys": keys}, headers=api_headers)


def post_busy_job(client, api_headers, cpu_name="W01", busy=True):
    client.post(
        "/api/crafts",
        json={"source": "me_controller", "jobs": [{"name": cpu_name, "busy": busy}]},
        headers=api_headers,
    )


class TestCreateCancelRequest:
    def test_requires_user_id(self, client):
        res = client.post("/api/craft/cancel", json={"cpu_name": "W01"})
        assert res.status_code == 400

    def test_requires_valid_craft_key(self, client, api_headers):
        post_busy_job(client, api_headers)
        res = client.post("/api/craft/cancel", json={"cpu_name": "W01"}, headers=user_headers("not-a-real-key"))
        assert res.status_code == 403

    def test_unknown_cpu_rejected(self, client, api_headers):
        sync_keys(client, api_headers, ["alice"])
        post_busy_job(client, api_headers)
        res = client.post("/api/craft/cancel", json={"cpu_name": "NoSuchCPU"}, headers=user_headers("alice"))
        assert res.status_code == 404

    def test_idle_cpu_rejected(self, client, api_headers):
        sync_keys(client, api_headers, ["alice"])
        post_busy_job(client, api_headers, busy=False)
        res = client.post("/api/craft/cancel", json={"cpu_name": "W01"}, headers=user_headers("alice"))
        assert res.status_code == 400
        assert "not currently busy" in res.get_json()["error"]

    def test_busy_cpu_creates_pending_request(self, client, api_headers):
        sync_keys(client, api_headers, ["alice"])
        post_busy_job(client, api_headers)
        res = client.post("/api/craft/cancel", json={"cpu_name": "W01"}, headers=user_headers("alice"))
        assert res.status_code == 200
        assert "id" in res.get_json()


class TestCancelLifecycle:
    def _create_pending(self, client, api_headers, user_id="alice", cpu_name="W01"):
        sync_keys(client, api_headers, [user_id])
        post_busy_job(client, api_headers, cpu_name=cpu_name)
        res = client.post("/api/craft/cancel", json={"cpu_name": cpu_name}, headers=user_headers(user_id))
        return res.get_json()["id"]

    def test_appears_in_lua_pending_poll(self, client, api_headers):
        req_id = self._create_pending(client, api_headers)
        res = client.get("/api/craft/cancel/pending", headers=api_headers)
        ids = [r["id"] for r in res.get_json()["requests"]]
        assert req_id in ids

    def test_owner_can_poll_its_status(self, client, api_headers):
        req_id = self._create_pending(client, api_headers, user_id="alice")
        res = client.get(f"/api/craft/cancel/{req_id}", headers=user_headers("alice"))
        assert res.status_code == 200
        assert res.get_json()["status"] == "pending"

    def test_a_different_user_cannot_read_it(self, client, api_headers):
        req_id = self._create_pending(client, api_headers, user_id="alice")
        sync_keys(client, api_headers, ["alice", "bob"])
        res = client.get(f"/api/craft/cancel/{req_id}", headers=user_headers("bob"))
        # 404, not 403/200 with someone else's data - existence itself
        # isn't confirmed to a non-owner.
        assert res.status_code == 404

    def test_success_result_resolves_it_and_leaves_pending_poll(self, client, api_headers):
        req_id = self._create_pending(client, api_headers)
        res = client.post(
            f"/api/craft/cancel/{req_id}/result", json={"success": True}, headers=api_headers)
        assert res.status_code == 200

        res = client.get(f"/api/craft/cancel/{req_id}", headers=user_headers("alice"))
        data = res.get_json()
        assert data["status"] == "resolved"
        assert data["success"] is True

        # No longer shows up as pending work for Lua once resolved.
        res = client.get("/api/craft/cancel/pending", headers=api_headers)
        assert req_id not in [r["id"] for r in res.get_json()["requests"]]

    def test_failure_result_carries_a_reason(self, client, api_headers):
        req_id = self._create_pending(client, api_headers)
        client.post(
            f"/api/craft/cancel/{req_id}/result",
            json={"success": False, "reason": "CPU was not busy - nothing to cancel"},
            headers=api_headers)
        res = client.get(f"/api/craft/cancel/{req_id}", headers=user_headers("alice"))
        data = res.get_json()
        assert data["success"] is False
        assert data["reason"] == "CPU was not busy - nothing to cancel"

    def test_result_for_unknown_id_is_404(self, client, api_headers):
        res = client.post("/api/craft/cancel/99999/result", json={"success": True}, headers=api_headers)
        assert res.status_code == 404
