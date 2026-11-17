"""Reading a request's JSON body that should be an object. Routes read
its fields with .get(), so a body that's valid JSON but not an object (a
list, a string, a number) made that an AttributeError and a 500 - on
public routes too (signing in, first-run setup), which let anyone fill
the log with tracebacks."""

from flask import request


def json_object():
    """The request's JSON body if it's an object, else {} (no body, one
    that doesn't parse, or another JSON type): each route's own checks
    then refuse it as missing its fields, with a 400."""
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def text(payload, key):
    """payload[key] with its ends stripped when it's a string, else "" -
    for a required name, which the route then refuses as missing."""
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""
