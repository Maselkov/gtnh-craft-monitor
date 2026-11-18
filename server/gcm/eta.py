"""A running job's time left (its eta_seconds), from two estimates:

- History: how long finished jobs making the same item, in about the
  same amount, took to get from the progress this one is at to their
  end. Progress (gcm/progress.py) counts steps, and a job's many cheap
  steps finish first while its few slow ones (the multiblocks near the
  top) come last: a job can be 70% "done" a tenth of the way through.
  A straight line from the pace can't see that; a past job's own curve
  has it in.
- Pace: the progress made over the last PACE_WINDOW_SECONDS, carried on
  to 100%. For an item not made before. Recent rather than since the
  start, so a job that slows down as it reaches its slow steps gets a
  longer estimate instead of keeping its quick start's.

When both are there, the longer wins: both tend to come out short
(the pace, as above; history, when machines are busier than last time),
and too short is the mistake people notice.

A job's curve is kept as when it reached each of MARKS (10%, 20%, ...
90%), in seconds since it started: craft_events is never pruned, so
each job adds a few dozen bytes, not its every report."""

import itertools
import statistics

MARKS = tuple(range(10, 100, 10))

# The pace has to have been watched this long, over this much progress,
# before it says anything about the rest of the job.
ETA_MIN_SECONDS = 120
ETA_MIN_PROGRESS = 10
# How far back the pace looks.
PACE_WINDOW_SECONDS = 600

# Past jobs used for an item, latest first; and how far another amount
# can be from this job's to count, as a ratio either way. Their times
# are scaled by the ratio: within this range, near enough.
HISTORY_JOBS = 20
HISTORY_AMOUNT_RATIO = 2


def new_state():
    """The per-job fields this module keeps in a tracking entry."""
    return {
        "marks": {},           # mark -> seconds after the start it was reached
        "pace_samples": [],    # (time, percent) where the percent changed
        "progress_at": None,   # when the percent last changed
        "history": None,       # past runs (see load_history), once loaded
        "history_for": None,   # the (output, amount) they were loaded for
    }


def note_progress(eta, started_at, sampled_at, percent, seen_from_start):
    """Folds a report's progress into a job's eta state."""
    samples = eta["pace_samples"]
    previous = samples[-1] if samples else None
    if previous is None or percent != previous[1]:
        samples.append((sampled_at, percent))
        eta["progress_at"] = sampled_at
        if seen_from_start:
            _note_marks(eta["marks"], started_at, previous, sampled_at, percent)
    # Keep the window's samples, and the latest one before it as its
    # anchor.
    cutoff = sampled_at - PACE_WINDOW_SECONDS
    while len(samples) > 2 and samples[1][0] <= cutoff:
        samples.pop(0)


def _note_marks(marks, started_at, previous, sampled_at, percent):
    # Between two reports progress went from previous to percent; a mark
    # it passed is put where a straight line between them crosses it.
    t0, p0 = previous if previous else (started_at, 0)
    for mark in MARKS:
        if mark in marks or percent < mark:
            continue
        if percent > p0 and mark > p0:
            at = t0 + (mark - p0) / (percent - p0) * (sampled_at - t0)
        else:
            at = sampled_at
        marks[mark] = round(max(0.0, at - started_at), 1)


def pace_eta(eta, first_sample_at, first_progress, last_sample_at, percent):
    """Seconds left at the pace of the last PACE_WINDOW_SECONDS, or None:
    until the pace has been watched long enough, or with no progress in
    the window."""
    if first_sample_at is None or first_progress is None or percent is None:
        return None
    if last_sample_at - first_sample_at < ETA_MIN_SECONDS or percent - first_progress < ETA_MIN_PROGRESS:
        return None
    anchor_at, anchor_percent = eta["pace_samples"][0]
    for at, p in eta["pace_samples"]:
        if at <= last_sample_at - PACE_WINDOW_SECONDS:
            anchor_at, anchor_percent = at, p
    span, gained = last_sample_at - anchor_at, percent - anchor_percent
    if span <= 0 or gained <= 0:
        return None
    return span * (100 - percent) / gained


def marks_text(marks):
    """A job's marks as craft_events.progress_marks keeps them: seconds
    at each of MARKS, comma-separated, empty for one not reached."""
    if not marks:
        return None
    return ",".join("" if m not in marks else f"{marks[m]:g}" for m in MARKS)


def parse_marks(text):
    out = {}
    for mark, value in zip(MARKS, (text or "").split(","), strict=False):
        try:
            out[mark] = float(value)
        except ValueError:
            pass
    return out


def _time_at(marks, total, percent):
    """When a past job of `total` seconds reached `percent`, from its
    marks, with 0% at its start and 100% at its end."""
    points = [(0, 0.0)] + sorted(marks.items()) + [(100, total)]
    for (p0, t0), (p1, t1) in itertools.pairwise(points):
        if p0 <= percent <= p1:
            if p1 == p0:
                return t0
            return t0 + (percent - p0) / (p1 - p0) * (t1 - t0)
    return total


def history_eta(runs, amount, percent, since_progress):
    """Seconds left from past runs [(seconds, amount, marks)]: the median
    of how long each had left from `percent`, scaled to this job's
    amount, less the time this job has already sat at that percent.
    None with no runs, or when this job is already past all of them."""
    if not runs or percent is None:
        return None
    left = []
    for total, past_amount, marks in runs:
        scale = amount / past_amount if amount and past_amount else 1
        left.append(scale * (total - _time_at(marks, total, percent)))
    remaining = statistics.median(left) - max(0.0, since_progress)
    return remaining if remaining > 0 else None


def estimate(eta, first_sample_at, first_progress, last_sample_at, percent):
    """(seconds left, basis) for a job: basis "history" or "pace", which
    gave the longer estimate; (None, None) when neither can say."""
    since = last_sample_at - eta["progress_at"] if eta["progress_at"] is not None else 0
    from_history = history_eta(eta["history"], (eta["history_for"] or (None, None))[1], percent, since)
    from_pace = pace_eta(eta, first_sample_at, first_progress, last_sample_at, percent)
    if from_history is None and from_pace is None:
        return None, None
    if from_pace is None or (from_history is not None and from_history >= from_pace):
        return from_history, "history"
    return from_pace, "pace"
