"""Backups from the page: every database, consistently (db.backup_all()),
the push signing key and the generated API key, zipped for an admin to
download. One is made at a time, on the data volume beside the
databases, and deleted once it's downloaded or after BACKUP_KEEP_SECONDS.

restore() takes such a zip back: it checks every file in it before
touching anything, then copies each database over the live one with the
same online backup API, run the other way."""

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
# The biggest upload restore() takes. waitress is told the same (app.py).
RESTORE_MAX_BYTES = 4 * 1024**3
# Room for the upload and the databases unzipped from it.
RESTORE_SPACE_FACTOR = 3


class BackupBusy(Exception):
    pass


class NoSpace(Exception):
    pass


class TooBig(Exception):
    pass


class BadBackup(Exception):
    """The upload isn't a backup this server can restore; the message says
    why, for the admin."""


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
                for key_path in (config.VAPID_KEY_PATH, config.API_KEY_PATH):
                    if os.path.exists(key_path):
                        zf.write(key_path, os.path.basename(key_path))
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


def _keys():
    """name in the zip -> live path."""
    return {"vapid_private.pem": config.VAPID_KEY_PATH, "api_key": config.API_KEY_PATH}


def _receive(stream, length, path):
    with open(path, "wb") as f:
        received = 0
        while chunk := stream.read(1024 * 1024):
            received += len(chunk)
            if received > RESTORE_MAX_BYTES:
                raise TooBig()
            f.write(chunk)
    if received != length:
        raise BadBackup("The upload was cut short. Try again.")


def _check(work, upload):
    """Unzips the upload into work and checks it: {name: path} of every
    file to restore. Raises BadBackup."""
    try:
        zf = zipfile.ZipFile(upload)
    except zipfile.BadZipFile:
        raise BadBackup("That isn't a zip file.") from None
    databases, keys = db.database_names(), _keys()
    with zf:
        names = zf.namelist()
        unknown = [n for n in names if n not in databases and n not in keys]
        if unknown:
            raise BadBackup(f"That isn't a backup from this server: it has {', '.join(sorted(unknown)[:3])}.")
        if not any(n in databases for n in names):
            raise BadBackup("That backup has no databases in it.")
        # Its own sizes, so a small zip that unpacks huge can't fill the disk.
        if sum(info.file_size for info in zf.infolist()) > shutil.disk_usage(config.DATA_DIR).free:
            raise NoSpace()
        files = {}
        for name in names:
            files[name] = zf.extract(name, work)
    for name, path in files.items():
        if name in databases:
            why = db.why_not_restorable(name, path)
            if why:
                raise BadBackup(f"{name} in that backup {why}.")
    return files


def _apply(files):
    databases, keys = db.database_names(), _keys()
    for name, path in files.items():
        if name in databases:
            db.restore_copy(name, path)
        else:
            live = keys[name]
            with open(path, "rb") as src:
                data = src.read()
            temp = live + ".restoring"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(temp, live)


def restore(stream, length):
    """Restores every file in an uploaded backup zip over the live ones,
    while the server runs. Nothing is changed unless the whole zip checks
    out. Returns the names restored; the caller reloads what the server
    keeps in memory (gcm.reload_data()). Raises BackupBusy, NoSpace,
    TooBig or BadBackup."""
    if length is None:
        raise BadBackup("The upload had no size. Try again.")
    if length > RESTORE_MAX_BYTES:
        raise TooBig()
    if not _lock.acquire(blocking=False):
        raise BackupBusy()
    try:
        _discard_locked()
        if shutil.disk_usage(config.DATA_DIR).free < length * RESTORE_SPACE_FACTOR:
            raise NoSpace()
        work = tempfile.mkdtemp(prefix=_PREFIX, dir=config.DATA_DIR)
        try:
            upload = os.path.join(work, "upload.zip")
            _receive(stream, length, upload)
            files = _check(os.path.join(work, "files"), upload)
            os.remove(upload)
            _apply(files)
            return sorted(files)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        _lock.release()
