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
     either way. The server classifies this from how many steps the job
     had left (gcm/progress.py) at the moment of the transition instead.

craft_events is a permanent, unpruned log of the end of every job on
every CPU, regardless of whether anyone has it pinned -
cheap (a few dozen writes a day at most) - and is what the Crafts
tab's history lists.

The per-CPU memory it compares against lives in gcm/state.py
(cpu_last_busy, cpu_last_known) and isn't persisted."""

import time

from gcm import commands, progress, push, state, store


# How much faster than its observed pace a job may have gone in the gap
# between its last busy sample and being seen idle. Steps finish in
# bursts rather than evenly, so the pace is only a rough guide.
_PACE_SLACK = 2


def _classify_status(name, entry, ended_at):
    """"finished" or "incomplete" for the job in `entry` on CPU `name`,
    seen idle at ended_at.

    The last busy sample can be seconds old by then, so its steps left
    are not the steps left when the job ended - a quick job sampled with
    several steps to go has often finished them before it's seen idle.
    Steps left only count against a job that couldn't have finished
    them in that gap at the pace it was going."""
    if _cancelled_from_page(name, entry["started_at"]):
        return "incomplete"
    steps_left = entry.get("steps_left")
    # steps_left is None specifically means the main 5s status poll never
    # captured even ONE snapshot of this CPU while it was busy, before it
    # went idle again - confirmed (via the FusionTech Mk-IV test earlier)
    # that a REJECTED craft request never flips a CPU busy at all, so an
    # entirely-invisible-but-real busy period is strong evidence of a
    # fast, genuine SUCCESS that simply outran the polling interval - not
    # evidence of a failure we just failed to observe. Treating it as
    # "incomplete" was actively wrong in exactly that case.
    # The final output's own step can't be seen reaching zero - the CPU
    # goes idle as soon as it does - so one step left counts as finished.
    if steps_left is None or steps_left <= 1:
        return "finished"
    first, last = entry["first_sample_at"], entry["last_sample_at"]
    if last <= first:
        # One sample is only the progress baseline: no pace to judge by,
        # and a job that short most likely just finished.
        return "finished"
    pace = entry["progress"] / (last - first)  # percent per second
    reachable = entry["progress"] + pace * (ended_at - last) * _PACE_SLACK
    # Everything but the final output's step, as above.
    return "finished" if reachable >= 100 - 100 / entry["steps_total"] else "incomplete"


def _cancelled_from_page(name, started_at):
    """Whether a cancel sent from the page for CPU `name` since the job
    began reached the game and wasn't refused. Checked here rather than
    when the cancel's result arrives: the game can report the CPU idle
    before it reports the cancel's result."""
    return bool(commands.cancel_requests.select(
        lambda r: r["cpu_name"] == name
        and r["created_at"] >= started_at
        and "picked_up_at" in r
        and r.get("success") is not False
    ))


def _new_job_entry(started_at, seen_from_start=True):
    # cpu_last_known entry for a job first seen at started_at -
    # seen_from_start False when it was already running then, so its
    # real start is unknown. "output"
    # is the job's final output as craft_monitor.lua reported it - kept
    # apart from "label", which a craft request fills in with the
    # browser's name for the item until a status report names the output.
    return {
        "label": None,
        "icon": None,
        "progress": None,
        "steps_left": None,
        "steps_total": None,
        "peaks": {},  # gcm/progress.py's per-step baseline for this job
        "output": None,
        "started_at": started_at,
        "seen_from_start": seen_from_start,
        # When the first status report with steps in it (the progress
        # baseline) and the latest one were taken - the job's pace.
        "first_sample_at": None,
        "last_sample_at": None,
        # Set for a browser request's job craft_monitor.lua is watching,
        # and its outcome once reported (see report_outcome()).
        "request_id": None,
        "outcome": None,
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


def output_identity(job):
    """{mod, internal, damage} of what the job on this CPU is making, the
    form craft_monitor.lua compares against the CPU's finalOutput() - or
    None when that isn't reported."""
    if not job.get("final_output_internal"):
        return None
    return {
        "mod": job.get("final_output_mod"),
        "internal": job.get("final_output_internal"),
        "damage": job.get("final_output_damage"),
    }


def same_output(a, b):
    """Whether two output identities name the same item."""
    return all(a.get(key) == b.get(key) for key in ("mod", "internal", "damage"))


# How long the end of a watched request's job waits for the game to
# report its outcome (see report_outcome()) before it's judged without it.
# craft_monitor.lua checks every 1.5s, but a tick also waits on its HTTP
# calls.
OUTCOME_WAIT_SECONDS = 15


def _end_job_locked(name, label=None, icon=None, hold=True):
    """Records the end of the job tracked on CPU `name`. Caller holds
    state.tracking_lock.

    A browser request's job that craft_monitor.lua is watching for its
    outcome, and hasn't reported one yet, is held in state.cpu_ending
    instead (unless hold=False) - the game can see the CPU go idle a
    moment before it reports how the job ended."""
    last_known = state.cpu_last_known.pop(name, None) or _new_job_entry(time.time(), False)
    label = label or last_known.get("label")
    icon = icon or last_known.get("icon")
    ended_at = time.time()
    if hold and last_known.get("request_id") is not None and last_known.get("outcome") is None:
        state.cpu_ending[name] = {
            "entry": last_known, "label": label, "icon": icon, "ended_at": ended_at,
        }
        return
    _record_end_locked(name, last_known, label, icon, ended_at)


def _record_end_locked(name, entry, label, icon, ended_at):
    outcome = entry.get("outcome")
    if outcome is not None:
        status = "finished" if outcome == "finished" else "incomplete"
    else:
        status = _classify_status(name, entry, ended_at)
    output = entry.get("output")
    completions = store.crafts.record_job_end(
        name,
        label,
        icon,
        status,
        entry.get("progress"),
        started_at=entry["started_at"] if entry.get("seen_from_start") else None,
        item=output and {"mod": output[1], "internal": output[2], "damage": output[3]},
    )
    push.notify_completions(completions, label, icon, status)


def _release_ending_locked(name):
    """Records CPU `name`'s held job end, if any, with what's known now."""
    held = state.cpu_ending.pop(name, None)
    if held:
        _record_end_locked(name, held["entry"], held["label"], held["icon"], held["ended_at"])


def _release_overdue_locked(now):
    for name, held in list(state.cpu_ending.items()):
        if now - held["ended_at"] >= OUTCOME_WAIT_SECONDS:
            _release_ending_locked(name)


def report_outcome(request_id, cpu_name, outcome):
    """How the game says a browser request's job ended: "finished" or
    "cancelled", from the request's own AE2 crafting link - certain,
    unlike judging it from the job's last samples. Returns False when no
    job for that request is tracked on cpu_name any more (it already
    ended and was judged without this)."""
    with state.tracking_lock:
        held = state.cpu_ending.get(cpu_name)
        if held and held["entry"].get("request_id") == request_id:
            held["entry"]["outcome"] = outcome
            _release_ending_locked(cpu_name)
            return True
        entry = state.cpu_last_known.get(cpu_name)
        if entry and entry.get("request_id") == request_id:
            entry["outcome"] = outcome
            return True
        return False


def process_jobs(jobs):
    """Detects the end of each CPU's job against our own server-side
    memory of its last state, logs a permanent craft_events row for
    each, and fans out + auto-unpins any users who had that CPU pinned.

    A job ends when its CPU goes busy->idle, or when a busy CPU reports
    a different final output than before - a CPU that finishes and
    starts its next job between two polls is never seen idle, and its
    pins must not carry over to the new job. A back-to-back job making
    the same item, or a CPU without a Crafting Monitor (no final output
    at all), still can't be told apart from one long job.

    Also sets each job's progress_percent, steps_done and steps_total
    (gcm/progress.py), replacing anything the game sent.

    An idle CPU can carry last_busy: craft_monitor.lua's last look at the
    job between its full reports, taken `age` seconds ago - fresher than
    our last report of it, so the job's end is judged from that instead.
    It's removed from the job either way; it's not for the page."""
    now = time.time()
    with state.tracking_lock:
        _release_overdue_locked(now)
        for job in jobs:
            last_busy = job.pop("last_busy", None)
            name = job.get("name")
            if not name:
                continue
            busy = bool(job.get("busy"))
            if busy:
                # A new job: a held end must be recorded before anyone can
                # pin this one, or it would take their pins.
                _release_ending_locked(name)
            was_busy = state.cpu_last_busy.get(name)
            output = _job_output(job)

            if was_busy is None and not busy:
                store.crafts.drop_cpu_pins(name)

            if was_busy is True and busy is False:
                _fold_last_busy(state.cpu_last_known.get(name), last_busy, now)
                _end_job_locked(name, job.get("final_output"), job.get("final_output_icon"))

            if was_busy is True and busy:
                previous = state.cpu_last_known.get(name, {}).get("output")
                if output and previous and output != previous:
                    # Not held: the new job can be pinned right away.
                    _end_job_locked(name, hold=False)

            if busy:
                entry = state.cpu_last_known.get(name)
                if entry is None:
                    # A CPU busy in the first report since the server
                    # started has been running for who knows how long.
                    entry = state.cpu_last_known[name] = _new_job_entry(now, was_busy is not None)
                if output:
                    entry["output"] = output
                    entry["label"] = job.get("final_output")
                    entry["icon"] = job.get("final_output_icon")
                _update_progress(entry, job, now)
            else:
                job["progress_percent"] = None

            state.cpu_last_busy[name] = busy


def _fold_last_busy(entry, last_busy, now):
    """Folds a job's last_busy snapshot (see process_jobs()) into its
    entry, as a sample taken when the snapshot was. Ignored if malformed."""
    if entry is None or not isinstance(last_busy, dict):
        return
    age = last_busy.get("age")
    if isinstance(age, bool) or not isinstance(age, (int, float)):
        return
    sampled_at = now - max(0, age)
    if entry["last_sample_at"] is not None and sampled_at <= entry["last_sample_at"]:
        return  # not newer than the last full report
    pending, active = last_busy.get("pending"), last_busy.get("active")
    if not isinstance(pending, list) or not isinstance(active, list):
        return
    _update_progress(entry, {"pending": pending, "active": active}, sampled_at)


def _update_progress(entry, job, sampled_at):
    percent, steps_done, steps_total = progress.update(entry["peaks"], job)
    if percent is not None:
        # Never backwards, even if a step's remaining amount grows mid-job.
        percent = max(percent, entry["progress"] or 0)
        entry["progress"] = percent
        entry["steps_left"] = steps_total - steps_done
        entry["steps_total"] = steps_total
        if entry["first_sample_at"] is None:
            entry["first_sample_at"] = sampled_at
        entry["last_sample_at"] = sampled_at
    job["progress_percent"] = entry["progress"]
    job["steps_done"] = steps_done
    job["steps_total"] = steps_total


def start_requested_job(user_id, cpu_name, label, icon, requested_at, request_id=None):
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
    Steps left are unknown (None) rather than guessed, which
    _classify_status reads as "finished" for exactly this reason.

    The game only starts a request on a CPU it saw idle, so a job we're
    still tracking there from before the request was made has already
    ended - it's closed first, or the new pin would inherit its end. A
    job first seen after the request is this request's own.

    request_id is given when craft_monitor.lua keeps watching the request
    for how its job ends (see report_outcome())."""
    with state.tracking_lock:
        _release_ending_locked(cpu_name)
        entry = state.cpu_last_known.get(cpu_name)
        if state.cpu_last_busy.get(cpu_name) and entry and entry["started_at"] < requested_at:
            _end_job_locked(cpu_name, hold=False)
            entry = None
        if entry is None:
            entry = state.cpu_last_known[cpu_name] = _new_job_entry(time.time())
        if label and not entry["output"]:
            entry["label"] = label
        if icon and not entry["output"]:
            entry["icon"] = icon
        if request_id is not None:
            entry["request_id"] = request_id
        state.cpu_last_busy[cpu_name] = True
        store.crafts.pin_cpu(user_id, cpu_name)
