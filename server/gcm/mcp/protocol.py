"""The Model Context Protocol over Streamable HTTP, as /mcp speaks it:
JSON-RPC, one message per POST, every answer a single JSON object
(never an SSE stream), and no sessions. Tools are all it offers.

Written here rather than with the MCP Python SDK: the SDK is ASGI and
this server is WSGI (Flask on waitress), and without streams or
sessions the protocol is small.

Two eras of the protocol are served on the one endpoint (the spec's
"dual-era" server):

- Modern (2026-07-28): no handshake. Each request names its version in
  params._meta and in the MCP-Protocol-Version header, which must agree,
  as must the Mcp-Method and Mcp-Name headers with the body. Results
  carry resultType and the server's identity in _meta.
- Legacy (2025-03-26 to 2025-11-25): an initialize request picks the
  version, and later requests name it in the MCP-Protocol-Version header.
  As no session is kept, a request is served by its header alone.

handle() takes the parsed body and the headers and returns (HTTP
status, JSON body or None); routes/mcp.py does the HTTP and the auth."""

import base64
import binascii
import json

from gcm import config
from gcm.mcp import instructions, prompts, tools

MODERN_VERSIONS = ["2026-07-28"]
LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS

_META_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022

def server_info():
    return {"name": "gtnh-craft-monitor", "title": "GTNH Craft Monitor", "version": config.SERVER_VERSION or "dev"}


def error(msg_id, code, message, data=None):
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": error}


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _decode_header(value):
    """A header value with the spec's =?base64?...?= encoding undone."""
    if value is not None and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    return value


def _unsupported(msg_id, requested):
    return 400, error(
        msg_id, UNSUPPORTED_VERSION, "Unsupported protocol version",
        {"supported": SUPPORTED_VERSIONS, "requested": requested},
    )


def handle(message, headers, user):
    """Answers one JSON-RPC message for a signed-in user."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        if isinstance(message, list):
            return 400, error(None, INVALID_REQUEST, "Batches aren't supported: send one message per request")
        return 400, error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 request")
    method = message["method"]
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return 400, error(message.get("id"), INVALID_PARAMS, "params must be an object")

    if "id" not in message:
        # Notifications (the legacy notifications/initialized and
        # notifications/cancelled): nothing to do for any of them.
        return 202, None
    msg_id = message["id"]

    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    header_version = headers.get("MCP-Protocol-Version")

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return 200, _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": server_info(),
            "instructions": instructions.build(user),
        })

    if _META_VERSION in meta:
        version = meta[_META_VERSION]
        if header_version is None:
            return 400, error(msg_id, HEADER_MISMATCH, "Header mismatch: MCP-Protocol-Version header is missing")
        if header_version != version:
            return 400, error(
                msg_id, HEADER_MISMATCH,
                f"Header mismatch: MCP-Protocol-Version header value '{header_version}' does not match body value '{version}'",
            )
        if version not in MODERN_VERSIONS:
            return _unsupported(msg_id, version)
        mismatch = _header_mismatch(method, params, headers)
        if mismatch:
            return 400, error(msg_id, HEADER_MISMATCH, mismatch)
        modern = True
    else:
        # Legacy: the version initialize settled on, in the header; a
        # 2025-03-26 client didn't send one.
        version = header_version or "2025-03-26"
        if version not in LEGACY_VERSIONS:
            return _unsupported(msg_id, version)
        modern = False

    if method == "server/discover":
        return 200, _modern_result(msg_id, {
            "supportedVersions": SUPPORTED_VERSIONS,
            "capabilities": {"tools": {}, "prompts": {}},
            "instructions": instructions.build(user),
            # Per user and live (who's connected, the base's state).
            "ttlMs": 300000,
            "cacheScope": "private",
        })
    if method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tools.listing(user)}
        if modern:
            # Per user: viewers aren't offered operators' tools.
            result.update({"ttlMs": 3600000, "cacheScope": "private"})
    elif method == "prompts/list":
        result = {"prompts": prompts.listing(user)}
        if modern:
            result.update({"ttlMs": 3600000, "cacheScope": "private"})
    elif method == "prompts/get":
        try:
            result = prompts.get(params.get("name"), params.get("arguments"), user)
        except prompts.PromptError as e:
            return 200, error(msg_id, INVALID_PARAMS, str(e))
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return 200, error(msg_id, INVALID_PARAMS, "params.name must be a tool name")
        if not tools.exists(name):
            return 200, error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        answer, is_error = tools.call(name, params.get("arguments"), user)
        result = {"content": [{"type": "text", "text": _compact_json(answer)}], "isError": is_error}
    else:
        return (404 if modern else 200), error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
    return 200, (_modern_result(msg_id, result) if modern else _result(msg_id, result))


def _modern_result(msg_id, result):
    return _result(msg_id, {"resultType": "complete", **result, "_meta": {_META_SERVER_INFO: server_info()}})


def _header_mismatch(method, params, headers):
    """Why the modern request headers don't agree with the body, or None."""
    header_method = headers.get("Mcp-Method")
    if header_method is None:
        return "Header mismatch: Mcp-Method header is missing"
    if header_method != method:
        return f"Header mismatch: Mcp-Method header value '{header_method}' does not match body value '{method}'"
    if method in ("tools/call", "prompts/get"):
        raw = headers.get("Mcp-Name")
        if raw is None:
            return "Header mismatch: Mcp-Name header is missing"
        name = _decode_header(raw)
        if name != params.get("name"):
            return f"Header mismatch: Mcp-Name header value '{raw}' does not match body value '{params.get('name')}'"
    return None


def _compact_json(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
