"""Authentication: the Lua-side API key, the signed-in user and their
role, the first-run setup code, and the route decorators that enforce
them. Tokens and sessions are stored by gcm/store/users.py.

Every route declares its policy with exactly one decorator below
(tests/test_route_auth.py enforces this), placed under @bp.route:

    @auth.api_key_required   the in-game scripts (X-API-Key header)
    @auth.login_required     any signed-in user; g.user is the user
    @auth.operator_required  operator or admin role; g.user is the user
    @auth.admin_required     admin role; g.user is the user
    @auth.viewer_required    the base's data: anyone, or on a private
                             server (is_private()) any signed-in user;
                             g.user is the user or None
    @auth.public             no check, deliberately
    @auth.custom_check       checked inside the view - say why there

login_required can be stacked on top of operator_required to answer an
anonymous request with 401 before the role check's 403."""

import functools
import hmac
import secrets

from flask import g, jsonify, request

from gcm import config, store


def _api_key_valid():
    supplied_key = request.headers.get("X-API-Key", "")
    return bool(config.API_KEY) and hmac.compare_digest(supplied_key, config.API_KEY)


def session_user():
    """The signed-in user for this request, or None. Looked up once per
    request - the security hooks and the route decorators all ask."""
    if "session_user" not in g:
        g.session_user = _lookup_session_user()
    return g.session_user


def _lookup_session_user():
    session_token = request.cookies.get("gcm_session")
    if not session_token:
        return None
    return store.users.session_user(session_token)


def is_private():
    """Whether only signed-in users may see this server's crafts, network
    and power - set by an admin (Manage users) or `app.py visibility`.
    Off unless set: a self-hosted server is usually only reachable by
    people its owner trusts."""
    return store.settings.load().get("private") is True


def set_private(private):
    store.settings.save({"private": bool(private)})


def is_admin(user):
    return user is not None and user["role"] == "admin"


def is_operator(user):
    return user is not None and user["role"] in ("operator", "admin")


# First-run setup (no users yet): a one-time code, printed at startup,
# that lets whoever opens the setup link create the first admin. Only
# kept in memory, so each restart prints a new one.
_setup_code = None


def start_setup():
    global _setup_code
    _setup_code = None if store.users.count() else secrets.token_urlsafe(24)


def reset():
    global _setup_code
    _setup_code = None


def setup_code():
    """The current setup code, or None once a user exists - made here or
    with `app.py create-admin` while the server runs."""
    global _setup_code
    if _setup_code and store.users.count():
        _setup_code = None
    return _setup_code


def finish_setup(code, display_name):
    """Creates the first admin if `code` is the setup code. Returns
    (user, token, session_token), or None for a wrong code or once any
    user exists."""
    global _setup_code
    expected = setup_code()
    if not expected or not hmac.compare_digest(code.encode(), expected.encode()):
        return None
    created = store.users.create_first_admin(display_name, config.SESSION_LIFETIME_SECONDS)
    _setup_code = None
    return created


def _policy(name, check=None):
    """Builds a decorator that tags the view with its auth policy and, if
    check is given, runs it first: check() returns an error response to
    send instead of the view, or None to let the request through."""

    def decorator(view):
        if check is None:
            wrapped = view
        else:

            @functools.wraps(view)
            def wrapped(*args, **kwargs):
                refusal = check()
                if refusal is not None:
                    return refusal
                return view(*args, **kwargs)

        policies = getattr(view, "auth_policies", ())
        wrapped.auth_policies = policies + (name,)
        return wrapped

    return decorator


def _check_api_key():
    if not _api_key_valid():
        return jsonify({"error": "unauthorized"}), 401
    return None


def _check_login():
    g.user = session_user()
    if not g.user:
        return jsonify({"error": "authentication required"}), 401
    return None


def _check_viewer():
    g.user = session_user()
    if not g.user and is_private():
        return jsonify({"error": "authentication required"}), 401
    return None


def _check_operator():
    g.user = session_user()
    if not is_operator(g.user):
        return jsonify({"error": "operator access required"}), 403
    return None


def _check_admin():
    g.user = session_user()
    if not is_admin(g.user):
        return jsonify({"error": "administrator access required"}), 403
    return None


api_key_required = _policy("api_key", _check_api_key)
login_required = _policy("login", _check_login)
viewer_required = _policy("viewer", _check_viewer)
operator_required = _policy("operator", _check_operator)
admin_required = _policy("admin", _check_admin)
public = _policy("public")
custom_check = _policy("custom")
