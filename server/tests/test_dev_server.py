"""dev.py's fake game only talks to the real API, so these keep it in
step with the endpoints it imitates the oc/ scripts on."""

import json
import zipfile

import dev
import pytest
from conftest import TEST_API_KEY, login_as


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


def test_icon_bundle_covers_every_item(tmp_path):
    dev.install_icons(str(tmp_path))
    bundle = tmp_path / "gamedata" / "dev"
    lookup = json.loads((bundle / "icons_lookup.json").read_text())
    with zipfile.ZipFile(bundle / "images.zip") as zf:
        names = set(zf.namelist())
    paths = list(lookup["by_key"].values()) + list(lookup["fluids_by_key"].values())
    assert len(paths) == len(dev.CATALOG)
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


def test_patterns_give_plans_that_show_each_case(game, client):
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
    assert [a["provider"] for a in plate["alternatives"]] == ["Bending Machine T4:EV", "Fluid Solidifier {Plate}"]
    # Gear <- plate <- ingot <- molten <- ingot: the cycle is past the first
    # answer's two levels, so ask for the molten step's own.
    molten = ask("Inconel-625 Gear", 100000, path="0.0.0", version=first["version"])["node"]
    assert molten["name"] == "Molten Inconel-625"
    assert [c["status"] for c in molten["children"]] == ["cycle"]
    assert [m["name"] for m in plan("Ultimate Circuit")["missing"]] == ["Draconium Ingot"]
