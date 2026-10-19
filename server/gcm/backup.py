"""Backups from the page: every database, consistently (db.backup_all()),
and the push signing key, zipped for an admin to download. One is made at
a time, on the data volume beside the databases, and deleted once it's
downloaded or after BACKUP_KEEP_SECONDS."""

import glob
import os
import secrets
import shutil
import tempfile
import threading
import time
import zipfile

from gcm import config, db

BACKUP_KEEP_SECONDS = 600
# Room to leave on top of the databases' own size: the copies are written
# out before they're zipped.
SPACE_MARGIN = 1.1
_PREFIX = ".backup-"


class BackupBusy(Exception):
    pass


class NoSpace(Exception):
    pass


_lock = threading.Lock()
_prepared = None  # {id, dir, path, name, created}


def _database_bytes():
    total = 0
    for path in (config.APP_DB_PATH, config.POWER_DB_PATH, config.ITEM_HISTORY_DB_PATH):
        for suffix in ("", "-wal"):
            try:
                total += os.path.getsize(path + suffix)
            except OSError:
                pass
    return total


def _discard_locked():
    """Removes the prepared backup, and any left by a server that stopped
    before cleaning up. Caller holds _lock."""
    global _prepared
    _prepared = None
    for leftover in glob.glob(os.path.join(config.DATA_DIR, _PREFIX + "*")):
        shutil.rmtree(leftover, ignore_errors=True)


def prepare(now=None):
    """Makes a backup zip and returns {id, name, bytes} for take(). Raises
    BackupBusy while another is being made, NoSpace when the copies
    wouldn't fit."""
    global _prepared
    now = time.time() if now is None else now
    if not _lock.acquire(blocking=False):
        raise BackupBusy()
    try:
        _discard_locked()
        needed = _database_bytes() * SPACE_MARGIN
        if shutil.disk_usage(config.DATA_DIR).free < needed:
            raise NoSpace()
        work = tempfile.mkdtemp(prefix=_PREFIX, dir=config.DATA_DIR)
        try:
            copies = db.backup_all(os.path.join(work, "databases"))
            name = time.strftime("gcm-backup-%Y%m%d-%H%M.zip", time.localtime(now))
            path = os.path.join(work, name)
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for copy in copies:
                    zf.write(copy, os.path.basename(copy))
                    os.remove(copy)  # free the space as it goes
                if os.path.exists(config.VAPID_KEY_PATH):
                    zf.write(config.VAPID_KEY_PATH, os.path.basename(config.VAPID_KEY_PATH))
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        _prepared = {"id": secrets.token_hex(16), "dir": work, "path": path, "name": name, "created": now}
        return {"id": _prepared["id"], "name": name, "bytes": os.path.getsize(path)}
    finally:
        _lock.release()


def take(backup_id, now=None):
    """(path, name, cleanup) of the prepared backup with this id, or None.
    It can be taken once; cleanup() deletes its files once they've been
    sent (and the next prepare() would anyway)."""
    global _prepared
    now = time.time() if now is None else now
    with _lock:
        p = _prepared
        if p is None or not secrets.compare_digest(p["id"], backup_id or ""):
            return None
        _prepared = None
        if now - p["created"] > BACKUP_KEEP_SECONDS:
            shutil.rmtree(p["dir"], ignore_errors=True)
            return None
    return p["path"], p["name"], lambda: shutil.rmtree(p["dir"], ignore_errors=True)


def reset():
    """Forgets and deletes any prepared backup (tests)."""
    with _lock:
        _discard_locked()
