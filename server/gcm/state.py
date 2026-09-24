"""In-memory live state shared across routes: the latest crafting-CPU
status, the ME network snapshot and in-progress scan, craft/cancel
request queues, CPU transition tracking and Lua debug dumps. Never
persisted - a restart starts empty (except the network snapshot,
reloaded from SQLite at startup)."""

import time
import threading

from gcm import store


# Latest crafting-CPU status POSTed by craft_monitor.lua.
crafts_lock = threading.Lock()
crafts = {
    "jobs": [],
    "source": None,
    "received_at": None,  # server-side wall clock, for staleness checks
}


# ---------------------------------------------------------------------
# ME network item browser (fed by oc/network_browser.lua)
#
# In-memory as the live source of truth (unlike power/craft history,
# this is "what's in the network right now", not a time series) - but
# ALSO mirrored into the network_snapshot SQLite table on every real
# scan (see store/items.py save_snapshot()/load_snapshot()),
# specifically so a server restart doesn't leave the Network tab empty
# until the next scan completes.
#
# A scan is a start/batch*/finish sequence, not one big POST - the whole
# reason getItemsInNetworkById() is viable at all is that both the
# candidate catalog AND the results are streamed through in small
# batches on the Lua side (confirmed via repeated testing: holding the
# whole ~10,885-entry catalog in memory at once left only ~850KB free at
# the low point on a ~4MB computer; streaming both sides left ~1.9MB -
# real margin, not just "didn't crash this time"). Buffering into
# network_buffer during the scan and only promoting it to
# network on /finish means a browser reading GET /api/network
# mid-scan still sees the last COMPLETE snapshot, not a half-built one.
network_lock = threading.Lock()
network_buffer = []
network = {
    "items": [],
    "item_count": 0,
    "updated_at": None,
    "in_progress": False,
    "scan_started_at": None,
    # True from server startup (if a persisted snapshot was found to
    # load) until the FIRST real scan completes afterward - lets the
    # frontend show "this is what we had before the restart" rather
    # than presenting reconstructed data as if it were a fresh scan.
    "is_reconstructed": False,
    # The active scan's identity - a fresh random token minted on every
    # scan/start, required back on every scan/batch and scan/finish.
    # NOT exposed to the frontend (network_get() lists its own
    # response fields explicitly, so this simply isn't one of them) -
    # purely internal bookkeeping for the mechanism explained in full at
    # network_scan_finish() in routes/network.py.
    "current_scan_token": None,
    # How many scan/batch calls THIS process has actually accepted for
    # the current scan token - independently counted server-side, not
    # trusted from whatever Lua claims. Compared at scan/finish against
    # the chunks_sent count Lua reports, so the server verifies
    # completeness rather than assuming it.
    "chunks_received": 0,
}


# ---------------------------------------------------------------------
# Craft requests and cancellations (in-memory, ephemeral - not SQLite,
# same reasoning as the network browser: a request's lifetime is minutes
# at most, nothing here needs to survive a server restart). Endpoints
# are in routes/craft_requests.py.
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

    def add(self, record):
        """Stores a new pending record, assigning and returning its id."""
        with self.lock:
            req_id = self._next_id
            self._next_id += 1
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
    on_expire=lambda req_id, reason: store.requests.resolve_request(
        req_id, "failed", reason, None, only_if_open=True
    ),
)


# ---------------------------------------------------------------------
# Craft cancellation. Simpler than craft REQUESTS: AE2's cancel() call
# (confirmed from source) resolves synchronously on Lua's side - call
# it, get true/false back immediately, no multi-poll-cycle isComputing/
# CraftingStatus tracking needed the way .request() requires. So this is
# one round trip per cancellation, not a whole pending-state lifecycle -
# a lighter-weight mirror of the craft-request pattern, not a full copy
# of it.
# id -> {id, user_id, cpu_name, status, success, reason, created_at}
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


# In-memory only, deliberately not persisted. After a restart, a CPU
# whose pinned job finished while the server was down is first seen
# idle with no prior state; routes/crafts.py drops its pins
# then (no completion is recorded, since what happened is unknown),
# so they don't fire on the next unrelated job.
tracking_lock = threading.Lock()
cpu_last_busy = {}  # cpu_name -> bool
# cpu_name -> the current job's {label, icon, progress, output,
# started_at}, only while busy (see _new_job_entry() in tracking.py)
cpu_last_known = {}


# Small ad-hoc debugging channel: the Lua side can POST a raw dump here
# instead of printing to the OC terminal (which has essentially no
# scrollback and clips anything longer than a screenful). Keeps the last
# few dumps only - this isn't meant to be a permanent log.
debug_dumps = []


def reset():
    """Clears every piece of in-memory state back to a fresh process.
    Keep it in sync when adding module-level state here."""
    with crafts_lock:
        crafts["jobs"] = []
        crafts["source"] = None
        crafts["received_at"] = None
    with network_lock:
        network_buffer.clear()
        network.update(
            items=[],
            item_count=0,
            updated_at=None,
            in_progress=False,
            scan_started_at=None,
            is_reconstructed=False,
            current_scan_token=None,
            chunks_received=0,
        )
    craft_requests.reset()
    cancel_requests.reset()
    with tracking_lock:
        cpu_last_busy.clear()
        cpu_last_known.clear()
    debug_dumps.clear()
