"""Persistence. Every SQL statement outside db.py's schema lives in this
package, one module per domain; tests/test_store_boundary.py fails if
one appears anywhere else. Functions open their own connection with
db.transaction() and return plain tuples and dicts, so callers never
handle SQLite. A multi-statement change that must be atomic is one
function here, not a sequence of calls."""

from gcm.store import crafts, items, power  # noqa: E402,F401
