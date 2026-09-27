"""ME network items: the network_snapshot mirror and change-only
item_history (item_history.db), and per-user item pins
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
    now = time.time()
    rows = [
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
    with db.transaction(db.item_history_db) as conn:
        conn.execute("DELETE FROM network_snapshot")
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

    if not changes:
        return
    with db.transaction(db.item_history_db) as conn:
        conn.executemany(
            "INSERT INTO item_history (item_key, label, size, recorded_at) VALUES (?, ?, ?, ?)",
            changes,
        )


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
            "DELETE FROM item_history WHERE item_key = ?", [(k,) for k in base_keys]
        )
        conn.executemany(
            "INSERT INTO item_history (item_key, label, size, recorded_at) VALUES (?, ?, ?, ?)",
            [(k, label, size, now) for k, (label, size) in current_sizes.items() if k in base_keys],
        )
        conn.execute("DELETE FROM pending_nbt_cleanup")
    return True


def history(key, since):
    """(recorded_at, size) rows for one item from `since` on (None: all),
    ascending."""
    with db.transaction(db.item_history_db) as conn:
        if since is None:
            return conn.execute(
                "SELECT recorded_at, size FROM item_history WHERE item_key = ? "
                "ORDER BY recorded_at ASC",
                (key,),
            ).fetchall()

        rows = conn.execute(
            "SELECT recorded_at, size FROM item_history WHERE item_key = ? AND recorded_at >= ? "
            "ORDER BY recorded_at ASC",
            (key, since),
        ).fetchall()
        # A step chart starting mid-air (no point until the first
        # change INSIDE the range) looks wrong/misleading - prepend
        # the last known value from BEFORE the range started, if any,
        # so the line correctly holds its prior value from the very
        # left edge of the chart.
        lead = conn.execute(
            "SELECT recorded_at, size FROM item_history WHERE item_key = ? AND recorded_at < ? "
            "ORDER BY recorded_at DESC LIMIT 1",
            (key, since),
        ).fetchone()
    if lead:
        rows = [(since, lead[1])] + rows
    return rows


def last_recorded(key):
    """(label, size) from the newest history row that has a label, or
    (None, None)."""
    with db.transaction(db.item_history_db) as conn:
        row = conn.execute(
            "SELECT label, size FROM item_history WHERE item_key = ? AND label IS NOT NULL "
            "ORDER BY recorded_at DESC LIMIT 1",
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
