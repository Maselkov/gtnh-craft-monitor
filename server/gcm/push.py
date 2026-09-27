"""Web Push: a notification for each finished pinned craft, delivered
through the browser's push service, so it arrives even with every tab
closed or a phone's page suspended. static/sw.js shows it.

The server signs each push with its own VAPID key pair, generated once
and kept in the data directory - replacing it invalidates every saved
subscription, so it must survive restarts. Sending happens on one
background thread: a slow or unreachable push service must never hold
up the game's status POST that noticed the craft ending."""

import base64
import json
import logging
import os
import queue
import threading
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from gcm import config, icons, store

log = logging.getLogger(__name__)

# Subscription endpoints are only accepted on these push services. The
# server POSTs to whatever URL a browser hands it, so an open list would
# let any signed-in user point it at addresses inside the network.
# Entries starting with "." match any subdomain.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",  # Chrome, Edge on Android, most Chromium browsers
    "updates.push.services.mozilla.com",  # Firefox
    "web.push.apple.com",  # Safari, iOS home screen apps
    ".push.apple.com",
    ".notify.windows.com",  # Edge on Windows
)

# How long a push service keeps trying to reach an offline device.
TTL_SECONDS = 24 * 3600
SEND_TIMEOUT_SECONDS = 10

_vapid = None
_queue = queue.Queue()
_worker_lock = threading.Lock()
_worker = None


def load_keys():
    """Loads the VAPID key pair, generating it on first startup."""
    global _vapid
    path = config.VAPID_KEY_PATH
    if not os.path.exists(path):
        vapid = Vapid()
        vapid.generate_keys()
        # Created 0600 - it's the only secret that proves a push is ours.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(vapid.private_pem())
    _vapid = Vapid.from_file(path)


def public_key():
    """The applicationServerKey browsers subscribe with: the raw public
    key, base64url without padding."""
    raw = _vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def endpoint_allowed(endpoint):
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.port not in (None, 443):
        return False
    return any(
        host.endswith(entry) if entry.startswith(".") else host == entry
        for entry in PUSH_SERVICE_HOSTS
    )


def message(completion_id, label, icon, status):
    """What the notification says, and the item's icon. The page shows
    the same when it notices the completion itself, under the same tag,
    so a device getting both shows it once."""
    what = label or "A pinned craft"
    if status == "incomplete":
        title, body = "Craft stopped", what + " stopped before finishing (cancelled or interrupted)."
    else:
        title, body = "Craft finished", what + " is done crafting."
    payload = {"title": title, "body": body, "tag": f"craft-completion-{completion_id}"}
    icon_url = icons.notification_icon_url(icon)
    if icon_url:
        payload["icon"] = icon_url
    return payload


def notify_completions(completions, label, icon, status):
    """Queues a push to every device of each user in completions, a list
    of (user_id, completion_id). Returns immediately."""
    if not completions:
        return
    _ensure_worker()
    _queue.put((list(completions), label, icon, status))


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="web-push", daemon=True)
            _worker.start()


def _run():
    while True:
        item = _queue.get()
        try:
            deliver(*item)
        except Exception:
            log.exception("web push delivery failed")
        finally:
            _queue.task_done()


def deliver(completions, label, icon, status):
    completion_by_user = dict(completions)
    for subscription in store.push.subscriptions_for(list(completion_by_user)):
        payload = message(completion_by_user[subscription["user_id"]], label, icon, status)
        try:
            _send(subscription, json.dumps(payload))
        except WebPushException as error:
            code = error.response.status_code if error.response is not None else None
            if code in (404, 410):
                # Unsubscribed, expired, or the browser's data was cleared.
                store.push.delete_endpoint(subscription["endpoint"])
            else:
                log.warning("web push to %s failed: %s", urlsplit(subscription["endpoint"]).hostname, error)
        except Exception as error:
            log.warning("web push to %s failed: %s", urlsplit(subscription["endpoint"]).hostname, error)


def _send(subscription, data):
    webpush(
        subscription_info={
            "endpoint": subscription["endpoint"],
            "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
        },
        data=data,
        vapid_private_key=_vapid,
        # A fresh dict each time - webpush() adds aud/exp to it in place.
        vapid_claims={"sub": config.VAPID_SUBJECT or subscription["origin"]},
        ttl=TTL_SECONDS,
        timeout=SEND_TIMEOUT_SECONDS,
    )


def wait_idle():
    """Blocks until every queued push has been attempted. For tests."""
    _queue.join()
