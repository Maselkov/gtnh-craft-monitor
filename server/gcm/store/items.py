"""ME network items: the network_snapshot mirror and change-only
item_history and history_items (item_history.db), and per-user item pins
(app.db)."""

import time

from gcm import db


def item_key(mod, internal, damage, kind, variant=None):
    # Canonical, stable identifier - built explicitly rather than
    # relying on dict/JSON key ordering, since this is used both as the
    # SQLite storage key and as the browser's shareable URL parameter.
    # variant (see gcm/inventory.py) separates NBT variants of one item
    # id; items without NBT have none, keeping the key they always had.
    key = f"{mod or ''}|{internal or ''}|{damage if damage is not None else ''}|{kind or 'item'}"
    return f"{key}|{variant}" if variant else key


def key_of(item):
    return item_key(
        item.get("mod"),
        item.get("internal"),
        item.get("damage"),
        item.get("kind"),
        item.get("variant"),
    )


def save_snapshot(items):
    """Wholesale replace, not change-only - this is a MIRROR of the live
    snapshot, not a history log. Runs after every real scan; the DELETE+
    INSERT happens in one transaction so a reader never sees a half-
    written table.

    INSERT OR REPLACE, not a plain INSERT: a production crash came from
    NBT variants of one item sharing a key, before keys carried the
    variant. inventory.finish_scan() now merges any stacks that still
    share one, so REPLACE is only a last line of defense."""
    rows = _snapshot_rows(items)
    with db.transaction(db.item_history_db) as conn:
        conn.execute("DELETE FROM network_snapshot")
        _insert_snapshot_rows(conn, rows)


def update_snapshot(removed_keys, items):
    """Replaces just some of the mirror's rows: deletes removed_keys,
    then writes items - for a check of a few watched items between full
    scans (inventory.apply_levels()), where rewriting every row would be
    thousands of writes for a handful of changes."""
    rows = _snapshot_rows(items)
    with db.transaction(db.item_history_db) as conn:
        conn.executemany(
            "DELETE FROM network_snapshot WHERE item_key = ?", [(key,) for key in removed_keys]
        )
        _insert_snapshot_rows(conn, rows)


def _snapshot_rows(items):
    now = time.time()
    return [
        (
            key_of(it),
            it.get("mod"),
            it.get("internal"),
            it.get("damage"),
            it.get("kind") or "item",
            it.get("name"),
            it.get("size", 0) or 0,
            1 if it.get("isCraftable") else 0,
            now,
            it.get("variant"),
            it.get("variant_name"),
        )
        for it in items
    ]


def _insert_snapshot_rows(conn, rows):
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO network_snapshot "
            "(item_key, mod, internal, damage, kind, name, size, is_craftable, updated_at, "
            "variant, variant_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def load_snapshot():
    """(items, updated_at) as last saved by save_snapshot(); items is
    empty and updated_at None if nothing was. Items carry no icon - the
    caller attaches those."""
    with db.transaction(db.item_history_db) as conn:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind, name, size, is_craftable, updated_at, "
            "variant, variant_name FROM network_snapshot"
        ).fetchall()
    items = []
    for r in rows:
        item = {
            "mod": r[0],
            "internal": r[1],
            "damage": r[2],
            "kind": r[3],
            "name": r[4],
            "size": r[5],
            "isCraftable": bool(r[6]),
        }
        if r[8]:
            item["variant"] = r[8]
        if r[9]:
            item["variant_name"] = r[9]
        items.append(item)
    return items, max((r[7] for r in rows), default=None)


def _item_ids(conn, keys_labels):
    """{item_key: id} for each (item_key, label), adding history_items
    rows as needed and keeping each one's latest non-empty label."""
    conn.executemany(
        "INSERT INTO history_items (item_key, label) VALUES (?, ?) "
        "ON CONFLICT (item_key) DO UPDATE SET label = COALESCE(excluded.label, label)",
        keys_labels,
    )
    keys = list({key for key, _ in keys_labels})
    ids = {}
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        ids.update(conn.execute(
            f"SELECT item_key, id FROM history_items WHERE item_key IN ({','.join('?' * len(chunk))})",
            chunk,
        ).fetchall())
    return ids


def add_rows(rows):
    """Writes (item_key, label, size, recorded_at) rows. label may be
    None (an item gone from the network), which keeps the last one."""
    if not rows:
        return
    with db.transaction(db.item_history_db) as conn:
        _add_rows(conn, rows)


def _add_rows(conn, rows):
    ids = _item_ids(conn, [(key, label) for key, label, _, _ in rows])
    conn.executemany(
        "INSERT OR REPLACE INTO item_history (item_id, recorded_at, size) VALUES (?, ?, ?)",
        [(ids[key], recorded_at, size) for key, _, size, recorded_at in rows],
    )


def record_changes(old_items, new_items):
    """old_items/new_items: lists of item dicts (mod, internal, damage,
    kind, name, size). Writes one row per item whose size differs from
    the last-known value, including items present before but absent now
    (size implicitly 0 - they only vanish from a scan by genuinely
    having zero stock and no craftable pattern, since getItemsInNetworkById/
    getFluidsInNetwork only ever return entries AE2 itself considers
    present)."""
    old_by_key = {key_of(it): it.get("size", 0) for it in old_items or []}

    now = time.time()
    changes = []
    seen_keys = set()
    for it in new_items or []:
        key = key_of(it)
        seen_keys.add(key)
        new_size = it.get("size", 0)
        if old_by_key.get(key) != new_size:
            changes.append((key, it.get("name"), new_size, now))

    for key, old_size in old_by_key.items():
        if key not in seen_keys and old_size != 0:
            changes.append((key, None, 0, now))

    add_rows(changes)


def clear_legacy_variant_history(base_keys, current_sizes):
    """One-time cleanup, done by the first scan after NBT variants got
    their own keys (the item_history migration leaves a marker): history
    recorded under the plain key of an item with NBT variants mixed every
    variant's count into one series. Deletes it, then records the current
    size of any plain stack still under that key so its chart has a
    starting point. Returns whether it ran."""
    now = time.time()
    with db.transaction(db.item_history_db) as conn:
        if not conn.execute("SELECT 1 FROM pending_nbt_cleanup").fetchone():
            return False
        conn.executemany(
            "DELETE FROM item_history WHERE item_id = (SELECT id FROM history_items WHERE item_key = ?)",
            [(k,) for k in base_keys],
        )
        _add_rows(conn, [(k, label, size, now) for k, (label, size) in current_sizes.items() if k in base_keys])
        conn.execute("DELETE FROM pending_nbt_cleanup")
    return True


def _item_id(conn, key):
    row = conn.execute("SELECT id FROM history_items WHERE item_key = ?", (key,)).fetchone()
    return row[0] if row else None


def history(key, since):
    """(recorded_at, size) rows for one item from `since` on (None: all),
    ascending."""
    with db.transaction(db.item_history_db) as conn:
        item_id = _item_id(conn, key)
        if item_id is None:
            return []
        if since is None:
            return conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_id = ? "
                "ORDER BY recorded_at ASC",
                (item_id,),
            ).fetchall()

        rows = conn.execute(
            "SELECT recorded_at, size FROM item_history WHERE item_id = ? AND recorded_at >= ? "
            "ORDER BY recorded_at ASC",
            (item_id, since),
        ).fetchall()
        # A step chart starting mid-air (no point until the first
        # change INSIDE the range) looks wrong/misleading - prepend
        # the last known value from BEFORE the range started, if any,
        # so the line correctly holds its prior value from the very
        # left edge of the chart.
        lead = conn.execute(
            "SELECT recorded_at, size FROM item_history WHERE item_id = ? AND recorded_at < ? "
            "ORDER BY recorded_at DESC LIMIT 1",
            (item_id, since),
        ).fetchone()
    if lead:
        rows = [(since, lead[1])] + rows
    return rows


def last_recorded(key):
    """(label, size): the item's last known name and its newest recorded
    size, or (None, None) when it was never recorded with a name."""
    with db.transaction(db.item_history_db) as conn:
        row = conn.execute(
            "SELECT i.label, (SELECT size FROM item_history h WHERE h.item_id = i.id "
            "ORDER BY h.recorded_at DESC LIMIT 1) "
            "FROM history_items i WHERE i.item_key = ? AND i.label IS NOT NULL",
            (key,),
        ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def pins(user_id):
    with db.transaction(db.app_db) as conn:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind, variant FROM user_item_pins WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return [
        {"mod": r[0], "internal": r[1], "damage": r[2], "kind": r[3], "variant": r[4]}
        for r in rows
    ]


def pin(user_id, mod, internal, damage, kind, variant=None):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_item_pins "
            "(user_id, item_key, mod, internal, damage, kind, variant, pinned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                item_key(mod, internal, damage, kind, variant),
                mod,
                internal,
                damage,
                kind,
                variant or None,
                time.time(),
            ),
        )


def unpin(user_id, mod, internal, damage, kind, variant=None):
    with db.transaction(db.app_db) as conn:
        conn.execute(
            "DELETE FROM user_item_pins WHERE user_id = ? AND item_key = ?",
            (user_id, item_key(mod, internal, damage, kind, variant)),
        )


def thin(tier, bucket_seconds, before):
    """Keeps only the last row per item per bucket_seconds (UTC-aligned)
    for rows recorded before `before`, starting where this tier last
    stopped. The last row of a bucket is the value the step chart holds
    through it, so charts and rates read the same, just coarser. Works
    item by item along the primary key; returns the rows deleted."""
    with db.transaction(db.item_history_db) as conn:
        row = conn.execute("SELECT until FROM history_thinned WHERE tier = ?", (tier,)).fetchone()
        item_ids = [r[0] for r in conn.execute("SELECT id FROM history_items")]
    since = row[0] if row else 0
    # Whole buckets only: a bucket straddling `before` waits for the next run.
    before = before // bucket_seconds * bucket_seconds
    if before <= since:
        return 0
    deleted = 0
    for start in range(0, len(item_ids), 500):
        with db.transaction(db.item_history_db) as conn:
            for item_id in item_ids[start:start + 500]:
                deleted += conn.execute(
                    "DELETE FROM item_history WHERE item_id = ? AND recorded_at >= ? AND recorded_at < ? "
                    "AND recorded_at NOT IN ("
                    " SELECT MAX(recorded_at) FROM item_history"
                    " WHERE item_id = ? AND recorded_at >= ? AND recorded_at < ?"
                    " GROUP BY CAST(recorded_at / ? AS INTEGER))",
                    (item_id, since, before, item_id, since, before, bucket_seconds),
                ).rowcount
    with db.transaction(db.item_history_db) as conn:
        conn.execute(
            "INSERT INTO history_thinned (tier, until) VALUES (?, ?) "
            "ON CONFLICT (tier) DO UPDATE SET until = excluded.until",
            (tier, before),
        )
    return deleted


def vacuum_if_mostly_free():
    return db.vacuum_if_mostly_free(db.item_history_db)
