"""Thins old item history (store.items.thin): after HISTORY_HOURLY_AFTER_DAYS
one row per item per hour, after HISTORY_DAILY_AFTER_DAYS one per day.
Runs on a background thread started by app.py, a few minutes after
startup and then daily; tests and dev.py don't start it.

Power readings aren't thinned: at one a minute they're ~40 MB a year."""

import logging
import threading
import time

from gcm import config, store

log = logging.getLogger(__name__)

FIRST_RUN_DELAY_SECONDS = 300
INTERVAL_SECONDS = 86400

_thread = None


def tiers():
    """(tier name, bucket seconds, age in days) for each tier that's on."""
    out = []
    if config.HISTORY_HOURLY_AFTER_DAYS > 0:
        out.append(("hourly", 3600, config.HISTORY_HOURLY_AFTER_DAYS))
    if config.HISTORY_DAILY_AFTER_DAYS > 0:
        out.append(("daily", 86400, config.HISTORY_DAILY_AFTER_DAYS))
    return out


def run_once(now=None):
    """One pass over every tier; returns {tier: rows deleted}."""
    now = time.time() if now is None else now
    deleted = {}
    for tier, bucket, days in tiers():
        deleted[tier] = store.items.thin(tier, bucket, now - days * 86400)
    if any(deleted.values()):
        log.info("Thinned item history: %s rows deleted", deleted)
        if store.items.vacuum_if_mostly_free():
            log.info("Compacted item_history.db")
    return deleted


def _run():
    time.sleep(FIRST_RUN_DELAY_SECONDS)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("thinning item history failed")
        time.sleep(INTERVAL_SECONDS)


def start():
    global _thread
    if _thread is None and tiers():
        _thread = threading.Thread(target=_run, name="history-retention", daemon=True)
        _thread.start()
