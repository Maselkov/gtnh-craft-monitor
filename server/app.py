"""
GTNH Craft Monitor - server side
---------------------------------
Receives crafting-CPU status, power readings and ME network scans POSTed
by the in-game OpenComputers scripts and serves a small webpage that
displays them. The server itself lives in the gcm package; this file is
just the entrypoint.

Run directly (see docs/server.md for the optional environment variables):
    pip install -r requirements.txt
    python app.py

Create an admin from the command line instead of the setup link:
    python app.py create-admin <display name>

Recover a lost admin token (revokes the user's existing tokens/sessions):
    python app.py new-token <display name>

Back up every database, consistently, while the server keeps running:
    python app.py backup <directory>

Or via Docker (see Dockerfile / docker-compose.yml in this project).
"""

import os
import sys

from gcm import auth, config, create_app, db, retention, store


def _cli_create_admin(display_name):
    created = store.users.create(display_name, "admin")
    if not created:
        raise SystemExit(f"A user named {display_name!r} already exists")
    print(created[1])


def _log_setup_status(log):
    code = auth.setup_code()
    if code:
        base = config.PUBLIC_URL or f"http://<this-host>:{os.environ.get('PORT', '8420')}"
        log.warning(
            "No users yet. To create the first admin, open\n\n    %s/#setup=%s\n\n"
            'or run: python app.py create-admin "<your name>"',
            base,
            code,
        )
    elif store.users.live_bootstrap_tokens():
        log.warning(
            "An admin still signs in with the token from GCM_BOOTSTRAP_ADMIN_TOKEN, "
            "which is kept in .env. Give them a new one (Manage users -> New token) "
            "and remove it from .env."
        )


def _cli_new_token(display_name):
    """Revokes a user's tokens and sessions and prints a new token - the
    recovery path when the only admin has lost theirs."""
    user_id = store.users.id_for_display_name(display_name)
    if not user_id:
        raise SystemExit(f"No user named {display_name!r}")
    print(store.users.replace_credentials(user_id))


if __name__ == "__main__":
    app = create_app()
    if len(sys.argv) == 3 and sys.argv[1] == "create-admin":
        _cli_create_admin(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "new-token":
        _cli_new_token(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "backup":
        for path in db.backup_all(sys.argv[2]):
            print(path)
        sys.exit(0)
    config.require_runtime_secrets()
    import logging

    from waitress import serve

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _log_setup_status(logging.getLogger("gcm"))
    retention.start()
    port = int(os.environ.get("PORT", "8420"))
    # Single process on purpose: crafts, network scans, and craft/cancel
    # requests are held in module-level memory (gcm/state.py), so multiple
    # worker processes would each see a different copy.
    # clear_untrusted_proxy_headers=False: waitress otherwise strips every
    # X-Forwarded-* header (its own trusted_proxy is unset), so ProxyFix
    # never sees them and TRUSTED_PROXIES has no effect. The app already
    # decides which peers to trust in gcm/security.py.
    serve(app, host="0.0.0.0", port=port, threads=8, clear_untrusted_proxy_headers=False)
