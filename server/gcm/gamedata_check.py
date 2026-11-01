"""Whether the game data looks older than the GTNH version the game
server runs, for the admins' notice. OpenComputers can't read the
modpack version, and it matters: the item scan only asks for the ids in
the selected version's item catalog, so items a newer pack added never
show up, and nothing says why.

The evidence is item ids (mod:name, the catalog's own lines; variants
would only add noise from NBT and damage values) that the patterns and
crafting jobs report, neither of which goes through the catalog, but
which the selected catalog doesn't have. An unknown id only counts once
a newer version's catalog is found to have it: the rest are items NEI
hides (the catalog is NEI's item list), a modified pack's or junk.
Newer catalogs come from installed bundles or from the published
releases (item_catalog.txt, ~325 KB each), cached in DATA_DIR/catalogs/.
Fluids and essentia aren't in catalogs, so they aren't checked.

A pre-release's game data can't be told from its final release's this
way (2.9.0-RC-1 to 2.9.0 added four ids, none a network is likely to
hold), so that case goes by name: the selected data is a pre-release and
the final release of that version is out."""

import json
import logging
import os
import re
import threading
import time
import urllib.request

from gcm import config, gamedata, patterns

log = logging.getLogger(__name__)

CATALOG_FILE = "item_catalog.txt"
# Rechecked this often with nothing new reported, for releases published
# since: the release list is cached as long.
RECHECK_SECONDS = gamedata.AVAILABLE_TTL_SECONDS
# Unknown ids remembered from crafting jobs, at most.
MAX_JOB_IDS = 1000
# Ids listed per kind in the notice.
MAX_LISTED = 50
_FORMATTING = re.compile("§.")

_lock = threading.Lock()
# (catalog version, the selected catalog's ids), read once per version.
_known = (None, None)
# The selected catalog version, and the ids from crafting jobs it lacks
# ("mod:name" -> label) since it was selected.
_from_jobs = {"version": None, "ids": {}}
_result = {"key": None, "at": 0.0, "notice": {}}
_job = {"thread": None}


def reset():
    """Forgets everything. For tests."""
    global _known
    with _lock:
        _known = (None, None)
        _from_jobs.update(version=None, ids={})
        _result.update(key=None, at=0.0, notice={})


def _dir():
    return os.path.join(config.DATA_DIR, "catalogs")


def _read_ids(path):
    with open(path, encoding="utf-8") as f:
        return frozenset(line.strip() for line in f if line.strip())


def _known_ids():
    """(catalog version, ids) of the selected catalog, or (None, None)
    without one."""
    global _known
    version = gamedata.catalog_version()
    with _lock:
        if _known[0] == version:
            return _known
    path = gamedata.catalog_path()
    try:
        ids = _read_ids(path) if path else None
    except OSError:
        ids = None
    known = (version, ids) if ids is not None else (None, None)
    with _lock:
        _known = known
    return known


def _item_id(entry):
    mod, internal = entry.get("mod"), entry.get("internal")
    if not (isinstance(mod, str) and mod and isinstance(internal, str) and internal):
        return None
    return f"{mod}:{internal}"


def _label(name, fallback):
    label = _FORMATTING.sub("", name).strip() if isinstance(name, str) else ""
    return label or fallback


def note_jobs(jobs):
    """Remembers the item ids in a craft_monitor.lua report that the
    selected catalog lacks: they're gone from the next report."""
    version, known = _known_ids()
    if known is None:
        return
    found = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        stacks = [{"mod": job.get("final_output_mod"), "internal": job.get("final_output_internal"),
                   "name": job.get("final_output")}]
        for field in ("active", "pending", "stored"):
            stacks += [s for s in job.get(field) or [] if isinstance(s, dict)]
        for stack in stacks:
            item_id = _item_id(stack)
            if item_id and item_id not in known:
                found.setdefault(item_id, _label(stack.get("name"), item_id))
    with _lock:
        if _from_jobs["version"] != version:
            _from_jobs.update(version=version, ids={})
        ids = _from_jobs["ids"]
        for item_id, label in found.items():
            if item_id not in ids and len(ids) < MAX_JOB_IDS:
                ids[item_id] = label


def unknown_ids():
    """{"mod:name": label} of the items the patterns and crafting jobs
    report that the selected catalog doesn't have, or {} without game
    data."""
    version, known = _known_ids()
    if known is None:
        return {}
    out = {}
    pattern_list, _ = patterns.current()
    for p in pattern_list:
        for entry in p.get("inputs", []) + p.get("outputs", []):
            item_id = _item_id(entry) if entry.get("kind") == "item" else None
            if item_id and item_id not in known:
                out.setdefault(item_id, _label(entry.get("name"), item_id))
    with _lock:
        if _from_jobs["version"] == version:
            for item_id, label in _from_jobs["ids"].items():
                out.setdefault(item_id, label)
    return out


def notice():
    """For the admins' notice:

    - {kind: "behind", selected, suggest, count, suggest_count, ids,
      unpublished_count, unpublished}: ids are the unknown items a newer
      catalog has ([{id, label, version}], version the oldest that has
      it), count of them; suggest is the version to switch to, which has
      suggest_count of them; unpublished the unknown items no catalog
      has ([{id, label}]).
    - {kind: "prerelease", selected, suggest, unpublished_count,
      unpublished}: the final release of the selected pre-release is out.
    - {pending: True} while that's worked out in the background.
    - {} otherwise."""
    selected = gamedata.selected_version()
    if not selected:
        return {}
    unknown = unknown_ids()
    key = (gamedata.catalog_version(), frozenset(unknown))
    with _lock:
        same = _result["key"] == key
        if same and time.time() - _result["at"] < RECHECK_SECONDS:
            return _result["notice"]
        thread = _job["thread"]
        if thread is None or not thread.is_alive():
            thread = threading.Thread(target=_run, args=(key, selected, unknown),
                                      name="gamedata-check", daemon=True)
            _job["thread"] = thread
            thread.start()
        # A recheck for new releases keeps showing what it showed.
        return _result["notice"] if same else {"pending": True}


def _run(key, selected, unknown):
    try:
        result = check(selected, unknown)
    except Exception:
        log.exception("Checking the game data against newer versions failed")
        result = {}
    with _lock:
        _result.update(key=key, at=time.time(), notice=result)


def _candidates():
    """{version: its release, or None if it's only installed} of every
    version there's a catalog for or to download, and whether the
    release list could be fetched."""
    releases, error = gamedata.available()
    out = {r["version"]: r for r in releases}
    for v in gamedata.installed():
        out.setdefault(v["version"], None)
    return out, error is None


def _catalog(version, release):
    """version's catalog ids: its installed bundle's, or its release's,
    downloaded or cached. None if there's neither."""
    path = gamedata.bundle_file(version, CATALOG_FILE)
    if path is None and release is not None and CATALOG_FILE in release["assets"]:
        path = _fetch(release)
    if path is None:
        return None
    try:
        return _read_ids(path)
    except OSError:
        return None


def _fetch(release):
    """The cached copy of release's catalog, downloaded first if there's
    none or the release was rebuilt since. None if that fails."""
    version = release["version"]
    os.makedirs(_dir(), exist_ok=True)
    dest = os.path.join(_dir(), f"{version}.txt")
    meta = os.path.join(_dir(), f"{version}.json")
    try:
        with open(meta, encoding="utf-8") as f:
            if json.load(f).get("data_asset_id") == release.get("data_asset_id") and os.path.isfile(dest):
                return dest
    except (OSError, ValueError, AttributeError):
        pass
    part = dest + ".part"
    try:
        request = urllib.request.Request(release["assets"]["data.json"], headers={"User-Agent": "gtnh-craft-monitor"})
        with urllib.request.urlopen(request, timeout=60) as response:
            expected = json.load(response)["files"][CATALOG_FILE]["sha256"]
        if gamedata.download(release["assets"][CATALOG_FILE], part, count=False) != expected:
            raise ValueError(f"{CATALOG_FILE} doesn't match its checksum in data.json")
        os.replace(part, dest)
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({"data_asset_id": release.get("data_asset_id")}, f)
    except Exception as error:
        if os.path.exists(part):
            os.remove(part)
        log.warning("Couldn't download GTNH %s's item catalog: %s", version, error)
        return None
    return dest


def _prune(keep):
    """Removes the cached catalogs of versions not in keep."""
    if not os.path.isdir(_dir()):
        return
    for name in os.listdir(_dir()):
        if re.sub(r"\.(txt|json)(\.part)?$", "", name) not in keep:
            os.remove(os.path.join(_dir(), name))


def _choice_key(version, have):
    """Most of the ids first, then the oldest version, a final release
    before its own pre-releases."""
    numbers, stage, number = gamedata.version_key(version)
    return (-have, numbers, gamedata.is_prerelease(version), stage, number)


def check(selected, unknown):
    """notice()'s answer for the selected version and its unknown ids
    ({"mod:name": label}), worked out (may download catalogs)."""
    selected_key = gamedata.version_key(selected)
    if selected_key is None:
        return {}
    candidates, listed = _candidates()
    newer = sorted((v for v in candidates if (gamedata.version_key(v) or ()) > selected_key),
                   key=lambda v: (gamedata.version_key(v), v))
    if listed:
        _prune(set(newer))

    first_in = {}   # unknown id -> the oldest newer version that has it
    have = {}       # newer version -> how many of the unknown ids it has
    if unknown:
        for version in newer:
            ids = _catalog(version, candidates[version])
            if ids is None:
                continue
            found = [i for i in unknown if i in ids]
            have[version] = len(found)
            for item_id in found:
                first_in.setdefault(item_id, version)
    unpublished = [{"id": i, "label": label} for i, label in sorted(unknown.items(), key=lambda kv: kv[1].lower())
                   if i not in first_in]
    rest = {"unpublished_count": len(unpublished), "unpublished": unpublished[:MAX_LISTED]}

    if first_in:
        suggest = min((v for v in have if have[v]), key=lambda v: _choice_key(v, have[v]))
        ids = [{"id": i, "label": unknown[i], "version": v}
               for i, v in sorted(first_in.items(), key=lambda kv: unknown[kv[0]].lower())]
        return {"kind": "behind", "selected": selected, "suggest": suggest, "count": len(ids),
                "suggest_count": have[suggest], "ids": ids[:MAX_LISTED], **rest}
    if gamedata.is_prerelease(selected):
        final = next((v for v in newer if gamedata.version_key(v)[0] == selected_key[0]
                      and not gamedata.is_prerelease(v)), None)
        if final:
            return {"kind": "prerelease", "selected": selected, "suggest": final, **rest}
    return {}
