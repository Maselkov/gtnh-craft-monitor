"""The Model Context Protocol endpoint: AI assistants (claude.ai, Claude
Code and other MCP clients) read the base through the tools in
gcm/mcp/tools.py, signed in as a user with an access token. The protocol
is gcm/mcp/protocol.py; this is the HTTP around it."""

from flask import Blueprint, g, jsonify, request

from gcm import auth
from gcm.mcp import protocol


bp = Blueprint("mcp", __name__)


def _origin_allowed():
    # The spec asks for this against DNS rebinding: a browser page on
    # another site can't make its visitor's browser call /mcp. MCP
    # clients run server-side or natively and send no Origin.
    origin = request.headers.get("Origin")
    return origin is None or origin == request.host_url.rstrip("/")


@bp.route("/mcp", methods=["POST"])
@auth.bearer_required
def mcp_post():
    if not _origin_allowed():
        return jsonify(protocol.error(None, protocol.INVALID_REQUEST, "Origin not allowed")), 403
    message = request.get_json(silent=True)
    if message is None:
        return jsonify(protocol.error(None, protocol.PARSE_ERROR, "Parse error: send one JSON-RPC message")), 400
    status, body = protocol.handle(message, request.headers, g.user)
    if body is None:
        return "", status
    return jsonify(body), status


@bp.route("/mcp", methods=["GET", "DELETE"])
@auth.public
def mcp_other():
    # No server-to-client stream and no sessions to end: the spec's
    # answer to both is 405.
    response = jsonify({"error": "POST JSON-RPC messages to this endpoint"})
    response.headers["Allow"] = "POST"
    return response, 405
