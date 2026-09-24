import sqlite3
import time

from gcm import config, db, store
from gcm.routes import power


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
    kicks in (POWER_MAX_POINTS=800) - a first version of this test
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
        # POWER_MAX_POINTS and forces real bucket-averaging.
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
        assert len(day_points) == power.POWER_MAX_POINTS


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
                (1000.0, 500000, 5000000))
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
        assert rows == [(1000.0, 500000.0, 5000000.0)]  # old data untouched

    def test_migration_is_idempotent(self):
        # Running it multiple times (as every server startup does)
        # must never error, even once the columns already exist.
        db.init_power_db()
        db.init_power_db()
        db.init_power_db()


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
        assert result == sorted(rows)

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
