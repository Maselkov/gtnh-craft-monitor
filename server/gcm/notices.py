"""The page's notices someone dismissed, kept server-wide in app.db's
settings: each one is about the server (an upgrade's pattern check, the
computers' scripts, the game data, the base's Interface Terminal), so
dismissing it once holds for everyone who sees it, on every browser. A
notice is dismissed for one value - a version pair, a release - and
comes back when its value changes.

Each notice has the role that sees it, and may dismiss it: the pattern
check is the operators' too, since they fix patterns in game; the rest
are admin jobs."""

from gcm import auth, store

ROLES = {
    "recipe_check": "operator",
    "scripts_update": "admin",
    "gamedata": "admin",
    "terminal": "admin",
}
_PREFIX = "notice_dismissed."
MAX_VALUE_LENGTH = 200


def _sees(user, name):
    return auth.is_admin(user) if ROLES[name] == "admin" else auth.is_operator(user)


def dismissed(user):
    """{name: the value it was dismissed for}, for the notices user sees."""
    stored = store.settings.load()
    return {
        name: stored[_PREFIX + name]
        for name in ROLES
        if _sees(user, name) and isinstance(stored.get(_PREFIX + name), str)
    }


def dismiss(user, name, value):
    """Returns None, or (what's wrong with the request, HTTP status)."""
    if name not in ROLES:
        return f"unknown notice: {name}", 400
    if not _sees(user, name):
        return "admin access required", 403
    if not isinstance(value, str) or not 0 < len(value) <= MAX_VALUE_LENGTH:
        return f"value must be a string of 1 to {MAX_VALUE_LENGTH} characters", 400
    store.settings.save({_PREFIX + name: value})
    return None
