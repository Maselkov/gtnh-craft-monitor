"""dev.py's fake game only talks to the real API, so these keep it in
step with the endpoints it imitates the oc/ scripts on."""

import json
import zipfile

import dev
import pytest
from conftest import TEST_API_KEY, login_as
from gcm import oredict


@pytest.fixture()
def game(flask_app, monkeypatch):
    monkeypatch.setattr(dev, "API_KEY", TEST_API_KEY)
    return dev.FakeGame(flask_app)


def test_tick_reports_crafts_power_and_a_scan(game, client):
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
    jobs = {j["name"]: j for j in client.get("/api/crafts").get_json()["jobs"]}
    assert jobs[free]["busy"] and jobs[free]["final_output"] == "Titanium Gear"


def test_cancel_request_idles_the_cpu(game, client):
    game.tick()
    login_as(client, "usr_dev", role="operator")
    assert client.post("/api/craft/cancel", json={"cpu_name": "a00"}).status_code == 200
    game.tick()
    jobs = {j["name"]: j for j in client.get("/api/crafts").get_json()["jobs"]}
    assert not jobs["a00"]["busy"]


def test_icon_bundle_covers_every_item_but_essentia(tmp_path):
    dev.install_icons(str(tmp_path))
    bundle = tmp_path / "gamedata" / "dev"
    lookup = json.loads((bundle / "icons_lookup.json").read_text())
    with zipfile.ZipFile(bundle / "images.zip") as zf:
        names = set(zf.namelist())
    paths = list(lookup["by_key"].values()) + list(lookup["fluids_by_key"].values())
    # Essentia has none, to show the page's stand-in.
    assert len(paths) == sum(1 for it in dev.CATALOG if it["kind"] != "essentia")
    assert set(paths) <= names
    assert json.loads((tmp_path / "gamedata" / "selected.json").read_text())["version"] == "dev"


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
    oredict.load(str(path))
    try:
        diamond = plan("Block of Diamond", 100000)["root"]["children"][0]
    finally:
        oredict.load(None)
    assert diamond["name"] == "Diamond"
    assert [s["name"] for s in diamond["substitutes"]] == ["Industrial Diamond"]
    assert any(c.get("substitute") and c["pattern"]["provider"] == "Electric Implosion Compressor"
               for c in diamond["children"])
