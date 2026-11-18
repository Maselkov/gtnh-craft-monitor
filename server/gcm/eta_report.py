"""How far off a running job's time left would have been, replayed over
finished jobs (`python app.py eta-report`): at 30%, 50% and 70% of each
recent finished job with progress marks (gcm/eta.py), what each way of
estimating it would have said against how long the job really had left.

  old       what the server said before gcm/eta.py: the pace since the
            start, carried on to 100%
  pace      the pace of the last PACE_WINDOW_SECONDS
  history   the item's earlier finished jobs, as the server reads them
  shown     what the page shows now: the longer of pace and history

The marks are only every 10%, so the replayed pace is coarser than the
live one."""

import statistics

from gcm import eta, store

CHECKPOINTS = (30, 50, 70)
# Shorter than this left, a few seconds either way swing the ratio.
MIN_LEFT_SECONDS = 30
METHODS = ("old", "pace", "history", "shown")


def _replayed_pace(marks, at, percent):
    """The pace estimate at `percent`, `at` seconds in, from the marks."""
    if at < eta.ETA_MIN_SECONDS or percent < eta.ETA_MIN_PROGRESS:
        return None
    anchor_at, anchor_percent = 0.0, 0
    for p, t in sorted(marks.items()):
        if t <= at - eta.PACE_WINDOW_SECONDS:
            anchor_at, anchor_percent = t, p
    if at <= anchor_at or percent <= anchor_percent:
        return None
    return (at - anchor_at) * (100 - percent) / (percent - anchor_percent)


def replay(limit):
    """{method: [estimate / actual time left]} over the latest `limit`
    finished jobs with marks."""
    ratios = {m: [] for m in METHODS}
    for job_id, mod, internal, damage, amount, total, marks_text in store.crafts.timed_jobs(limit):
        marks = eta.parse_marks(marks_text)
        runs = [
            (seconds, past_amount, eta.parse_marks(past_marks))
            for _, seconds, past_amount, past_marks in store.crafts.past_runs(
                {"mod": mod, "internal": internal, "damage": damage}, amount,
                eta.HISTORY_JOBS, eta.HISTORY_AMOUNT_RATIO, before_id=job_id)
        ]
        for percent in CHECKPOINTS:
            at = marks.get(percent)
            if at is None or total - at < MIN_LEFT_SECONDS:
                continue
            left = total - at
            pace = _replayed_pace(marks, at, percent)
            history = eta.history_eta(runs, amount, percent, 0)
            shown = max((x for x in (pace, history) if x is not None), default=None)
            found = {"old": at * (100 - percent) / percent if at >= eta.ETA_MIN_SECONDS else None,
                     "pace": pace, "history": history, "shown": shown}
            for method, value in found.items():
                if value is not None:
                    ratios[method].append(value / left)
    return ratios


def report(limit):
    """replay() as a table, one line per method."""
    ratios = replay(limit)
    lines = [
        f"Time left at {', '.join(f'{p}%' for p in CHECKPOINTS)} of the latest {limit} finished jobs,",
        "as a multiple of what the job really had left (1.00 is spot on):",
        "",
        f"{'':9}{'estimates':>10}{'median':>9}{'within 1.5x':>13}{'under half':>12}",
    ]
    for method in METHODS:
        values = ratios[method]
        if not values:
            lines.append(f"{method:9}{0:>10}{'-':>9}{'-':>13}{'-':>12}")
            continue
        close = sum(1 / 1.5 <= v <= 1.5 for v in values) / len(values)
        short = sum(v < 0.5 for v in values) / len(values)
        lines.append(f"{method:9}{len(values):>10}{statistics.median(values):>9.2f}{close:>13.0%}{short:>12.0%}")
    return "\n".join(lines)
