"""Game data for a GTNH version: item icons (images.zip), the icon lookup
and the OC scanner's item catalog. Which bundle is live, what's installed,
what can be downloaded, and installing one.

The data depends on the modpack version the server's players run, not on
this app's version. .github/workflows/gtnh-data.yml publishes a bundle
for each GTNH release as a GitHub Release tagged gtnh-data-<version>
(built by tools/icon-export/). An admin picks one on the Game data page
(gcm/routes/gamedata.py); it's downloaded into
DATA_DIR/gamedata/<version>/ in the background and goes live without a
restart. tools/icon-export/run.sh --install puts a locally built bundle in
the same place.

A bundle's icons come in more than one texture set (TEXTURES): the same
paths rendered with the pack's own textures, and with the GTNH Faithful
x32 resource pack. Only the chosen set's zip is downloaded; the lookup and
catalog are shared.

With no bundle installed there are no icons, and the scanner has no
catalog to download."""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request

from gcm import config, icons, inventory

TAG_PREFIX = "gtnh-data-"
# data.json first: it holds the others' sizes and checksums.
BASE_FILES = ("data.json", "icons_lookup.json", "item_catalog.txt")
TEXTURES = {
    "default": {"file": "images.zip", "name": "Default"},
    "faithful32": {
        "file": "images-faithful32.zip",
        "name": "Faithful 32x",
        "credit": "GTNH Faithful x32 textures by Ethryan and contributors",
        "url": "https://github.com/Ethryan/GTNH-Faithful-Textures",
    },
}
SELECTED_FILE = "selected.json"
# Written next to a downloaded bundle: which upload of the release it came
# from. CI replaces a release when it rebuilds a version, which gives its
# data.json asset a new id.
RELEASE_FILE = "release.json"
AVAILABLE_TTL_SECONDS = 600
# Versions become directory names, so keep them to tag-safe characters.
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_lock = threading.Lock()
_status = {}
_available = {}


def reset():
    """Forgets download state and the release list. For tests."""
    with _lock:
        _status.clear()
        _status.update(
            state="idle", version=None, textures=None, done_bytes=0, total_bytes=0, error=None
        )
        _available.clear()
        _available.update(fetched_at=0.0, releases=[], error=None)


reset()


def valid_version(version):
    return isinstance(version, str) and bool(_VERSION_RE.match(version))


def texture_sets():
    """The texture sets, for the page: id, name and any credit."""
    return [{"id": id_, **info} for id_, info in TEXTURES.items()]


def _bundle_dir(version):
    return os.path.join(config.GAMEDATA_DIR, version)


def _has_base(version):
    return valid_version(version) and all(
        os.path.isfile(os.path.join(_bundle_dir(version), name)) for name in BASE_FILES
    )


def _textures_on_disk(version):
    if not _has_base(version):
        return []
    return [
        id_
        for id_, info in TEXTURES.items()
        if os.path.isfile(os.path.join(_bundle_dir(version), info["file"]))
    ]


def _read_selected():
    try:
        with open(os.path.join(config.GAMEDATA_DIR, SELECTED_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_selected(version, textures, previous):
    os.makedirs(config.GAMEDATA_DIR, exist_ok=True)
    path = os.path.join(config.GAMEDATA_DIR, SELECTED_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"version": version, "textures": textures, "previous": previous}, f)
    os.replace(path + ".tmp", path)


def selected():
    """(version, textures) of the live bundle, or (None, None)."""
    data = _read_selected()
    version = data.get("version")
    textures = data.get("textures") or "default"
    if textures in _textures_on_disk(version):
        return version, textures
    return None, None


def selected_version():
    return selected()[0]


def build_id(version):
    """Short id of the installed build of version (from its data.json), so
    a rebuilt release under the same version name gets new icon URLs and a
    new catalog version: browsers cache icons for a week, and the scanner
    only refetches its catalog when the version it's told changes."""
    try:
        return hashlib.sha256(_read_bytes(os.path.join(_bundle_dir(version), "data.json"))).hexdigest()[:8]
    except OSError:
        return None


def _installed_asset_id(version):
    try:
        with open(os.path.join(_bundle_dir(version), RELEASE_FILE), encoding="utf-8") as f:
            return json.load(f).get("data_asset_id")
    except (OSError, ValueError, AttributeError):
        return None


def update_available(release):
    """Whether release is a newer build of a version that's on disk. Bundles
    that didn't come from a release (run.sh --install) record none, so they
    never count as outdated."""
    installed_id = _installed_asset_id(release["version"])
    return (
        installed_id is not None
        and _has_base(release["version"])
        and release.get("data_asset_id") != installed_id
    )


def installed():
    """Bundles on disk, newest first, with their data.json details and the
    texture sets downloaded for them."""
    versions = []
    if os.path.isdir(config.GAMEDATA_DIR):
        for name in os.listdir(config.GAMEDATA_DIR):
            textures = _textures_on_disk(name)
            if not textures:
                continue
            try:
                with open(os.path.join(_bundle_dir(name), "data.json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            versions.append(
                {"version": name, "generated_at": meta.get("generated_at"), "textures": textures}
            )
    versions.sort(key=lambda v: v["generated_at"] or "", reverse=True)
    return versions


def activate():
    """Makes the selected bundle live, or no icons if there's none. Called
    at startup and after an install."""
    version, textures = selected()
    if version:
        # The icon URL prefix busts browser caches when any of these changes.
        build = build_id(version)
        prefix = f"{version}~{build}" if textures == "default" else f"{version}~{textures}~{build}"
        icons.load(
            os.path.join(_bundle_dir(version), "icons_lookup.json"),
            os.path.join(_bundle_dir(version), TEXTURES[textures]["file"]),
            prefix,
        )
    else:
        icons.load(None, None)


def catalog_version():
    # Same catalog whatever the textures, so the scanner only refetches it
    # when the GTNH version or its build changes.
    version = selected_version()
    return f"{version}~{build_id(version)}" if version else None


def catalog_path():
    version = selected_version()
    return os.path.join(_bundle_dir(version), "item_catalog.txt") if version else None


def _github_json(url):
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "gtnh-craft-monitor"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def available(refresh=False):
    """(releases, error): the bundles published for download, newest
    first, each with the texture sets it has. Cached for a few minutes;
    GitHub rate-limits anonymous API use."""
    with _lock:
        fresh = time.time() - _available["fetched_at"] < AVAILABLE_TTL_SECONDS
        if fresh and not refresh:
            return list(_available["releases"]), _available["error"]
    releases, error = [], None
    try:
        for release in _github_json(
            f"{config.GAMEDATA_API_URL}/repos/{config.GAMEDATA_REPO}/releases?per_page=100"
        ):
            tag = release.get("tag_name", "")
            version = tag[len(TAG_PREFIX):]
            if not tag.startswith(TAG_PREFIX) or not valid_version(version) or release.get("draft"):
                continue
            assets = {a["name"]: a for a in release.get("assets", [])}
            textures = {
                id_: assets[info["file"]]["size"]
                for id_, info in TEXTURES.items()
                if info["file"] in assets
            }
            if not textures or not all(name in assets for name in BASE_FILES):
                continue
            releases.append(
                {
                    "version": version,
                    "published_at": release.get("published_at"),
                    "base_size": sum(assets[name]["size"] for name in BASE_FILES),
                    "textures": textures,
                    "data_asset_id": assets["data.json"].get("id"),
                    "assets": {name: a["browser_download_url"] for name, a in assets.items()},
                }
            )
    except Exception as error_:  # network, HTTP or unexpected JSON
        error = f"Couldn't list game data releases from GitHub: {error_}"
    releases.sort(key=lambda r: r["published_at"] or "", reverse=True)
    with _lock:
        _available.update(fetched_at=time.time(), releases=releases, error=error)
    return list(releases), error


def status():
    with _lock:
        return dict(_status)


def start_install(version, textures="default"):
    """Selects version with the given textures, downloading whatever isn't
    on disk yet first, or the whole bundle again if the release has been
    rebuilt since it was downloaded. Returns an error message, or None once
    it's selected or downloading."""
    if not valid_version(version):
        return "Unknown version."
    if textures not in TEXTURES:
        return "Unknown textures."
    with _lock:
        if _status["state"] == "installing":
            return f"Already installing {_status['version']}."
    releases, error = available()
    release = next((r for r in releases if r["version"] == version), None)
    outdated = release is not None and update_available(release)
    if textures in _textures_on_disk(version) and not outdated:
        _select(version, textures)
        with _lock:
            _status.update(
                state="idle", version=None, textures=None, done_bytes=0, total_bytes=0, error=None
            )
        return None
    if release is None:
        return error or f"No game data published for {version}."
    if textures not in release["textures"]:
        return f"GTNH {version} has no {TEXTURES[textures]['name']} icons."
    total = release["textures"][textures]
    if not _has_base(version) or outdated:
        total += release["base_size"]
    with _lock:
        if _status["state"] == "installing":
            return f"Already installing {_status['version']}."
        _status.update(
            state="installing",
            version=version,
            textures=textures,
            done_bytes=0,
            total_bytes=total,
            error=None,
        )
    threading.Thread(
        target=_install, args=(release, textures), name="gamedata-install", daemon=True
    ).start()
    return None


def _select(version, textures):
    old = _read_selected()
    previous = old.get("version") if old.get("version") != version else old.get("previous")
    _write_selected(version, textures, previous)
    activate()
    inventory.refresh_icons()
    _prune()


def _prune():
    """Keeps the selected bundle and the one before it, for going back."""
    selected_ = _read_selected()
    keep = {selected_.get("version"), selected_.get("previous")}
    for name in os.listdir(config.GAMEDATA_DIR):
        path = os.path.join(config.GAMEDATA_DIR, name)
        if os.path.isdir(path) and name not in keep and not name.endswith(".part"):
            shutil.rmtree(path, ignore_errors=True)


def _download(url, path):
    """Streams url to path, counting progress. Returns the SHA-256."""
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "gtnh-craft-monitor"})
    with urllib.request.urlopen(request, timeout=60) as response, open(path, "wb") as f:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            digest.update(chunk)
            with _lock:
                _status["done_bytes"] += len(chunk)
    return digest.hexdigest()


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _install(release, textures):
    """Downloads into <version>.part/, then moves it into place. If the
    version's lookup and catalog are already installed and the release
    hasn't been rebuilt since, only the texture set's zip is fetched."""
    version = release["version"]
    final = _bundle_dir(version)
    part = final + ".part"
    try:
        shutil.rmtree(part, ignore_errors=True)
        os.makedirs(part)
        _download(release["assets"]["data.json"], os.path.join(part, "data.json"))
        with open(os.path.join(part, "data.json"), encoding="utf-8") as f:
            expected = json.load(f)["files"]
        reuse_base = _has_base(version) and _read_bytes(
            os.path.join(part, "data.json")
        ) == _read_bytes(os.path.join(final, "data.json"))
        if not reuse_base:
            with _lock:
                _status["total_bytes"] = release["base_size"] + release["textures"][textures]
        names = [TEXTURES[textures]["file"]]
        if not reuse_base:
            names = list(BASE_FILES[1:]) + names
        for name in names:
            digest = _download(release["assets"][name], os.path.join(part, name))
            if digest != expected[name]["sha256"]:
                raise ValueError(f"{name} doesn't match its checksum in data.json")
        record = os.path.join(part, RELEASE_FILE)
        with open(record, "w", encoding="utf-8") as f:
            json.dump({"data_asset_id": release.get("data_asset_id")}, f)
        if reuse_base:
            for name in (TEXTURES[textures]["file"], RELEASE_FILE):
                os.replace(os.path.join(part, name), os.path.join(final, name))
            shutil.rmtree(part, ignore_errors=True)
        else:
            shutil.rmtree(final, ignore_errors=True)
            os.replace(part, final)
        _select(version, textures)
        with _lock:
            _status.update(state="done", error=None)
    except Exception as error:
        shutil.rmtree(part, ignore_errors=True)
        with _lock:
            _status.update(state="error", error=f"Installing {version} failed: {error}")


def install_configured_version():
    """Startup: installs GTNH_VERSION (with GTNH_TEXTURES, if set) unless
    it's already selected, for setups that pin the version in their
    environment instead of using the page."""
    version = config.GTNH_VERSION
    if not version:
        return
    current_version, current_textures = selected()
    textures = config.GTNH_TEXTURES or (
        current_textures if current_version == version else "default"
    )
    if (version, textures) == (current_version, current_textures):
        return
    error = start_install(version, textures)
    if error:
        print(f"[gamedata] GTNH_VERSION={version}: {error}")
