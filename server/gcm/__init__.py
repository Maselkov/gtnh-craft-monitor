"""GTNH Craft Monitor server package. create_app() is the only place
startup work happens - importing any gcm module has no side effects."""

from flask import Flask

from gcm import auth, charts, commands, config, db, icons, inventory, security, state, store
from gcm.routes import craft_requests, crafts, network, pages, power, users


def create_app(data_dir=None, api_key=None):
    """Points the app at its data directory, runs every startup step
    (schema creation/migration, restart cleanup, admin bootstrap, network
    snapshot reload) and returns a Flask app with every route registered.
    data_dir/api_key default to the DATA_DIR/API_KEY environment
    variables."""
    config.configure(data_dir, api_key)
    icons.load_lookup()
    db.init_craft_db()
    store.requests.close_orphaned()
    store.users.prune_sessions()
    last_craft_id, last_cancel_id = store.requests.last_ids()
    commands.craft_requests.start_after(last_craft_id)
    commands.cancel_requests.start_after(last_cancel_id)
    auth.bootstrap_admin()
    db.init_power_db()
    db.init_item_history_db()
    inventory.load_snapshot()
    pages.load_index_html()

    app = Flask(__name__, root_path=config.SERVER_DIR)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    security.init_app(app)
    for blueprint in (
        crafts.bp,
        craft_requests.bp,
        network.bp,
        power.bp,
        users.bp,
        pages.bp,
    ):
        app.register_blueprint(blueprint)
    return app


def reset_runtime_state():
    """Clears every piece of in-memory state back to a fresh process.
    Used by the test suite between tests."""
    state.reset()
    commands.reset()
    charts.reset()
