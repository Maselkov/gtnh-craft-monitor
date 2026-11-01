"""dev.py's fake game only talks to the real API, so these keep it in
step with the endpoints it imitates the oc/ scripts on."""

import json
import zipfile

import dev
import pytest
from conftest import TEST_API_KEY, bundle_index, login_as, ore_dict_index
from gcm import config, gamedata, gamedata_check, item_info, oredict, patterns, recipe_index, recipes, state


@pytest.fixture()
def game(flask_app, monkeypatch):
    monkeypatch.setattr(dev, "API_KEY", TEST_API_KEY)
    # Some of everything: a random stock that runs out of one of the items
    # a plan below reads changes what that plan shows.
    return dev.FakeGame(flask_app, stock={it["name"]: 1000 for it in dev.CATALOG})


def test_tick_reports_crafts_power_and_a_scan(game, client):
    # A job's first report has no output, as a real one's can.
    game.tick()
    game.tick()

    jobs = {j["name"]: j for j in client.get("/api/crafts").get_json()["jobs"]}
    assert jobs["M00"]["busy"] and jobs["M00"]["final_output"] == "Dangote Distillus"
    assert len(jobs["M00"]["pending"]) + len(jobs["M00"]["active"]) >= 20
    assert not jobs["B01"]["busy"]

    assert client.get("/api/power").get_json()["latest"]["capacity"] == dev.CAPACITY
    assert len(client.get("/api/network").get_json()["items"]) > 20


def test_jobs_finish(game):
    cpu = game.cpus[1]
    for _ in range(500):
        if cpu.tick():
            return
    pytest.fail("a small job never finished")


def test_craft_request_is_accepted_on_a_free_cpu(game, client):
    login_as(client, "usr_dev", role="operator")
    res = client.post("/api/craft/request", json={
        "label": "Titanium Gear", "mod": "gregtech", "internal": "gt.metaitem.01",
        "damage": 30015, "amount": 4,
    })
    assert res.status_code == 200
    free = next(cpu.name for cpu in game.cpus if cpu.job is None)
    game.tick()
    game.tick()
    jobs = {j["name"]: j for j in client.get("/api/crafts").get_json()["jobs"]}
    assert jobs[free]["busy"] and jobs[free]["final_output"] == "Titanium Gear"


def test_cpu_without_a_monitor_reports_no_output(game, client):
    game.tick()
    game.tick()
    jobs = {j["name"]: j for j in client.get("/api/crafts").get_json()["jobs"]}
    assert jobs["a02"]["busy"] and "final_output" not in jobs["a02"]


def test_cancel_request_idles_the_cpu(game, client):
    game.tick()
    login_as(client, "usr_dev", role="operator")
    assert client.post("/api/craft/cancel", json={"cpu_name": "a00"}).status_code == 200
    game.tick()
    jobs = {j["name"]: j for j in client.get("/api/crafts").get_json()["jobs"]}
    assert not jobs["a00"]["busy"]


def test_icon_bundle_covers_every_item_but_essentia(tmp_path):
    dev.install_icons(str(tmp_path))
    bundle = tmp_path / "gamedata" / dev.GTNH_VERSION
    lookup = json.loads((bundle / "icons_lookup.json").read_text())
    with zipfile.ZipFile(bundle / "images.zip") as zf:
        names = set(zf.namelist())
    paths = list(lookup["by_key"].values()) + list(lookup["fluids_by_key"].values())
    # Essentia has none, to show the page's stand-in.
    assert len(paths) == sum(1 for it in dev.CATALOG if it["kind"] != "essentia")
    assert set(paths) <= names
    assert json.loads((tmp_path / "gamedata" / "selected.json").read_text())["version"] == dev.GTNH_VERSION


def test_the_version_before_has_the_recipes_the_pattern_check_flags(tmp_path):
    dev.install_icons(str(tmp_path))
    gamedata_dir = tmp_path / "gamedata"
    assert json.loads((gamedata_dir / "selected.json").read_text())["previous"] == dev.PREVIOUS_VERSION
    indexes = []
    for version in (dev.PREVIOUS_VERSION, dev.GTNH_VERSION):
        path = str(tmp_path / f"{version}.sqlite")
        recipe_index.build(str(gamedata_dir / version / "recipes.json.gz"), path)
        indexes.append(recipe_index.RecipeIndex(path))
    try:
        result = recipes.compare([patterns.normalize(p) for p in dev.pattern_scan()], *indexes)
    finally:
        for idx in indexes:
            idx.close()
    by_provider = {p["provider"]["name"]: p for p in result["patterns"]}
    assert {name: p["status"] for name, p in by_provider.items()} == {
        "Bending Machine T4:EV": "broken",
        "Circuit Assembler": "broken",
        "Lathe T3:HV": "moved",
        "Fluid Extractor": "changed",
        "Electric Implosion Compressor": "changed",
        "Infusion Altar": "unchecked",
    }
    assert result["counts"]["crafting"] == 4
    assert by_provider["Circuit Assembler"]["removed"] == {
        "name": "Ultimate Circuit", "used": False, "taken_apart_in": ["Unpackager"],
    }


@pytest.mark.parametrize("prerelease", [False, True])
def test_the_bundles_bring_the_game_data_notice(tmp_path, monkeypatch, prerelease):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "GAMEDATA_DIR", str(tmp_path / "gamedata"))
    monkeypatch.setattr(gamedata, "available", lambda refresh=False: ([], None))
    dev.install_icons(str(tmp_path), prerelease=prerelease)
    with state.patterns_lock:
        state.patterns["patterns"] = [patterns.normalize(p) for p in dev.pattern_scan()]

    selected = gamedata.selected_version()
    unknown = gamedata_check.unknown_ids()
    assert unknown == {f"gregtech:{dev.BY_NAME[dev.NEWER_ITEM]['internal']}": dev.NEWER_ITEM}
    result = gamedata_check.check(selected, unknown)
    if prerelease:
        assert (selected, result["kind"], result["suggest"]) == (dev.PRERELEASE_VERSION, "prerelease", dev.GTNH_VERSION)
        assert result["unpublished_count"] == 1
    else:
        assert (selected, result["kind"], result["suggest"]) == (dev.GTNH_VERSION, "behind", dev.NEXT_VERSION)


def test_the_bundles_descriptions_reach_the_page(tmp_path, game, client):
    dev.install_icons(str(tmp_path))
    item_info.load(bundle_index(tmp_path / "gamedata" / dev.GTNH_VERSION))
    try:
        game.tick()
        info = client.get("/api/network/info").get_json()
    finally:
        item_info.load(None)
    dtpf = dev.BY_NAME["Dimensionally Transcendent Plasma Forge"]
    dtpf_info = info["items"][f"gregtech|gt.metaitem.01|{dtpf['damage']}|item"]
    assert "DTPF" in dtpf_info["description"] and "Structure" in dtpf_info["shift_description"]
    assert info["mods"]["gregtech"] == "GregTech"
    lapotron = dev.BY_NAME["Lapotron Crystal"]
    assert dev.item_info()["rarity"]["by_key"][f"gregtech:gt.metaitem.01:{lapotron['damage']}"] == "b"


def test_ended_job_sends_its_last_look_once(game):
    cpu = game.cpus[1]
    assert any(cpu.tick() for _ in range(500))
    report = cpu.report()
    assert set(report["last_busy"]) == {"pending", "active", "age"}
    assert "last_busy" not in cpu.report()


def test_requested_job_reports_its_outcome(game, client):
    login_as(client, "usr_dev", role="operator")
    client.post("/api/craft/request", json={
        "label": "Titanium Gear", "mod": "gregtech", "internal": "gt.metaitem.01",
        "damage": 30015, "amount": 4,
    })
    free = next(cpu for cpu in game.cpus if cpu.job is None)
    game.tick()
    assert free.request_id is not None
    assert client.post("/api/craft/cancel", json={"cpu_name": free.name}).status_code == 200
    game.tick()
    assert free.request_id is None
    statuses = [c["status"] for c in client.get("/api/completions").get_json()["completions"]]
    assert statuses == ["incomplete"]


def test_patterns_give_plans_that_show_each_case(game, client, tmp_path):
    game.tick()
    login_as(client, "usr_alice")
    assert client.get("/api/network/patterns").get_json()["summary"]["patterns"] == len(dev.PATTERNS)

    def ask(name, amount=1, **extra):
        it = dev.BY_NAME[name]
        return client.get("/api/network/plan", query_string={
            "mod": it["mod"], "internal": it["internal"], "damage": it["damage"], "amount": amount, **extra,
        }).get_json()

    def plan(name, amount=1):
        return ask(name, amount)["plan"]

    first = ask("Inconel-625 Gear", 100000)
    plate = first["plan"]["root"]["children"][0]
    # More plates than either pattern can make: the later slot (the
    # solidifier) first, then the rest, split over parts.
    assert plate["split"] is True
    assert plate["children"][0]["pattern"]["provider"] == "Fluid Solidifier {Plate}"
    assert {c["status"] for c in plate["children"]} == {"via"}
    # Gear <- plate <- molten <- ingot <- molten: the loop is short, and
    # where is listed - ask for that step's own answer.
    short = next(m for m in first["plan"]["missing"] if m["name"] in ("Molten Inconel-625", "Inconel-625 Ingot"))
    at = short["at"][0]
    step = ask("Inconel-625 Gear", 100000, path=at, version=first["version"])["node"]
    assert step["name"] == short["name"] and step["status"] in ("cycle", "missing")
    assert [m["name"] for m in plan("Ultimate Circuit")["missing"]] == ["Draconium Ingot"]
    # Block of Diamond's pattern takes Industrial Diamonds too (the dev
    # bundle's ore dictionary): more than any stock covers takes them from
    # stock, and makes some with the implosion compressor.
    path = tmp_path / "ore_dict.json"
    path.write_text(json.dumps(dev.ore_dict()))
    oredict.load(ore_dict_index(path))
    try:
        diamond = plan("Block of Diamond", 100000)["root"]["children"][0]
    finally:
        oredict.load(None)
    assert diamond["name"] == "Diamond"
    assert [s["name"] for s in diamond["substitutes"]] == ["Industrial Diamond"]
    assert any(c.get("substitute") and c["pattern"]["provider"] == "Electric Implosion Compressor"
               for c in diamond["children"])
