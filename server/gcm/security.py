"""Request-level security: which proxies may set X-Forwarded-* headers,
endpoints blocked until the bootstrap token is rotated, cross-origin
checks on session writes, and browser security headers.

Endpoint names are blueprint-qualified ("crafts.pins_post");
tests/test_security_endpoints.py checks each one exists."""

import os
import ipaddress

from flask import Blueprint, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from gcm import auth


bp = Blueprint("security", __name__)


def init_app(app):
    """Installs the trusted-proxy WSGI wrapper and the request hooks below."""
    direct_wsgi_app = app.wsgi_app
    proxy_wsgi_app = ProxyFix(direct_wsgi_app, x_for=1, x_proto=1, x_host=1)

    def trusted_proxy_wsgi_app(environ, start_response):
        if _is_trusted_proxy_address(environ.get("REMOTE_ADDR", "")):
            environ["gtnh.trusted_proxy"] = True
            return proxy_wsgi_app(environ, start_response)
        return direct_wsgi_app(environ, start_response)

    app.wsgi_app = trusted_proxy_wsgi_app
    app.register_blueprint(bp)


def _parse_trusted_proxies(value):
    networks = []
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as error:
            raise RuntimeError(f"Invalid TRUSTED_PROXIES entry: {entry}") from error
    return networks


TRUSTED_PROXY_NETWORKS = _parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES", ""))


def _is_trusted_proxy_address(address):
    try:
        ip_address = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip_address in network for network in TRUSTED_PROXY_NETWORKS)


BOOTSTRAP_BLOCKED_ENDPOINTS = {
    "craft_requests.craft_request_post",
    "craft_requests.craft_requests_get",
    "craft_requests.craft_request_dismiss",
    "craft_requests.craft_cancel_post",
    "craft_requests.craft_cancel_get",
    "crafts.pins_get",
    "crafts.pins_post",
    "crafts.pins_unpin",
    "crafts.completions_get",
    "crafts.completions_ack",
    "crafts.completions_ack_all",
    "network.network_item_pins_get",
    "network.network_item_pins_post",
    "network.network_item_pins_unpin",
    "users.admin_user_post",
    "users.admin_token_revoke_post",
    "users.admin_users_get",
    "users.admin_user_history_get",
    "users.admin_user_delete",
    "users.admin_user_token_regenerate",
}

SESSION_WRITE_ENDPOINTS = {
    "users.auth_logout_post",
    "users.auth_rotate_bootstrap_post",
    "craft_requests.craft_request_post",
    "craft_requests.craft_request_dismiss",
    "craft_requests.craft_cancel_post",
    "crafts.pins_post",
    "crafts.pins_unpin",
    "crafts.completions_ack",
    "crafts.completions_ack_all",
    "network.network_item_pins_post",
    "network.network_item_pins_unpin",
    "users.admin_user_post",
    "users.admin_user_delete",
    "users.admin_user_token_regenerate",
    "users.admin_token_revoke_post",
}

# Responses that carry session or token state; never cached.
NO_STORE_ENDPOINTS = {
    "users.auth_session_get",
    "users.auth_login_post",
    "users.auth_logout_post",
    "users.auth_rotate_bootstrap_post",
    "users.admin_user_post",
    "users.admin_token_revoke_post",
    "users.admin_user_history_get",
    "users.admin_user_delete",
    "users.admin_user_token_regenerate",
}


@bp.before_app_request
def block_unrotated_bootstrap_sessions():
    if request.endpoint not in BOOTSTRAP_BLOCKED_ENDPOINTS:
        return None
    user = auth.session_user()
    if user and user["must_rotate_bootstrap"]:
        return jsonify({"error": "rotate the bootstrap token before continuing"}), 403
    return None


@bp.before_app_request
def reject_cross_origin_session_writes():
    if request.endpoint not in SESSION_WRITE_ENDPOINTS:
        return None
    if not auth.session_user():
        return None
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        return jsonify({"error": "cross-origin request rejected"}), 403
    return None


@bp.after_app_request
def add_browser_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if request.endpoint == "static":
        # JS modules import each other by plain path, without the ?v=
        # hash index.html puts on main.js - so every static file must be
        # revalidated (a cheap 304) rather than reused stale from cache.
        response.headers["Cache-Control"] = "no-cache"
    if request.endpoint in NO_STORE_ENDPOINTS:
        response.headers["Cache-Control"] = "no-store"
    return response
