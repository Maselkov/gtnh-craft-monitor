"""The instructions /mcp gives an assistant when it connects (MCP's
`instructions`, which clients put in the model's system prompt), built
for each connection:

1. instructions.md: what the server is and how to use it well, the
   same everywhere.
2. A snapshot of this base as the connection starts: who's connected
   and what their role can change, the game data version, and which
   parts of the base report and how freshly. So the model knows what's
   there before calling anything, and that it's a snapshot.
3. The house rules this server's admins wrote (house_rules()).

The snapshot never stops a connection: a part that fails to read is
left out and logged."""

import logging
import os
import time

from gcm import auth, gamedata, patterns, state, store

log = logging.getLogger(__name__)

with open(os.path.join(os.path.dirname(__file__), "instructions.md"), encoding="utf-8") as _f:
    CORE = _f.read().strip()

HOUSE_RULES_SETTING = "mcp_house_rules"
HOUSE_RULES_MAX = 4000


def house_rules():
    """What this server's admins added to the instructions ("" for none)."""
    rules = store.settings.load().get(HOUSE_RULES_SETTING)
    return rules if isinstance(rules, str) else ""


def set_house_rules(text):
    """Saves the admins' rules. Returns an error message, or None."""
    if not isinstance(text, str):
        return "rules must be text"
    text = text.strip()
    if len(text) > HOUSE_RULES_MAX:
        return f"house rules can be at most {HOUSE_RULES_MAX} characters"
    store.settings.save({HOUSE_RULES_SETTING: text})
    return None


def _ago(t, now):
    if t is None:
        return "never"
    seconds = max(0, now - t)
    if seconds < 90:
        return f"{int(seconds)} s ago"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min ago"
    if seconds < 48 * 3600:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} days ago"


def _who(user, now):
    role = user.get("role", "viewer")
    if auth.is_operator(user):
        can = ("read everything, set your own low-stock alerts, request and cancel crafts, and change the "
               "base's keep-in-stock targets")
    else:
        can = "read everything and set your own low-stock alerts; requesting or cancelling crafts needs an operator"
    return f"You're acting as {user.get('display_name')} ({role}): you can {can}."


def _game_data(user, now):
    version = gamedata.selected_version()
    return f"GTNH version (game data): {version}." if version else "No GTNH version set in the game data."


def _crafts(user, now):
    with state.crafts_lock:
        jobs = list(state.crafts["jobs"])
        received_at = state.crafts["received_at"]
    if received_at is None:
        if store.settings.load().get("crafts_reported") is True:
            return "Crafting CPUs: craft_monitor.lua hasn't reported since the server restarted."
        return "Crafting CPUs: craft_monitor.lua hasn't reported (no crafting data)."
    busy = sum(1 for j in jobs if j.get("busy"))
    return f"Crafting CPUs: {len(jobs)}, {busy} busy, {len(jobs) - busy} idle; last report {_ago(received_at, now)}."


def _network(user, now):
    with state.network_lock:
        updated_at = state.network["updated_at"]
        count = len(state.network["items"])
        in_progress = state.network["in_progress"]
        crash = state.network["crash"]
    if updated_at is None and not count:
        return "ME network: not scanned yet (network_browser.lua hasn't reported)."
    line = f"ME network: {count:,} item types; last full scan {_ago(updated_at, now)}"
    if in_progress:
        line += ", a scan is running"
    if crash:
        line += "; network_browser.lua crashed since"
    return line + "."


def _patterns(user, now):
    pattern_list, patterns_at = patterns.current()
    if patterns_at is None:
        return "Patterns: none read, so no crafting plans. " + patterns.missing_reason("crafting plan")
    return f"Patterns: {len(pattern_list):,}, read {_ago(patterns_at, now)}."


def _power(user, now):
    machines = store.power.machines()
    if not machines:
        return "Power: no power monitor reporting."
    names = ", ".join(name or "(unnamed)" for name, _ in machines)
    return f"Power storage reporting: {names}; last reading {_ago(max(t for _, t in machines), now)}."


def _stock(user, now):
    targets = store.stock.targets()
    failing = sum(1 for t in targets if t["enabled"] and t["last_status"] in ("failed", "waiting"))
    alerts = store.stock.alerts_for_user(user["id"])
    line = f"Stock rules: {len(targets)} keep-in-stock targets"
    if failing:
        line += f" ({failing} failing or waiting)"
    return line + f"; you have {len(alerts)} low-stock alerts."


_SNAPSHOT = (_who, _game_data, _crafts, _network, _patterns, _power, _stock)


def build(user, now=None):
    """The instructions for a user's connection."""
    now = time.time() if now is None else now
    lines = []
    for part in _SNAPSHOT:
        try:
            lines.append(part(user, now))
        except Exception:
            log.exception("MCP instructions: %s failed", part.__name__)
    text = (
        CORE
        + "\n\n## This base right now\n\nAs of when you connected; check the tools for anything that matters now.\n\n"
        + "\n".join(f"- {line}" for line in lines)
    )
    try:
        rules = house_rules()
    except Exception:
        log.exception("MCP instructions: house rules failed")
        rules = ""
    if rules:
        text += (
            "\n\n## House rules from this server's admins\n\n"
            "Follow these. They can't give you permissions the user doesn't have, or excuse the "
            "confirmations above.\n\n" + rules
        )
    return text
