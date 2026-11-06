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
    from the last POWER_WINDOW_SECONDS of (ts, stored, capacity, ...) rows
    (ascending), or None when they span too little time. Fitted rather
    than first-to-last, so one noisy reading at either end doesn't
    decide it."""
    recent = [r for r in rows if r[0] >= now - POWER_WINDOW_SECONDS]
    if len(recent) < 2 or recent[-1][0] - recent[0][0] < MIN_SPAN_SECONDS:
        return None
    rate = slope([(r[0], r[1]) for r in recent])
    if rate is None:
        return None
    stored, capacity = recent[-1][1], recent[-1][2]
    return {
        "per_second": rate,
        "window_seconds": recent[-1][0] - recent[0][0],
        "seconds_to_full": _eta(capacity - stored, rate),
        "seconds_to_empty": _eta(stored, -rate),
    }


# An LSC in wireless mode rebalances every 6000 ticks: 5 minutes at 20 TPS.
LSC_REBALANCE_SECONDS = 300
# Readings it takes for their average to stand for the whole cycle.
MIN_WIRELESS_READINGS = 10


def wireless_trade(rows, now):
    """EU/s an LSC in wireless mode sent to (positive) or drew from
    (negative) the wireless network over the last POWER_WINDOW_SECONDS
    of (ts, stored, capacity, wireless_threshold) rows, or None.

    Its own in/out averages can't say: a rebalance counts what it
    uploads as output and what it downloads as input, so over an hour
    they differ by little more than its passive loss. But each rebalance
    resets it to its threshold, and between them its own surplus (or
    deficit) builds up steadily from there. Readings land all over the
    cycle, so on average they catch half a cycle's worth: the rate is
    twice the average distance from the threshold, over a cycle. Server
    lag stretches a cycle past 5 real minutes, so it reads a little high
    then."""
    recent = [r for r in rows if r[0] >= now - POWER_WINDOW_SECONDS and r[3]]
    if not recent:
        return None
    threshold = recent[-1][3]  # one changed capacitor makes a new baseline
    recent = [r for r in recent if r[3] == threshold]
    if len(recent) < MIN_WIRELESS_READINGS or recent[-1][0] - recent[0][0] < MIN_SPAN_SECONDS:
        return None
    surplus = sum(r[1] - threshold for r in recent) / len(recent)
    return 2 * surplus / LSC_REBALANCE_SECONDS


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
