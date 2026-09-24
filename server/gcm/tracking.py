"""Craft completion tracking: turns each crafting-CPU status report into
job ends, and those into craft_events rows and completion
notifications for users who pinned the CPU.

Done server-side, rather than client-side (as it originally was, by
watching busy-state transitions in the browser) for two reasons that
design couldn't fix:
  1. A closed browser tab can't observe anything - it can only compare
     "last known state before closing" vs "state on reopening", which
     misses any completion-then-new-job cycle that happens entirely
     while no tab is open. The server is always running (as long as
     craft_monitor.lua is), so it never has that gap.
  2. The client had no way to distinguish a finished craft from a
     cancelled/interrupted one - "was busy, now isn't" looked identical
     either way. The server classifies this from the last known
     progress_percent at the moment of the transition instead.

craft_events is a permanent, unpruned log of the end of every job on
every CPU, regardless of whether anyone has it pinned -
cheap (a few dozen writes a day at most) and doubles as raw material
for any future historical/analytics view.

The per-CPU memory it compares against lives in gcm/state.py
(cpu_last_busy, cpu_last_known) and isn't persisted."""

import time

from gcm import state, store

FINISHED_THRESHOLD = 99  # progress_percent >= this counts as "finished"


def _classify_status(progress):
    # progress is None specifically means the main 5s status poll never
    # captured even ONE snapshot of this CPU while it was busy, before it
    # went idle again - confirmed (via the FusionTech Mk-IV test earlier)
    # that a REJECTED craft request never flips a CPU busy at all, so an
    # entirely-invisible-but-real busy period is strong evidence of a
    # fast, genuine SUCCESS that simply outran the polling interval - not
    # evidence of a failure we just failed to observe. Treating it as
    # "incomplete" was actively wrong in exactly that case.
    if progress is None:
        return "finished"
    return "finished" if progress >= FINISHED_THRESHOLD else "incomplete"


def _new_job_entry(started_at):
    # cpu_last_known entry for a job first seen at started_at. "output"
    # is the job's final output as craft_monitor.lua reported it - kept
    # apart from "label", which a craft request can fill in with its own
    # wording (a fluid request's label isn't the drop item AE2 reports).
    return {
        "label": None,
        "icon": None,
        "progress": None,
        "output": None,
        "started_at": started_at,
    }


def _job_output(job):
    """What the job on this CPU is making, as reported - None when the
    CPU has no Crafting Monitor to ask."""
    if not job.get("final_output"):
        return None
    return (
        job.get("final_output"),
        job.get("final_output_mod"),
        job.get("final_output_internal"),
        job.get("final_output_damage"),
    )


def _end_job_locked(name, label=None, icon=None):
    """Records the end of the job tracked on CPU `name`. Caller holds
    state.tracking_lock."""
    last_known = state.cpu_last_known.pop(name, {})
    progress = last_known.get("progress")
    store.crafts.record_job_end(
        name,
        label or last_known.get("label"),
        icon or last_known.get("icon"),
        _classify_status(progress),
        progress,
    )


def process_jobs(jobs):
    """Detects the end of each CPU's job against our own server-side
    memory of its last state, logs a permanent craft_events row for
    each, and fans out + auto-unpins any users who had that CPU pinned.

    A job ends when its CPU goes busy->idle, or when a busy CPU reports
    a different final output than before - a CPU that finishes and
    starts its next job between two polls is never seen idle, and its
    pins must not carry over to the new job. A back-to-back job making
    the same item, or a CPU without a Crafting Monitor (no final output
    at all), still can't be told apart from one long job."""
    with state.tracking_lock:
        for job in jobs:
            name = job.get("name")
            if not name:
                continue
            busy = bool(job.get("busy"))
            was_busy = state.cpu_last_busy.get(name)
            output = _job_output(job)

            if was_busy is None and not busy:
                store.crafts.drop_cpu_pins(name)

            if was_busy is True and busy is False:
                _end_job_locked(name, job.get("final_output"), job.get("final_output_icon"))

            if was_busy is True and busy:
                previous = state.cpu_last_known.get(name, {}).get("output")
                if output and previous and output != previous:
                    _end_job_locked(name)

            if busy:
                entry = state.cpu_last_known.setdefault(name, _new_job_entry(time.time()))
                if output:
                    entry["output"] = output
                    entry["label"] = job.get("final_output")
                    entry["icon"] = job.get("final_output_icon")
                if job.get("progress_percent") is not None:
                    entry["progress"] = job.get("progress_percent")

            state.cpu_last_busy[name] = busy


def start_requested_job(user_id, cpu_name, label, icon, requested_at):
    """A browser craft request the game reports it started on cpu_name:
    pins it for the requester and records the job as started.

    Pinned directly rather than through /api/pins, which requires the
    CPU to already show busy in the last status report - that only
    updates on craft_monitor.lua's regular 5s poll, and the game can
    report "accepted, CPU X" before then. The trust here comes from the
    game's own confirmation that it just watched this CPU get assigned.

    Seeding "busy" here also matters: without it, a craft that completes
    faster than the 5s poll is invisible to process_jobs() - it never
    sees a busy sample to compare against the later idle one, so the
    completion is never detected and the pin sits there forever. With
    it, the next poll can still detect the transition retroactively.
    progress is left unknown (None) rather than guessed, which
    _classify_status reads as "finished" for exactly this reason.

    The game only starts a request on a CPU it saw idle, so a job we're
    still tracking there from before the request was made has already
    ended - it's closed first, or the new pin would inherit its end. A
    job first seen after the request is this request's own."""
    with state.tracking_lock:
        entry = state.cpu_last_known.get(cpu_name)
        if state.cpu_last_busy.get(cpu_name) and entry and entry["started_at"] < requested_at:
            _end_job_locked(cpu_name)
            entry = None
        if entry is None:
            entry = state.cpu_last_known[cpu_name] = _new_job_entry(time.time())
        if label and not entry["output"]:
            entry["label"] = label
        if icon and not entry["output"]:
            entry["icon"] = icon
        state.cpu_last_busy[cpu_name] = True
        store.crafts.pin_cpu(user_id, cpu_name)
