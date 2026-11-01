"""Which of the network's patterns another GTNH version's recipes break:
GTNH doesn't publish recipe changes, and a processing pattern made for
the old recipe keeps running and quietly stalls. Compares two versions'
GregTech recipes (each bundle's recipes.json.gz, indexed by
gcm/recipe_index.py), before an upgrade (the selected version against a
published one) or after (the previous version against the selected
one).

Patterns are often partial on purpose: an EBF pattern leaves out the
gas, a DTPF one the catalyst, the programmed circuit sits in the input
bus. So a pattern isn't held against a recipe exactly. It matches one
when everything it makes is among the recipe's outputs (byproducts may
be left out), everything it takes is among the recipe's inputs (ore
dictionary equivalents count), and every amount it states is the same
multiple of the recipe's. A pattern can also make what a "finishing"
recipe makes from the recipe's output (FINISHING_MAPS: an EBF pattern
that outputs the cooled ingot, a vacuum freezer handling the hot one).

The check then asks whether what a pattern matched in the old version
still exists in the new one, in the same machine: that way the slack in
matching applies to both versions alike, and a pattern only looks broken
if its recipe really changed. Its machine is the recipe map it matched
in, narrowed by the pattern provider's name (what NEI calls the map's
machines) or by what its provider's other patterns matched.

Results, per pattern: "ok"; "changed" (still matches, but something the
pattern leaves out changed: a fluid, the circuit, the voltage tier, the
coil heat); "moved" (its machine no longer makes it, another one does);
"broken" (nothing makes it from those inputs any more); "unchecked" (it
matched nothing in the old version either: another mod's machine, or an
unusual setup); "crafting" (crafting-table patterns, which AE2 already
marks invalid itself)."""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from fractions import Fraction

from gcm import config, gamedata, icons, oredict, patterns
from gcm import recipe_index
from gcm.recipe_index import FLUID, ITEM

log = logging.getLogger(__name__)

FILE = "recipes.json.gz"
# Maps whose recipes a pattern may run its recipe's output through: a
# pattern for a hot ingot's EBF recipe that outputs the cooled ingot.
# Only these, not any one-in-one-out recipe, or a macerator would make
# chance matches.
FINISHING_MAPS = ("gt.recipe.vacuumfreezer",)
# Maps whose special value is the coil heat a recipe needs.
HEAT_MAPS = ("gt.recipe.blastfurnace",)
TIERS = ("ULV", "LV", "MV", "HV", "EV", "IV", "LuV", "ZPM", "UV", "UHV", "UEV", "UIV", "UMV", "UXV", "MAX")
STATUSES = ("broken", "moved", "changed", "unchecked", "crafting", "ok")
# Recipes shown for a broken pattern: what the new version makes its
# output with instead.
MAX_ALTERNATIVES = 5

_lock = threading.Lock()
_indexes = {}   # version -> (index path, recipe_index.RecipeIndex)
_results = {}   # (from, to, patterns updated_at, oredict version) -> result
_job = {"key": None, "error": None, "thread": None}


def _dir():
    return os.path.join(config.DATA_DIR, "recipes")


def source(version):
    """version's recipes file: its bundle's, or one fetched for a check.
    None if there's neither."""
    path = gamedata.bundle_file(version, FILE)
    if path:
        return path
    fetched = os.path.join(_dir(), f"{version}.json.gz")
    return fetched if os.path.isfile(fetched) else None


def index(version):
    """version's open RecipeIndex, built first if it's missing or out of
    date, or None without recipes for it."""
    path = source(version)
    if path is None:
        return None
    out = os.path.join(_dir(), f"{version}.sqlite")
    with _lock:
        cached = _indexes.get(version)
    if cached and recipe_index.is_current(out, path):
        return cached[1]
    os.makedirs(_dir(), exist_ok=True)
    if not recipe_index.is_current(out, path):
        log.info("Indexing the %s recipes...", version)
        started = time.monotonic()
        # As gamedata.index_path(): a separate process, so the memory the
        # JSON takes goes back to the OS.
        script = "import runpy, sys; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, recipe_index.__file__, path, out], capture_output=True, text=True
        )
        if result.returncode != 0:
            log.error("Indexing the %s recipes failed:\n%s", version, result.stderr.strip())
            return None
        log.info("Indexed the %s recipes in %.1fs", version, time.monotonic() - started)
    opened = recipe_index.RecipeIndex(out)
    with _lock:
        old = _indexes.get(version)
        _indexes[version] = (out, opened)
    if old:
        old[1].close()
    return opened


def fetch(version, keep=()):
    """Downloads version's recipes file from its game data release, for a
    check against a version that isn't installed, then prunes the fetched
    recipes of versions no longer needed but those in keep. Returns an
    error message, or None."""
    releases, error = gamedata.available()
    release = next((r for r in releases if r["version"] == version), None)
    if release is None:
        return error or f"No game data published for GTNH {version}."
    if FILE not in release["assets"]:
        return (f"GTNH {version}'s game data was built before it included recipes, "
                "so it can't be checked against until it's rebuilt.")
    os.makedirs(_dir(), exist_ok=True)
    dest = os.path.join(_dir(), f"{version}.json.gz")
    part = dest + ".part"
    try:
        with urllib.request.urlopen(_request(release["assets"]["data.json"]), timeout=60) as response:
            expected = json.load(response)["files"][FILE]["sha256"]
        digest = gamedata.download(release["assets"][FILE], part, count=False)
        if digest != expected:
            raise ValueError(f"{FILE} doesn't match its checksum in data.json")
        os.replace(part, dest)
    except Exception as error_:
        if os.path.exists(part):
            os.remove(part)
        return f"Couldn't download GTNH {version}'s recipes: {error_}"
    _prune({version, *keep})
    return None


def _request(url):
    return urllib.request.Request(url, headers={"User-Agent": "gtnh-craft-monitor"})


def _prune(keep):
    """Removes fetched recipes and indexes of versions no longer needed:
    those that aren't the selected or previous one, or in keep."""
    keep = set(keep) | {gamedata.selected_version(), gamedata.previous_version()}
    for name in os.listdir(_dir()):
        version = re.sub(r"\.(json\.gz|sqlite)(\.part|\.tmp)?$", "", name)
        if version in keep:
            continue
        with _lock:
            cached = _indexes.pop(version, None)
        if cached:
            cached[1].close()
        path = os.path.join(_dir(), name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)


# ------------------------------------------------------------ matching


def _item_key(entry):
    damage = entry.get("damage")
    return f"{entry.get('mod')}:{entry['internal']}:{int(damage) if damage is not None else 0}"


def _pattern_stacks(entries):
    """[(kind, key, size or None)] of a pattern side, or None if it has
    essentia (no GregTech recipe takes or makes that)."""
    out = []
    for e in entries:
        if e.get("kind") == "essentia":
            return None
        if e.get("kind") == "fluid":
            out.append((FLUID, str(e["internal"]).lower(), e.get("size")))
        elif e.get("mod"):
            out.append((ITEM, _item_key(e), e.get("size")))
    return out


class _Matcher:
    """Matching for one check: caches what the two versions make and
    each item's ore-dictionary names."""

    def __init__(self):
        self._making = {}
        self._ore = {}

    def making(self, idx, kind, key):
        cache_key = (id(idx), kind, key)
        if cache_key not in self._making:
            self._making[cache_key] = idx.making(kind, key)
        return self._making[cache_key]

    def _ore_names(self, key):
        if key not in self._ore:
            mod, _, rest = key.partition(":")
            internal, _, damage = rest.rpartition(":")
            self._ore[key] = set(oredict.names(mod, internal, 0 if damage == "*" else damage))
        return self._ore[key]

    def same(self, kind, pattern_key, recipe_key):
        if pattern_key == recipe_key:
            return True
        if kind != ITEM:
            return False
        if recipe_key.endswith(":*") and pattern_key.rpartition(":")[0] == recipe_key[:-2]:
            return True
        return bool(self._ore_names(pattern_key) & self._ore_names(recipe_key))

    def matches(self, idx, pattern_in, pattern_out):
        """Every match of the pattern in idx: [(map name, recipe body,
        finishing (map name, body) or None)]."""
        first_kind, first_key, _ = pattern_out[0]
        found, seen = [], set()
        for rid, map_name, body in self.making(idx, first_kind, first_key):
            if self._fits(pattern_in, pattern_out, body, None):
                found.append((map_name, body, None))
                seen.add(rid)
        if first_kind != ITEM:
            return found
        # Through a finishing recipe: the pattern's output made from one of
        # the recipe's.
        for _, fmap, fbody in self.making(idx, ITEM, first_key):
            if fmap not in FINISHING_MAPS or len(fbody["i"]) != 1 or fbody["i"][0][1] <= 0:
                continue
            for rid, map_name, body in self.making(idx, ITEM, fbody["i"][0][0]):
                if map_name in FINISHING_MAPS or rid in seen:
                    continue
                if self._fits(pattern_in, pattern_out, body, (fmap, fbody)):
                    found.append((map_name, body, (fmap, fbody)))
        return found

    def _fits(self, pattern_in, pattern_out, body, finishing):
        outputs = _merged([(ITEM, o[0], Fraction(o[1]), o[2] if len(o) > 2 else None) for o in body["o"]]
                          + [(FLUID, g[0], Fraction(g[1]), None) for g in body["g"]])
        if finishing:
            middle, per_run = finishing[1]["i"][0][0], finishing[1]["i"][0][1]
            made = next((o for o in outputs if o[0] == ITEM and o[1] == middle), None)
            if made is None:
                return False
            runs = made[2] / per_run
            outputs = [o for o in outputs if o is not made]
            outputs += [(ITEM, o[0], o[1] * runs, made[3]) for o in finishing[1]["o"]]
            outputs += [(FLUID, g[0], g[1] * runs, made[3]) for g in finishing[1]["g"]]
        ratios = []
        for kind, key, size in pattern_out:
            out = next((o for o in outputs if o[0] == kind and self.same(kind, key, o[1])), None)
            if out is None:
                return False
            # A chanced output's count says nothing about the recipe's.
            if size and out[2] and out[3] is None:
                ratios.append(Fraction(size) / out[2])
        inputs = _merged([(ITEM, i[0], i[1], None) for i in body["i"]] + [(FLUID, f[0], f[1], None) for f in body["f"]])
        used = set()
        for kind, key, size in pattern_in:
            at = next((n for n, i in enumerate(inputs)
                       if n not in used and i[0] == kind and self.same(kind, key, i[1])), None)
            if at is None:
                return False
            used.add(at)
            # n 0: an input the recipe doesn't use up, a circuit or a mold.
            if size and inputs[at][2] > 0:
                ratios.append(Fraction(size) / inputs[at][2])
        return len(set(ratios)) <= 1


def _merged(stacks):
    """(kind, key, n, chance) stacks with one entry per kind, key and
    chance, sizes summed, as a pattern's are (gcm/patterns.py): a recipe
    making 165 of something lists it as stacks of 64, 64 and 37. Inputs
    it doesn't use up (n 0) stay as they are."""
    out, at = [], {}
    for kind, key, n, chance in stacks:
        group = (kind, key, chance, n == 0)
        if group in at:
            k, key_, total, chance_ = out[at[group]]
            out[at[group]] = (k, key_, total + n, chance_)
        else:
            at[group] = len(out)
            out.append((kind, key, n, chance))
    return out


def _tier(eut):
    volts = 8
    for name in TIERS:
        if abs(eut) <= volts:
            return name
        volts *= 4
    return TIERS[-1]


def _omitted(pattern_in, pattern_out, match, matcher):
    """What a match takes and makes that the pattern doesn't say: the
    recipe's inputs and byproducts the pattern leaves out, its voltage
    tier and special value, and the same of a finishing recipe. Equal
    for two matches when nothing the pattern relies on without stating
    changed: a new byproduct clogs a machine as surely as a new input
    starves it."""
    map_name, body, finishing = match
    middle = finishing[1]["i"][0][0] if finishing else None
    parts = []
    stated_out = list(pattern_out)
    for rmap, rbody in [(map_name, body)] + ([finishing] if finishing else []):
        main = rbody is body
        stated = list(pattern_in) if main else []
        for kind, entries in ((ITEM, rbody["i"]), (FLUID, rbody["f"])):
            for entry in entries:
                hit = next((p for p in stated if p[0] == kind and matcher.same(kind, p[1], entry[0])), None)
                if hit:
                    stated.remove(hit)
                elif main or kind != ITEM:  # the finishing recipe's item is the main one's output
                    parts.append((rmap, "in", kind, entry[0], entry[1]))
        for kind, entries in ((ITEM, rbody["o"]), (FLUID, rbody["g"])):
            for entry in entries:
                if main and kind == ITEM and entry[0] == middle:
                    middle = None  # made for the finishing recipe
                    continue
                hit = next((p for p in stated_out if p[0] == kind and matcher.same(kind, p[1], entry[0])), None)
                if hit:
                    stated_out.remove(hit)
                else:
                    parts.append((rmap, "out", kind, entry[0], entry[1]))
        parts.append((rmap, "tier", _tier(rbody["e"])))
        if rbody["s"]:
            parts.append((rmap, "special", rbody["s"]))
    return frozenset(parts)


def _describe_changes(old, new, names):
    """Lines saying how the parts a pattern leaves out differ."""
    def name(kind, key):
        return names.get((kind, key)) or key

    lines = []
    for side, verb, past in (("in", "needs", "Needs"), ("out", "makes", "Makes")):
        old_s = {(p[0], p[2], p[3]): p[4] for p in old if p[1] == side}
        new_s = {(p[0], p[2], p[3]): p[4] for p in new if p[1] == side}
        for k in sorted(new_s.keys() - old_s.keys()):
            lines.append(f"Now also {verb} {_amount(k[1], new_s[k])} {name(k[1], k[2])}")
        for k in sorted(old_s.keys() - new_s.keys()):
            lines.append(f"No longer {verb} {name(k[1], k[2])}")
        for k in sorted(old_s.keys() & new_s.keys()):
            if old_s[k] != new_s[k]:
                lines.append(f"{past} {_amount(k[1], new_s[k])} {name(k[1], k[2])} (was {_amount(k[1], old_s[k])})")
    for what in ("tier", "special"):
        old_v = {p[0]: p[2] for p in old if p[1] == what}
        new_v = {p[0]: p[2] for p in new if p[1] == what}
        for rmap in sorted(old_v.keys() | new_v.keys()):
            if old_v.get(rmap) == new_v.get(rmap):
                continue
            if what == "tier":
                lines.append(f"Voltage tier {new_v.get(rmap)} (was {old_v.get(rmap)})")
            elif rmap in HEAT_MAPS:
                lines.append(f"Coil heat {new_v.get(rmap, 0)} K (was {old_v.get(rmap, 0)} K)")
            else:
                lines.append(f"Special value {new_v.get(rmap, 0)} (was {old_v.get(rmap, 0)})")
    return lines


def _amount(kind, n):
    if kind == FLUID:
        return f"{n} mB"
    return "a non-consumed" if n == 0 else f"{n}x"


# ------------------------------------------------------------- machines


def _norm(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().casefold()


def _maps_named(provider_name, idx):
    """The maps whose machine the provider is named after: the longest
    machine name the provider's name is, or starts with."""
    name = _norm(provider_name)
    best, maps = 0, set()
    for map_name in idx.maps():
        for machine in idx.machines(map_name):
            m = _norm(machine["name"])
            if m and (name == m or (name.startswith(m) and not name[len(m)].isalnum())):
                if len(m) > best:
                    best, maps = len(m), {map_name}
                elif len(m) == best:
                    maps.add(map_name)
    return maps


# ---------------------------------------------------------------- check


def compare(pattern_list, old_idx, new_idx):
    """{"counts": {status: n}, "patterns": [result, ...]} for the
    patterns (gcm/patterns.py's normalized ones) between two versions'
    indexes. "ok" and "crafting" patterns are counted, not listed."""
    matcher = _Matcher()
    rows = []
    for p in pattern_list:
        row = {"pattern": p, "status": None}
        rows.append(row)
        if p.get("crafting"):
            row["status"] = "crafting"
            continue
        pattern_in, pattern_out = _pattern_stacks(p["inputs"]), _pattern_stacks(p["outputs"])
        if not pattern_in or not pattern_out:
            row["status"] = "unchecked"
            continue
        row["in"], row["out"] = pattern_in, pattern_out
        row["old"] = matcher.matches(old_idx, pattern_in, pattern_out)
        if not row["old"]:
            row["status"] = "unchecked"

    # Which machine: the provider's name, else what its other patterns
    # matched in one map only, else the pattern's own single map.
    named, votes = {}, {}
    for row in rows:
        if row["status"]:
            continue
        key = patterns.provider_key(row["pattern"]["provider"])
        if key not in named:
            named[key] = _maps_named(row["pattern"]["provider"].get("name"), old_idx)
        maps = {m[0] for m in row["old"]}
        if len(maps) == 1:
            votes.setdefault(key, set()).update(maps)
    for row in rows:
        if row["status"]:
            continue
        key = patterns.provider_key(row["pattern"]["provider"])
        maps = {m[0] for m in row["old"]}
        narrowed = next((maps & s for s in (named[key], votes.get(key, set())) if maps & s), None)
        row["machine_known"] = narrowed is not None or len(maps) == 1
        row["maps"] = narrowed or maps

    names = {}
    out_rows, counts = [], dict.fromkeys(STATUSES, 0)
    for row in rows:
        if not row["status"]:
            _classify(row, matcher, new_idx, names)
        counts[row["status"]] += 1
        if row["status"] not in ("ok", "crafting"):
            out_rows.append(row)
    _fill_names(out_rows, names, old_idx, new_idx)
    return {
        "counts": counts,
        "patterns": [_present(row, names, old_idx, new_idx) for row in out_rows],
    }


def _classify(row, matcher, new_idx, names):
    old = [m for m in row["old"] if m[0] in row["maps"]]
    new_all = matcher.matches(new_idx, row["in"], row["out"])
    new = [m for m in new_all if m[0] in row["maps"]]
    row["old_shown"] = old[0]
    if not new:
        if new_all:
            row["status"] = "moved"
            row["new_shown"] = new_all[:MAX_ALTERNATIVES]
        else:
            row["status"] = "broken"
            first_kind, first_key, _ = row["out"][0]
            row["new_shown"] = [
                (map_name, body, None) for _, map_name, body in matcher.making(new_idx, first_kind, first_key)
            ][:MAX_ALTERNATIVES]
        return
    old_sigs = {_omitted(row["in"], row["out"], m, matcher): m for m in old}
    new_sigs = {_omitted(row["in"], row["out"], m, matcher): m for m in new}
    if old_sigs.keys() & new_sigs.keys():
        row["status"] = "ok"
        return
    # The closest pair, to say what changed.
    old_sig, new_sig = min(
        ((o, n) for o in old_sigs for n in new_sigs), key=lambda pair: len(pair[0] ^ pair[1])
    )
    row["status"] = "changed"
    row["old_shown"] = old_sigs[old_sig]
    row["new_shown"] = [new_sigs[new_sig]]
    row["changes"] = (old_sig, new_sig)


def _fill_names(rows, names, old_idx, new_idx):
    """Display names of every item and fluid the shown recipes use, the
    new version's first."""
    wanted = {ITEM: set(), FLUID: set()}
    for row in rows:
        for match in [row.get("old_shown")] + list(row.get("new_shown") or []):
            if not match:
                continue
            for _, body in [(match[0], match[1])] + ([match[2]] if match[2] else []):
                for key, *_ in body["i"] + body["o"]:
                    wanted[ITEM].add(key)
                for key, *_ in body["f"] + body["g"]:
                    wanted[FLUID].add(key)
    for idx in (old_idx, new_idx):
        for kind, keys in wanted.items():
            for key, name in idx.names(kind, keys).items():
                names[(kind, key)] = name


def _entry(kind, key, n, chance, names):
    """A recipe's stack as the page shows items (with an icon)."""
    if kind == FLUID:
        entry = {"mod": None, "internal": key, "damage": None, "kind": "fluid"}
    else:
        mod, _, rest = key.partition(":")
        internal, _, damage = rest.rpartition(":")
        entry = {"mod": mod, "internal": internal, "damage": 0 if damage == "*" else int(damage), "kind": "item"}
    entry["name"] = names.get((kind, key)) or entry["internal"]
    entry["size"] = n
    if chance:
        entry["chance"] = chance / 100
    return entry


def _recipe(match, names, idx):
    map_name, body, finishing = match
    steps = []
    for rmap, rbody in [(map_name, body)] + ([finishing] if finishing else []):
        entries = {
            "inputs": [_entry(ITEM, i[0], i[1], None, names) for i in rbody["i"]]
            + [_entry(FLUID, f[0], f[1], None, names) for f in rbody["f"]],
            "outputs": [_entry(ITEM, o[0], o[1], o[2] if len(o) > 2 else None, names) for o in rbody["o"]]
            + [_entry(FLUID, g[0], g[1], None, names) for g in rbody["g"]],
        }
        for side in entries.values():
            icons.attach_item_icons(side)
        steps.append({
            "map": rmap,
            "machines": [m["name"] for m in idx.machines(rmap)],
            **entries,
            "eut": rbody["e"],
            "tier": _tier(rbody["e"]),
            "ticks": rbody["d"],
            "special": rbody["s"],
            "heat": rmap in HEAT_MAPS,
        })
    return steps


def _present(row, names, old_idx, new_idx):
    p = row["pattern"]
    out = {
        "status": row["status"],
        "provider": p["provider"],
        "slot": p.get("slot"),
        "inputs": p["inputs"],
        "outputs": p["outputs"],
    }
    if row["status"] == "unchecked":
        return out
    out["machine_known"] = row["machine_known"]
    out["machines"] = sorted({m for map_name in row["maps"] for m in [x["name"] for x in old_idx.machines(map_name)]})
    out["old"] = _recipe(row["old_shown"], names, old_idx)
    out["new"] = [_recipe(m, names, new_idx) for m in row.get("new_shown") or []]
    if row["status"] == "changed":
        old_sig, new_sig = row["changes"]
        out["changes"] = _describe_changes(old_sig, new_sig, names)
    if row["status"] == "moved":
        out["moved_to"] = sorted({m for match in row["new_shown"] for m in [x["name"] for x in new_idx.machines(match[0])]})
    return out


# ------------------------------------------------------------- running


def default_versions():
    """(from, to) for a check with neither given: the upgrade just made
    (previous -> selected), or (selected, None)."""
    selected = gamedata.selected_version()
    previous = gamedata.previous_version()
    return (previous, selected) if previous else (selected, None)


def _key(from_version, to_version):
    _, updated_at = patterns.current()
    return (from_version, to_version, updated_at, oredict.version())


def result(from_version, to_version):
    """The check from one version to another: ("ready", result),
    ("preparing", None) while recipes download or index in the
    background, or ("error", message)."""
    for v in (from_version, to_version):
        if not gamedata.valid_version(v):
            return "error", "Pick two GTNH versions to compare."
    if from_version == to_version:
        return "error", "Pick two different GTNH versions."
    pattern_list, updated_at = patterns.current()
    if updated_at is None:
        return "error", patterns.missing_reason("recipe check")
    key = _key(from_version, to_version)
    with _lock:
        if key in _results:
            return "ready", _results[key]
        running = _job["thread"] is not None and _job["thread"].is_alive()
        if _job["key"] == key and not running and _job["error"]:
            return "error", _job["error"]
        if not running:
            thread = threading.Thread(target=_run, args=(key, pattern_list), name="recipe-check", daemon=True)
            _job.update(key=key, error=None, thread=thread)
            thread.start()
    return "preparing", None


def _run(key, pattern_list):
    from_version, to_version = key[:2]
    try:
        indexes = []
        for v in (from_version, to_version):
            if source(v) is None:
                # Both kept: fetching the second mustn't prune the first.
                error = fetch(v, keep=(from_version, to_version))
                if error:
                    raise ValueError(error)
            idx = index(v)
            if idx is None:
                raise ValueError(f"Couldn't read GTNH {v}'s recipes.")
            indexes.append(idx)
        started = time.monotonic()
        checked = compare(pattern_list, *indexes)
        checked.update(**{"from": from_version, "to": to_version, "checked_at": time.time()})
        log.info("Checked %d patterns from %s to %s in %.1fs: %s", len(pattern_list), from_version, to_version,
                 time.monotonic() - started, checked["counts"])
        with _lock:
            # Only the latest few: a new pattern scan makes the rest stale.
            while len(_results) >= 4:
                _results.pop(next(iter(_results)))
            _results[key] = checked
    except Exception as error:
        log.warning("Recipe check from %s to %s failed: %s", from_version, to_version, error)
        with _lock:
            _job["error"] = str(error)


def notice():
    """For the page's notice after an upgrade: {from, to, counts} when the
    upgrade from the previous version breaks or changes patterns,
    {"pending": True} while that's being worked out in the background,
    {} otherwise."""
    from_version, to_version = default_versions()
    if not (from_version and to_version) or patterns.current()[1] is None:
        return {}
    state, checked = result(from_version, to_version)
    if state == "preparing":
        return {"pending": True}
    if state != "ready":
        return {}
    counts = {k: checked["counts"][k] for k in ("broken", "moved", "changed")}
    if not any(counts.values()):
        return {}
    return {"from": from_version, "to": to_version, "counts": counts}


def reset():
    """Forgets results and closes indexes. For tests."""
    with _lock:
        _results.clear()
        _job.update(key=None, error=None, thread=None)
        for _, idx in _indexes.values():
            idx.close()
        _indexes.clear()
