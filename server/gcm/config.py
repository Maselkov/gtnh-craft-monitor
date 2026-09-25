"""Runtime settings, read from the environment. Paths are set by
configure(), called from create_app() - nothing here touches the
filesystem at import time. Other modules read these as config.NAME at
call time (never `from config import NAME`), so configure() and test
monkeypatching take effect everywhere."""

import ipaddress
import os


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


API_KEY = os.environ.get("API_KEY", "")
STALE_AFTER_SECONDS = int(os.environ.get("STALE_AFTER_SECONDS", "30"))
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") != "0"
SESSION_LIFETIME_SECONDS = int(os.environ.get("SESSION_LIFETIME_SECONDS", str(7 * 86400)))
# Proxies allowed to set X-Forwarded-* headers (see gcm/security.py).
TRUSTED_PROXY_NETWORKS = _parse_trusted_proxies(os.environ.get("TRUSTED_PROXIES", ""))

# OpenGraph chart PNGs (gcm/charts.py).
CHART_CACHE_TTL_SECONDS = int(os.environ.get("CHART_CACHE_TTL_SECONDS", "60"))
CHART_CACHE_MAX_ENTRIES = int(os.environ.get("CHART_CACHE_MAX_ENTRIES", "64"))
CHART_RATE_LIMIT_PER_MINUTE = int(os.environ.get("CHART_RATE_LIMIT_PER_MINUTE", "30"))
CHART_MAX_TRACKED_CLIENTS = int(os.environ.get("CHART_MAX_TRACKED_CLIENTS", "4096"))

# Game data bundles (gcm/gamedata.py): the repo whose gtnh-data-* releases
# are offered on the Game data page, and optionally a GTNH version (and
# icon textures: default or faithful32) to install at startup without
# using the page.
GAMEDATA_REPO = os.environ.get("GAMEDATA_REPO", "Maselkov/gtnh-craft-monitor-data")
GAMEDATA_API_URL = os.environ.get("GAMEDATA_API_URL", "https://api.github.com").rstrip("/")
GTNH_VERSION = os.environ.get("GTNH_VERSION", "").strip()
GTNH_TEXTURES = os.environ.get("GTNH_TEXTURES", "").strip()

DATA_DIR = None
GAMEDATA_DIR = None
IMAGES_ZIP_PATH = None
POWER_DB_PATH = None
ITEM_HISTORY_DB_PATH = None
APP_DB_PATH = None
LEGACY_CRAFT_DB_PATH = None

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Read-only data that ships with the server, kept out of DATA_DIR: in
# Docker DATA_DIR is a volume mounted over the image, which would hide
# anything a release puts there. Used, with IMAGES_ZIP_PATH, only until a
# game data bundle is installed.
ICONS_LOOKUP_PATH = os.path.join(SERVER_DIR, "reference", "icons_lookup.json")


def configure(data_dir=None, api_key=None):
    global API_KEY, DATA_DIR, GAMEDATA_DIR, IMAGES_ZIP_PATH
    global POWER_DB_PATH, ITEM_HISTORY_DB_PATH, APP_DB_PATH, LEGACY_CRAFT_DB_PATH
    if api_key is not None:
        API_KEY = api_key
    DATA_DIR = data_dir or os.environ.get("DATA_DIR") or os.path.join(SERVER_DIR, "data")
    GAMEDATA_DIR = os.path.join(DATA_DIR, "gamedata")
    IMAGES_ZIP_PATH = os.path.join(DATA_DIR, "images.zip")
    POWER_DB_PATH = os.path.join(DATA_DIR, "power.db")
    ITEM_HISTORY_DB_PATH = os.path.join(DATA_DIR, "item_history.db")
    APP_DB_PATH = os.path.join(DATA_DIR, "app.db")
    # app.db's name before it held more than craft history; startup
    # renames it (db.adopt_legacy_app_db()).
    LEGACY_CRAFT_DB_PATH = os.path.join(DATA_DIR, "craft_history.db")


def require_runtime_secrets():
    if len(API_KEY) < 32 or API_KEY == "change-me":
        raise RuntimeError(
            "API_KEY must be a unique secret of at least 32 characters. "
            "Set it in .env before starting the server."
        )
