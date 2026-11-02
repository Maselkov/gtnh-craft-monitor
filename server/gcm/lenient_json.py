"""Reads request bodies from the game that aren't quite JSON. Two kinds
are known:

- Control characters sent raw in strings: json.lua used to escape only
  the usual few, and scripts already in the field still do.
- Text that isn't UTF-8: some players' games send a label's § (the
  formatting-code sign, "§a16k§r ME Storage Cell") as the single byte
  0xA7, as cp1252/Latin-1 write it, rather than UTF-8's C2 A7 -
  presumably Java's default charset on Windows somewhere between OC and
  the HTTP request, which json.lua never sees.

Either made Python refuse the body, so every scan batch holding such an
item was a 400 and the scan was never complete. A body that fails to
parse is read again with any byte that isn't part of valid UTF-8 taken
as cp1252, and control characters allowed in strings; neither can
change the body's structure, only a string's text. That's logged the
first time for each path, and a body that still won't parse every time."""

import codecs
import logging

from flask import has_request_context, request
from flask.json.provider import DefaultJSONProvider

from gcm import state

log = logging.getLogger(__name__)

_ERRORS = "gcm-cp1252"


def _as_cp1252(e):
    """A codec error handler: the first byte that isn't valid UTF-8 as
    cp1252 (Latin-1 for the five bytes cp1252 leaves undefined), then
    decoding carries on after it."""
    byte = e.object[e.start:e.start + 1]
    try:
        return byte.decode("cp1252"), e.start + 1
    except UnicodeDecodeError:
        return byte.decode("latin-1"), e.start + 1


def _near(s, e):
    """The text around where e says parsing failed, for the log."""
    pos = getattr(e, "pos", None)
    if pos is None:
        pos = getattr(e, "start", 0)
    return s[max(0, pos - 80):pos + 40]


class LenientJSONProvider(DefaultJSONProvider):
    def __init__(self, app):
        super().__init__(app)
        codecs.register_error(_ERRORS, _as_cp1252)

    def loads(self, s, **kwargs):
        try:
            return super().loads(s, **kwargs)
        except ValueError as e:
            text = s.decode("utf-8", _ERRORS) if isinstance(s, (bytes, bytearray)) else s
            path = request.path if has_request_context() else "?"
            try:
                value = super().loads(text, **{**kwargs, "strict": False})
            except ValueError:
                log.warning("Unreadable JSON sent to %s: %s, near %r", path, e, _near(s, e))
                raise
            if path not in state.lenient_json_paths:
                state.lenient_json_paths.add(path)
                log.warning(
                    "Read JSON sent to %s leniently (%s), near %r - not logged again until a restart",
                    path, e, _near(s, e),
                )
            return value
