"""Rates of change and time-to-full/empty estimates, for power readings
and item stock history."""

# Shorter than this and a few noisy readings swing the rate wildly.
MIN_SPAN_SECONDS = 600

# An estimate further out than this reads as "steady", not as a date.
MAX_ETA_SECONDS = 365 * 86400

# The window the power ETA is judged over: recent enough to follow a
# change in load, long enough to smooth out machines cycling.
POWER_WINDOW_SECONDS = 3600


def _eta(amount, rate):
    """Seconds for `amount` to be used up at `rate` per second, or None
    when that's too far off to mean anything."""
    if rate <= 0 or amount <= 0:
        return None
    seconds = amount / rate
    return seconds if seconds <= MAX_ETA_SECONDS else None


def slope(points):
    """Least-squares change per second of (t, value) points, or None
    with fewer than two distinct times."""
    n = len(points)
    if n < 2:
        return None
    mean_t = sum(p[0] for p in points) / n
    mean_v = sum(p[1] for p in points) / n
    var_t = sum((p[0] - mean_t) ** 2 for p in points)
    if var_t == 0:
        return None
    return sum((p[0] - mean_t) * (p[1] - mean_v) for p in points) / var_t


def power_trend(rows, now):
    """{per_second, window_seconds, seconds_to_full, seconds_to_empty}
    from the last POWER_WINDOW_SECONDS of (ts, stored, capacity) rows
    (ascending), or None when they span too little time. Fitted rather
    than first-to-last, so one noisy reading at either end doesn't
    decide it."""
    recent = [r for r in rows if r[0] >= now - POWER_WINDOW_SECONDS]
    if len(recent) < 2 or recent[-1][0] - recent[0][0] < MIN_SPAN_SECONDS:
        return None
    rate = slope([(r[0], r[1]) for r in recent])
    if rate is None:
        return None
    _, stored, capacity = recent[-1]
    return {
        "per_second": rate,
        "window_seconds": recent[-1][0] - recent[0][0],
        "seconds_to_full": _eta(capacity - stored, rate),
        "seconds_to_empty": _eta(stored, -rate),
    }


def stock_trend(rows, now):
    """{per_second, window_seconds, seconds_to_empty} from an item's
    (recorded_at, size) change rows (ascending, the first one holding
    the value at the range's start), or None when they span too little
    time. Stock only changes on a scan, so it's first-to-last rather
    than a fit: the value holds between rows, and a fit through the
    change points alone would weigh a burst of small changes over one
    big one."""
    if not rows or now - rows[0][0] < MIN_SPAN_SECONDS:
        return None
    window = now - rows[0][0]
    current = rows[-1][1]
    rate = (current - rows[0][1]) / window
    return {
        "per_second": rate,
        "window_seconds": window,
        "seconds_to_empty": _eta(current, -rate),
    }
