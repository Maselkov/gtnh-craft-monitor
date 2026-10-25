"""gcm_agent.lua: one per in-game computer. Each heartbeat (every minute)
says what the computer runs and how it's doing; the reply hands it at
most one command an admin queued on the Scripts dialog. Commands are
only "update" (to the latest release or to main) and "reboot": gcm.lua
downloads from its own fixed repository, so nothing here can make a
computer run code that isn't a real release or main.

All in memory, like the craft request queue: heartbeats refill it within
a minute of a restart, and a command is only worth anything while an
admin is waiting on it."""

import re
import threading
import time

from gcm import config, gamedata

POLL_SECONDS = 60
OFFLINE_AFTER_SECONDS = 3 * POLL_SECONDS
# A queued command not fetched by then is dropped: the computer is
# off, and an update shouldn't happen whenever it comes back.
PICKUP_TIMEOUT_SECONDS = 3 * POLL_SECONDS
# Downloading every script can take a while on a slow card.
RESULT_TIMEOUT_SECONDS = 600
# gcm_agent heartbeats as soon as rc starts it after a reboot.
BOOT_TIMEOUT_SECONDS = 3 * POLL_SECONDS
# How long a command that went fine stays on the computer's card once
# it's over (back from its reboot, if it rebooted). Failures stay until
# RETENTION_SECONDS, so they aren't missed.
COMMAND_SHOWN_SECONDS = 600
RETENTION_SECONDS = 86400
LATEST_TTL_SECONDS = 3600
LATEST_ERROR_TTL_SECONDS = 300

ACTIONS = {("update", "release"), ("update", "main"), ("reboot", None)}

_lock = threading.Lock()
_agents = {}    # address -> its last heartbeat, plus last_seen
_commands = {}  # id -> command
_next_id = 1
_latest = {"tag": None, "error": None, "fetched_at": 0}


def reset():
    global _next_id
    with _lock:
        _agents.clear()
        _commands.clear()
        _next_id = 1
        _latest.update(tag=None, error=None, fetched_at=0)


def _clean_report(report):
    def text(v, limit=64):
        return v[:limit] if isinstance(v, str) else None

    def number(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    services = report.get("services")
    return {
        "version": text(report.get("version")),
        "components": [text(c) for c in report.get("components") or [] if isinstance(c, str)][:16]
        if isinstance(report.get("components"), list) else [],
        "services": [
            {"name": text(s.get("name")), "running": s.get("running") is True}
            for s in (services if isinstance(services, list) else [])[:16]
            if isinstance(s, dict) and isinstance(s.get("name"), str)
        ],
        "free_memory": number(report.get("free_memory")),
        "total_memory": number(report.get("total_memory")),
        "uptime": number(report.get("uptime")),
    }


def _expire_locked(now):
    for cmd_id, cmd in list(_commands.items()):
        if cmd["status"] == "queued" and now - cmd["created_at"] > PICKUP_TIMEOUT_SECONDS:
            _finish_locked(cmd, False, "The computer didn't pick it up - is gcm_agent running?", now)
        elif cmd["status"] == "sent" and now - cmd["sent_at"] > RESULT_TIMEOUT_SECONDS:
            _finish_locked(cmd, False, "No result from the computer - check in game.", now)
        elif cmd.get("awaiting_boot") and now - cmd["finished_at"] > BOOT_TIMEOUT_SECONDS:
            cmd["awaiting_boot"] = False
            _finish_locked(cmd, False, "Didn't come back after the reboot - check in game.", now)
        elif cmd["status"] == "done" and now - cmd["finished_at"] > RETENTION_SECONDS:
            del _commands[cmd_id]


def _finish_locked(cmd, ok, message, now):
    cmd.update(status="done", ok=ok, message=message, finished_at=now)


def _came_back(cmd, uptime, now):
    """Whether a heartbeat's uptime says the computer rebooted since it
    reported cmd's result."""
    if uptime is None:
        return False
    if cmd.get("uptime_before") is not None:
        return uptime < cmd["uptime_before"]
    return uptime <= now - cmd["finished_at"]


def heartbeat(address, report):
    """Records a heartbeat; returns the command to hand over, or None.
    Each command is handed over once: running an update twice is
    harmless, but a reboot twice isn't."""
    now = time.time()
    with _lock:
        _expire_locked(now)
        _agents[address] = {**_clean_report(report), "address": address, "last_seen": now}
        for cmd in _commands.values():
            if cmd["address"] == address and cmd.get("awaiting_boot") and _came_back(cmd, _agents[address]["uptime"], now):
                cmd.update(awaiting_boot=False, back_at=now)
        for cmd in sorted(_commands.values(), key=lambda c: c["id"]):
            if cmd["address"] == address and cmd["status"] == "queued":
                cmd.update(status="sent", sent_at=now)
                return {"id": cmd["id"], "action": cmd["action"], "ref": cmd["ref"]}
    return None


def queue(address, action, ref, user_id):
    """Queues a command for a computer; returns (id, None) or (None,
    error)."""
    global _next_id
    if (action, ref) not in ACTIONS:
        return None, "unknown command"
    now = time.time()
    with _lock:
        _expire_locked(now)
        if address not in _agents:
            return None, "unknown computer"
        if any(c["address"] == address and (c["status"] != "done" or c.get("awaiting_boot"))
               for c in _commands.values()):
            return None, "this computer already has a command waiting"
        cmd_id = _next_id
        _next_id += 1
        _commands[cmd_id] = {
            "id": cmd_id, "address": address, "action": action, "ref": ref, "user_id": user_id,
            "status": "queued", "created_at": now,
        }
    return cmd_id, None


def _rebooting(cmd, ok, message, rebooting):
    if rebooting is not None:
        return rebooting is True
    # gcm_agent from before it said so: it reboots after a reboot command
    # and after an update that changed something, and says so.
    return ok is True and (cmd["action"] == "reboot" or (isinstance(message, str) and message.endswith("rebooting.")))


def result(cmd_id, ok, message, version, rebooting=None):
    """The computer's report on a command. False for an unknown or
    already finished one. A computer about to reboot is watched for
    coming back (heartbeat())."""
    now = time.time()
    with _lock:
        cmd = _commands.get(cmd_id)
        if cmd is None or cmd["status"] == "done":
            return False
        _finish_locked(cmd, ok is True, message[:300] if isinstance(message, str) else None, now)
        if _rebooting(cmd, ok, message, rebooting):
            agent = _agents.get(cmd["address"])
            cmd.update(awaiting_boot=True, uptime_before=agent and agent["uptime"])
        if isinstance(version, str) and cmd["address"] in _agents:
            _agents[cmd["address"]]["version"] = version[:64]
        return True


def _public_command(cmd):
    return {k: cmd.get(k) for k in (
        "id", "action", "ref", "status", "ok", "message", "created_at", "finished_at", "awaiting_boot", "back_at",
    )}


def _still_shown(cmd, now):
    if cmd["status"] != "done" or not cmd["ok"] or cmd.get("awaiting_boot"):
        return True
    return now - (cmd.get("back_at") or cmd["finished_at"]) <= COMMAND_SHOWN_SECONDS


def snapshot():
    """Every computer that has sent a heartbeat, newest command first."""
    now = time.time()
    with _lock:
        _expire_locked(now)
        out = []
        for address, agent in sorted(_agents.items()):
            mine = sorted((c for c in _commands.values() if c["address"] == address), key=lambda c: -c["id"])
            out.append({
                **agent,
                "offline": now - agent["last_seen"] > OFFLINE_AFTER_SECONDS,
                "command": _public_command(mine[0]) if mine and _still_shown(mine[0], now) else None,
            })
    return out


def _version_key(tag):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag or "")
    return tuple(int(p) for p in match.groups()) if match else None


def latest_release():
    """(tag, error): the newest vX.Y.Z tag of the scripts' repository, the
    one `gcm update` picks. Cached; GitHub rate-limits anonymous use."""
    now = time.time()
    with _lock:
        ttl = LATEST_ERROR_TTL_SECONDS if _latest["error"] else LATEST_TTL_SECONDS
        if _latest["fetched_at"] and now - _latest["fetched_at"] < ttl:
            return _latest["tag"], _latest["error"]
    tag, error = None, None
    try:
        tags = gamedata.github_json(f"{config.GAMEDATA_API_URL}/repos/{config.SCRIPTS_REPO}/tags?per_page=100")
        versions = [(_version_key(t.get("name")), t.get("name")) for t in tags if isinstance(t, dict)]
        versions = [v for v in versions if v[0]]
        tag = max(versions)[1] if versions else None
        if not tag:
            error = "no release tags found"
    except Exception as e:  # network, rate limit, bad JSON: say so, don't fail the page
        error = f"couldn't check GitHub: {e}"
    with _lock:
        _latest.update(tag=tag, error=error, fetched_at=now)
    return tag, error


def is_behind(version, latest):
    """Whether an installed version is an older release than latest. A
    branch (main) or unknown version isn't compared."""
    have, want = _version_key(version), _version_key(latest)
    return bool(have and want and have < want)
