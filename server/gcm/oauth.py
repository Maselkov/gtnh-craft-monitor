"""OAuth 2.1 for AI assistants connecting to /mcp, the way the MCP
authorization spec and claude.ai's connectors use it. The server is
both the resource (/mcp) and its own authorization server, signing users
in with their existing access tokens:

1. /mcp answers 401 with a pointer to the protected resource metadata
   (RFC 9728), which names this server as the authorization server, and
   its metadata (RFC 8414) lists the endpoints below.
2. The client registers itself (RFC 7591): public clients only, no
   secret. A Client ID Metadata Document isn't supported, so Claude
   falls back to registering.
3. /oauth/authorize shows a consent page (routes/oauth.py); allowing it
   sends the browser back to the client with a one-use code, bound to
   the PKCE challenge (S256 only).
4. /oauth/token trades the code and the PKCE verifier for an access
   token (an hour) and a refresh token (30 days), and a refresh token
   for new ones. Refresh tokens rotate: one used twice revokes the
   grant. The tokens and grants are kept by gcm/store/oauth.py.

Redirect URIs must be https, or http to a loopback address (a native
client such as Claude Code), where the port is ignored when matching
(RFC 8252 7.3): the client picks a free one each time."""

import base64
import hashlib
import hmac
import ipaddress
from urllib.parse import urlencode, urlsplit, urlunsplit

from flask import request

from gcm import config

MAX_REDIRECT_URIS = 10
MAX_URI_LENGTH = 2000
MAX_CLIENT_NAME = 100


def base_url():
    """This server's address as clients see it: PUBLIC_URL when set (it
    must be, behind a proxy that doesn't forward the host), else the
    request's own."""
    return config.PUBLIC_URL or request.host_url.rstrip("/")


def resource_url():
    return base_url() + "/mcp"


def protected_resource_metadata():
    return {
        "resource": resource_url(),
        "authorization_servers": [base_url()],
        "bearer_methods_supported": ["header"],
        "resource_name": "GTNH Craft Monitor",
    }


def authorization_server_metadata():
    base = base_url()
    return {
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "authorization_response_iss_parameter_supported": True,
    }


def _is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def redirect_uri_error(uri):
    """Why a redirect URI can't be registered, or None."""
    if not isinstance(uri, str) or not uri or len(uri) > MAX_URI_LENGTH:
        return "each redirect URI must be a non-empty string"
    try:
        parts = urlsplit(uri)
        hostname = parts.hostname
    except ValueError:
        return f"not a URL: {uri}"
    if parts.fragment:
        return "a redirect URI can't have a fragment"
    if not hostname:
        return f"not a URL: {uri}"
    if parts.scheme == "https":
        return None
    if parts.scheme == "http" and _is_loopback(hostname):
        return None
    return "redirect URIs must be https, or http to localhost"


def _without_port(uri):
    parts = urlsplit(uri)
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, parts.query, ""))


def redirect_uri_allowed(client, uri):
    """Whether a client may be sent back to this URI: exactly one it
    registered, or a loopback one that differs only in its port."""
    if not isinstance(uri, str):
        return False
    if uri in client["redirect_uris"]:
        return True
    try:
        parts = urlsplit(uri)
        if parts.scheme != "http" or not parts.hostname or not _is_loopback(parts.hostname):
            return False
        loose = _without_port(uri)
    except ValueError:
        return False
    return any(
        urlsplit(registered).scheme == "http" and _without_port(registered) == loose
        for registered in client["redirect_uris"]
    )


def redirect_target(uri, params):
    """The redirect URI with params added to its query."""
    parts = urlsplit(uri)
    query = parts.query + ("&" if parts.query else "") + urlencode(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def redirect_origin(uri):
    """scheme://host[:port] of a URI, for the consent page's form-action."""
    parts = urlsplit(uri)
    return f"{parts.scheme}://{parts.netloc}"


def pkce_matches(verifier, challenge):
    """RFC 7636 S256: BASE64URL(SHA256(verifier)) == challenge."""
    if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128:
        return False
    digest = hashlib.sha256(verifier.encode("ascii", "replace")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(expected, challenge)


def resource_ok(resource):
    """A resource indicator (RFC 8707), if the client sent one, must name
    this server's /mcp."""
    if resource in (None, ""):
        return True
    return resource.rstrip("/") == resource_url()
