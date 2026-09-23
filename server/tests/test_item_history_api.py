def run_scan(client, api_headers, items):
    start_res = client.post("/api/network/scan/start", headers=api_headers)
    token = start_res.get_json()["scan_token"]
    client.post("/api/network/scan/batch", json={"items": items, "scan_token": token}, headers=api_headers)
    return client.post(
        "/api/network/scan/finish",
        json={"scan_token": token, "chunks_sent": 1, "total_errors": 0},
        headers=api_headers)


IRON = {"mod": "minecraft", "internal": "iron_ingot", "damage": 0, "kind": "item"}


class TestItemHistoryEndpoint:
    def test_missing_internal_rejected(self, client):
        res = client.get("/api/network/history?mod=minecraft")
        assert res.status_code == 400

    def test_no_history_yet_returns_empty(self, client):
        res = client.get("/api/network/history?mod=minecraft&internal=iron_ingot&damage=0&kind=item")
        data = res.get_json()
        assert data["points"] == []
        assert data["latest"] is None

    def test_unchanged_value_across_scans_records_once(self, client, api_headers):
        item = dict(IRON, name="Iron Ingot", size=500)
        run_scan(client, api_headers, [item])
        run_scan(client, api_headers, [item])  # same size again
        run_scan(client, api_headers, [item])

        res = client.get("/api/network/history?mod=minecraft&internal=iron_ingot&damage=0&kind=item&range=lifetime")
        data = res.get_json()
        assert len(data["points"]) == 1
        assert data["latest"]["size"] == 500

    def test_changed_value_records_a_new_point(self, client, api_headers):
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=500)])
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=300)])

        res = client.get("/api/network/history?mod=minecraft&internal=iron_ingot&damage=0&kind=item&range=lifetime")
        data = res.get_json()
        assert len(data["points"]) == 2
        assert data["latest"]["size"] == 300

    def test_item_vanishing_entirely_records_a_zero(self, client, api_headers):
        # getItemsInNetworkById/getFluidsInNetwork only ever return
        # entries AE2 itself considers present - an item disappearing
        # from one scan to the next means it genuinely hit zero stock
        # with no craftable pattern, which is itself a meaningful,
        # real change worth recording.
        run_scan(client, api_headers, [dict(IRON, name="Iron Ingot", size=500)])
        run_scan(client, api_headers, [])  # iron_ingot no longer present at all

        res = client.get("/api/network/history?mod=minecraft&internal=iron_ingot&damage=0&kind=item&range=lifetime")
        data = res.get_json()
        assert len(data["points"]) == 2
        assert data["latest"]["size"] == 0

    def test_fluid_and_item_with_same_internal_name_are_tracked_separately(self, client, api_headers):
        # The Cryotheum-shaped bug this project actually hit: the same
        # substance can be tracked as kind=item (via a craft-job pseudo-
        # item) and kind=fluid (via the real network scan) - these must
        # never collide into the same history stream.
        run_scan(client, api_headers, [
            {"mod": None, "internal": "cryotheum", "damage": None, "kind": "fluid", "name": "Cryotheum", "size": 1000},
        ])

        fluid_history = client.get(
            "/api/network/history?internal=cryotheum&kind=fluid&range=lifetime").get_json()
        item_history = client.get(
            "/api/network/history?internal=cryotheum&kind=item&range=lifetime").get_json()

        assert len(fluid_history["points"]) == 1
        assert item_history["points"] == []  # never recorded under the wrong kind
