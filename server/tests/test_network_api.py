import sqlite3

from gcm import config, icons, state
from gcm.routes import network
from conftest import login_as


def run_scan(client, api_headers, batches):
    start_res = client.post("/api/network/scan/start", headers=api_headers)
    token = start_res.get_json()["scan_token"]
    for batch in batches:
        client.post(
            "/api/network/scan/batch",
            json={"items": batch, "scan_token": token},
            headers=api_headers,
        )
    return client.post(
        "/api/network/scan/finish",
        json={"scan_token": token, "chunks_sent": len(batches), "total_errors": 0},
        headers=api_headers,
    )


ITEM = {
    "name": "Iron Ingot",
    "size": 500,
    "mod": "minecraft",
    "internal": "iron_ingot",
    "damage": 0,
    "kind": "item",
    "isCraftable": False,
}
FLUID = {
    "name": "Molten Silicone",
    "size": 9500,
    "mod": None,
    "internal": "molten.silicone",
    "damage": None,
    "kind": "fluid",
    "isCraftable": False,
}


class TestScanLifecycle:
    def test_unauthenticated_scan_start_rejected(self, client):
        res = client.post("/api/network/scan/start")
        assert res.status_code == 401

    def test_full_scan_populates_network_state(self, client, api_headers):
        res = run_scan(client, api_headers, [[ITEM], [FLUID]])
        assert res.status_code == 200
        assert res.get_json()["item_count"] == 2

        data = client.get("/api/network").get_json()
        assert data["item_count"] == 2
        assert data["in_progress"] is False
        names = {it["name"] for it in data["items"]}
        assert names == {"Iron Ingot", "Molten Silicone"}

    def test_in_progress_flag_during_a_scan(self, client, api_headers):
        client.post("/api/network/scan/start", headers=api_headers)
        data = client.get("/api/network").get_json()
        assert data["in_progress"] is True
        assert data["scan_started_at"] is not None

    def test_bare_network_page_shows_no_data_before_first_scan(self, client):
        data = client.get("/api/network").get_json()
        assert data["item_count"] == 0
        assert data["items"] == []


class TestDuplicateItemKeyRegression:
    """Direct regression test for a real production crash: two entries
    resolving to the same item_key within one scan caused an uncaught
    sqlite3.IntegrityError (UNIQUE constraint) in _persist_network_snapshot,
    which crashed the whole scan/finish request with a 500 - even though
    the live in-memory state had already updated successfully. Fixed with
    INSERT OR REPLACE plus a defensive try/except around the whole
    persistence step. This test reproduces the exact scenario."""

    def test_duplicate_item_key_does_not_crash_scan_finish(self, client, api_headers):
        duplicate_a = dict(ITEM, size=100)
        duplicate_b = dict(
            ITEM, size=250
        )  # same mod/internal/damage/kind - same item_key
        res = run_scan(client, api_headers, [[duplicate_a], [duplicate_b]])
        assert res.status_code == 200  # NOT 500

    def test_later_duplicate_wins_in_the_snapshot_table(self, client, api_headers):
        duplicate_a = dict(ITEM, size=100)
        duplicate_b = dict(ITEM, size=250)
        run_scan(client, api_headers, [[duplicate_a], [duplicate_b]])

        conn = sqlite3.connect(config.ITEM_HISTORY_DB_PATH)
        try:
            rows = conn.execute("SELECT size FROM network_snapshot").fetchall()
        finally:
            conn.close()
        assert len(rows) == 1  # no crash, no duplicate row
        assert rows[0][0] == 250  # the later occurrence won


class TestSnapshotRestartRecovery:
    def test_reload_after_simulated_restart_restores_items_with_icons(
        self, client, api_headers, monkeypatch
    ):
        monkeypatch.setattr(
            icons, "_icons_by_key",
            {"minecraft:iron_ingot:0": "item/minecraft/iron_ingot.png"},
        )
        monkeypatch.setattr(icons, "_fluids_by_key", {})
        monkeypatch.setattr(icons, "_icons_by_label", {})

        craftable_item = dict(ITEM, isCraftable=True)
        run_scan(client, api_headers, [[craftable_item]])

        # Simulate a server restart: wipe the in-memory state exactly like
        # a fresh process would start with, then call the same function
        # that real startup calls.
        state.network["items"] = []
        state.network["item_count"] = 0
        state.network["is_reconstructed"] = False

        network.load_network_snapshot()

        data = client.get("/api/network").get_json()
        assert data["is_reconstructed"] is True
        assert data["item_count"] == 1
        restored = data["items"][0]
        assert restored["name"] == "Iron Ingot"
        assert restored["isCraftable"] is True
        # The actual bug this regression guards: icons are resolved at
        # LIVE ingestion time (scan/batch), a path reconstructed items
        # never go through - _load_network_snapshot() has to call
        # _attach_item_icons() itself, or this comes back None.
        assert restored["icon"] == "item/minecraft/iron_ingot.png"

    def test_is_reconstructed_clears_once_a_real_scan_completes(
        self, client, api_headers
    ):
        run_scan(client, api_headers, [[ITEM]])
        state.network["is_reconstructed"] = (
            True  # simulate the post-restart state directly
        )

        run_scan(client, api_headers, [[ITEM]])
        assert client.get("/api/network").get_json()["is_reconstructed"] is False


class TestNetworkItemPins:
    def test_pin_requires_sign_in(self, client):
        res = client.post(
            "/api/network/pins", json={"internal": "iron_ingot", "mod": "minecraft"}
        )
        assert res.status_code == 401

    def test_pin_then_list_it(self, client):
        login_as(client, "usr_alice")
        res = client.post(
            "/api/network/pins",
            json={
                "mod": "minecraft",
                "internal": "iron_ingot",
                "damage": 0,
                "kind": "item",
            },
        )
        assert res.status_code == 200

        res = client.get("/api/network/pins")
        pins = res.get_json()["pins"]
        assert len(pins) == 1
        assert pins[0]["internal"] == "iron_ingot"

    def test_pin_a_fluid_with_null_mod_and_damage(self, client):
        login_as(client, "usr_alice")
        # The tricky nullable-field case that motivated using a joined
        # item_key as the primary key instead of a composite key over
        # individually-nullable columns.
        res = client.post(
            "/api/network/pins",
            json={
                "mod": None,
                "internal": "cryotheum",
                "damage": None,
                "kind": "fluid",
            },
        )
        assert res.status_code == 200
        pins = client.get("/api/network/pins").get_json()["pins"]
        assert pins[0]["kind"] == "fluid"

    def test_pins_are_per_user(self, client):
        login_as(client, "usr_alice")
        client.post(
            "/api/network/pins",
            json={
                "mod": "minecraft",
                "internal": "iron_ingot",
                "damage": 0,
                "kind": "item",
            },
        )
        login_as(client, "usr_bob")
        res = client.get("/api/network/pins")
        assert res.get_json()["pins"] == []

    def test_pin_twice_does_not_duplicate(self, client):
        payload = {
            "mod": "minecraft",
            "internal": "iron_ingot",
            "damage": 0,
            "kind": "item",
        }
        login_as(client, "usr_alice")
        client.post("/api/network/pins", json=payload)
        client.post("/api/network/pins", json=payload)
        pins = client.get("/api/network/pins").get_json()["pins"]
        assert len(pins) == 1

    def test_unpin_removes_it(self, client):
        payload = {
            "mod": "minecraft",
            "internal": "iron_ingot",
            "damage": 0,
            "kind": "item",
        }
        login_as(client, "usr_alice")
        client.post("/api/network/pins", json=payload)
        client.post("/api/network/pins/unpin", json=payload)
        assert client.get("/api/network/pins").get_json()["pins"] == []


class TestScanCompletenessVerification:
    """Direct regression test for a real reported bug: the item history
    chart sometimes showed a sharp drop to 0, timed to a server restart.
    Root cause: a scan interrupted mid-way (server restart, or a batch
    that failed to POST) still got its (partial) results committed via
    scan/finish - every item present in the PREVIOUS snapshot but
    missing from this incomplete one got recorded as "vanished" (size
    0), even though it was still really there and was just never
    re-sent.

    Originally fixed with a retention-fraction sanity check (reject if
    the scan came back under some % of the last snapshot's size) -
    deliberately REMOVED and replaced with this mutual chunk-count
    verification instead, after working through a real flaw in that
    approach: no threshold could be simultaneously loose enough to
    accept a genuine, large, intentional network change and tight
    enough to reliably catch a partial scan, and a correctly-tokened,
    complete-but-smaller scan could get stuck being rejected forever
    against a snapshot it could never update past. This approach has no
    threshold to tune: the server independently counts chunks it
    actually received (chunks_received) and compares against what Lua
    reports attempting to send (chunks_sent), also requiring
    total_errors == 0 to catch a candidate-batch query failing outright
    (which produces no chunk at all, so a transit-only count can't see
    it) - a scan is either provably complete or it isn't."""

    def test_matching_chunks_and_zero_errors_is_accepted(self, client, api_headers):
        res = run_scan(client, api_headers, [[ITEM]])
        assert "rejected" not in res.get_json()
        assert client.get("/api/network").get_json()["item_count"] == 1

    def test_fewer_received_chunks_than_sent_is_rejected(self, client, api_headers):
        # Simulates a batch that failed to POST entirely (a connection
        # blip, say) - Lua claims it attempted 3 chunks, but the server
        # only actually received 2 of them.
        run_scan(client, api_headers, [[ITEM]])  # known-good baseline

        token = client.post("/api/network/scan/start", headers=api_headers).get_json()[
            "scan_token"
        ]
        client.post(
            "/api/network/scan/batch",
            json={"items": [ITEM], "scan_token": token},
            headers=api_headers,
        )
        client.post(
            "/api/network/scan/batch",
            json={"items": [FLUID], "scan_token": token},
            headers=api_headers,
        )
        res = client.post(
            "/api/network/scan/finish",
            json={
                "scan_token": token,
                "chunks_sent": 3,
                "total_errors": 0,
            },  # claims 3, only 2 actually arrived
            headers=api_headers,
        )
        body = res.get_json()
        assert body["rejected"] is True
        # The original baseline is untouched, not replaced by the incomplete data.
        assert client.get("/api/network").get_json()["item_count"] == 1

    def test_nonzero_errors_rejected_even_if_chunk_counts_match(
        self, client, api_headers
    ):
        # A getItemsInNetworkById() call failing outright never produces
        # a chunk to send in the first place, so chunk counts alone
        # could match perfectly while data is still silently missing -
        # this is exactly the gap total_errors closes.
        run_scan(client, api_headers, [[ITEM]])

        token = client.post("/api/network/scan/start", headers=api_headers).get_json()[
            "scan_token"
        ]
        client.post(
            "/api/network/scan/batch",
            json={"items": [ITEM], "scan_token": token},
            headers=api_headers,
        )
        res = client.post(
            "/api/network/scan/finish",
            json={
                "scan_token": token,
                "chunks_sent": 1,
                "total_errors": 1,
            },  # counts match, but 1 error occurred
            headers=api_headers,
        )
        assert res.get_json()["rejected"] is True
        assert client.get("/api/network").get_json()["item_count"] == 1

    def test_missing_chunks_sent_or_total_errors_fails_closed(
        self, client, api_headers
    ):
        # An older Lua deployment without these fields, or a malformed
        # payload - either way, nothing to verify completeness against,
        # so this must not silently fall back to trusting the data.
        run_scan(client, api_headers, [[ITEM]])

        token = client.post("/api/network/scan/start", headers=api_headers).get_json()[
            "scan_token"
        ]
        client.post(
            "/api/network/scan/batch",
            json={"items": [ITEM], "scan_token": token},
            headers=api_headers,
        )
        res = client.post(
            "/api/network/scan/finish", json={"scan_token": token}, headers=api_headers
        )
        assert res.get_json()["rejected"] is True

    def test_genuine_mass_removal_with_zero_errors_is_correctly_accepted(
        self, client, api_headers
    ):
        # THE key case the old percentage-threshold approach could never
        # get right: a real, deliberate, dramatic size drop (voiding
        # most of a network in one go) with every query and POST
        # succeeding and chunk counts matching must be accepted as
        # truth, not guessed at as "probably incomplete" just because
        # it's much smaller than before.
        full_batch = [
            {
                "name": f"Item {i}",
                "size": 100,
                "mod": "minecraft",
                "internal": f"item_{i}",
                "damage": 0,
                "kind": "item",
            }
            for i in range(100)
        ]
        run_scan(client, api_headers, [full_batch])
        assert client.get("/api/network").get_json()["item_count"] == 100

        tiny_but_complete = full_batch[:5]  # a genuine 95% drop
        res = run_scan(client, api_headers, [tiny_but_complete])
        assert "rejected" not in res.get_json()
        assert client.get("/api/network").get_json()["item_count"] == 5

    def test_rejected_scan_does_not_record_false_vanish_history(
        self, client, api_headers
    ):
        # The actual user-visible symptom this whole mechanism exists to
        # prevent: items missing from a rejected incomplete scan must
        # NOT get a size=0 history point recorded, since they never
        # really vanished.
        item = {
            "name": "Neutronium Ingot",
            "size": 500,
            "mod": "gregtech",
            "internal": "gt.metaitem.01",
            "damage": 11129,
            "kind": "item",
        }
        run_scan(client, api_headers, [[item]])

        token = client.post("/api/network/scan/start", headers=api_headers).get_json()[
            "scan_token"
        ]
        client.post(
            "/api/network/scan/batch",
            json={"items": [], "scan_token": token},
            headers=api_headers,
        )
        client.post(
            "/api/network/scan/finish",
            json={
                "scan_token": token,
                "chunks_sent": 2,
                "total_errors": 0,
            },  # claims 2, only 1 arrived
            headers=api_headers,
        )

        history = client.get(
            "/api/network/history?mod=gregtech&internal=gt.metaitem.01&damage=11129&kind=item&range=lifetime"
        ).get_json()
        # Only the original scan's point - no false "vanished" (size=0)
        # point from the rejected incomplete scan.
        assert len(history["points"]) == 1
        assert history["points"][0]["size"] == 500


class TestScanTokenMechanism:
    """A separate, earlier defense from the chunk-count verification
    above: scan/start mints a fresh random token, required back on
    every scan/batch and scan/finish. Structurally correct rather than
    a heuristic - it directly answers "does this data belong to the
    scan currently considered active" instead of guessing from how much
    data showed up. Catches BOTH a server restart mid-scan (a fresh
    process has no memory of any previously-issued token at all) AND
    two scans overlapping on the SAME process with no restart involved
    (a newer scan/start immediately invalidates whatever the previous
    scan was using) - the retention-fraction check can only ever catch
    dramatic cases of the first, and can't catch the second at all."""

    def test_scan_start_returns_a_token(self, client, api_headers):
        res = client.post("/api/network/scan/start", headers=api_headers)
        token = res.get_json().get("scan_token")
        assert token
        assert isinstance(token, str)

    def test_batch_with_correct_token_is_accepted(self, client, api_headers):
        token = client.post("/api/network/scan/start", headers=api_headers).get_json()[
            "scan_token"
        ]
        res = client.post(
            "/api/network/scan/batch",
            json={"items": [ITEM], "scan_token": token},
            headers=api_headers,
        )
        assert res.get_json()["ok"] is True

    def test_batch_with_wrong_token_is_rejected(self, client, api_headers):
        client.post("/api/network/scan/start", headers=api_headers)
        res = client.post(
            "/api/network/scan/batch",
            json={"items": [ITEM], "scan_token": "totally-wrong-token"},
            headers=api_headers,
        )
        body = res.get_json()
        assert body["ok"] is False
        assert body["error"] == "stale_scan_token"

    def test_batch_with_missing_token_is_rejected(self, client, api_headers):
        client.post("/api/network/scan/start", headers=api_headers)
        res = client.post(
            "/api/network/scan/batch", json={"items": [ITEM]}, headers=api_headers
        )
        assert res.get_json()["ok"] is False

    def test_finish_with_wrong_token_does_not_commit_and_keeps_previous_snapshot(
        self, client, api_headers
    ):
        # A full, valid scan first - the known-good snapshot to protect.
        run_scan(client, api_headers, [[ITEM]])
        assert client.get("/api/network").get_json()["item_count"] == 1

        # Start a new scan, post a batch with its real token (so the
        # in-memory buffer has real data), then try to finish with a
        # WRONG token - simulating exactly what a restart-interrupted
        # scan would look like from the server's point of view: data in
        # the buffer, but no valid proof it belongs to a real, current,
        # complete scan cycle.
        token = client.post("/api/network/scan/start", headers=api_headers).get_json()[
            "scan_token"
        ]
        client.post(
            "/api/network/scan/batch",
            json={"items": [FLUID], "scan_token": token},
            headers=api_headers,
        )
        res = client.post(
            "/api/network/scan/finish",
            json={"scan_token": "wrong-token"},
            headers=api_headers,
        )
        assert res.get_json()["ok"] is False

        # The original snapshot is completely untouched.
        data = client.get("/api/network").get_json()
        assert data["item_count"] == 1
        assert data["items"][0]["name"] == "Iron Ingot"
        assert data["in_progress"] is False  # doesn't get stuck "in progress" forever

    def test_simulated_restart_invalidates_the_old_scan_token(
        self, client, api_headers
    ):
        # A full scan establishes a known-good snapshot.
        run_scan(client, api_headers, [[ITEM]])

        # Start a scan, get its token - this represents the token
        # network_browser.lua would have stored locally.
        old_token = client.post(
            "/api/network/scan/start", headers=api_headers
        ).get_json()["scan_token"]

        # Simulate a server restart: directly reset the in-memory scan
        # token the way a fresh process would start with (nil), exactly
        # like _load_network_snapshot() is the real equivalent for the
        # item snapshot itself after a real restart.
        state.network["current_scan_token"] = (
            "some-other-token-a-fresh-process-would-never-know-about"
        )

        # Lua, unaware anything happened, keeps using its OLD (now
        # stale) token for the rest of this scan cycle.
        res = client.post(
            "/api/network/scan/batch",
            json={"items": [FLUID], "scan_token": old_token},
            headers=api_headers,
        )
        assert res.get_json()["ok"] is False
        assert res.get_json()["error"] == "stale_scan_token"

    def test_overlapping_scan_without_any_restart_still_gets_invalidated(
        self, client, api_headers
    ):
        # No restart at all here - just two scans starting back to back
        # on the SAME process, which scan/start's fresh-token-every-time
        # behavior should still correctly separate.
        token_a = client.post(
            "/api/network/scan/start", headers=api_headers
        ).get_json()["scan_token"]
        token_b = client.post(
            "/api/network/scan/start", headers=api_headers
        ).get_json()["scan_token"]
        assert token_a != token_b

        # A straggling batch from scan A arriving after scan B has
        # already started must be rejected - it doesn't belong to the
        # scan the server now considers active.
        res = client.post(
            "/api/network/scan/batch",
            json={"items": [ITEM], "scan_token": token_a},
            headers=api_headers,
        )
        assert res.get_json()["ok"] is False

        # But scan B's own token still works normally.
        res = client.post(
            "/api/network/scan/batch",
            json={"items": [ITEM], "scan_token": token_b},
            headers=api_headers,
        )
        assert res.get_json()["ok"] is True


class TestNetworkRevalidation:
    def test_unchanged_snapshot_answers_304(self, client, api_headers):
        first = client.get("/api/network")
        etag = first.headers["ETag"]
        assert first.headers["Cache-Control"] == "no-cache"

        again = client.get("/api/network", headers={"If-None-Match": etag})
        assert again.status_code == 304
        assert again.get_data() == b""

    def test_new_scan_changes_the_etag(self, client, api_headers):
        etag = client.get("/api/network").headers["ETag"]
        client.post("/api/network/scan/start", headers=api_headers)

        res = client.get("/api/network", headers={"If-None-Match": etag})
        assert res.status_code == 200
        assert res.get_json()["in_progress"] is True
