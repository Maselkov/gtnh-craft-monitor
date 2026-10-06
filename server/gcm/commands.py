"""Craft requests and cancellations submitted from the browser, queued
in memory for craft_monitor.lua to poll, with their audit rows in
craft_request_history / craft_cancel_history kept in step. Endpoints
are in routes/craft_requests.py.

In memory rather than SQLite, same reasoning as the network browser:
a request's lifetime is minutes at most, and nothing here needs to
survive a server restart (startup closes any history row a previous
process left open - see store.requests.close_orphaned())."""

import threading
import time

from gcm import icons, inventory, store


class CommandQueue:
    """In-memory queue of browser-submitted commands that craft_monitor.lua
    polls for. A request is "picked up" once the game has fetched it from
    /pending. One that's never picked up means the game isn't polling,
    and it's expired rather than left to run whenever the game comes
    back; a picked-up one gets result_timeout to report back. Expired
    and otherwise finished records are kept for `retention` seconds
    after finishing so the browser can still show the outcome.

    Each record is handed to the game at most once: running a craft or
    a cancel twice does real damage (double resources, or cancelling the
    next job on that CPU), while a lost /pending response only costs a
    request that times out.

    Ids keep counting up across restarts (see start_after()): the game
    reports results by id, and may still be holding an id from before a
    restart that must not match a new request.

    Every change to a record goes through a method here, so the whole
    lifecycle (pending -> picked up -> finished -> dropped after
    retention) lives in this class. Endpoints get copies, never the
    stored records."""

    def __init__(
        self,
        pickup_timeout,
        result_timeout,
        retention,
        unclaimed_reason,
        finished_status,
        finished_at_field,
        on_expire,
        expired_fields=None,
    ):
        self.pickup_timeout = pickup_timeout
        self.result_timeout = result_timeout
        self.retention = retention
        self.unclaimed_reason = unclaimed_reason
        self.finished_status = finished_status
        self.finished_at_field = finished_at_field
        self.on_expire = on_expire  # (req_id, reason), called outside the lock
        self.expired_fields = expired_fields or {}
        self.lock = threading.Lock()
        self.requests = {}
        self._next_id = 1

    def reset(self):
        with self.lock:
            self.requests.clear()
            self._next_id = 1

    def start_after(self, last_id):
        """Makes new ids start above last_id - the highest id any earlier
        process handed out."""
        with self.lock:
            self._next_id = max(self._next_id, last_id + 1)

    def add(self, record, persist):
        """Stores a new pending record, assigning and returning its id.

        persist(req_id) writes the record's history row first, outside
        the lock: if it raises, the record is never queued, so the game
        can't run a request the browser was told failed (and might
        submit again). The id it reserved is simply skipped."""
        with self.lock:
            req_id = self._next_id
            self._next_id += 1
        persist(req_id)
        with self.lock:
            self.requests[req_id] = {"id": req_id, **record, "status": "pending"}
        return req_id

    def _expire_locked(self):
        now = time.time()
        expired = []
        for req_id, req in list(self.requests.items()):
            if req["status"] == "pending":
                picked_up_at = req.get("picked_up_at")
                if picked_up_at is None:
                    if now - req["created_at"] <= self.pickup_timeout:
                        continue
                    reason = self.unclaimed_reason
                elif now - picked_up_at > self.result_timeout:
                    reason = "no result from the game - check in-game"
                else:
                    continue
                req.update(self.expired_fields)
                req["status"] = self.finished_status
                req["reason"] = reason
                req[self.finished_at_field] = now
                expired.append((req_id, reason))
            else:
                finished_at = req.get(self.finished_at_field, req["created_at"])
                if now - finished_at > self.retention:
                    del self.requests[req_id]
        return expired

    def _record_expired(self, expired):
        for req_id, reason in expired:
            self.on_expire(req_id, reason)

    def select(self, predicate):
        """Expires stale records, then returns copies of those matching
        predicate."""
        with self.lock:
            expired = self._expire_locked()
            selected = [dict(r) for r in self.requests.values() if predicate(r)]
        self._record_expired(expired)
        return selected

    def claim_pending(self):
        """For the game's /pending poll: expires stale records, marks every
        pending one not yet picked up as picked up, and returns copies of
        just those."""
        now = time.time()
        with self.lock:
            expired = self._expire_locked()
            pending = [
                r
                for r in self.requests.values()
                if r["status"] == "pending" and "picked_up_at" not in r
            ]
            for r in pending:
                r["picked_up_at"] = now
            pending = [dict(r) for r in pending]
        self._record_expired(expired)
        return pending

    def resolve(self, req_id, status, remove=False, **fields):
        """Records the game's result for a request: sets status, the
        finished-at field and `fields`, and with remove=True drops the
        record. Returns a copy of the record, or None for an unknown id.

        A record that already expired is still updated: the expiry only
        meant the game was slow to answer, and its answer is what really
        happened in-game."""
        with self.lock:
            req = self.requests.get(req_id)
            if req is None:
                return None
            req.update(fields)
            req["status"] = status
            req[self.finished_at_field] = time.time()
            if remove:
                del self.requests[req_id]
            return dict(req)

    def dismiss(self, req_id, user_id):
        """Drops a finished record the user owns, once they've seen its
        outcome. A pending one stays: the game may still report on it,
        and that result must land somewhere."""
        with self.lock:
            req = self.requests.get(req_id)
            if req and req["user_id"] == user_id and req["status"] != "pending":
                del self.requests[req_id]


# A picked-up craft request stays pending while AE2 plans the craft
# (minutes for a big job), so it gets a much longer result timeout than
# a cancellation.
craft_requests = CommandQueue(
    pickup_timeout=120,
    result_timeout=3600,
    retention=86400,
    unclaimed_reason="the game didn't pick up this request - is craft_monitor running?",
    finished_status="failed",
    finished_at_field="failed_at",
    on_expire=lambda req_id, reason: _expire_craft_request(req_id, reason),
)


def _expire_craft_request(req_id, reason):
    store.requests.resolve_request(req_id, "failed", reason, None, only_if_open=True)
    store.stock.record_auto_result(req_id, "failed", reason)


def queue_craft(user_id, label, mod, internal, damage, amount, kind, variant=None,
                variant_name=None, source="manual", target_key=None):
    """Queues a craft request for craft_monitor.lua and writes its history
    row; returns its id. source is "manual" for one made on the page, or
    "auto" for a keep-in-stock target's (target_key: that target's item
    key)."""
    created_at = time.time()
    return craft_requests.add(
        {
            "user_id": user_id,
            "label": label,
            "mod": mod,
            "internal": internal,
            "damage": damage,
            "amount": amount,
            "kind": kind,  # "item" or "fluid" - craft_monitor.lua's request
            # loop needs this to pick the right getCraftables()
            # filter shape (confirmed via a real successful
            # fluid request: items filter by name+damage,
            # fluids only matched when filtered by label)
            "icon": icons.resolve_icon(mod, internal, damage, label, variant, variant_name=variant_name),
            # An NBT variant (gcm/inventory.py): craft_monitor.lua picks
            # the pattern whose output has exactly this NBT (tag, from the
            # last scan), or, without one, the only pattern with this label.
            "variant": variant,
            "variant_name": str(variant_name) if variant_name else None,
            "tag": inventory.variant_tag(mod, internal, damage, kind, variant),
            "source": source,
            "target_key": target_key,
            # status: pending -> accepted (removed from list, real pin
            # takes over) | failed (stays until dismissed)
            "reason": None,
            "cpu_name": None,
            "created_at": created_at,
        },
        lambda req_id: store.requests.record_request(
            req_id, user_id, label, mod, internal, damage, amount, kind, created_at, source
        ),
    )


# Craft cancellation. Simpler than craft REQUESTS: AE2's cancel() call
# (confirmed from source) resolves synchronously on Lua's side - call
# it, get true/false back immediately, no multi-poll-cycle isComputing/
# CraftingStatus tracking needed the way .request() requires. So this is
# one round trip per cancellation, not a whole pending-state lifecycle -
# a lighter-weight mirror of the craft-request pattern, not a full copy
# of it.
# id -> {id, user_id, cpu_name, expected_output, status, success,
# reason, created_at}
#
# The browser stops waiting after ~15s and tells the user the game
# didn't respond, so an unclaimed cancel must not run later (it could
# hit a different job on that CPU by then).
cancel_requests = CommandQueue(
    pickup_timeout=20,
    result_timeout=300,
    retention=600,
    unclaimed_reason="the game didn't pick up this cancellation",
    finished_status="resolved",
    finished_at_field="resolved_at",
    expired_fields={"success": False},
    on_expire=lambda req_id, reason: store.requests.resolve_cancel(
        req_id, False, reason, only_if_open=True
    ),
)


def reset():
    craft_requests.reset()
    cancel_requests.reset()
