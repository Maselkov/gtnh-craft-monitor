"""OAuth endpoints for AI assistants connecting to /mcp (gcm/oauth.py
explains the flow): discovery metadata, client registration, the
consent page and the token endpoint."""

import hmac
import html
import secrets
import time
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, jsonify, make_response, redirect, request

from gcm import auth, config, oauth, store


bp = Blueprint("oauth", __name__)

# What a consent form's anti-forgery value is made with: per process, so
# a form left open across a restart just needs reloading.
_FORM_KEY = secrets.token_bytes(32)

# Endpoints a browser-based MCP client (one on another origin) calls
# directly: they carry no cookies, so any origin may.
_CORS_ENDPOINTS = {
    "oauth.protected_resource_metadata",
    "oauth.authorization_server_metadata",
    "oauth.register",
    "oauth.token",
}


@bp.after_request
def _cors(response):
    if request.endpoint in _CORS_ENDPOINTS:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, MCP-Protocol-Version"
    return response


# ---------------------------------------------------------------------
# Discovery


@bp.route("/.well-known/oauth-protected-resource", methods=["GET"])
@bp.route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
@auth.public
def protected_resource_metadata():
    return jsonify(oauth.protected_resource_metadata())


@bp.route("/.well-known/oauth-authorization-server", methods=["GET"])
@bp.route("/.well-known/oauth-authorization-server/mcp", methods=["GET"])
@auth.public
def authorization_server_metadata():
    return jsonify(oauth.authorization_server_metadata())


# ---------------------------------------------------------------------
# Registration (RFC 7591)


def _registration_error(description):
    return jsonify({"error": "invalid_client_metadata", "error_description": description}), 400


@bp.route("/oauth/register", methods=["POST"])
@auth.public
def register():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _registration_error("send the client metadata as a JSON object")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > oauth.MAX_REDIRECT_URIS:
        return jsonify({"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"}), 400
    for uri in uris:
        error = oauth.redirect_uri_error(uri)
        if error:
            return jsonify({"error": "invalid_redirect_uri", "error_description": error}), 400
    method = body.get("token_endpoint_auth_method", "none")
    if method != "none":
        return _registration_error("only public clients (token_endpoint_auth_method none) are supported")
    grant_types = body.get("grant_types", ["authorization_code"])
    if not isinstance(grant_types, list) or not set(grant_types) <= {"authorization_code", "refresh_token"}:
        return _registration_error("grant_types may only be authorization_code and refresh_token")
    if body.get("response_types", ["code"]) != ["code"]:
        return _registration_error("response_types must be [\"code\"]")
    name = body.get("client_name")
    if not isinstance(name, str) or not name.strip():
        name = urlsplit(uris[0]).hostname or "An app"
    name = " ".join(name.split())[: oauth.MAX_CLIENT_NAME]

    client_id = store.oauth.create_client(name, uris, request.remote_addr)
    if client_id is None:
        return jsonify({"error": "temporarily_unavailable",
                        "error_description": "too many registrations; try again later"}), 429
    return jsonify({
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "client_name": name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }), 201


# ---------------------------------------------------------------------
# Authorization: the consent page

_AUTHORIZE_FIELDS = (
    "response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state",
    "resource", "scope",
)


def _form_value(session_id):
    return hmac.new(_FORM_KEY, session_id.encode(), "sha256").hexdigest()


def _authorize_request(source):
    """Checks an authorization request. Returns (params, client, None),
    or (params, client, error): error is (page message, None) when the
    client can't safely be sent back to, else (OAuth error code,
    description) to send it."""
    params = {k: source.get(k) for k in _AUTHORIZE_FIELDS if source.get(k) is not None}
    client = store.oauth.client(params.get("client_id"))
    if client is None:
        return params, None, ("This app isn't registered with this server. Try connecting it again.", None)
    if not oauth.redirect_uri_allowed(client, params.get("redirect_uri")):
        return params, client, ("This app asked to be sent somewhere it didn't register.", None)
    if params.get("response_type") != "code":
        return params, client, ("unsupported_response_type", "only response_type=code is supported")
    if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
        return params, client, ("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if not oauth.resource_ok(params.get("resource")):
        return params, client, ("invalid_target", f"this server's resource is {oauth.resource_url()}")
    return params, client, None


def _back_to_client(params, values):
    values = {**values, "iss": oauth.base_url()}
    if params.get("state") is not None:
        values["state"] = params["state"]
    return redirect(oauth.redirect_target(params["redirect_uri"], values), code=302)


def _page(title, body, status=200, form_action_origin=None):
    """A small server-rendered page in the site's style. form-action has
    to allow where the form's POST redirects to (the app's callback),
    or browsers block the redirect."""
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · GTNH Craft Monitor</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body class="oauth-page">
<main class="modal-box oauth-box">
{body}
</main>
</body>
</html>"""
    response = make_response(page, status)
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    form_action = "'self'" + (f" {form_action_origin}" if form_action_origin else "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; "
        f"form-action {form_action}; frame-ancestors 'none'"
    )
    return response


def _error_page(message, status=400):
    return _page("Can't connect", f"""
<h1 class="oauth-title">Can't connect this app</h1>
<p class="oauth-text">{html.escape(message)}</p>""", status)


def _hidden_fields(params):
    return "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in params.items()
    )


# What an app can do as each role, through the tools in gcm/mcp/tools.py.
ROLE_CAN = {
    "viewer": "read the base: items, crafts, power and stock rules",
    "operator": "read the base: items, crafts, power and stock rules",
    "admin": "read the base: items, crafts, power and stock rules",
}


def _consent_page(params, client, user, message=None, status=200):
    name = html.escape(client["name"])
    redirect_parts = urlsplit(params["redirect_uri"])
    host = html.escape(redirect_parts.hostname or "")
    local = redirect_parts.scheme == "http"
    note = f'<p class="oauth-text oauth-error">{html.escape(message)}</p>' if message else ""
    if user is None:
        body = f"""
<h1 class="oauth-title">Connect {name}</h1>
<p class="oauth-text">Sign in with your access token to connect <b>{name}</b> to this server.</p>
{note}
<form method="post" action="/oauth/authorize">
{_hidden_fields(params)}
<label class="modal-label" for="oauthToken">Access token</label>
<input id="oauthToken" class="modal-amount-input" type="password" name="token" autocomplete="current-password" required autofocus>
<div class="modal-buttons oauth-buttons">
<button class="modal-btn primary" type="submit" name="action" value="signin">Sign in</button>
</div>
</form>"""
        return _page("Sign in", body, status)

    where = (f"an app on this computer (<b>{host}</b>). Only allow it if you started connecting it just now."
             if local else f"<b>{host}</b>.")
    body = f"""
<h1 class="oauth-title">Connect {name}?</h1>
<p class="oauth-text"><b>{name}</b> wants to act as <b>{html.escape(user["display_name"])}</b>
({html.escape(user["role"])}) on this server. It will be able to
{ROLE_CAN.get(user["role"], ROLE_CAN["viewer"])}.</p>
<p class="oauth-text oauth-muted">You'll be sent back to {where}
An admin can disconnect it in Manage users.</p>
{note}
<form method="post" action="/oauth/authorize">
{_hidden_fields(params)}
<input type="hidden" name="form" value="{_form_value(user["session_id"])}">
<div class="modal-buttons oauth-buttons">
<button class="modal-btn secondary" type="submit" name="action" value="deny">Cancel</button>
<button class="modal-btn primary" type="submit" name="action" value="allow">Allow</button>
</div>
</form>"""
    return _page("Connect an app", body, status, oauth.redirect_origin(params["redirect_uri"]))


@bp.route("/oauth/authorize", methods=["GET"])
@auth.custom_check  # anyone may see the page; it asks for a sign-in
def authorize_get():
    params, client, error = _authorize_request(request.args)
    if error and error[1] is None:
        return _error_page(error[0])
    if error:
        return _back_to_client(params, {"error": error[0], "error_description": error[1]})
    return _consent_page(params, client, auth.session_user())


def _from_this_site():
    """Whether a form POST came from this server's own pages. Browsers
    send Origin on every cross-site POST (Sec-Fetch-Site too): without
    this, another site could post a sign-in with its own token and sign
    the visitor in as someone else."""
    origin = request.headers.get("Origin")
    if origin is not None and origin != request.host_url.rstrip("/"):
        return False
    return request.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")


@bp.route("/oauth/authorize", methods=["POST"])
@auth.custom_check  # same-site only; signing in needs no session, allowing checks it and the form value
def authorize_post():
    if not _from_this_site():
        return _error_page("This form has to be sent from this server's own page.", 403)
    params, client, error = _authorize_request(request.form)
    if error and error[1] is None:
        return _error_page(error[0])
    if error:
        return _back_to_client(params, {"error": error[0], "error_description": error[1]})
    action = request.form.get("action")

    if action == "signin":
        signed_in = store.users.sign_in((request.form.get("token") or "").strip(), config.SESSION_LIFETIME_SECONDS)
        if not signed_in:
            return _consent_page(params, client, None, "That token didn't work.", 401)
        _, session_token = signed_in
        response = redirect("/oauth/authorize?" + urlencode(params), code=303)
        response.set_cookie(
            "gcm_session", session_token, max_age=config.SESSION_LIFETIME_SECONDS,
            secure=config.SESSION_COOKIE_SECURE, httponly=True, samesite="Lax", path="/",
        )
        return response

    user = auth.session_user()
    if user is None:
        return _consent_page(params, client, None, "Your session ended: sign in again.", 401)
    if not hmac.compare_digest(request.form.get("form") or "", _form_value(user["session_id"])):
        return _consent_page(params, client, user, "That form had expired: try again.", 403)
    if action == "deny":
        return _back_to_client(params, {"error": "access_denied"})
    if action != "allow":
        return _consent_page(params, client, user, "Choose Allow or Cancel.", 400)
    code = store.oauth.create_code(
        client["id"], user["id"], user.get("access_token_id"), params["redirect_uri"], params["code_challenge"]
    )
    return _back_to_client(params, {"code": code})


# ---------------------------------------------------------------------
# Tokens


def _token_error(error, description, status=400):
    return jsonify({"error": error, "error_description": description}), status


@bp.route("/oauth/token", methods=["POST"])
@auth.public
def token():
    form = request.form
    grant_type = form.get("grant_type")
    client = store.oauth.client(form.get("client_id"))
    if client is None:
        return _token_error("invalid_client", "unknown client_id", 401)
    if not oauth.resource_ok(form.get("resource")):
        return _token_error("invalid_target", f"this server's resource is {oauth.resource_url()}")

    if grant_type == "authorization_code":
        details = store.oauth.take_code(form.get("code"))
        if details is None or details["client_id"] != client["id"]:
            return _token_error("invalid_grant", "the code is unknown, used or expired")
        if form.get("redirect_uri") != details["redirect_uri"]:
            return _token_error("invalid_grant", "redirect_uri doesn't match the authorization request")
        if not oauth.pkce_matches(form.get("code_verifier"), details["code_challenge"]):
            return _token_error("invalid_grant", "code_verifier doesn't match the code_challenge")
        tokens = store.oauth.create_grant(details)
        if tokens is None:
            return _token_error("invalid_grant", "the user's access has been revoked")
    elif grant_type == "refresh_token":
        tokens = store.oauth.refresh(form.get("refresh_token"), client["id"])
        if tokens is None:
            return _token_error("invalid_grant", "the refresh token is unknown, used, expired or revoked")
    else:
        return _token_error("unsupported_grant_type", "use authorization_code or refresh_token")
    return jsonify({**tokens, "token_type": "Bearer"})
