import os
import sqlite3
import time

import pytest

from gcm import config, db, retention, store

KEY = store.items.item_key("gregtech", "gt.metaitem.01", 11305, "item")
DAY = 86400


def value_at(rows, t):
    """The step chart's value at t: the last row at or before it."""
    before = [size for at, size in rows if at <= t]
    return before[-1] if before else None


def old_schema_db(path, rows):
    """An item_history.db at schema version 3 (before compaction)."""
    db.migrate(lambda: sqlite3.connect(path), db.ITEM_HISTORY_MIGRATIONS[:3])
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO item_history (item_key, label, size, recorded_at) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


class TestCompactMigration:
    def test_rows_and_latest_label_carried_over(self, tmp_path):
        path = str(tmp_path / "h.db")
        old_schema_db(path, [
            (KEY, "Old Name", 10, 100.0),
            (KEY, "New Name", 20, 200.0),
            (KEY, None, 0, 300.0),          # gone from the network: no label
            (KEY, "New Name", 25, 300.0),   # same time, written later: wins
            ("other|x|0|item", "Other", 5, 150.0),
        ])
        db.migrate(lambda: sqlite3.connect(path), db.ITEM_HISTORY_MIGRATIONS)
        conn = sqlite3.connect(path)
        try:
            items = dict(conn.execute("SELECT item_key, label FROM history_items").fetchall())
            rows = conn.execute(
                "SELECT recorded_at, size FROM item_history h JOIN history_items i ON i.id = h.item_id "
                "WHERE i.item_key = ? ORDER BY recorded_at", (KEY,)).fetchall()
        finally:
            conn.close()
        assert items == {KEY: "New Name", "other|x|0|item": "Other"}
        assert rows == [(100.0, 10), (200.0, 20), (300.0, 25)]

    def test_about_five_times_smaller(self, tmp_path):
        rows = [(f"gregtech|gt.metaitem.0{n % 3}|{n}|item", f"Some Long Item Name {n}", t * 7, 1.79e9 + t * 600)
                for n in range(200) for t in range(100)]
        before, after = str(tmp_path / "before.db"), str(tmp_path / "after.db")
        old_schema_db(before, rows)
        old_schema_db(after, rows)
        db.migrate(lambda: sqlite3.connect(after), db.ITEM_HISTORY_MIGRATIONS)
        db.vacuum_if_mostly_free(lambda: sqlite3.connect(after))
        assert os.path.getsize(after) < os.path.getsize(before) / 3


class TestStore:
    def test_last_recorded_is_the_name_and_newest_size(self):
        store.items.add_rows([(KEY, "Steel Plate", 40, 1.0), (KEY, None, 0, 2.0)])
        assert store.items.last_recorded(KEY) == ("Steel Plate", 0)
        assert store.items.last_recorded("nope") == (None, None)

    def test_unknown_item_has_no_history(self):
        assert store.items.history("nope", None) == []


def ten_minute_history(days, now):
    """One change every 10 minutes for `days`, ending at now."""
    start = now - days * DAY
    return [(KEY, "Steel Plate", float(n % 97), start + n * 600) for n in range(days * 144)]


class TestThinning:
    NOW = 20833 * 86400.0  # a day boundary, so cutoffs fall on one

    def test_hourly_after_30_days_keeps_the_chart(self):
        rows = ten_minute_history(40, self.NOW)
        store.items.add_rows(rows)
        before = store.items.history(KEY, None)
        deleted = retention.run_once(self.NOW)
        after = store.items.history(KEY, None)

        cutoff = self.NOW - 30 * DAY
        old = [r for r in after if r[0] < cutoff]
        assert len(old) == 10 * 24  # one per hour for the 10 old days
        assert [r for r in after if r[0] >= cutoff] == [r for r in before if r[0] >= cutoff]
        assert deleted == {"hourly": len(before) - len(after), "daily": 0}
        # The chart reads the same up to every hour boundary (a row right on
        # one belongs to the next hour, so the check is a second before).
        for t in range(int(self.NOW - 40 * DAY) + 3600, int(self.NOW), 3600):
            assert value_at(after, t - 1) == value_at(before, t - 1)

    def test_next_run_only_does_new_days(self):
        store.items.add_rows(ten_minute_history(40, self.NOW))
        retention.run_once(self.NOW)
        assert retention.run_once(self.NOW) == {"hourly": 0, "daily": 0}
        assert retention.run_once(self.NOW + DAY)["hourly"] == 24 * 5  # the day that turned 30

    def test_daily_after_a_year(self, monkeypatch):
        monkeypatch.setattr(config, "HISTORY_DAILY_AFTER_DAYS", 35)
        store.items.add_rows(ten_minute_history(40, self.NOW))
        retention.run_once(self.NOW)
        after = store.items.history(KEY, None)
        assert len([r for r in after if r[0] < self.NOW - 35 * DAY]) == 5

    def test_tiers_turned_off(self, monkeypatch):
        monkeypatch.setattr(config, "HISTORY_HOURLY_AFTER_DAYS", 0)
        monkeypatch.setattr(config, "HISTORY_DAILY_AFTER_DAYS", 0)
        store.items.add_rows(ten_minute_history(40, self.NOW))
        assert retention.run_once(self.NOW) == {}
        assert len(store.items.history(KEY, None)) == 40 * 144

    def test_range_lead_value_survives(self, client):
        store.items.add_rows([(KEY, "Steel Plate", 5, self.NOW - 50 * DAY + 10),
                              (KEY, "Steel Plate", 6, self.NOW - 50 * DAY + 20)])
        retention.run_once(self.NOW)
        assert store.items.history(KEY, self.NOW - 40 * DAY) == [(self.NOW - 40 * DAY, 6)]


def test_vacuum_only_when_mostly_free(tmp_path):
    path = str(tmp_path / "v.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("x" * 500,)] * 2000)
    conn.commit()
    conn.close()
    assert db.vacuum_if_mostly_free(lambda: sqlite3.connect(path)) is False
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM t")
    conn.commit()
    conn.close()
    size = os.path.getsize(path)
    assert db.vacuum_if_mostly_free(lambda: sqlite3.connect(path)) is True
    assert os.path.getsize(path) < size / 5
