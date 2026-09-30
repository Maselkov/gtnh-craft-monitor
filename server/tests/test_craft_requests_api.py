import pytest

from gcm import commands, db, state, store
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
        # The CPU doesn't need to show busy in state.crafts["jobs"] for
        # this to work - tracking.start_requested_job() bypasses that check.
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
        conn = db.app_db()
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

    def test_dismiss_leaves_a_pending_request_alone(self, client, api_headers):
        # The game may still report on it; its result needs a record.
        req_id = self._create_pending_request(client)
        client.post(f"/api/craft/requests/{req_id}/dismiss")
        res = client.get("/api/craft/requests")
        assert [r["id"] for r in res.get_json()["requests"]] == [req_id]


class TestHistoryWrittenFirst:
    def test_request_is_not_queued_when_its_history_row_fails(
        self, client, api_headers, monkeypatch
    ):
        # The browser sees an error, so the game must not run the craft -
        # a retry would otherwise craft twice.
        def fail(*args):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(store.requests, "record_request", fail)
        login_as(client, "usr_operator", role="operator")
        with pytest.raises(RuntimeError):
            client.post("/api/craft/request", json=valid_request_payload())

        res = client.get("/api/craft/requests/pending", headers=api_headers)
        assert res.get_json()["requests"] == []

    def test_result_is_not_applied_when_its_history_row_fails(
        self, client, api_headers, monkeypatch
    ):
        # The record stays pending, so its expiry can still close the row.
        login_as(client, "usr_operator", role="operator")
        req_id = client.post("/api/craft/request", json=valid_request_payload()).get_json()["id"]

        def fail(*args, **kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(store.requests, "resolve_request", fail)
        with pytest.raises(RuntimeError):
            client.post(
                f"/api/craft/requests/{req_id}/result",
                json={"status": "failed", "reason": "no pattern"},
                headers=api_headers,
            )

        (req,) = commands.craft_requests.select(lambda r: r["id"] == req_id)
        assert req["status"] == "pending"


class TestNbtVariantRequests:
    TURBINE = {"mod": "gregtech", "internal": "gt.metatool.01", "damage": 176, "kind": "item"}

    def _scan_turbines(self, client, api_headers):
        from gcm import nbt
        from nbt_fixtures import tag_hex

        def turbine(material):
            return tag_hex([("GT.ToolStats", nbt.COMPOUND, [("PrimaryMaterial", nbt.STRING, material)])])

        tags = {m: turbine(m) for m in ("Neutronium", "Steel")}
        token = client.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
        client.post("/api/network/scan/batch", headers=api_headers, json={"scan_token": token, "items": [
            dict(self.TURBINE, name="Huge Turbine", size=0, isCraftable=True, hasTag=True, tag=tags[m])
            for m in tags
        ]})
        client.post("/api/network/scan/finish", headers=api_headers,
                    json={"scan_token": token, "chunks_sent": 1, "total_errors": 0})
        items = client.get("/api/network").get_json()["items"]
        return {it["variant_name"]: it for it in items}, tags

    def test_request_carries_the_variants_exact_nbt_to_the_game(self, client, api_headers):
        items, tags = self._scan_turbines(client, api_headers)
        assert set(items) == {"Neutronium", "Steel"}

        login_as(client, "usr_operator", role="operator")
        steel = items["Steel"]
        res = client.post("/api/craft/request", json=valid_request_payload(
            label="Huge Turbine", amount=1, variant=steel["variant"], variant_name="Steel", **self.TURBINE))
        assert res.status_code == 200

        pending = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
        assert pending[0]["variant"] == steel["variant"]
        assert pending[0]["tag"] == tags["Steel"]

        # The browser's own list doesn't carry the tag.
        mine = client.get("/api/craft/requests").get_json()["requests"]
        assert mine[0]["variant_name"] == "Steel"
        assert "tag" not in mine[0]

    def test_match_finds_the_pattern_with_the_same_nbt_in_any_key_order(self, client, api_headers):
        from gcm import nbt
        from nbt_fixtures import tag_hex

        def stats(material, order=1):
            entries = [("PrimaryMaterial", nbt.STRING, material), ("MaxDamage", nbt.INT, 100)]
            return tag_hex([("GT.ToolStats", nbt.COMPOUND, entries[::order])])

        token = client.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
        client.post("/api/network/scan/batch", headers=api_headers, json={"scan_token": token, "items": [
            dict(self.TURBINE, name="Huge Turbine", size=0, isCraftable=True, hasTag=True, tag=stats("Steel"))]})
        client.post("/api/network/scan/finish", headers=api_headers,
                    json={"scan_token": token, "chunks_sent": 1, "total_errors": 0})
        steel = client.get("/api/network").get_json()["items"][0]

        login_as(client, "usr_operator", role="operator")
        req_id = client.post("/api/craft/request", json=valid_request_payload(
            label="Huge Turbine", amount=1, variant=steel["variant"], **self.TURBINE)).get_json()["id"]

        # The pattern reports the same NBT with its keys the other way
        # round - different bytes, so Lua's own comparison misses it.
        reordered = stats("Steel", -1)
        assert reordered != stats("Steel")
        res = client.post(f"/api/craft/requests/{req_id}/match", headers=api_headers,
                          json={"tags": [stats("Neutronium"), False, reordered]})
        assert res.get_json()["index"] == 3

    def test_match_miss_reports_each_patterns_variant(self, client, api_headers):
        items, tags = self._scan_turbines(client, api_headers)
        login_as(client, "usr_operator", role="operator")
        req_id = client.post("/api/craft/request", json=valid_request_payload(
            label="Huge Turbine", amount=1, variant=items["Steel"]["variant"], **self.TURBINE)).get_json()["id"]
        body = client.post(f"/api/craft/requests/{req_id}/match", headers=api_headers,
                           json={"tags": [tags["Neutronium"], "zz", None]}).get_json()
        assert body["index"] is None
        assert body["variant"] == items["Steel"]["variant"]
        assert body["variants"] == [items["Neutronium"]["variant"], None, None]

    def test_match_needs_the_api_key_and_a_known_request(self, client, api_headers):
        assert client.post("/api/craft/requests/1/match", json={"tags": []}).status_code == 401
        assert client.post("/api/craft/requests/999/match", headers=api_headers,
                           json={"tags": []}).status_code == 404
        assert client.post("/api/craft/requests/999/match", headers=api_headers,
                           json={"tags": "x"}).status_code == 400

    def test_unknown_variant_has_no_tag(self, client, api_headers):
        login_as(client, "usr_operator", role="operator")
        client.post("/api/craft/request", json=valid_request_payload(variant="Labc"))
        pending = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
        assert pending[0]["variant"] == "Labc"
        assert pending[0]["tag"] is None

    def test_plain_request_has_no_variant(self, client, api_headers):
        login_as(client, "usr_operator", role="operator")
        client.post("/api/craft/request", json=valid_request_payload())
        pending = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
        assert pending[0]["variant"] is None and pending[0]["tag"] is None
