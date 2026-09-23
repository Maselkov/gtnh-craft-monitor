import app as app_module
from conftest import login_as


class TestCraftsEndpoint:
    def test_unauthenticated_post_rejected(self, client):
        res = client.post("/api/crafts", json={"jobs": []})
        assert res.status_code == 401

    def test_post_then_get_round_trip(self, client, api_headers):
        payload = {
            "source": "me_controller",
            "jobs": [{"name": "W01", "busy": True, "final_output": "Neutronium Ingot"}],
        }
        res = client.post("/api/crafts", json=payload, headers=api_headers)
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

        res = client.get("/api/crafts")
        data = res.get_json()
        assert data["source"] == "me_controller"
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["name"] == "W01"
        assert data["stale"] is False

    def test_no_data_yet_is_stale(self, client):
        res = client.get("/api/crafts")
        data = res.get_json()
        assert data["stale"] is True
        assert data["age_seconds"] is None

    def test_invalid_payload_rejected(self, client, api_headers):
        res = client.post(
            "/api/crafts",
            data="not json",
            content_type="application/json",
            headers=api_headers,
        )
        assert res.status_code == 400

    def test_missing_jobs_defaults_to_empty_list(self, client, api_headers):
        res = client.post(
            "/api/crafts", json={"source": "me_controller"}, headers=api_headers
        )
        assert res.status_code == 200
        assert client.get("/api/crafts").get_json()["jobs"] == []


class TestCpuPins:
    def _post_busy_job(self, client, api_headers, cpu_name="W01", busy=True):
        client.post(
            "/api/crafts",
            json={
                "source": "me_controller",
                "jobs": [{"name": cpu_name, "busy": busy}],
            },
            headers=api_headers,
        )

    def test_pin_requires_user_id_header(self, client):
        res = client.post("/api/pins", json={"cpu_name": "W01"})
        assert res.status_code == 400

    def test_cannot_pin_unknown_cpu(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        res = client.post("/api/pins", json={"cpu_name": "NoSuchCPU"})
        assert res.status_code == 404

    def test_cannot_pin_idle_cpu(self, client, api_headers):
        self._post_busy_job(client, api_headers, busy=False)
        login_as(client, "alice")
        res = client.post("/api/pins", json={"cpu_name": "W01"})
        assert res.status_code == 400
        assert "not currently busy" in res.get_json()["error"]

    def test_pin_busy_cpu_then_list_it(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        res = client.post("/api/pins", json={"cpu_name": "W01"})
        assert res.status_code == 200

        res = client.get("/api/pins")
        assert res.get_json()["pins"] == ["W01"]

    def test_pins_are_per_user(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})

        login_as(client, "bob")
        res = client.get("/api/pins")
        assert res.get_json()["pins"] == []

    def test_double_pin_does_not_duplicate(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        client.post("/api/pins", json={"cpu_name": "W01"})
        res = client.get("/api/pins")
        assert res.get_json()["pins"] == ["W01"]

    def test_unpin_removes_it(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        res = client.post("/api/pins/unpin", json={"cpu_name": "W01"})
        assert res.status_code == 200
        assert client.get("/api/pins").get_json()["pins"] == []

    def test_pin_on_cpu_first_seen_idle_is_dropped(self, client, api_headers):
        # Simulates a server restart: the pin survives in SQLite, but the
        # job finished while the server was down, so the first poll sees
        # the CPU idle with no remembered busy state.
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        app_module._cpu_last_busy.clear()

        self._post_busy_job(client, api_headers, busy=False)
        assert client.get("/api/pins").get_json()["pins"] == []

        # ...and the CPU's next job finishing doesn't notify alice.
        self._post_busy_job(client, api_headers)
        self._post_busy_job(client, api_headers, busy=False)
        assert client.get("/api/completions").get_json()["completions"] == []

    def test_pin_on_busy_cpu_survives_restart(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        app_module._cpu_last_busy.clear()

        self._post_busy_job(client, api_headers)
        assert client.get("/api/pins").get_json()["pins"] == ["W01"]
        self._post_busy_job(client, api_headers, busy=False)
        assert len(client.get("/api/completions").get_json()["completions"]) == 1
