"""Persistence. Every SQL statement outside db.py's schema lives in this
package, one module per domain; tests/test_store_boundary.py fails if
one appears anywhere else.

Functions open their own connection with db.transaction() and return
plain tuples and dicts, so callers never handle SQLite. A change that
must be atomic across several statements is one function here, not a
sequence of calls. The exceptions are a few token helpers in users.py
that take a `conn` to join a caller's transaction."""

from gcm.store import crafts, items, power, requests, users  # noqa: F401
