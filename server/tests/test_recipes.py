import gzip
import hashlib
import json
import os
import shutil
import subprocess
import time

import pytest

from conftest import login_as
from gcm import config, gamedata, oredict, patterns, recipe_index, recipes
from test_gamedata import admin, github, make_bundle  # noqa: F401


@pytest.fixture(autouse=True)
def no_fetched_recipes():
    """Recipes fetched and indexed under DATA_DIR, gone after each test."""
    yield
    recipes.reset()
    shutil.rmtree(os.path.join(config.DATA_DIR, "recipes"), ignore_errors=True)

EBF = "gt.recipe.blastfurnace"
FREEZER = "gt.recipe.vacuumfreezer"
LCR = "gt.recipe.largechemicalreactor"
MIXER = "gt.recipe.multimixer"
MACERATOR = "gt.recipe.macerator"
UNPACKAGER = "gt.recipe.unpackager"

DUST = "gregtech:gt.metaitem.01:2028"
HOT = "gregtech:gt.metaitem.01:12028"
INGOT = "gregtech:gt.metaitem.01:11028"
CIRCUIT = "gregtech:gt.integrated_circuit:1"
FERTILIZER = "gregtech:gt.metaitem.02:32472"
APATITE = "gregtech:gt.metaitem.01:2530"
MACHINES = {
    EBF: ["Electric Blast Furnace"],
    FREEZER: ["Vacuum Freezer"],
    LCR: ["Large Chemical Reactor"],
    MIXER: ["Industrial Mixing Machine"],
    MACERATOR: ["Basic Macerator", "Pulverizer"],
    UNPACKAGER: ["Basic Unpackager", "Advanced Unpackager"],
}


def ebf(dust=1, gas="nitrogen", heat=1700, eut=120):
    return {"i": [[DUST, dust], [CIRCUIT, 0]], "f": [[gas, 1000]], "o": [[HOT, 1]], "e": eut, "d": 400, "s": heat}


def freezer(fluid=None):
    recipe = {"i": [[HOT, 1]], "o": [[INGOT, 1]], "e": 120, "d": 100}
    if fluid:
        recipe["f"] = [[fluid, 100]]
    return recipe


def fertilizer():
    return {"i": [[APATITE, 1]], "f": [["water", 1000]], "o": [[FERTILIZER, 2]], "e": 30, "d": 200}


def write_recipes(path, maps):
    """A recipes.json.gz as Recipes.java writes it, from {map: [recipe]}
    with keys in place of indices."""
    items, fluids = [], []

    def index(table, key):
        if key not in table:
            table.append(key)
        return table.index(key)

    out = {}
    for map_name, recipe_list in maps.items():
        converted = []
        for r in recipe_list:
            c = {k: v for k, v in r.items() if k in ("e", "d", "s")}
            for side, table in (("i", items), ("o", items), ("f", fluids), ("g", fluids)):
                if r.get(side):
                    c[side] = [[index(table, e[0]), *e[1:]] for e in r[side]]
            converted.append(c)
        machines = [{"name": n, "key": f"gregtech:gt.blockmachines:{i}"} for i, n in enumerate(MACHINES[map_name])]
        out[map_name] = {"machines": machines, "recipes": converted}
    data = {"format": 1, "items": items, "fluids": fluids, "maps": out,
            "item_names": [f"Name of {k}" for k in items], "fluid_names": [f.title() for f in fluids]}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)
    return path


@pytest.fixture()
def versions(tmp_path):
    """build(old maps, new maps) -> (old index, new index)."""
    opened = []

    def build(old, new):
        out = []
        for name, maps in (("old", old), ("new", new)):
            source = write_recipes(str(tmp_path / f"{name}.json.gz"), maps)
            path = str(tmp_path / f"{name}.sqlite")
            recipe_index.build(source, path)
            out.append(recipe_index.RecipeIndex(path))
        opened.extend(out)
        return out

    yield build
    for idx in opened:
        idx.close()


def stack(key, size):
    mod, _, rest = key.partition(":")
    internal, _, damage = rest.rpartition(":")
    return {"mod": mod, "internal": internal, "damage": int(damage), "kind": "item", "name": key, "size": size}


def fluid(name, size):
    return {"mod": None, "internal": name, "damage": None, "kind": "fluid", "name": name, "size": size}


def pattern(inputs, outputs, provider="Electric Blast Furnace", crafting=False, x=0):
    return {"provider": {"name": provider, "x": x, "y": 0, "z": 0, "dim": 0}, "slot": 0, "crafting": crafting,
            "exact": True, "inputs": inputs, "outputs": outputs}


EBF_PATTERN = pattern([stack(DUST, 1)], [stack(HOT, 1)])


def check(versions, old, new, pattern_list):
    return recipes.compare(pattern_list, *versions(old, new))


def only(result):
    assert len(result["patterns"]) == 1, result
    return result["patterns"][0]


class TestMatching:
    def test_a_partial_pattern_on_an_unchanged_recipe_is_ok(self, versions):
        maps = {EBF: [ebf()]}
        result = check(versions, maps, maps, [EBF_PATTERN])
        assert result["counts"]["ok"] == 1 and result["patterns"] == []

    def test_a_scaled_pattern_matches(self, versions):
        maps = {EBF: [ebf()]}
        result = check(versions, maps, maps, [pattern([stack(DUST, 16)], [stack(HOT, 16)])])
        assert result["counts"]["ok"] == 1

    def test_a_recipe_listing_one_item_in_several_stacks(self, versions):
        # A pattern merges them (gcm/patterns.py); GregTech lists 165 as
        # 64, 64 and 37.
        maps = {EBF: [dict(ebf(), o=[[HOT, 64], [HOT, 64], [HOT, 37]])]}
        result = check(versions, maps, maps, [pattern([stack(DUST, 2)], [stack(HOT, 330)])])
        assert result["counts"]["ok"] == 1

    def test_amounts_have_to_be_one_multiple(self, versions):
        maps = {EBF: [ebf()]}
        result = check(versions, maps, maps, [pattern([stack(DUST, 16)], [stack(HOT, 8)])])
        assert only(result)["status"] == "unchecked"

    def test_more_dust_per_ingot_breaks_it(self, versions):
        result = check(versions, {EBF: [ebf()]}, {EBF: [ebf(dust=2)]}, [EBF_PATTERN])
        broken = only(result)
        assert broken["status"] == "broken"
        assert broken["old"][0]["inputs"][0]["size"] == 1
        # What the new version makes it with instead.
        assert broken["new"][0][0]["inputs"][0]["size"] == 2
        assert broken["new"][0][0]["inputs"][0]["name"] == f"Name of {DUST}"
        assert "removed" not in broken

    def test_an_item_nothing_makes_or_uses_any_more_is_removed(self, versions):
        unpack = {"i": [[HOT, 1]], "o": [[DUST, 1]], "e": 30, "d": 100}
        result = check(versions, {EBF: [ebf()]}, {UNPACKAGER: [unpack]}, [EBF_PATTERN])
        broken = only(result)
        assert broken["status"] == "broken" and broken["new"] == []
        # Taking it apart, which GTNH keeps for removed items, isn't a use.
        assert broken["removed"] == {"name": f"Name of {HOT}", "used": False, "taken_apart_in": ["Basic Unpackager"]}

    def test_an_item_nothing_makes_but_something_uses(self, versions):
        result = check(versions, {EBF: [ebf()]}, {FREEZER: [freezer()]}, [EBF_PATTERN])
        assert only(result)["removed"] == {"name": f"Name of {HOT}", "used": True, "taken_apart_in": []}

    def test_a_changed_gas_is_a_change_outside_the_pattern(self, versions):
        result = check(versions, {EBF: [ebf()]}, {EBF: [ebf(gas="helium")]}, [EBF_PATTERN])
        changed = only(result)
        assert changed["status"] == "changed"
        assert sorted(changed["changes"]) == ["No longer needs Nitrogen", "Now also needs 1000 mB Helium"]

    def test_heat_and_voltage_changes_are_named(self, versions):
        result = check(versions, {EBF: [ebf()]}, {EBF: [ebf(heat=2700, eut=480)]}, [EBF_PATTERN])
        assert sorted(only(result)["changes"]) == ["Coil heat 2700 K (was 1700 K)", "Voltage tier HV (was MV)"]

    def test_the_cooled_ingot_matches_through_the_vacuum_freezer(self, versions):
        maps = {EBF: [ebf()], FREEZER: [freezer()]}
        cooled = pattern([stack(DUST, 4)], [stack(INGOT, 4)])
        assert check(versions, maps, maps, [cooled])["counts"]["ok"] == 1

    def test_a_new_byproduct_is_a_change(self, versions):
        # 2.9.0's bisphenol A: diluted hydrochloric acid instead of water,
        # which clogs an LCR whose pattern only lists the bisphenol.
        def bisphenol(byproduct):
            return {"i": [[CIRCUIT, 0]], "f": [["acetone", 1000], ["phenol", 2000]],
                    "g": [["molten.bisphenol_a", 1000], [byproduct, 2000]], "e": 30, "d": 160}
        lcr_pattern = pattern([fluid("acetone", 1000), fluid("phenol", 2000)], [fluid("molten.bisphenol_a", 1000)],
                              provider="Large Chemical Reactor")
        result = check(versions, {LCR: [bisphenol("water")]}, {LCR: [bisphenol("dilutedhydrochloricacid_gt5u")]},
                       [lcr_pattern])
        assert only(result)["changes"] == [
            "Now also makes 2000 mB Dilutedhydrochloricacid_Gt5U", "No longer makes Water",
        ]

    def test_a_freezer_that_now_needs_a_fluid_is_a_change(self, versions):
        cooled = pattern([stack(DUST, 1)], [stack(INGOT, 1)])
        result = check(versions, {EBF: [ebf()], FREEZER: [freezer()]},
                       {EBF: [ebf()], FREEZER: [freezer("liquid_helium")]}, [cooled])
        changed = only(result)
        assert changed["status"] == "changed"
        assert changed["changes"] == ["Now also needs 100 mB Liquid_Helium"]
        assert [step["map"] for step in changed["new"][0]] == [EBF, FREEZER]

    def test_only_finishing_maps_chain(self, versions):
        # A macerator turning the hot ingot into the ingot isn't a step
        # anyone automates.
        maps = {EBF: [ebf()], MACERATOR: [freezer()]}
        cooled = pattern([stack(DUST, 1)], [stack(INGOT, 1)])
        assert only(check(versions, maps, maps, [cooled]))["status"] == "unchecked"

    def test_ore_dictionary_equivalents_match(self, versions, monkeypatch):
        other_dust = "bartworks:gt.bwMetaGenerateddust:2028"
        names = {DUST: ["dustCopper"], other_dust: ["dustCopper"]}
        monkeypatch.setattr(oredict, "names", lambda mod, internal, damage: names.get(f"{mod}:{internal}:{damage}", []))
        maps = {EBF: [ebf()]}
        result = check(versions, maps, maps, [pattern([stack(other_dust, 1)], [stack(HOT, 1)])])
        assert result["counts"]["ok"] == 1

    def test_a_wildcard_input_takes_any_damage(self, versions):
        maps = {EBF: [dict(ebf(), i=[["gregtech:gt.metaitem.01:*", 1]])]}
        assert check(versions, maps, maps, [EBF_PATTERN])["counts"]["ok"] == 1

    def test_other_mods_and_crafting_patterns_are_not_checked(self, versions):
        maps = {EBF: [ebf()]}
        crafting = pattern([stack(DUST, 1)], [stack(HOT, 1)], crafting=True)
        altar = pattern([stack("minecraft:stone:0", 1)], [stack("BloodMagic:weakBloodOrb:0", 1)], provider="Altar")
        result = check(versions, maps, maps, [crafting, altar])
        assert result["counts"]["crafting"] == 1 and result["counts"]["unchecked"] == 1
        assert [p["status"] for p in result["patterns"]] == ["unchecked"]


class TestMachines:
    def test_a_recipe_that_moved_machine(self, versions):
        lcr_pattern = pattern([stack(APATITE, 1)], [stack(FERTILIZER, 2)], provider="Large Chemical Reactor")
        result = check(versions, {LCR: [fertilizer()]}, {MIXER: [fertilizer()]}, [lcr_pattern])
        moved = only(result)
        assert moved["status"] == "moved"
        assert moved["machines"] == ["Large Chemical Reactor"]
        assert moved["moved_to"] == ["Industrial Mixing Machine"]

    def test_the_provider_name_picks_the_machine(self, versions):
        # Made in both before, only in the mixer now: the LCR's pattern
        # moved, the mixer's didn't.
        old = {LCR: [fertilizer()], MIXER: [fertilizer()]}
        new = {MIXER: [fertilizer()]}
        lcr = pattern([stack(APATITE, 1)], [stack(FERTILIZER, 2)], provider="Large Chemical Reactor", x=1)
        mixer = pattern([stack(APATITE, 1)], [stack(FERTILIZER, 2)], provider="Industrial Mixing Machine", x=2)
        result = check(versions, old, new, [lcr, mixer])
        assert result["counts"]["moved"] == 1 and result["counts"]["ok"] == 1
        assert only(result)["provider"]["x"] == 1

    def test_the_providers_other_patterns_pick_the_machine(self, versions):
        old = {LCR: [fertilizer(), ebf()], MIXER: [fertilizer()]}
        new = {LCR: [ebf()], MIXER: [fertilizer()]}
        fert = pattern([stack(APATITE, 1)], [stack(FERTILIZER, 2)], provider="Fertilizer line")
        # Only the LCR makes this one, so the provider is an LCR.
        other = pattern([stack(DUST, 1)], [stack(HOT, 1)], provider="Fertilizer line")
        moved = only(check(versions, old, new, [fert, other]))
        assert moved["status"] == "moved" and moved["machine_known"]

    def test_an_unknown_machine_is_ok_if_any_of_its_machines_still_makes_it(self, versions):
        old = {LCR: [fertilizer()], MIXER: [fertilizer()]}
        new = {MIXER: [fertilizer(), dict(fertilizer(), e=480)]}
        fert = pattern([stack(APATITE, 1)], [stack(FERTILIZER, 2)], provider="Fertilizer line")
        assert check(versions, old, new, [fert])["counts"]["ok"] == 1


@pytest.fixture()
def bundles(tmp_path, monkeypatch):
    """Two versions' recipes on disk, as their bundles would have them, and
    a pattern scan."""
    files = {
        "2.9.0-RC-1": write_recipes(str(tmp_path / "rc1.json.gz"), {LCR: [fertilizer()], EBF: [ebf()]}),
        "2.9.0": write_recipes(str(tmp_path / "290.json.gz"), {MIXER: [fertilizer()], EBF: [ebf(gas="helium")]}),
    }
    monkeypatch.setattr(gamedata, "bundle_file", lambda version, name: files.get(version))
    monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
    monkeypatch.setattr(gamedata, "previous_version", lambda: "2.9.0-RC-1")
    lcr = pattern([stack(APATITE, 1)], [stack(FERTILIZER, 2)], provider="Large Chemical Reactor")
    monkeypatch.setattr(patterns, "current", lambda: ([lcr, EBF_PATTERN], 1234.0))
    return files


def finished():
    thread = recipes._job["thread"]
    if thread:
        thread.join(10)


class TestApi:
    def test_after_an_upgrade_it_checks_the_previous_version_against_this_one(self, admin, bundles):
        first = admin.get("/api/admin/recipe-check")
        assert first.status_code == 202 and first.get_json()["state"] == "preparing"
        finished()
        data = admin.get("/api/admin/recipe-check").get_json()
        assert data["state"] == "ready" and (data["from"], data["to"]) == ("2.9.0-RC-1", "2.9.0")
        assert data["counts"]["moved"] == 1 and data["counts"]["changed"] == 1
        assert {p["status"] for p in data["patterns"]} == {"moved", "changed"}

    def test_the_notice_counts_what_the_upgrade_broke(self, admin, bundles):
        assert admin.get("/api/recipe-notice").get_json() == {"pending": True}
        finished()
        notice = admin.get("/api/recipe-notice").get_json()
        assert notice == {"from": "2.9.0-RC-1", "to": "2.9.0", "counts": {"broken": 0, "moved": 1, "changed": 1}}

    def test_no_notice_when_nothing_changed(self, admin, bundles, monkeypatch):
        monkeypatch.setattr(patterns, "current", lambda: ([pattern([stack(DUST, 1)], [stack(INGOT, 1)])], 1.0))
        admin.get("/api/recipe-notice")
        finished()
        assert admin.get("/api/recipe-notice").get_json() == {}

    def test_without_a_pattern_scan(self, admin, bundles, monkeypatch):
        monkeypatch.setattr(patterns, "current", lambda: ([], None))
        for terminal, why in ((False, "no_terminal"), (True, "reading"), (None, "unknown")):
            patterns.note_terminal(terminal)
            response = admin.get("/api/admin/recipe-check")
            data = response.get_json()
            assert response.status_code == 409 and data["patterns"] == why
            assert data["error"] == patterns.missing_reason("pattern check")
            assert "recipe check" not in data["error"]
            # The Game data dialog says so before the button's pressed.
            gamedata_page = admin.get("/api/admin/gamedata").get_json()
            assert gamedata_page["patterns_missing"] == why
            assert gamedata_page["patterns_missing_reason"] == data["error"]

    def test_a_pattern_scan_leaves_nothing_missing(self, admin, bundles):
        page = admin.get("/api/admin/gamedata").get_json()
        assert page["patterns_missing"] is None and page["patterns_missing_reason"] is None

    def test_other_errors_dont_blame_the_patterns(self, admin, bundles):
        data = admin.get("/api/admin/recipe-check?from=2.9.0&to=2.9.0").get_json()
        assert "patterns" not in data

    def test_the_same_version_twice(self, admin, bundles):
        response = admin.get("/api/admin/recipe-check?from=2.9.0&to=2.9.0")
        assert response.status_code == 409

    def test_operators_see_the_upgrades_check_only(self, client, bundles):
        login_as(client, "usr_op", "operator")
        client.get("/api/recipe-check")
        finished()
        data = client.get("/api/recipe-check").get_json()
        assert data["state"] == "ready" and (data["from"], data["to"]) == ("2.9.0-RC-1", "2.9.0")
        assert client.get("/api/recipe-notice").get_json()["counts"]["moved"] == 1
        # Another version means downloading its recipes: an admin's call.
        assert client.get("/api/admin/recipe-check?from=2.8.4&to=2.9.0").status_code == 403
        # The operators' check ignores versions asked for.
        assert client.get("/api/recipe-check?from=2.8.4&to=2.9.0").get_json()["from"] == "2.9.0-RC-1"

    def test_without_an_upgrade_operators_are_told_so(self, client, bundles, monkeypatch):
        monkeypatch.setattr(recipes, "default_versions", lambda: ("2.9.0", None))
        login_as(client, "usr_op", "operator")
        response = client.get("/api/recipe-check")
        assert response.status_code == 409 and "no upgrade to check" in response.get_json()["error"]

    def test_viewers_see_nothing(self, client, bundles):
        for path in ("/api/admin/recipe-check", "/api/recipe-check", "/api/recipe-notice"):
            assert client.get(path).status_code in (401, 403)
        login_as(client, "usr_viewer", "viewer")
        for path in ("/api/admin/recipe-check", "/api/recipe-check", "/api/recipe-notice"):
            assert client.get(path).status_code == 403


def with_recipes(bundle, data, sha256=None):
    """make_bundle()'s files plus a recipes.json.gz, listed in data.json
    as CI lists it."""
    bundle["recipes.json.gz"] = data
    manifest = json.loads(bundle["data.json"])
    manifest["files"]["recipes.json.gz"] = {"size": len(data), "sha256": sha256 or hashlib.sha256(data).hexdigest()}
    bundle["data.json"] = json.dumps(manifest).encode()
    return bundle


class TestIndexes:
    @pytest.fixture()
    def shipped(self, tmp_path, bundles, monkeypatch):
        """Each version's recipes in a bundle directory of its own, with an
        index built beside them, as a host builds them for shared bundles."""
        files = {}
        for version, path in bundles.items():
            bundle = tmp_path / "shared" / version
            bundle.mkdir(parents=True)
            files[version] = str(bundle / recipes.FILE)
            shutil.copy(path, files[version])
            recipe_index.build(files[version], str(bundle / recipe_index.FILE))
        monkeypatch.setattr(gamedata, "bundle_file", lambda version, name: files.get(version))
        return files

    def test_an_index_beside_the_bundle_is_used_as_it_is(self, admin, shipped, monkeypatch):
        def no_building(*args, **kwargs):
            raise AssertionError("built an index the bundle came with")

        monkeypatch.setattr(subprocess, "run", no_building)
        admin.get("/api/admin/recipe-check")
        finished()
        assert admin.get("/api/admin/recipe-check").get_json()["state"] == "ready"
        assert not os.path.exists(os.path.join(config.DATA_DIR, "recipes"))

    def test_an_out_of_date_one_is_built_again_in_the_servers_own_directory(self, admin, shipped):
        write_recipes(shipped["2.9.0"], {MIXER: [fertilizer()], EBF: [ebf(gas="helium"), ebf(dust=2)]})
        admin.get("/api/admin/recipe-check")
        finished()
        assert admin.get("/api/admin/recipe-check").get_json()["state"] == "ready"
        assert os.listdir(os.path.join(config.DATA_DIR, "recipes")) == ["2.9.0.sqlite"]

    def test_building_one_out_of_memory_is_said(self, admin, client, bundles, monkeypatch):
        # How it ends when the kernel kills it: no output, a negative status.
        monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, -9, "", ""))
        admin.get("/api/recipe-notice")
        finished()
        notice = admin.get("/api/recipe-notice").get_json()
        assert (notice["from"], notice["to"]) == ("2.9.0-RC-1", "2.9.0")
        assert "2.9.0-RC-1's recipes" in notice["error"] and "memory" in notice["error"]
        # Operators can't do anything about it.
        login_as(client, "usr_op", "operator")
        assert client.get("/api/recipe-notice").get_json() == {}

    def test_building_one_failing_says_why(self, admin, bundles, monkeypatch):
        failed = subprocess.CompletedProcess([], 1, "", "Traceback ...\nValueError: not a recipes file\n")
        monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: failed)
        admin.get("/api/admin/recipe-check")
        finished()
        response = admin.get("/api/admin/recipe-check")
        assert response.status_code == 409
        assert response.get_json()["error"] == "Couldn't index GTNH 2.9.0-RC-1's recipes: ValueError: not a recipes file."


class TestFetching:
    def test_two_versions_that_arent_installed(self, admin, github, bundles):
        # Fetching the second mustn't prune the first.
        for version, name in (("2.8.0", "2.9.0-RC-1"), ("2.8.1", "2.9.0")):
            with open(bundles[name], "rb") as f:
                github.bundles[version] = with_recipes(make_bundle(version), f.read())
        admin.get("/api/admin/recipe-check?from=2.8.0&to=2.8.1")
        finished()
        data = admin.get("/api/admin/recipe-check?from=2.8.0&to=2.8.1").get_json()
        assert data["state"] == "ready" and data["counts"]["moved"] == 1 and data["counts"]["changed"] == 1

    def test_a_version_that_isnt_installed_is_fetched(self, admin, github, bundles):
        with open(bundles["2.9.0"], "rb") as f:
            github.bundles["2.9.1"] = with_recipes(make_bundle("2.9.1"), f.read())
        admin.get("/api/admin/recipe-check?from=2.9.0-RC-1&to=2.9.1")
        finished()
        data = admin.get("/api/admin/recipe-check?from=2.9.0-RC-1&to=2.9.1").get_json()
        assert data["state"] == "ready" and data["counts"]["moved"] == 1
        assert os.path.isfile(os.path.join(config.DATA_DIR, "recipes", "2.9.1.json.gz"))
        assert "recipes.json.gz" in github.downloads

    def test_a_release_built_before_recipes(self, admin, github, bundles):
        github.bundles["2.8.4"] = make_bundle("2.8.4")
        admin.get("/api/admin/recipe-check?from=2.8.4&to=2.9.0")
        finished()
        response = admin.get("/api/admin/recipe-check?from=2.8.4&to=2.9.0")
        assert response.status_code == 409
        assert "rebuilt" in response.get_json()["error"]

    def test_a_bad_checksum_is_refused(self, admin, github, bundles):
        github.bundles["2.9.1"] = with_recipes(make_bundle("2.9.1"), b"not the file data.json describes", "0" * 64)
        admin.get("/api/admin/recipe-check?from=2.9.0&to=2.9.1")
        finished()
        response = admin.get("/api/admin/recipe-check?from=2.9.0&to=2.9.1")
        assert "checksum" in response.get_json()["error"]
        assert not os.path.exists(os.path.join(config.DATA_DIR, "recipes", "2.9.1.json.gz"))


def test_an_index_is_rebuilt_when_its_recipes_change(tmp_path):
    source = write_recipes(str(tmp_path / "r.json.gz"), {EBF: [ebf()]})
    path = str(tmp_path / "r.sqlite")
    recipe_index.build(source, path)
    assert recipe_index.is_current(path, source)
    time.sleep(0.01)
    write_recipes(source, {EBF: [ebf(), ebf(dust=2)]})
    assert not recipe_index.is_current(path, source)
