from gcm import security


def all_security_endpoint_names():
    return (
        security.BOOTSTRAP_BLOCKED_ENDPOINTS
        | security.SESSION_WRITE_ENDPOINTS
        | security.NO_STORE_ENDPOINTS
    )


def test_every_listed_endpoint_exists(flask_app):
    # The security hooks match on request.endpoint by name; a typo or a
    # route moved to another blueprint would silently drop its protection.
    missing = all_security_endpoint_names() - set(flask_app.view_functions)
    assert missing == set()
