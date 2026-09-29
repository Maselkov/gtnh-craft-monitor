"""In-memory live state shared across routes: the latest crafting-CPU
status, the ME network snapshot and in-progress scan, CPU transition
tracking and Lua debug dumps. Never persisted - a restart starts empty
(except the network snapshot, reloaded from SQLite at startup). The
craft/cancel request queues are in gcm/commands.py, since they also
keep their history rows in step.

Plain data only: nothing here reads or writes the databases."""

import threading


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
# The in-progress scan's NBT tags (hex, as network_browser.lua sent
# them), by item key - promoted to network["tags"] with the items.
network_tag_buffer = {}
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
    # finish_scan() in gcm/inventory.py.
    "current_scan_token": None,
    # How many scan/batch calls THIS process has actually accepted for
    # the current scan token - independently counted server-side, not
    # trusted from whatever Lua claims. Compared at scan/finish against
    # the chunks_sent count Lua reports, so the server verifies
    # completeness rather than assuming it.
    "chunks_received": 0,
    # Item key -> the NBT tag (hex) of each NBT variant in the snapshot,
    # for craft requests to pick the pattern with exactly that NBT
    # (craft_monitor.lua). Not served to the frontend, not persisted: a
    # request made before the first scan after a restart matches by
    # label alone.
    "tags": {},
}


# In-memory only, deliberately not persisted. After a restart, a CPU
# whose pinned job finished while the server was down is first seen
# idle with no prior state; routes/crafts.py drops its pins
# then (no completion is recorded, since what happened is unknown),
# so they don't fire on the next unrelated job.
tracking_lock = threading.Lock()
cpu_last_busy = {}  # cpu_name -> bool
# cpu_name -> the current job's {label, icon, progress, steps_left,
# steps_total, peaks, output, started_at, first_sample_at,
# last_sample_at, request_id, outcome}, only while busy (see _new_job_entry() in
# tracking.py). Lost on restart, so an in-flight job's progress
# re-baselines from the first report after it.
cpu_last_known = {}
# cpu_name -> {entry, label, icon, ended_at}: a watched browser request's
# job that ended before the game reported its outcome, recorded once it
# does or after tracking.OUTCOME_WAIT_SECONDS.
cpu_ending = {}


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
        network_tag_buffer.clear()
        network.update(
            tags={},
            items=[],
            item_count=0,
            updated_at=None,
            in_progress=False,
            scan_started_at=None,
            is_reconstructed=False,
            current_scan_token=None,
            chunks_received=0,
        )
    with tracking_lock:
        cpu_last_busy.clear()
        cpu_last_known.clear()
        cpu_ending.clear()
    debug_dumps.clear()
