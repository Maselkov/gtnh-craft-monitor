"""Runtime settings, read from the environment. Paths are set by
configure(), called from create_app() - nothing here touches the
filesystem at import time. Other modules read these as config.NAME at
call time (never `from config import NAME`), so configure() and test
monkeypatching take effect everywhere."""

import ipaddress
import os
import secrets


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
# The in-game scripts' repository, for the Scripts dialog's "update
# available" (gcm/agents.py). gcm.lua itself always uses its own.
SCRIPTS_REPO = os.environ.get("SCRIPTS_REPO", "Maselkov/gtnh-craft-monitor")
GTNH_VERSION = os.environ.get("GTNH_VERSION", "").strip()
GTNH_TEXTURES = os.environ.get("GTNH_TEXTURES", "").strip()

# Contact the push services are given with every Web Push (gcm/push.py):
# a mailto: or https: URL. Empty means the page's own URL.
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "").strip()

# Webhooks (gcm/webhooks.py): where base-wide notifications are posted
# (comma-separated; Discord webhooks get embeds), and which events.
# PUBLIC_URL is the page's address as seen from outside, for icon and
# item links in them; without it they're left out.
WEBHOOK_URLS = [u.strip() for u in os.environ.get("WEBHOOK_URLS", "").split(",") if u.strip()]
WEBHOOK_EVENTS = {e.strip() for e in os.environ.get("WEBHOOK_EVENTS", "crafts,autocraft").split(",") if e.strip()}
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")

# Keep-in-stock targets (gcm/stock.py): auto-crafts only start while more
# than this many crafting CPUs are idle, leaving those for requests made
# by hand; and a target whose auto-craft failed waits this long before
# trying again.
AUTOCRAFT_KEEP_IDLE_CPUS = int(os.environ.get("AUTOCRAFT_KEEP_IDLE_CPUS", "1"))
AUTOCRAFT_RETRY_SECONDS = int(os.environ.get("AUTOCRAFT_RETRY_SECONDS", "1800"))

# The live crafting tree (gcm/job_tree.py): a step that's crafting but
# hasn't moved for this long is shown as stuck.
CRAFT_STALL_SECONDS = int(os.environ.get("CRAFT_STALL_SECONDS", "600"))

# The most items (each NBT variant counts) and patterns one scan may send
# (gcm/inventory.py, gcm/patterns.py). A scan past either is abandoned, so
# a runaway or hostile script can't fill memory or the item history. A
# whole GTNH item catalog is ~11k ids; an item takes ~600 bytes in memory.
MAX_SCAN_ITEMS = int(os.environ.get("MAX_SCAN_ITEMS", "100000"))
MAX_SCAN_PATTERNS = int(os.environ.get("MAX_SCAN_PATTERNS", "25000"))

# Item history thinning (gcm/retention.py): older than this many days,
# one row per item per hour, then per day. 0 turns a tier off.
HISTORY_HOURLY_AFTER_DAYS = int(os.environ.get("HISTORY_HOURLY_AFTER_DAYS", "30"))
HISTORY_DAILY_AFTER_DAYS = int(os.environ.get("HISTORY_DAILY_AFTER_DAYS", "365"))

DATA_DIR = None
GAMEDATA_DIR = None
POWER_DB_PATH = None
ITEM_HISTORY_DB_PATH = None
APP_DB_PATH = None
VAPID_KEY_PATH = None
API_KEY_PATH = None
LEGACY_CRAFT_DB_PATH = None

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def configure(data_dir=None, api_key=None):
    global API_KEY, DATA_DIR, GAMEDATA_DIR
    global POWER_DB_PATH, ITEM_HISTORY_DB_PATH, APP_DB_PATH, LEGACY_CRAFT_DB_PATH
    global VAPID_KEY_PATH, API_KEY_PATH
    if api_key is not None:
        API_KEY = api_key
    DATA_DIR = data_dir or os.environ.get("DATA_DIR") or os.path.join(SERVER_DIR, "data")
    GAMEDATA_DIR = os.path.join(DATA_DIR, "gamedata")
    POWER_DB_PATH = os.path.join(DATA_DIR, "power.db")
    ITEM_HISTORY_DB_PATH = os.path.join(DATA_DIR, "item_history.db")
    APP_DB_PATH = os.path.join(DATA_DIR, "app.db")
    VAPID_KEY_PATH = os.path.join(DATA_DIR, "vapid_private.pem")
    API_KEY_PATH = os.path.join(DATA_DIR, "api_key")
    # app.db's name before it held more than craft history; startup
    # renames it (db.adopt_legacy_app_db()).
    LEGACY_CRAFT_DB_PATH = os.path.join(DATA_DIR, "craft_history.db")


def load_api_key():
    """Without API_KEY in the environment, uses the key in DATA_DIR/api_key,
    generating it on first startup - so a server runs with no .env."""
    global API_KEY
    if API_KEY:
        return
    if not os.path.exists(API_KEY_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        # Created 0600: it's what lets a computer post as the game.
        fd = os.open(API_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32) + "\n")
    with open(API_KEY_PATH, encoding="utf-8") as f:
        API_KEY = f.read().strip()


def require_runtime_secrets():
    if len(API_KEY) < 32 or API_KEY == "change-me":
        raise RuntimeError(
            "API_KEY must be a unique secret of at least 32 characters. "
            "Fix it in .env, or remove it to use a generated one."
        )
