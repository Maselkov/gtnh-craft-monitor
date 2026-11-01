"""The page's admin notices an admin dismissed, kept server-wide in
app.db's settings: each one is about the server (an upgrade's pattern
check, the computers' scripts, the game data, the base's Interface
Terminal), so dismissing it once holds for every admin on every browser.
A notice is dismissed for one value - a version pair, a release - and
comes back when its value changes."""

from gcm import store

NAMES = ("recipe_check", "scripts_update", "gamedata", "terminal")
_PREFIX = "notice_dismissed."
MAX_VALUE_LENGTH = 200


def dismissed():
    """{name: the value it was dismissed for}."""
    stored = store.settings.load()
    return {name: stored[_PREFIX + name] for name in NAMES if isinstance(stored.get(_PREFIX + name), str)}


def dismiss(name, value):
    """Returns None, or what's wrong with the request."""
    if name not in NAMES:
        return f"unknown notice: {name}"
    if not isinstance(value, str) or not 0 < len(value) <= MAX_VALUE_LENGTH:
        return f"value must be a string of 1 to {MAX_VALUE_LENGTH} characters"
    store.settings.save({_PREFIX + name: value})
    return None
