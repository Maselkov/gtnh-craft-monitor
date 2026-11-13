"""Logs every request that takes a second or more, with how many others
were running alongside it.

waitress serves with a few threads (app.py), so a handful of slow
requests can hold all of them and leave every other request, the
page's polls and the OC computer's reports among them, queued behind.
This log says which requests were slow and whether the threads were
all busy at the time ("8 running"): enough to tell one slow endpoint
from a pile-up. Time spent queued before a thread picks a request up
isn't counted; it shows up as the "running" number staying at the
thread count."""

import logging
import threading
import time

from flask import g, request

log = logging.getLogger(__name__)

SLOW_SECONDS = 1.0

_lock = threading.Lock()
_running = 0


def init_app(app):
    """Registered before every other hook, so a request a later hook
    turns away (a 401, a 403) is still timed."""
    app.before_request_funcs.setdefault(None, []).insert(0, _start)
    app.after_request(_note_status)
    app.teardown_request(_finish)


def running():
    """Requests under way right now, for the tests."""
    return _running


def _start():
    global _running
    with _lock:
        _running += 1
        g.slow_requests_running = _running
    g.slow_requests_started = time.monotonic()


def _note_status(response):
    g.slow_requests_status = response.status_code
    return response


def _finish(exc):
    global _running
    started = g.pop("slow_requests_started", None)
    if started is None:
        return
    with _lock:
        _running -= 1
    elapsed = time.monotonic() - started
    if elapsed < SLOW_SECONDS:
        return
    # The path alone: a query string can carry an OAuth code or a token.
    status = "error" if exc is not None else g.pop("slow_requests_status", "?")
    log.info(
        "slow request: %s %s took %.1fs (%s), %d running when it started",
        request.method, request.path, elapsed, status, g.pop("slow_requests_running", 0),
    )
