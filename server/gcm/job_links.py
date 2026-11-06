"""Links to one crafting job: /crafts?cpu=<name>&job=<time>, the time
(whole seconds) when the server first saw the job on that CPU. That's
known from the job's first report, so a link can be shared while it
runs, and craft_events keeps it (first_seen_at), so the link still says
how the job went after it ends.

A link names a job, not a state: the page and its link preview show the
job as it is when they're opened. lookup() says which of these it is:

    live     still running on its CPU
    ending   the CPU went idle and the game is still reporting how the
             job ended (gcm/tracking.py holds the end up to 15s)
    ended    in craft history: how it went, how long it took
    gone     not running and not in history: the server was down when
             it ended, or the link is made up

The job's identity doesn't survive a server restart: a job running then
is seen anew, at a later time and not from its start. So when no job was
first seen at the link's time, the first one seen after it on that CPU
is taken as the link's job if it wasn't seen from its start. That's
wrong only when the job ended and another started while the server was
down."""

import math
from urllib.parse import urlencode

from flask import has_request_context, request

from gcm import config, state, store


def path(cpu, first_seen):
    """The page's path for the job on `cpu` first seen at `first_seen`
    (a Unix time). A query rather than a path segment: a CPU's name can
    have a "/" in it, which proxies mangle in a path."""
    return "/crafts?" + urlencode({"cpu": cpu, "job": math.floor(first_seen)})


def url(cpu, first_seen):
    """path() as an absolute URL: PUBLIC_URL's, else the address the
    current request came in on; None outside a request without one."""
    if config.PUBLIC_URL:
        base = config.PUBLIC_URL
    elif has_request_context():
        base = request.url_root.rstrip("/")
    else:
        return None
    return base + path(cpu, first_seen)


def parse(args):
    """(cpu, job time) from a link's query args, or None."""
    cpu = args.get("cpu")
    job = args.get("job")
    if not cpu or not job or len(cpu) > 200:
        return None
    try:
        t = int(job)
    except ValueError:
        return None
    return (cpu, t) if t > 0 else None


def _live(cpu):
    """The CPU's busy job in the latest report, with reported_at, when
    that came in (its eta_seconds is from then), or None."""
    with state.crafts_lock:
        job = next((j for j in state.crafts.get("jobs", []) if j.get("name") == cpu), None)
        received_at = state.crafts.get("received_at")
    if job and job.get("busy") and job.get("started_at") is not None:
        return {**job, "reported_at": received_at}
    return None


def _ending(cpu):
    """(first seen, seen from start) of a job on the CPU whose end is
    held for the game's outcome, or None."""
    with state.tracking_lock:
        held = state.cpu_ending.get(cpu)
        if not held:
            return None
        entry = held["entry"]
        return entry["started_at"], entry.get("seen_from_start", True)


def lookup(cpu, t):
    """{state, ...} for the job a link names (see the module's notes):
    live has the report's `job`, ended the history `event`."""
    exact = store.crafts.job_end_seen_at(cpu, t, t + 1)
    if exact:
        return {"state": "ended", "cpu": cpu, "event": exact}
    job = _live(cpu)
    if job and math.floor(job["started_at"]) == t:
        return {"state": "live", "cpu": cpu, "job": job}
    held = _ending(cpu)
    if held and math.floor(held[0]) == t:
        return {"state": "ending", "cpu": cpu}
    # The job may have been seen anew after a restart (see above): it's
    # the first one seen after t, if that wasn't seen from its start.
    later = store.crafts.next_job_end(cpu, t + 1)
    if later:
        if later["startedAt"] is None:
            return {"state": "ended", "cpu": cpu, "event": later}
        return {"state": "gone", "cpu": cpu}
    if held and held[0] >= t + 1 and not held[1]:
        return {"state": "ending", "cpu": cpu}
    if job and job["started_at"] >= t + 1 and not job.get("started_known", True):
        return {"state": "live", "cpu": cpu, "job": job}
    return {"state": "gone", "cpu": cpu}


def duration(seconds):
    """'42s', '4m 12s', '3h 5m', '2d 4h', as the page's formatDuration()."""
    s = max(0, round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"
