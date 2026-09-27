"""Crafting-job progress from a CPU status report, measured per step.

A step is one item or fluid type the job still has to produce, from its
pending and active lists. Each step's progress is how far its remaining
amount has fallen from the largest amount seen for it during this job
(its peak), and the job's progress is the average over all steps,
each counted equally.

Why this shape:
  - Remaining over peak has no units, so a fluid counted in mB weighs the
    same as an item counted in stacks - no conversion factor, which
    wouldn't exist anyway (fluids aren't all ingot-based).
  - Counting each step equally stops 4096 cheap wires from outweighing
    the one slow item.
  - `stored` is ignored: it also holds the raw ingredients the CPU pulled
    at the start, which shrink as they're consumed, so it isn't "done".
  - There's no time weighting. Recipe durations aren't available, machines
    are shared between concurrent jobs, and "active" includes items still
    queued behind others, so observed rates wouldn't describe this job.
    The result says how many steps are done, not how much time is left."""


def _key(item):
    return (item.get("mod"), item.get("internal"), item.get("damage"), item.get("name"))


def update(peaks, job):
    """Folds one status report of a busy job into `peaks` (the job's
    step key -> largest remaining amount seen, updated in place) and
    returns (percent, steps_done, steps_total). percent is None when no
    step has been seen yet."""
    remaining = {}
    for item in (job.get("pending") or []) + (job.get("active") or []):
        if not isinstance(item, dict):
            continue
        key = _key(item)
        remaining[key] = remaining.get(key, 0) + (item.get("size") or 0)

    for key, amount in remaining.items():
        if amount > peaks.get(key, 0):
            peaks[key] = amount
    # A step seen earlier that's missing now has nothing left: done.
    for key in peaks:
        remaining.setdefault(key, 0)

    steps = [key for key in remaining if peaks.get(key, 0) > 0]
    if not steps:
        return None, 0, 0
    done = sum(1 - remaining[key] / peaks[key] for key in steps)
    steps_done = sum(1 for key in steps if remaining[key] == 0)
    return int(done / len(steps) * 100), steps_done, len(steps)
