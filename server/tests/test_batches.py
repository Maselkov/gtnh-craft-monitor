import pytest

from gcm import commands, nbt, state, store
from conftest import login_as

ARM = "gregtech|gt.metaitem.01|32653|item"
CONVEYOR = "gregtech|gt.metaitem.01|32633|item"
SOLDER = "|molten.solderingalloy||fluid"
STONE = "minecraft|stone|0|item"


@pytest.fixture(autouse=True)
def network():
    with state.network_lock:
        state.network["items"] = [
            {"mod": "gregtech", "internal": "gt.metaitem.01", "damage": 32653, "kind": "item",
             "name": "Robot Arm (IV)", "size": 3, "isCraftable": True},
            {"mod": "gregtech", "internal": "gt.metaitem.01", "damage": 32633, "kind": "item",
             "name": "Conveyor Module (IV)", "size": 0, "isCraftable": True},
            {"mod": None, "internal": "molten.solderingalloy", "damage": None, "kind": "fluid",
             "name": "Molten Soldering Alloy", "size": 500, "isCraftable": True},
            {"mod": "minecraft", "internal": "stone", "damage": 0, "kind": "item", "name": "Stone",
             "size": 9, "isCraftable": False},
        ]


@pytest.fixture()
def kit_slots():
    with state.crafts_lock:
        state.crafts["kit_slots"] = 2


@pytest.fixture()
def operator(client):
    login_as(client, "usr_operator", role="operator")
    return client


def test_adding_merges_and_describes(operator):
    res = operator.post("/api/batch/add", json={"items": [{"key": ARM, "amount": 4}, {"key": CONVEYOR, "amount": 2}]})
    assert res.status_code == 200
    res = operator.post("/api/batch/add", json={"items": [{"key": ARM, "amount": 1, "from": "Assembler kit"}]})
    items = res.get_json()["items"]
    assert [(i["key"], i["amount"]) for i in items] == [(ARM, 5), (CONVEYOR, 2)]
    arm = items[0]
    assert (arm["name"], arm["have"], arm["craftable"], arm["kind"], arm["damage"]) == (
        "Robot Arm (IV)", 3, True, "item", 32653)
    # The batch is the account's, wherever it signs in from.
    assert [i["key"] for i in operator.get("/api/batch").get_json()["items"]] == [ARM, CONVEYOR]


def test_put_replaces_and_empty_clears(operator):
    operator.put("/api/batch", json={"items": [{"key": ARM, "amount": 1}]})
    res = operator.put("/api/batch", json={"items": [{"key": CONVEYOR, "amount": 7, "name": "Conveyor"}]})
    assert [(i["key"], i["amount"]) for i in res.get_json()["items"]] == [(CONVEYOR, 7)]
    assert operator.put("/api/batch", json={"items": []}).get_json()["items"] == []
    assert store.batches.get("usr_operator") == []


def test_bad_batches(operator):
    for items in (None, [{"key": "nope", "amount": 1}], [{"key": ARM, "amount": 0}],
                  [{"key": ARM, "amount": True}], [{"key": ARM + "|variant", "amount": 1}],
                  [{"key": f"gregtech|x|{i}|item", "amount": 1} for i in range(101)]):
        assert operator.put("/api/batch", json={"items": items}).status_code == 400, items
    assert operator.post("/api/batch/add", json={"items": []}).status_code == 400


def test_needs_an_operator(client):
    assert client.get("/api/batch").status_code == 401
    login_as(client, "usr_viewer", role="viewer")
    assert client.get("/api/batch").status_code == 403
    assert client.post("/api/batch/add", json={"items": [{"key": ARM, "amount": 1}]}).status_code == 403
    assert client.post("/api/craft/request-batch-job", json={"items": [{"key": ARM, "amount": 1}]}).status_code == 403


def test_a_batch_is_queued_as_one_job(operator, kit_slots, api_headers):
    operator.put("/api/batch", json={"items": [{"key": ARM, "amount": 4}]})
    res = operator.post("/api/craft/request-batch-job", json={"items": [
        {"key": ARM, "amount": 4}, {"key": CONVEYOR, "amount": 2}]})
    assert res.status_code == 200
    assert res.get_json()["label"] == "Robot Arm (IV) batch kit"
    [req] = commands.craft_requests.select(lambda r: True)
    # The output is the first item, named; the inputs are each one's
    # stock plus how many to craft, as for a kit.
    assert (req["label"], req["mod"], req["internal"], req["damage"], req["amount"]) == (
        "Robot Arm (IV) batch kit", "gregtech", "gt.metaitem.01", 32653, 1)
    assert nbt.parse_hex(req["tag"])["display"][1]["Name"][1] == "Robot Arm (IV) batch kit"
    assert [n["label"] for n in req["kit_names"]] == ["Robot Arm (IV) batch kit", "Robot Arm (IV) batch kit #2"]
    assert req["kit_inputs"] == [
        {"name": "gregtech:gt.metaitem.01", "damage": 32653, "size": 7},
        {"name": "gregtech:gt.metaitem.01", "damage": 32633, "size": 2},
    ]
    # Requested: the batch starts again.
    assert operator.get("/api/batch").get_json()["items"] == []
    # The game takes it as it takes a kit, and names the pin with it.
    [pending] = operator.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
    assert pending["kit_inputs"] == req["kit_inputs"]


def test_a_batch_is_queued_separately(operator):
    res = operator.post("/api/craft/request-batch-job", json={"separate": True, "items": [
        {"key": ARM, "amount": 4}, {"key": SOLDER, "amount": 144}]})
    assert res.status_code == 200
    queued = commands.craft_requests.select(lambda r: True)
    assert sorted((r["label"], r["amount"], r["kind"]) for r in queued) == [
        ("Molten Soldering Alloy", 144, "fluid"), ("Robot Arm (IV)", 4, "item")]
    assert all("kit_inputs" not in r for r in queued)


def test_one_job_needs_a_kit_slot(operator):
    res = operator.post("/api/craft/request-batch-job", json={"items": [{"key": ARM, "amount": 1}]})
    assert res.status_code == 409
    assert commands.craft_requests.select(lambda r: True) == []


def test_fluids_need_a_script_that_writes_them(operator, kit_slots):
    items = [{"key": ARM, "amount": 1}, {"key": SOLDER, "amount": 144}]
    res = operator.post("/api/craft/request-batch-job", json={"items": items})
    assert res.status_code == 400 and "fluids" in res.get_json()["error"]
    with state.crafts_lock:
        state.crafts["kit_fluids"] = True
    assert operator.post("/api/craft/request-batch-job", json={"items": items}).status_code == 200
    [req] = commands.craft_requests.select(lambda r: True)
    assert req["kit_inputs"][1] == {"name": "molten.solderingalloy", "damage": 0, "size": 644, "type": "fluid"}


def test_only_fluids_cant_be_one_job(operator, kit_slots):
    with state.crafts_lock:
        state.crafts["kit_fluids"] = True
    res = operator.post("/api/craft/request-batch-job", json={"items": [{"key": SOLDER, "amount": 144}]})
    assert res.status_code == 400 and "at least one item" in res.get_json()["error"]


def test_an_item_without_a_pattern_is_refused(operator, kit_slots):
    res = operator.post("/api/craft/request-batch-job", json={"items": [
        {"key": ARM, "amount": 1}, {"key": STONE, "amount": 1}]})
    assert res.status_code == 400 and "Stone" in res.get_json()["error"]
    assert commands.craft_requests.select(lambda r: True) == []


def test_the_crafts_report_says_whether_fluids_can_go_in(client, api_headers):
    client.post("/api/crafts", headers=api_headers, json={"source": "t", "jobs": [], "kit_slots": 1,
                                                           "kit_fluids": True})
    assert state.kit_fluids()
    client.post("/api/crafts", headers=api_headers, json={"source": "t", "jobs": [], "kit_slots": 1})
    assert not state.kit_fluids()
