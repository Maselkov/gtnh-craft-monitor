import json
import sqlite3

import pytest

from gcm import config, icons, item_info, oredict, state, store
from gcm import inventory
from conftest import bundle_index, login_as
from nbt_fixtures import seed_tag


def run_scan(client, api_headers, batches, **finish):
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
        json={"scan_token": token, "chunks_sent": len(batches), "total_errors": 0, **finish},
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


def test_scan_start_names_the_ids_that_had_many_stacks(client, api_headers, monkeypatch):
    monkeypatch.setattr(inventory, "HEAVY_ID_STACKS", 3)
    bees = [{**ITEM, "name": "Drone", "mod": "Forestry", "internal": "beeDroneGE", "damage": 0,
             "hasTag": True, "tag": seed_tag("wheat", i, 1, 1)} for i in range(3)]
    run_scan(client, api_headers, [bees + [ITEM, FLUID]])
    assert len([i for i in client.get("/api/network").get_json()["items"] if i["internal"] == "beeDroneGE"]) == 3
    start = client.post("/api/network/scan/start", headers=api_headers).get_json()
    assert start["heavy_ids"] == ["Forestry:beeDroneGE"]


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

    def test_duplicates_are_merged_into_one_summed_entry(self, client, api_headers):
        duplicate_a = dict(ITEM, size=100)
        duplicate_b = dict(ITEM, size=250)
        run_scan(client, api_headers, [[duplicate_a], [duplicate_b]])

        conn = sqlite3.connect(config.ITEM_HISTORY_DB_PATH)
        try:
            rows = conn.execute("SELECT size FROM network_snapshot").fetchall()
        finally:
            conn.close()
        assert rows == [(350,)]
        assert client.get("/api/network").get_json()["item_count"] == 1


SEED = {
    "name": "Sugar Beet Seeds",
    "size": 27,
    "mod": "cropsnh",
    "internal": "genericSeed",
    "damage": 0,
    "kind": "item",
    "isCraftable": False,
    "hasTag": True,
}
SEED_Q = {"mod": "cropsnh", "internal": "genericSeed", "damage": 0, "kind": "item"}


def history_sizes(client, variant=None, **query):
    params = dict(SEED_Q, **{"range": "day", **query})
    if variant:
        params["variant"] = variant
    return [p["size"] for p in client.get("/api/network/history", query_string=params).get_json()["points"]]


class TestNbtVariants:
    def test_each_nbt_variant_is_its_own_item_with_its_own_history(self, client, api_headers):
        beet = dict(SEED, tag=seed_tag("sugarbeet", 1, 1, 1))
        wheat = dict(SEED, name="Wheat Seeds", size=3100, tag=seed_tag("wheat", 1, 1, 1))
        for _ in range(3):
            run_scan(client, api_headers, [[beet, wheat]])

        items = client.get("/api/network").get_json()["items"]
        assert len(items) == 2
        variants = {it["name"]: it["variant"] for it in items}
        assert len(set(variants.values())) == 2
        assert all("tag" not in it and "hasTag" not in it for it in items)

        assert history_sizes(client, variants["Sugar Beet Seeds"]) == [27]
        assert history_sizes(client, variants["Wheat Seeds"]) == [3100]

    def test_same_label_different_stats_are_told_apart(self, client, api_headers):
        low = dict(SEED, tag=seed_tag("sugarbeet", 1, 1, 1))
        high = dict(SEED, size=5, tag=seed_tag("sugarbeet", 12, 8, 1))
        run_scan(client, api_headers, [[low, high]])

        items = client.get("/api/network").get_json()["items"]
        assert sorted(it["variant_name"] for it in items) == [
            "Gr 1 · Ga 1 · Re 1", "Gr 12 · Ga 8 · Re 1"]

    def test_undescribable_same_label_variants_get_short_ids(self, client, api_headers):
        from nbt_fixtures import tag_hex
        from gcm import nbt
        a = dict(SEED, tag=tag_hex([("x", nbt.INT, 1)]))
        b = dict(SEED, tag=tag_hex([("x", nbt.INT, 2)]))
        run_scan(client, api_headers, [[a, b]])

        items = client.get("/api/network").get_json()["items"]
        assert sorted(it["variant_name"] for it in items) == sorted(
            "#" + it["variant"][-4:] for it in items)

    def test_without_tags_variants_split_by_label_and_merge_within_it(self, client, api_headers):
        # allowItemStackNBTTags off: only hasTag and the label to go on.
        beet_a = dict(SEED, size=20)
        beet_b = dict(SEED, size=7)
        wheat = dict(SEED, name="Wheat Seeds", size=3100)
        run_scan(client, api_headers, [[beet_a, wheat, beet_b]])

        items = {it["name"]: it for it in client.get("/api/network").get_json()["items"]}
        assert set(items) == {"Sugar Beet Seeds", "Wheat Seeds"}
        assert items["Sugar Beet Seeds"]["size"] == 27
        assert items["Sugar Beet Seeds"]["variant"].startswith("L")
        assert "variant_name" not in items["Sugar Beet Seeds"]

    def test_unreadable_tag_falls_back_to_the_label(self, client, api_headers):
        run_scan(client, api_headers, [[dict(SEED, tag="not hex")]])
        item = client.get("/api/network").get_json()["items"][0]
        assert item["variant"].startswith("L")

    def test_items_without_nbt_keep_their_old_key(self, client, api_headers):
        run_scan(client, api_headers, [[ITEM]])
        item = client.get("/api/network").get_json()["items"][0]
        assert "variant" not in item
        points = client.get("/api/network/history", query_string={
            "mod": "minecraft", "internal": "iron_ingot", "damage": 0, "kind": "item",
        }).get_json()["points"]
        assert [p["size"] for p in points] == [500]

    def test_variants_survive_a_restart(self, client, api_headers):
        run_scan(client, api_headers, [[dict(SEED, tag=seed_tag("sugarbeet", 12, 8, 1))]])
        before = client.get("/api/network").get_json()["items"]
        state.network["items"] = []
        inventory.load_snapshot()
        after = client.get("/api/network").get_json()["items"]
        assert [(it["variant"], it["variant_name"]) for it in after] == [
            (it["variant"], it["variant_name"]) for it in before]

    def test_first_scan_clears_mixed_pre_variant_history_once(self, client, api_headers):
        base_key = store.items.item_key("cropsnh", "genericSeed", 0, "item")
        store.items.add_rows([(base_key, "Wheat Seeds", 3100, 1.0), (base_key, "Sugar Beet Seeds", 27, 2.0)])
        conn = sqlite3.connect(config.ITEM_HISTORY_DB_PATH)
        try:
            # Left by the migration; reset_state empties every table.
            conn.execute("INSERT INTO pending_nbt_cleanup (id) VALUES (1)")
            conn.commit()
        finally:
            conn.close()

        plain = dict(SEED, name="Seeds", size=4, hasTag=False)
        beet = dict(SEED, tag=seed_tag("sugarbeet", 1, 1, 1))
        run_scan(client, api_headers, [[plain, beet]])
        # The mixed rows are gone; the plain stack still under the old key
        # starts over from its current size.
        assert history_sizes(client, range="lifetime") == [4]

        # Only once: later history under the old key is kept.
        run_scan(client, api_headers, [[dict(plain, size=9), beet]])
        run_scan(client, api_headers, [[dict(plain, size=9), beet]])
        assert history_sizes(client, range="lifetime") == [4, 9]

    def test_pins_are_per_variant(self, client, api_headers):
        login_as(client, "usr_alice")
        client.post("/api/network/pins", json=dict(SEED_Q, variant="aaa"))
        client.post("/api/network/pins", json=dict(SEED_Q, variant="bbb"))
        pins = client.get("/api/network/pins").get_json()["pins"]
        assert sorted(p["variant"] for p in pins) == ["aaa", "bbb"]

        client.post("/api/network/pins/unpin", json=dict(SEED_Q, variant="aaa"))
        pins = client.get("/api/network/pins").get_json()["pins"]
        assert [p["variant"] for p in pins] == ["bbb"]


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

        inventory.load_snapshot()

        data = client.get("/api/network").get_json()
        assert data["is_reconstructed"] is True
        assert data["item_count"] == 1
        restored = data["items"][0]
        assert restored["name"] == "Iron Ingot"
        assert restored["isCraftable"] is True
        # The actual bug this regression guards: icons are resolved at
        # LIVE ingestion time (scan/batch), a path reconstructed items
        # never go through - inventory.load_snapshot() has to call
        # icons.attach_item_icons() itself, or this comes back None.
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
        # like inventory.load_snapshot() is the real equivalent for the
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

    def test_says_which_game_data_is_picked_and_a_change_of_it_changes_the_etag(self, client, monkeypatch):
        from gcm import gamedata

        first = client.get("/api/network")
        assert first.get_json()["game_data"] is None
        monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
        res = client.get("/api/network", headers={"If-None-Match": first.headers["ETag"]})
        assert res.status_code == 200
        assert res.get_json()["game_data"] == "2.9.0"


class TestCrashReports:
    REPORT = {"error": "not enough memory", "phase": "Batch 30: calling getItemsInNetworkById()",
              "free_memory": 1024}

    def test_needs_the_api_key(self, client):
        assert client.post("/api/network/crashed", json=self.REPORT).status_code == 401

    def test_ends_the_dead_scan_and_is_shown_until_one_finishes(self, client, api_headers):
        run_scan(client, api_headers, [[ITEM]])
        etag = client.get("/api/network").headers["ETag"]
        client.post("/api/network/scan/start", headers=api_headers)
        assert client.post("/api/network/crashed", json=self.REPORT, headers=api_headers).get_json() == {"ok": True}

        res = client.get("/api/network", headers={"If-None-Match": etag})
        assert res.status_code == 200
        data = res.get_json()
        assert data["in_progress"] is False
        assert data["scan_started_at"] is None
        crash = data["crash"]
        assert (crash["error"], crash["phase"], crash["free_memory"]) == (
            "not enough memory", "Batch 30: calling getItemsInNetworkById()", 1024)
        assert data["items"][0]["size"] == 500, "the last full scan's numbers stay"

        run_scan(client, api_headers, [[ITEM]])
        assert client.get("/api/network").get_json()["crash"] is None

    def test_odd_values_are_tidied(self, client, api_headers):
        client.post("/api/network/crashed", headers=api_headers,
                    json={"error": "x" * 2000, "phase": None, "free_memory": True})
        crash = client.get("/api/network").get_json()["crash"]
        assert (len(crash["error"]), crash["phase"], crash["free_memory"]) == (500, None, None)
        assert client.post("/api/network/crashed", headers=api_headers, data="nope",
                           content_type="application/json").status_code == 400


@pytest.fixture()
def game_info(tmp_path):
    """Makes the given item_info.json (and ore_dict.json) the live ones."""
    def load(info, ore=None):
        (tmp_path / "item_info.json").write_text(json.dumps(info))
        if ore is not None:
            (tmp_path / "ore_dict.json").write_text(json.dumps(ore))
        index = bundle_index(tmp_path)
        item_info.load(index)
        if ore is not None:
            oredict.load(index)
    yield load
    item_info.load(None)
    oredict.load(None)


class TestItemInfo:
    INFO = {
        "tooltips": {
            "by_key": {"minecraft:iron_ingot:0": "§7Fe", "cropsnh:genericSeed:0": "Some seeds"},
            "by_key_label": {"cropsnh:genericSeed:0|Wheat Seeds": "§eWheat"},
        },
        "shift_tooltips": {"by_key": {"minecraft:iron_ingot:0": "§7Fe, in detail"}},
        "rarity": {"by_key": {"minecraft:iron_ingot:0": "e"}},
        "fluids": {"molten.silicone": {"temperature": 1500, "gaseous": False}},
        "mods": {"minecraft": "Minecraft", "IC2": "IndustrialCraft 2"},
    }
    ORE = {"ingotIron": ["minecraft:iron_ingot:0"], "ingotAnyIron": ["minecraft:iron_ingot:*"]}

    def test_only_items_in_the_network_with_any(self, client, api_headers, game_info):
        game_info(self.INFO, self.ORE)
        run_scan(client, api_headers, [[ITEM, FLUID, dict(ITEM, internal="gold_ingot", name="Gold Ingot")]])
        res = client.get("/api/network/info")
        assert res.get_json() == {
            "items": {
                "minecraft|iron_ingot|0|item": {
                    "description": "§7Fe", "shift_description": "§7Fe, in detail", "rarity": "e",
                    "ore": ["ingotIron", "ingotAnyIron"],
                },
                "|molten.silicone||fluid": {"description": "§7Temperature: §e1,500 K\n§7State: §eLiquid"},
            },
            "mods": {"minecraft": "Minecraft", "IC2": "IndustrialCraft 2"},
        }
        assert client.get("/api/network/info", headers={"If-None-Match": res.headers["ETag"]}).status_code == 304

    def test_an_nbt_variant_has_its_own_by_label_or_else_its_items(self, client, api_headers, game_info):
        game_info(self.INFO)
        beet = dict(SEED, tag=seed_tag("sugarbeet", 1, 1, 1))
        wheat = dict(SEED, name="Wheat Seeds", size=3100, tag=seed_tag("wheat", 1, 1, 1))
        run_scan(client, api_headers, [[beet, wheat]])
        variants = {it["name"]: it["variant"] for it in client.get("/api/network").get_json()["items"]}
        got = client.get("/api/network/info").get_json()["items"]
        assert got[f"cropsnh|genericSeed|0|item|{variants['Wheat Seeds']}"] == {"description": "§eWheat"}
        assert got[f"cropsnh|genericSeed|0|item|{variants['Sugar Beet Seeds']}"] == {"description": "Some seeds"}

    def test_a_gregtech_tools_material(self, game_info):
        game_info({"tooltips": {"by_key_material": {"gregtech:gt.metatool.01:170|Neutronium": "Neutronium turbine"},
                                "by_key": {"gregtech:gt.metatool.01:170": "A turbine"}}})
        tool = {"mod": "gregtech", "internal": "gt.metatool.01", "damage": 170, "kind": "item",
                "name": "Huge Turbine", "variant": "x", "variant_name": "Neutronium #1a2b"}
        assert item_info.describe(tool) == {"description": "Neutronium turbine"}
        assert item_info.describe(dict(tool, variant_name="Iron")) == {"description": "A turbine"}

    def test_plasma_and_gas(self, game_info):
        game_info({"fluids": {"plasma.helium": {"temperature": 10000, "gaseous": True},
                              "steam": {"temperature": 500, "gaseous": True}}})
        fluid = {"internal": "plasma.helium", "kind": "fluid"}
        assert item_info.describe(fluid)["description"].endswith("Plasma")
        assert item_info.describe(dict(fluid, internal="steam"))["description"].endswith("Gas")

    def test_formulas_lead_the_description_unless_the_tooltip_has_them(self, game_info):
        game_info({
            "tooltips": {"by_key": {"gregtech:gt.metaitem.01:11028": "§eTi", "gregtech:gt.metaitem.01:1": "A cell"}},
            "formulas": {"by_key": {"gregtech:gt.metaitem.01:11028": "Ti", "gregtech:gt.metaitem.01:1": "H₂O",
                                    "gregtech:gt.metaitem.01:2": "§kXy"}},
            "fluids": {"molten.titanium": {"temperature": 1941, "gaseous": False, "formula": "Ti"}},
        })
        item = {"mod": "gregtech", "internal": "gt.metaitem.01", "kind": "item"}
        assert item_info.describe(dict(item, damage=11028)) == {"description": "§eTi", "formula": "Ti"}
        assert item_info.describe(dict(item, damage=1)) == {"description": "§eH₂O\nA cell", "formula": "H₂O"}
        assert item_info.describe(dict(item, damage=2)) == {"description": "§e§kXy", "formula": "§kXy"}
        assert item_info.describe({"internal": "molten.titanium", "kind": "fluid"}) == {
            "description": "§eTi\n§7Temperature: §e1,941 K\n§7State: §eLiquid", "formula": "Ti"}

    def test_mod_names_in_any_case(self, game_info):
        game_info(self.INFO)
        assert item_info.mod_name("ic2") == "IndustrialCraft 2"
        assert item_info.mod_name("nope") is None

    def test_the_item_dialog_gets_it_with_the_history(self, client, api_headers, game_info):
        game_info(self.INFO, self.ORE)
        run_scan(client, api_headers, [[ITEM]])
        query = {"mod": "minecraft", "internal": "iron_ingot", "damage": 0, "kind": "item"}
        info = client.get("/api/network/history", query_string=query).get_json()["info"]
        assert info == {"description": "§7Fe", "shift_description": "§7Fe, in detail", "rarity": "e",
                        "ore": ["ingotIron", "ingotAnyIron"], "mod_name": "Minecraft"}
        query["internal"] = "gold_ingot"
        info = client.get("/api/network/history", query_string=query).get_json()["info"]
        assert info == {"mod_name": "Minecraft"}

    def test_none_without_game_data(self, client, api_headers):
        run_scan(client, api_headers, [[ITEM]])
        assert client.get("/api/network/info").get_json() == {"items": {}, "mods": {}}


class TestScanSizeLimit:
    def items(self, n, start=0):
        return [dict(ITEM, internal=f"thing_{i}", name=f"Thing {i}") for i in range(start, start + n)]

    def test_a_scan_up_to_the_limit_is_kept(self, client, api_headers, monkeypatch):
        monkeypatch.setattr(config, "MAX_SCAN_ITEMS", 4)
        run_scan(client, api_headers, [self.items(2), self.items(2, start=2)])
        assert client.get("/api/network").get_json()["item_count"] == 4

    def test_a_scan_past_the_limit_is_abandoned_and_says_why(self, client, api_headers, monkeypatch):
        run_scan(client, api_headers, [[ITEM]])
        monkeypatch.setattr(config, "MAX_SCAN_ITEMS", 3)
        token = client.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
        first = client.post("/api/network/scan/batch", json={"items": self.items(2), "scan_token": token},
                            headers=api_headers)
        assert first.get_json()["ok"] is True
        over = client.post("/api/network/scan/batch", json={"items": self.items(2, start=2), "scan_token": token},
                           headers=api_headers)
        # The code network_browser.lua stops a scan on.
        assert over.get_json()["error"] == "stale_scan_token"
        finish = client.post("/api/network/scan/finish",
                             json={"scan_token": token, "chunks_sent": 2, "total_errors": 0}, headers=api_headers)
        assert finish.get_json()["ok"] is False

        network = client.get("/api/network").get_json()
        assert network["item_count"] == 1 and network["in_progress"] is False
        assert state.network_buffer == []
        assert "MAX_SCAN_ITEMS" in state.network["last_rejected"]["reason"]
        # Nothing from it reached the history.
        assert store.items.history(store.items.key_of(self.items(1)[0]), 0) == []
