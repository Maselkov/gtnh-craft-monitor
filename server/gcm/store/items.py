"""ME network items: the network_snapshot mirror and change-only
item_history (item_history.db), and per-user item pins
(craft_history.db)."""

import time

from gcm import db


def item_key(mod, internal, damage, kind):
    # Canonical, stable identifier - built explicitly rather than
    # relying on dict/JSON key ordering, since this is used both as the
    # SQLite storage key and as the browser's shareable URL parameter.
    return f"{mod or ''}|{internal or ''}|{damage if damage is not None else ''}|{kind or 'item'}"


def _key_of(item):
    return item_key(
        item.get("mod"), item.get("internal"), item.get("damage"), item.get("kind")
    )


def save_snapshot(items):
    """Wholesale replace, not change-only - this is a MIRROR of the live
    snapshot, not a history log. Runs after every real scan; the DELETE+
    INSERT happens in one transaction so a reader never sees a half-
    written table.

    INSERT OR REPLACE, not a plain INSERT - confirmed from a real
    production crash: a scan's item list can apparently contain two or
    more entries resolving to the same item_key (exact cause not
    pinned down - a plain INSERT just surfaces it as an uncaught
    IntegrityError instead of handling it). Duplicates aren't corruption
    worth treating as fatal either way, so REPLACE just lets the later
    occurrence in the list win, matching ordinary "last write wins"
    semantics rather than crashing the entire scan/finish request over
    what's genuinely a best-effort persistence step, not the actual
    scan result the website itself depends on."""
    now = time.time()
    rows = [
        (
            _key_of(it),
            it.get("mod"),
            it.get("internal"),
            it.get("damage"),
            it.get("kind") or "item",
            it.get("name"),
            it.get("size", 0) or 0,
            1 if it.get("isCraftable") else 0,
            now,
        )
        for it in items
    ]
    with db.transaction(db.item_history_db) as conn:
        conn.execute("DELETE FROM network_snapshot")
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO network_snapshot "
                "(item_key, mod, internal, damage, kind, name, size, is_craftable, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )


def load_snapshot():
    """(items, updated_at) as last saved by save_snapshot(); items is
    empty and updated_at None if nothing was. Items carry no icon - the
    caller attaches those."""
    with db.transaction(db.item_history_db) as conn:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind, name, size, is_craftable, updated_at "
            "FROM network_snapshot"
        ).fetchall()
    items = [
        {
            "mod": r[0],
            "internal": r[1],
            "damage": r[2],
            "kind": r[3],
            "name": r[4],
            "size": r[5],
            "isCraftable": bool(r[6]),
        }
        for r in rows
    ]
    return items, max((r[7] for r in rows), default=None)


def record_changes(old_items, new_items):
    """old_items/new_items: lists of item dicts (mod, internal, damage,
    kind, name, size). Writes one row per item whose size differs from
    the last-known value, including items present before but absent now
    (size implicitly 0 - they only vanish from a scan by genuinely
    having zero stock and no craftable pattern, since getItemsInNetworkById/
    getFluidsInNetwork only ever return entries AE2 itself considers
    present)."""
    old_by_key = {_key_of(it): it.get("size", 0) for it in old_items or []}

    now = time.time()
    changes = []
    seen_keys = set()
    for it in new_items or []:
        key = _key_of(it)
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
    with db.transaction(db.craft_db) as conn:
        rows = conn.execute(
            "SELECT mod, internal, damage, kind FROM user_item_pins WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return [{"mod": r[0], "internal": r[1], "damage": r[2], "kind": r[3]} for r in rows]


def pin(user_id, mod, internal, damage, kind):
    with db.transaction(db.craft_db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_item_pins "
            "(user_id, item_key, mod, internal, damage, kind, pinned_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                item_key(mod, internal, damage, kind),
                mod,
                internal,
                damage,
                kind,
                time.time(),
            ),
        )


def unpin(user_id, mod, internal, damage, kind):
    with db.transaction(db.craft_db) as conn:
        conn.execute(
            "DELETE FROM user_item_pins WHERE user_id = ? AND item_key = ?",
            (user_id, item_key(mod, internal, damage, kind)),
        )
