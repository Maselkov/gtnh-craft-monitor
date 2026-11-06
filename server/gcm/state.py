"""In-memory live state shared across routes: the latest crafting-CPU
status, the ME network snapshot and in-progress scan, CPU transition
tracking and Lua debug dumps. Never persisted - a restart starts empty
(except the network snapshot, reloaded from SQLite at startup). The
craft/cancel request queues are in gcm/commands.py, since they also
keep their history rows in step.

Plain data only: nothing here reads or writes the databases."""

import threading

# How many of a script's reports may be missed before its feed counts
# as stale. Each limit is the larger of its fixed default and this many
# of the interval the script reported, so a script set to report less
# often isn't called stale between reports.
MISSED_REPORTS = 3
_MAX_INTERVAL_SECONDS = 86400


def reported_interval(value):
    """A script's interval as its payload gave it, in seconds, or None
    when it sent none (older scripts) or junk."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 < value <= _MAX_INTERVAL_SECONDS else None


def stale_after(default, interval):
    """Seconds without a report before a feed is stale: default, or
    MISSED_REPORTS of the script's interval if that's longer."""
    return default if interval is None else max(default, MISSED_REPORTS * interval)


# Latest crafting-CPU status POSTed by craft_monitor.lua.
crafts_lock = threading.Lock()
crafts = {
    "jobs": [],
    "source": None,
    "received_at": None,  # server-side wall clock, for staleness checks
    "poll_seconds": None,  # craft_monitor.lua's POLL_SECONDS, if it said
    # How many kit slots craft_monitor.lua found (patterns in the kit
    # interface it may rewrite to request a multiblock kit as one job);
    # None from a craft_monitor.lua too old to say.
    "kit_slots": None,
}


def kit_slots():
    """How many kit slots the game last reported: 0 when it has none or
    never said."""
    with crafts_lock:
        return crafts["kit_slots"] or 0


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
    # When a check of just the stock rules' items (inventory.apply_levels())
    # last changed the snapshot - between full scans, so updated_at (the
    # last full scan) stays as it was.
    "levels_at": None,
    # network_browser.lua's last crash report ({error, phase, free_memory,
    # at}), until its next scan finishes - see inventory.record_crash().
    "crash": None,
    # How long the last accepted scan took, and the last scan turned
    # away ({at, reason}) - for the Status dialog (gcm/health.py).
    "last_scan_seconds": None,
    "last_rejected": None,
    # The token of a scan ended for going past MAX_SCAN_ITEMS, so its
    # scan/finish doesn't replace that reason with "stale scan token".
    "abandoned_scan_token": None,
    # network_browser.lua's SCAN_INTERVAL_SECONDS, as its last scan/start
    # said (None from older scripts).
    "scan_interval_seconds": None,
}


# power_monitor.lua's POLL_SECONDS, by machine name, as each machine's
# last reading said (gcm/health.py).
power_lock = threading.Lock()
power_poll_seconds = {}


# The network's patterns, from network_browser.lua's pattern scans
# (gcm/patterns.py): the same start/batch/finish protocol as the item
# scan, buffered until a scan is known complete. Mirrored into SQLite.
patterns_lock = threading.Lock()
pattern_buffer = []
patterns = {
    "patterns": [],
    "updated_at": None,
    "in_progress": False,
    "current_scan_token": None,
    "chunks_received": 0,
    # Whether network_browser.lua found an ME Interface Terminal, as its
    # last network scan said: None until one does (older scripts, or
    # pattern scans turned off).
    "terminal": None,
    # {at, reason} of the last scan turned away as incomplete.
    "last_rejected": None,
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
# Names of CPUs that have reported a final output, so have a Crafting
# Monitor (see tracking.MONITOR_GRACE_SECONDS).
cpu_has_monitor = set()


# Small ad-hoc debugging channel: the Lua side can POST a raw dump here
# instead of printing to the OC terminal (which has essentially no
# scrollback and clips anything longer than a screenful). Keeps the last
# few dumps only - this isn't meant to be a permanent log.
debug_dumps = []

# Paths that have had a body read leniently (gcm/lenient_json.py), which
# is logged only the first time for each.
lenient_json_paths = set()


def reset():
    """Clears every piece of in-memory state back to a fresh process.
    Keep it in sync when adding module-level state here."""
    with crafts_lock:
        crafts["jobs"] = []
        crafts["source"] = None
        crafts["received_at"] = None
        crafts["poll_seconds"] = None
        crafts["kit_slots"] = None
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
            levels_at=None,
            crash=None,
            last_scan_seconds=None,
            last_rejected=None,
            abandoned_scan_token=None,
            scan_interval_seconds=None,
        )
    with power_lock:
        power_poll_seconds.clear()
    with patterns_lock:
        pattern_buffer.clear()
        patterns.update(
            patterns=[],
            updated_at=None,
            in_progress=False,
            current_scan_token=None,
            chunks_received=0,
            terminal=None,
            last_rejected=None,
        )
    with tracking_lock:
        cpu_last_busy.clear()
        cpu_last_known.clear()
        cpu_ending.clear()
        cpu_has_monitor.clear()
    debug_dumps.clear()
    lenient_json_paths.clear()
