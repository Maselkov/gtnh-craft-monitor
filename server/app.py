"""
GTNH Craft Monitor - server side
---------------------------------
Receives crafting-CPU status, power readings and ME network scans POSTed
by the in-game OpenComputers scripts and serves a small webpage that
displays them. The server itself lives in the gcm package; this file is
just the entrypoint.

Run directly (see README for the required environment variables):
    pip install -r requirements.txt
    python app.py

Recover a lost admin token (revokes the user's existing tokens/sessions):
    python app.py new-token <display name>

Or via Docker (see Dockerfile / docker-compose.yml in this project).
"""

import os
import sys
import time

from gcm import auth, config, create_app, db


def _cli_new_token(display_name):
    """Revokes a user's tokens and sessions and prints a new token - the
    recovery path when the only admin has lost theirs."""
    conn = db.craft_db()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE display_name = ?", (display_name,)
        ).fetchone()
        if not row:
            raise SystemExit(f"No user named {display_name!r}")
        now = time.time()
        conn.execute(
            "UPDATE access_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, row[0]),
        )
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (now, row[0]),
        )
        token = auth.create_access_token(conn, row[0])
        conn.commit()
    finally:
        conn.close()
    print(token)


if __name__ == "__main__":
    app = create_app()
    if len(sys.argv) == 3 and sys.argv[1] == "new-token":
        _cli_new_token(sys.argv[2])
        sys.exit(0)
    config.require_runtime_secrets()
    auth.require_initial_admin()
    import logging

    from waitress import serve

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("PORT", "8420"))
    # Single process on purpose: crafts, network scans, and craft/cancel
    # requests are held in module-level memory (gcm/state.py), so multiple
    # worker processes would each see a different copy.
    # clear_untrusted_proxy_headers=False: waitress otherwise strips every
    # X-Forwarded-* header (its own trusted_proxy is unset), so ProxyFix
    # never sees them and TRUSTED_PROXIES has no effect. The app already
    # decides which peers to trust in gcm/security.py.
    serve(app, host="0.0.0.0", port=port, threads=8, clear_untrusted_proxy_headers=False)
