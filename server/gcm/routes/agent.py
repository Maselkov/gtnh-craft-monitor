"""gcm_agent.lua's heartbeat and command results, gcm install's
connection check, and the admin Status dialog that reads them, queues
commands (gcm/agents.py) and shows how to connect a computer."""

from flask import Blueprint, g, jsonify, request

from gcm import agents, auth, config, health

bp = Blueprint("agent", __name__)

_MAX_ADDRESS_LENGTH = 64


@bp.route("/api/agent/heartbeat", methods=["POST"])
@auth.api_key_required
def agent_heartbeat():
    payload = request.get_json(silent=True)
    address = payload.get("address") if isinstance(payload, dict) else None
    if not isinstance(address, str) or not 0 < len(address) <= _MAX_ADDRESS_LENGTH:
        return jsonify({"error": "missing address"}), 400
    return jsonify({"command": agents.heartbeat(address, payload),
                    "auto_restart": agents.settings()["auto_restart"]})


@bp.route("/api/agent/ping", methods=["POST"])
@auth.api_key_required
def agent_ping():
    """gcm install's check that the computer reaches this server with
    the key it was given, before it saves them."""
    return jsonify({"ok": True})


@bp.route("/api/agent/commands/<int:cmd_id>/result", methods=["POST"])
@auth.api_key_required
def agent_command_result(cmd_id):
    payload = request.get_json(silent=True) or {}
    if not agents.result(cmd_id, payload.get("ok"), payload.get("message"), payload.get("version"),
                         payload.get("rebooting")):
        return jsonify({"error": "unknown command"}), 404
    return jsonify({"ok": True})


@bp.route("/api/admin/agents", methods=["GET"])
@auth.admin_required
def admin_agents_get():
    latest, error = agents.latest_release()
    computers = agents.snapshot()
    for computer in computers:
        computer["behind"] = agents.is_behind(computer["version"], latest)
    return jsonify({"computers": computers, "latest_release": latest, "latest_error": error})


@bp.route("/api/admin/agent-settings", methods=["GET"])
@auth.admin_required
def admin_agent_settings_get():
    """The Status dialog's checkboxes (gcm/agents.py), and the server's
    own version: auto-update needs one."""
    return jsonify({**agents.settings(), "server_version": config.SERVER_VERSION})


@bp.route("/api/admin/agent-settings", methods=["PUT"])
@auth.admin_required
def admin_agent_settings_put():
    """Body: any of {"auto_update": bool, "auto_restart": bool}."""
    error = agents.save_settings(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400
    return admin_agent_settings_get()


@bp.route("/api/admin/connect", methods=["GET"])
@auth.admin_required
def admin_connect_get():
    """What the Status dialog's "Connect a computer" commands need."""
    return jsonify({"api_key": config.API_KEY, "public_url": config.PUBLIC_URL})


@bp.route("/api/admin/health", methods=["GET"])
@auth.admin_required
def admin_health_get():
    """The Status dialog's Server section (gcm/health.py)."""
    return jsonify(health.snapshot())


@bp.route("/api/admin/agents/<address>/command", methods=["POST"])
@auth.admin_required
def admin_agent_command(address):
    """Body: {"action": "update", "ref": "release" | "main"} or
    {"action": "reboot"}."""
    payload = request.get_json(silent=True) or {}
    cmd_id, error = agents.queue(address, payload.get("action"), payload.get("ref"), g.user["id"])
    if error:
        return jsonify({"error": error}), 400
    return jsonify({"id": cmd_id})
