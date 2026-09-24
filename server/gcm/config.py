"""Runtime settings, read from the environment. Paths are set by
configure(), called from create_app() - nothing here touches the
filesystem at import time. Other modules read these as config.NAME at
call time (never `from config import NAME`), so configure() and test
monkeypatching take effect everywhere."""

import os

API_KEY = os.environ.get("API_KEY", "")
STALE_AFTER_SECONDS = int(os.environ.get("STALE_AFTER_SECONDS", "30"))
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") != "0"

DATA_DIR = None
ICONS_LOOKUP_PATH = None
IMAGES_ZIP_PATH = None
POWER_DB_PATH = None
ITEM_HISTORY_DB_PATH = None
CRAFT_HISTORY_DB_PATH = None

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def configure(data_dir=None, api_key=None):
    global API_KEY, DATA_DIR, ICONS_LOOKUP_PATH, IMAGES_ZIP_PATH
    global POWER_DB_PATH, ITEM_HISTORY_DB_PATH, CRAFT_HISTORY_DB_PATH
    if api_key is not None:
        API_KEY = api_key
    DATA_DIR = data_dir or os.environ.get("DATA_DIR") or os.path.join(SERVER_DIR, "data")
    ICONS_LOOKUP_PATH = os.path.join(DATA_DIR, "icons_lookup.json")
    IMAGES_ZIP_PATH = os.path.join(DATA_DIR, "images.zip")
    POWER_DB_PATH = os.path.join(DATA_DIR, "power.db")
    ITEM_HISTORY_DB_PATH = os.path.join(DATA_DIR, "item_history.db")
    CRAFT_HISTORY_DB_PATH = os.path.join(DATA_DIR, "craft_history.db")


def require_runtime_secrets():
    if len(API_KEY) < 32 or API_KEY == "change-me":
        raise RuntimeError(
            "API_KEY must be a unique secret of at least 32 characters. "
            "Set it in .env before starting the server."
        )
