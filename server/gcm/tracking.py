"""Craft completion tracking: turns each crafting-CPU status report into
busy->idle transitions, and those into craft_events rows and completion
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

craft_events is a permanent, unpruned log of every busy->idle
transition for every CPU, regardless of whether anyone has it pinned -
cheap (a few dozen writes a day at most) and doubles as raw material
for any future historical/analytics view.

The per-CPU memory it compares against lives in gcm/state.py
(cpu_last_busy, cpu_last_known) and isn't persisted."""

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


def process_jobs(jobs):
    """Detects busy->idle transitions against our own server-side memory
    of each CPU's last state, logs a permanent craft_events row for each,
    and fans out + auto-unpins any users who had that CPU pinned."""
    with state.tracking_lock:
        for job in jobs:
            name = job.get("name")
            if not name:
                continue
            busy = bool(job.get("busy"))
            was_busy = state.cpu_last_busy.get(name)

            if was_busy is None and not busy:
                store.crafts.drop_cpu_pins(name)

            if was_busy is True and busy is False:
                last_known = state.cpu_last_known.pop(name, {})
                progress = last_known.get("progress")
                store.crafts.record_job_end(
                    name,
                    job.get("final_output") or last_known.get("label"),
                    job.get("final_output_icon") or last_known.get("icon"),
                    _classify_status(progress),
                    progress,
                )

            if busy:
                entry = state.cpu_last_known.setdefault(
                    name, {"label": None, "icon": None, "progress": None}
                )
                if job.get("final_output"):
                    entry["label"] = job.get("final_output")
                    entry["icon"] = job.get("final_output_icon")
                if job.get("progress_percent") is not None:
                    entry["progress"] = job.get("progress_percent")

            state.cpu_last_busy[name] = busy


def note_job_started(cpu_name, label, icon):
    """Records that a job just started on cpu_name, for when the game
    reports an accepted craft request before its next status poll.
    Without this, a craft that completes faster than craft_monitor.lua's
    own 5s poll interval is invisible to process_jobs() entirely: it
    never observes a busy=true sample to compare against the later
    busy=false one, so the completion is never detected, the pin is
    never cleaned up, and it just sits there forever pointing at an idle
    CPU. Seeding "last known busy=true" here means the NEXT real poll -
    even if it's the first one that ever samples this CPU - can still
    correctly detect the transition retroactively. progress is left
    unknown (None) rather than guessed, which _classify_status reads as
    "finished" for exactly this reason."""
    with state.tracking_lock:
        state.cpu_last_busy[cpu_name] = True
        entry = state.cpu_last_known.setdefault(
            cpu_name, {"label": None, "icon": None, "progress": None}
        )
        if label:
            entry["label"] = label
        if icon:
            entry["icon"] = icon
