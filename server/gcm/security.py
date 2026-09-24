"""Request-level security: which proxies may set X-Forwarded-* headers,
routes blocked until the bootstrap token is rotated, cross-origin
checks on session writes, and browser security headers.

The first two are decided from each route's gcm.auth decorator and the
request method, not from lists of endpoint names. NO_STORE_ENDPOINTS is
still by name; tests/test_security_endpoints.py checks each one
exists."""

import base64
import hashlib
import ipaddress

from flask import Blueprint, current_app, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from gcm import auth, config


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


def _is_trusted_proxy_address(address):
    try:
        ip_address = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip_address in network for network in config.TRUSTED_PROXY_NETWORKS)


# Routes a bootstrap-token session may not use until the token is
# rotated: every route that acts as a signed-in user, read from its
# gcm.auth decorator so a new route is covered without being listed.
BOOTSTRAP_BLOCKED_POLICIES = {"login", "operator", "admin"}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _auth_policies():
    view = current_app.view_functions.get(request.endpoint)
    return getattr(view, "auth_policies", ())


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
    if not BOOTSTRAP_BLOCKED_POLICIES.intersection(_auth_policies()):
        return None
    user = auth.session_user()
    if user and user["must_rotate_bootstrap"]:
        return jsonify({"error": "rotate the bootstrap token before continuing"}), 403
    return None


@bp.before_app_request
def reject_cross_origin_session_writes():
    # Every state-changing request that carries a live session must come
    # from this origin - whichever route it is, so none can be forgotten.
    # Requests without a session (the game's API-key calls, login) have
    # no ambient credentials to abuse.
    if request.method in SAFE_METHODS:
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


def content_security_policy(external_scripts, inline_style_values):
    """The page's Content-Security-Policy. Scripts only from this origin
    plus the exact external script URLs index.html loads (not the whole
    CDN, which would let an injected tag pull any package from it); no
    inline script or event handlers at all. The only inline styles
    allowed are the exact style="..." attribute values index.html itself
    uses, by hash - they're listed under style-src rather than
    style-src-attr so a browser without style-src-attr support falls
    back to the same rule instead of hiding nothing."""
    style_hashes = " ".join(
        "'sha256-%s'" % base64.b64encode(hashlib.sha256(value.encode("utf-8")).digest()).decode()
        for value in sorted(set(inline_style_values))
    )
    directives = [
        "default-src 'self'",
        "script-src 'self' " + " ".join(sorted(set(external_scripts))),
        "style-src 'self'" + (f" 'unsafe-hashes' {style_hashes}" if style_hashes else ""),
        "img-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    return "; ".join(directives)

