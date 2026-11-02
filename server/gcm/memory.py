"""Hands memory the server has freed back to the OS, after the big
allocations of a network or pattern scan.

Python keeps small objects in its own pools, but a scan's lists, dicts
and longer strings come from glibc's malloc, which on free() only gives
back the very end of its main heap. The freed pages in the middle, and
those in the heaps of waitress's other threads, stay with the process, so
it stays the size of its biggest moment so far: the old snapshot, the new
one and the indexes built from it all alive at once, a little bigger
each scan. malloc_trim() returns every free page (measured with a big
base's pattern scans: 147 -> 106 MB with waitress's 8 threads). Without
glibc (musl, macOS) there's no malloc_trim, and this does nothing."""

import ctypes
import logging

log = logging.getLogger(__name__)

_UNSET = object()
_malloc_trim = _UNSET  # looked up on first use: importing gcm has no side effects


def _find():
    try:
        found = ctypes.CDLL(None).malloc_trim
    except (AttributeError, OSError):
        return None
    found.argtypes = [ctypes.c_size_t]
    found.restype = ctypes.c_int
    return found


def trim():
    """Returns free memory to the OS, if the C library can. Takes a few
    milliseconds, so it's for after a scan, not every request."""
    global _malloc_trim
    if _malloc_trim is _UNSET:
        _malloc_trim = _find()
    if _malloc_trim is None:
        return
    try:
        _malloc_trim(0)
    except Exception:
        log.exception("malloc_trim failed")
