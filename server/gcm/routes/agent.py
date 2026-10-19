"""gcm_agent.lua's heartbeat and command results, and the admin
Scripts dialog that reads them and queues commands (gcm/agents.py)."""

from flask import Blueprint, g, jsonify, request

from gcm import agents, auth

bp = Blueprint("agent", __name__)

_MAX_ADDRESS_LENGTH = 64


@bp.route("/api/agent/heartbeat", methods=["POST"])
@auth.api_key_required
def agent_heartbeat():
    payload = request.get_json(silent=True)
    address = payload.get("address") if isinstance(payload, dict) else None
    if not isinstance(address, str) or not 0 < len(address) <= _MAX_ADDRESS_LENGTH:
        return jsonify({"error": "missing address"}), 400
    return jsonify({"command": agents.heartbeat(address, payload)})


@bp.route("/api/agent/commands/<int:cmd_id>/result", methods=["POST"])
@auth.api_key_required
def agent_command_result(cmd_id):
    payload = request.get_json(silent=True) or {}
    if not agents.result(cmd_id, payload.get("ok"), payload.get("message"), payload.get("version")):
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
