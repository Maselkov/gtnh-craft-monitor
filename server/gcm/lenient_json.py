"""Reads request bodies that aren't quite JSON. The OC scripts' json.lua
used to send control characters in strings raw, and an item label with
one made every batch holding it a 400 - the same batches on every scan,
so the scan was never complete. A body that fails to parse is read again
with invalid UTF-8 replaced and control characters allowed in strings;
neither can change the body's structure, only a string's text."""

import logging

from flask import has_request_context, request
from flask.json.provider import DefaultJSONProvider

log = logging.getLogger(__name__)


def _near(s, e):
    """The text around where e says parsing failed, for the log."""
    pos = getattr(e, "pos", None)
    if pos is None:
        pos = getattr(e, "start", 0)
    return s[max(0, pos - 80):pos + 40]


class LenientJSONProvider(DefaultJSONProvider):
    def loads(self, s, **kwargs):
        try:
            return super().loads(s, **kwargs)
        except ValueError as e:
            text = s.decode("utf-8", "replace") if isinstance(s, (bytes, bytearray)) else s
            path = request.path if has_request_context() else "?"
            try:
                value = super().loads(text, **{**kwargs, "strict": False})
            except ValueError:
                log.warning("Unreadable JSON sent to %s: %s, near %r", path, e, _near(s, e))
                raise
            log.warning("Read JSON sent to %s leniently (%s), near %r", path, e, _near(s, e))
            return value
