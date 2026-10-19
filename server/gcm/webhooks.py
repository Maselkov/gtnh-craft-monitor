"""Webhooks: base-wide notifications posted to a chat channel, next to
each user's own Web Push (gcm/push.py). WEBHOOK_URLS lists where they
go: a Discord webhook gets an embed, anything else a JSON body whose
"text" Slack-style incoming webhooks show as is. WEBHOOK_EVENTS picks
which events are sent:

    crafts      a pinned craft finished or stopped
    autocraft   a keep-in-stock target's auto-craft failed
    stock       a low-stock alert fired (off by default: alerts are
                each user's own, and a channel is shared)

Like Web Push, sending happens on one background thread, so a slow
webhook never holds up the game's POST that noticed the event."""

import json
import logging
import queue
import threading
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from gcm import config, icons

log = logging.getLogger(__name__)

EVENTS = ("crafts", "autocraft", "stock")
SEND_TIMEOUT_SECONDS = 10
COLORS = {"good": 0x3FB950, "bad": 0xF85149, "warn": 0xD29922}

_queue = queue.Queue()
_worker_lock = threading.Lock()
_worker = None


def enabled(event):
    return bool(config.WEBHOOK_URLS) and event in config.WEBHOOK_EVENTS


def is_discord(url):
    parts = urlsplit(url)
    return (
        (parts.hostname or "").lower() in ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com")
        and parts.path.startswith("/api/webhooks/")
    )


def _absolute(path):
    base = config.PUBLIC_URL
    return f"{base}{path}" if base and path else None


def item_link(item):
    """The item's page on the Network tab (as history.js itemUrlPath
    builds it), for a dict with mod/internal/damage and optionally
    kind/variant, or None."""
    if not item or not item.get("internal"):
        return None
    enc = lambda v: quote(str(v), safe="")  # noqa: E731
    if item.get("kind") == "essentia":
        path = "@" + enc(item["internal"])
    elif item.get("kind") == "fluid":
        path = enc(item["internal"])
    else:
        parts = [item.get("mod") or "", item["internal"]]
        if item.get("damage") or item.get("variant"):
            parts.append(icons.damage_str(item.get("damage")) or 0)
        path = ":".join(enc(p) for p in parts)
        if item.get("variant"):
            path += "~" + enc(item["variant"])
    return _absolute("/network/item/" + path)


def event(kind, title, body, tone, icon=None, item=None):
    """A webhook event: what every format below is built from."""
    return {
        "event": kind,
        "title": title,
        "body": body,
        "tone": tone,
        "icon": _absolute(icons.notification_icon_url(icon)),
        "url": item_link(item),
    }


def discord_payload(ev):
    embed = {"title": ev["title"], "description": ev["body"], "color": COLORS[ev["tone"]]}
    if ev["icon"]:
        embed["thumbnail"] = {"url": ev["icon"]}
    if ev["url"]:
        embed["url"] = ev["url"]
    return {"username": "GTNH Monitor", "embeds": [embed], "allowed_mentions": {"parse": []}}


def generic_payload(ev):
    text = f"*{ev['title']}*: {ev['body']}"
    return {**{k: ev[k] for k in ("event", "title", "body", "icon", "url")}, "text": text}


def craft_ended(label, icon, status, item):
    if not enabled("crafts"):
        return
    what = label or "A pinned craft"
    if status == "incomplete":
        ev = event("crafts", "Craft stopped", f"{what} stopped before finishing.", "warn", icon, item)
    else:
        ev = event("crafts", "Craft finished", f"{what} is done crafting.", "good", icon, item)
    _put(ev)


def autocraft_failed(label, icon, reason, item):
    if enabled("autocraft"):
        _put(event("autocraft", "Auto-craft failed", f"Couldn't restock {label}: {reason}", "bad", icon, item))


def stock_low(text, icon, item):
    if enabled("stock"):
        _put(event("stock", "Low stock", text, "warn", icon, item))


def _put(ev):
    _ensure_worker()
    _queue.put(ev)


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="webhooks", daemon=True)
            _worker.start()


def _run():
    while True:
        ev = _queue.get()
        try:
            send(ev)
        except Exception:
            log.exception("webhook delivery failed")
        finally:
            _queue.task_done()


def send(ev):
    """Posts ev to every webhook now. Returns [(host, error or None)]."""
    results = []
    for url in config.WEBHOOK_URLS:
        payload = discord_payload(ev) if is_discord(url) else generic_payload(ev)
        host = urlsplit(url).hostname
        try:
            _post(url, payload)
            results.append((host, None))
        except Exception as error:
            log.warning("webhook to %s failed: %s", host, error)
            results.append((host, str(error)))
    return results


def _post(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "gtnh-craft-monitor"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
        response.read()


def wait_idle():
    """Blocks until every queued webhook has been attempted. For tests."""
    _queue.join()
