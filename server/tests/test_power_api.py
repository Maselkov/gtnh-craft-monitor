import pytest
import sqlite3
import time

from conftest import login_as
from gcm import config, db, store

REAL_TS = 1790121600.0  # 2026-09-23, after the impossible-times cutoff


class TestPowerEndpoint:
    def test_unauthenticated_post_rejected(self, client):
        res = client.post("/api/power", json={"stored": 100, "capacity": 1000})
        assert res.status_code == 401

    def test_missing_stored_or_capacity_rejected(self, client, api_headers):
        res = client.post("/api/power", json={"stored": 100}, headers=api_headers)
        assert res.status_code == 400

    def test_basic_post_then_get(self, client, api_headers):
        res = client.post("/api/power", json={"stored": 500000, "capacity": 5000000}, headers=api_headers)
        assert res.status_code == 200

        data = client.get("/api/power").get_json()
        assert data["latest"]["stored"] == 500000
        assert data["latest"]["capacity"] == 5000000

    def test_no_readings_yet(self, client):
        data = client.get("/api/power").get_json()
        assert data["latest"] is None
        assert data["points"] == []


class TestPowerTrendFields:
    def test_trend_fields_are_optional(self, client, api_headers):
        # Older power_monitor.lua deployments, or a storage structure
        # that doesn't report averages at all, simply won't send these -
        # must not be treated as an error.
        res = client.post("/api/power", json={"stored": 100, "capacity": 1000}, headers=api_headers)
        assert res.status_code == 200
        data = client.get("/api/power").get_json()
        assert data["latest"]["avg_eu_in_5s"] is None
        assert data["latest"]["avg_eu_out_5s"] is None

    def test_trend_fields_round_trip_when_present(self, client, api_headers):
        client.post(
            "/api/power",
            json={
                "stored": 4228798914455, "capacity": 5400000000000,
                "avg_eu_in_5s": 0, "avg_eu_out_5s": 350171922,
            },
            headers=api_headers)
        data = client.get("/api/power").get_json()
        assert data["latest"]["avg_eu_in_5s"] == 0
        assert data["latest"]["avg_eu_out_5s"] == 350171922

    def test_latest_trend_is_not_range_filtered(self, client, api_headers):
        # fetch_latest_power_reading() is a deliberately separate,
        # unfiltered query - it should reflect the single most recent
        # reading regardless of what chart range happens to be selected.
        client.post(
            "/api/power",
            json={"stored": 100, "capacity": 1000, "avg_eu_in_5s": 5, "avg_eu_out_5s": 2},
            headers=api_headers)
        data = client.get("/api/power?range=hour").get_json()
        assert data["latest"]["avg_eu_in_5s"] == 5


class TestLatestReadingIsRangeIndependent:
    """Direct regression test for a real reported bug: the live "currently
    stored" readout changed depending on which chart range button was
    selected (e.g. Day showing a different % than Week). Root cause:
    "latest" was being read from the range-filtered, DOWNSAMPLED points
    array's last bucket (an average), not a true single reading - and
    different ranges downsample into differently-sized buckets, so that
    last bucket's average genuinely differed between them.

    IMPORTANT: this only manifests once _downsample's bucketing actually
    kicks in (store.power.MAX_POINTS=800) - a first version of this test
    inserted only a handful of readings and PASSED even with the bug
    deliberately reintroduced, because that few rows never exceeds the
    cap and _downsample returns them unchanged either way. Confirmed
    directly (reverted the fix, watched this fail, restored it, watched
    it pass) rather than trusted on the first write - not a hypothetical
    concern, an actual mistake caught while writing this test."""

    def _seed_dense_readings(self, count=2000):
        # ALL packed into the last 24h specifically, dense enough that
        # even the "day" range - the shortest, so the hardest to
        # accidentally exceed the cap on - comfortably exceeds
        # store.power.MAX_POINTS and forces real bucket-averaging.
        now = time.time()
        rows = []
        for i in range(count):
            seconds_ago = 86400 * (count - 1 - i) / (count - 1)
            rows.append((now - seconds_ago, 1000 + i, 5_000_000))
        conn = sqlite3.connect(config.POWER_DB_PATH)
        try:
            conn.executemany("INSERT INTO power_readings (ts, stored, capacity) VALUES (?, ?, ?)", rows)
            conn.commit()
        finally:
            conn.close()
        return rows

    def test_latest_stored_capacity_identical_across_every_range(self, client):
        rows = self._seed_dense_readings()
        true_latest_stored = rows[-1][1]  # the actual last-inserted, un-averaged value

        results = {}
        for range_key in ("hour", "day", "week", "month", "lifetime"):
            data = client.get(f"/api/power?range={range_key}").get_json()
            results[range_key] = (data["latest"]["stored"], data["latest"]["capacity"])

        assert len(set(results.values())) == 1
        stored, capacity = next(iter(results.values()))
        assert stored == true_latest_stored
        assert capacity == 5_000_000

    def test_chart_points_still_downsample_per_range(self, client):
        # Confirms the fix didn't accidentally disable downsampling for
        # the CHART itself - only the live "latest" readout needed to
        # stop depending on it.
        self._seed_dense_readings()
        day_points = client.get("/api/power?range=day").get_json()["points"]
        assert len(day_points) == store.power.MAX_POINTS


class TestPowerDbMigration:
    """Confirms a real production concern directly: power.db already
    exists with real data for actual users, and the trend-field columns
    were added after the fact. _init_power_db() has to migrate an
    existing table in place (ALTER TABLE ADD COLUMN) without ever losing
    existing rows, not just work correctly on a brand new database."""

    def test_existing_rows_survive_migration_and_new_columns_appear(self, api_headers):
        # Simulate a pre-existing power.db from before the trend-field
        # feature existed: drop down to the OLD schema directly, insert
        # a row, then re-run the same init function real startup uses.
        conn = sqlite3.connect(config.POWER_DB_PATH)
        try:
            conn.execute("DROP TABLE IF EXISTS power_readings")
            conn.execute("""
                CREATE TABLE power_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    stored REAL NOT NULL,
                    capacity REAL NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO power_readings (ts, stored, capacity) VALUES (?, ?, ?)",
                (REAL_TS, 500000, 5000000))
            # Files from before schema versioning all report version 0.
            conn.execute("PRAGMA user_version = 0")
            conn.commit()
        finally:
            conn.close()

        db.init_power_db()  # the same migration real startup runs

        conn = sqlite3.connect(config.POWER_DB_PATH)
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(power_readings)")}
            rows = conn.execute("SELECT ts, stored, capacity FROM power_readings").fetchall()
        finally:
            conn.close()

        assert "avg_eu_in_5s" in cols
        assert "time_to_empty_minutes" in cols
        assert rows == [(REAL_TS, 500000.0, 5000000.0)]  # old data untouched
        machines = conn_rows("SELECT machine FROM power_readings")
        assert machines == [("",)]  # the unnamed machine's, so it keeps its chart

    def test_migration_is_idempotent(self):
        # Running it multiple times (as every server startup does)
        # must never error, even once the columns already exist.
        db.init_power_db()
        db.init_power_db()
        db.init_power_db()


def conn_rows(sql):
    conn = sqlite3.connect(config.POWER_DB_PATH)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class TestMachines:
    def post(self, client, api_headers, stored, machine=None):
        body = {"stored": stored, "capacity": 1000}
        if machine is not None:
            body["machine"] = machine
        return client.post("/api/power", json=body, headers=api_headers)

    def test_each_machine_has_its_own_readings(self, client, api_headers):
        self.post(client, api_headers, 100)
        self.post(client, api_headers, 200, "Turbines")
        self.post(client, api_headers, 300, " Turbines ")  # trimmed
        data = client.get("/api/power").get_json()
        assert data["machine"] == ""
        assert [m["name"] for m in data["machines"]] == ["", "Turbines"]
        # Each with its own latest reading, for the picker and the tab's fill.
        assert [(m["stored"], m["capacity"]) for m in data["machines"]] == [(100, 1000), (300, 1000)]
        assert [p["stored"] for p in data["points"]] == [100]
        turbines = client.get("/api/power?machine=Turbines").get_json()
        assert turbines["machine"] == "Turbines"
        assert [p["stored"] for p in turbines["points"]] == [200, 300]
        assert turbines["latest"]["stored"] == 300

    def test_only_named_machines_shows_the_first(self, client, api_headers):
        self.post(client, api_headers, 1, "LSC")
        self.post(client, api_headers, 2, "Batteries")
        data = client.get("/api/power").get_json()
        assert (data["machine"], data["latest"]["stored"]) == ("Batteries", 2)

    def test_unknown_machine_falls_back(self, client, api_headers):
        self.post(client, api_headers, 1, "LSC")
        assert client.get("/api/power?machine=nope").get_json()["machine"] == "LSC"

    def test_wireless_network_is_the_default_while_it_reports(self, client, api_headers):
        # An LSC in wireless mode sits near empty; the network holds the power.
        self.post(client, api_headers, 1, "LSC")
        client.post("/api/power", json={"stored": 6.5e16, "capacity": 0, "machine": "Wireless"}, headers=api_headers)
        data = client.get("/api/power").get_json()
        assert (data["machine"], data["latest"]["stored"]) == ("Wireless", 6.5e16)
        assert client.get("/api/power?machine=LSC").get_json()["machine"] == "LSC"
        assert 'content="Power · Wireless"' in client.get("/power").get_data(as_text=True)

    def test_lsc_in_wireless_mode_gets_its_threshold(self, client, api_headers):
        body = {"stored": 6.2e15, "capacity": 9.2e18, "machine": "LSC"}
        caps = {"UHV": 1, "UEV": 1, "UIV": 0, "UMV": 0, "UXV": 9, "bad": "x"}  # unknown tiers ignored
        client.post(
            "/api/power",
            json={**body, "wireless_mode": True, "capacitors": caps, "avg_eu_in_1h": 7, "avg_eu_out_1h": 5},
            headers=api_headers,
        )
        data = client.get("/api/power?machine=LSC").get_json()
        assert (data["latest"]["avg_eu_in_1h"], data["latest"]["avg_eu_out_1h"]) == (7, 5)
        assert data["latest"]["wireless_threshold"] == 6e13 + 6e15
        assert data["points"][-1]["wireless_threshold"] == 6e13 + 6e15
        assert data["machines"][0]["wireless_threshold"] == 6e13 + 6e15
        html = client.get("/power?machine=LSC").get_data(as_text=True)
        assert "102.3% of its 6.06P EU wireless threshold" in html

    def test_non_finite_numbers_are_refused_or_left_out(self, client, api_headers):
        # One Infinity stored would make every /api/power reply invalid JSON.
        post = lambda body: client.post("/api/power", json=body, headers=api_headers)  # noqa: E731
        assert post({"stored": float("inf"), "capacity": 1, "machine": "LSC"}).status_code == 400
        assert post({"stored": 1, "capacity": float("nan"), "machine": "LSC"}).status_code == 400
        assert post({"stored": "1", "capacity": 2, "machine": "LSC"}).status_code == 400
        assert post({
            "stored": 1, "capacity": 2, "machine": "LSC", "avg_eu_in_5s": float("inf"), "avg_eu_out_5s": "x",
            "wireless_mode": True, "capacitors": {"UEV": float("inf"), "UHV": 1},
        }).status_code == 200
        res = client.get("/api/power?machine=LSC")
        assert "Infinity" not in res.get_data(as_text=True) and "NaN" not in res.get_data(as_text=True)
        latest = res.get_json()["latest"]
        assert (latest["avg_eu_in_5s"], latest["avg_eu_out_5s"], latest["wireless_threshold"]) == (None, None, 6e13)

    def test_wireless_mode_is_kept_for_the_badge(self, client, api_headers):
        # An LSC that says it's not in wireless mode, one on an older
        # script that doesn't say, and batteries that never do.
        for machine, mode in (("Normal LSC", False), ("Old script", None), ("Batteries", "yes")):
            body = {"stored": 1, "capacity": 10, "machine": machine}
            if mode is not None:
                body["wireless_mode"] = mode
            client.post("/api/power", json=body, headers=api_headers)
        modes = {m["name"]: m["wireless_mode"] for m in client.get("/api/power").get_json()["machines"]}
        assert modes == {"Normal LSC": False, "Old script": None, "Batteries": None}

    def test_no_threshold_without_wireless_mode(self, client, api_headers):
        body = {"stored": 1, "capacity": 9, "machine": "LSC", "capacitors": {"UEV": 1}}
        client.post("/api/power", json={**body, "wireless_mode": False}, headers=api_headers)
        client.post("/api/power", json=body, headers=api_headers)
        client.post("/api/power", json={**body, "wireless_mode": True, "capacitors": {"UEV": 0}}, headers=api_headers)
        rows = conn_rows("SELECT wireless_threshold FROM power_readings")
        assert rows == [(None,), (None,), (None,)]

    def test_stale_wireless_network_is_not_the_default(self, client, api_headers):
        self.post(client, api_headers, 1, "LSC")
        client.post(
            "/api/power",
            json={"stored": 6.5e16, "capacity": 0, "machine": "Wireless", "timestamp": time.time() - 600},
            headers=api_headers,
        )
        assert client.get("/api/power").get_json()["machine"] == "LSC"

    def test_bad_names_rejected(self, client, api_headers):
        assert self.post(client, api_headers, 1, 5).status_code == 400
        assert self.post(client, api_headers, 1, "x" * 65).status_code == 400
        assert client.get("/api/power").get_json()["machines"] == []

    def test_chart_png_per_machine(self, client, api_headers):
        self.post(client, api_headers, 1, "LSC")
        res = client.get("/api/power/chart.png?machine=LSC")
        assert res.status_code == 200 and res.mimetype == "image/png"

    def test_link_preview_names_the_machine(self, client, api_headers):
        self.post(client, api_headers, 500, "Big Batteries")
        html = client.get("/power?machine=Big%20Batteries").get_data(as_text=True)
        assert 'content="Power · Big Batteries"' in html
        assert "chart.png?range=day&amp;machine=Big%20Batteries" in html


class TestPowerDownsampling:
    def _seed(self, rows):
        conn = sqlite3.connect(config.POWER_DB_PATH)
        try:
            conn.executemany(
                "INSERT INTO power_readings (ts, stored, capacity) VALUES (?, ?, ?)", rows
            )
            conn.commit()
        finally:
            conn.close()

    def test_under_the_cap_returns_raw_readings(self):
        now = time.time()
        rows = [(now - 60 * i, 1000 + i, 5000) for i in range(10)]
        self._seed(rows)
        result = store.power.readings(now - 3600, max_points=100)
        assert result == [(*row, None) for row in sorted(rows)]  # no wireless threshold

    def test_over_the_cap_is_averaged_into_ordered_buckets(self):
        now = time.time()
        rows = [(now - 60 * i, i * 10, 1000) for i in range(1000)]
        self._seed(rows)
        result = store.power.readings(None, max_points=50)
        assert len(result) == 50
        assert [r[0] for r in result] == sorted(r[0] for r in result)
        # Averaging never invents values outside the real data's range.
        assert min(r[1] for r in result) >= 0
        assert max(r[1] for r in result) <= 9990
        assert all(r[2] == 1000 for r in result)

    def test_readings_with_one_timestamp_do_not_divide_by_zero(self):
        now = time.time()
        self._seed([(now, i, 1000) for i in range(20)])
        result = store.power.readings(now - 3600, max_points=10)
        assert len(result) == 1
        assert result[0][1] == sum(range(20)) / 20


class TestTimestamps:
    def post(self, client, api_headers, ts):
        return client.post("/api/power", json={"stored": 1, "capacity": 2, "timestamp": ts}, headers=api_headers)

    @pytest.mark.parametrize("offset", [0, -3600, -86000, 200])
    def test_recent_ones_kept(self, client, api_headers, offset):
        now = time.time()
        assert self.post(client, api_headers, now + offset).status_code == 200
        assert client.get("/api/power").get_json()["latest"]["t"] == pytest.approx(now + offset)

    @pytest.mark.parametrize("ts", [1500000000, 12345, "now", True, "an hour ahead"])
    def test_far_off_or_not_a_number_rejected(self, client, api_headers, ts):
        if ts == "an hour ahead":
            ts = time.time() + 3600
        res = self.post(client, api_headers, ts)
        assert res.status_code == 400
        assert client.get("/api/power").get_json()["latest"] is None

    def test_migration_drops_impossible_readings(self):
        now = time.time()
        conn = sqlite3.connect(config.POWER_DB_PATH)
        try:
            conn.executemany(
                "INSERT INTO power_readings (ts, stored, capacity) VALUES (?, ?, ?)",
                [(1500000000, 1, 2), (now - 40 * 86400, 3, 4), (now, 5, 6), (now + 3 * 86400, 7, 8)],
            )
            conn.execute(f"PRAGMA user_version = {db.POWER_MIGRATIONS.index(db._power_drop_impossible_times)}")
            conn.commit()
        finally:
            conn.close()
        db.init_power_db()
        assert [r[0] for r in conn_rows("SELECT stored FROM power_readings ORDER BY ts")] == [3, 5]


class TestMachineNames:
    """Which machine a post is (store.power.resolve), and renaming."""

    def post(self, client, api_headers, stored, machine=None, address=None, lsc=True, **extra):
        body = {"stored": stored, "capacity": 1000, **extra}
        if machine is not None:
            body["machine"] = machine
        if address is not None:
            body["address"] = address
        if lsc:
            body["wireless_mode"] = False
        res = client.post("/api/power", json=body, headers=api_headers)
        assert res.status_code == 200, res.get_data(as_text=True)

    def names(self, client):
        return sorted(m["name"] for m in client.get("/api/power").get_json()["machines"])

    def stored(self, client, name):
        return [p["stored"] for p in client.get(f"/api/power?machine={name}").get_json()["points"]]

    def test_an_older_script_without_an_address_stays_unnamed(self, client, api_headers):
        self.post(client, api_headers, 1)
        self.post(client, api_headers, 2)
        assert self.names(client) == [""]

    def test_the_first_address_keeps_the_unnamed_history(self, client, api_headers):
        self.post(client, api_headers, 1)  # from before addresses were sent
        self.post(client, api_headers, 2, address="a")
        assert self.names(client) == [""]
        assert self.stored(client, "") == [1, 2]

    def test_a_second_unnamed_machine_numbers_both(self, client, api_headers):
        self.post(client, api_headers, 1, address="a")
        self.post(client, api_headers, 2, address="b")
        self.post(client, api_headers, 3, address="a")
        self.post(client, api_headers, 4, address="b")
        assert self.names(client) == ["LSC 1", "LSC 2"]
        assert self.stored(client, "LSC 1") == [1, 3]  # the old '' history moved with it
        assert self.stored(client, "LSC 2") == [2, 4]
        # An older script's unnamed posts follow the first one.
        self.post(client, api_headers, 5)
        assert self.stored(client, "LSC 1") == [1, 3, 5]

    def test_a_machine_that_isnt_an_lsc_is_storage(self, client, api_headers):
        self.post(client, api_headers, 1, address="a", lsc=False)
        self.post(client, api_headers, 2, address="b", lsc=False)
        self.post(client, api_headers, 3, address="c")
        assert self.names(client) == ["LSC 1", "Storage 1", "Storage 2"]

    def test_named_machines_keep_their_names(self, client, api_headers):
        self.post(client, api_headers, 1, "Batteries", address="a")
        self.post(client, api_headers, 2, address="b")
        self.post(client, api_headers, 3, "Batteries", address="a")
        assert self.names(client) == ["", "Batteries"]
        # Two computers naming theirs the same share it, as before.
        self.post(client, api_headers, 4, "Batteries", address="c")
        assert self.stored(client, "Batteries") == [1, 3, 4]

    def test_a_numbered_machine_named_in_machines_keeps_its_history(self, client, api_headers):
        self.post(client, api_headers, 1, address="a")
        self.post(client, api_headers, 2, address="b")
        self.post(client, api_headers, 3, "Nether", address="b")
        assert self.names(client) == ["LSC 1", "Nether"]
        assert self.stored(client, "Nether") == [2, 3]

    def test_the_wireless_network_is_unaffected(self, client, api_headers):
        self.post(client, api_headers, 1, address="a")
        self.post(client, api_headers, 2, address="b")
        client.post("/api/power", json={"stored": 9, "capacity": 0, "machine": "Wireless"}, headers=api_headers)
        assert self.names(client) == ["LSC 1", "LSC 2", "Wireless"]

    def test_bad_addresses_rejected(self, client, api_headers):
        for address in (5, "", "x" * 65):
            res = client.post("/api/power", json={"stored": 1, "capacity": 2, "address": address}, headers=api_headers)
            assert res.status_code == 400

    def rename(self, client, old, new):
        return client.put("/api/admin/power/machines", json={"from": old, "to": new})

    def test_rename_keeps_history_and_later_posts(self, client, api_headers):
        from gcm import state
        self.post(client, api_headers, 1, address="a")
        self.post(client, api_headers, 2, address="b", poll_seconds=30)
        login_as(client, "boss", role="admin")
        res = self.rename(client, "LSC 2", "  Nether LSC ")
        assert res.status_code == 200 and res.get_json() == {"name": "Nether LSC"}
        self.post(client, api_headers, 3, address="b")
        assert self.names(client) == ["LSC 1", "Nether LSC"]
        assert self.stored(client, "Nether LSC") == [2, 3]
        assert "LSC 2" not in state.power_poll_seconds and "Nether LSC" in state.power_poll_seconds

    def test_rename_a_machine_from_an_older_script(self, client, api_headers):
        self.post(client, api_headers, 1, "Batteries")
        self.post(client, api_headers, 2)
        login_as(client, "boss", role="admin")
        assert self.rename(client, "Batteries", "Big batteries").status_code == 200
        assert self.rename(client, "", "Home").status_code == 200
        self.post(client, api_headers, 3, "Batteries")
        self.post(client, api_headers, 4)
        assert self.names(client) == ["Big batteries", "Home"]
        assert self.stored(client, "Big batteries") == [1, 3]
        assert self.stored(client, "Home") == [2, 4]

    def test_rename_refusals(self, client, api_headers):
        self.post(client, api_headers, 1, "A")
        self.post(client, api_headers, 2, "B")
        login_as(client, "op", role="operator")
        assert self.rename(client, "A", "C").status_code == 403
        login_as(client, "boss", role="admin")
        assert self.rename(client, "A", "B").status_code == 409
        assert self.rename(client, "A", "   ").status_code == 400
        assert self.rename(client, "A", "x" * 65).status_code == 400
        assert self.rename(client, "nope", "C").status_code == 404
        assert client.put("/api/admin/power/machines", json={"from": "A"}).status_code == 400
        assert self.rename(client, "A", "A").status_code == 200
        assert self.names(client) == ["A", "B"]

    def test_a_configured_name_doesnt_take_over_another_machine_given_it(self, client, api_headers):
        # An admin named an unnamed machine "Batteries"; another computer's
        # MACHINES then names its own the same: that one is numbered.
        self.post(client, api_headers, 1, address="a")
        self.post(client, api_headers, 2, address="b")
        login_as(client, "boss", role="admin")
        assert self.rename(client, "LSC 2", "Batteries").status_code == 200
        self.post(client, api_headers, 3, "Batteries", address="c")
        self.post(client, api_headers, 4, "Batteries", address="c")
        self.post(client, api_headers, 5, address="b")
        assert self.names(client) == ["Batteries", "Batteries 2", "LSC 1"]
        assert self.stored(client, "Batteries") == [2, 5]
        assert self.stored(client, "Batteries 2") == [3, 4]
