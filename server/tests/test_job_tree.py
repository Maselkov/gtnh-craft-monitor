"""The live crafting tree of a busy CPU's job (gcm/job_tree.py) and its
routes."""

from gcm import config, job_tree, tracking
from test_patterns_api import run_pattern_scan


def item(name, damage, size=1):
    return {"kind": "item", "mod": "gregtech", "internal": "gt.metaitem.01", "damage": damage, "name": name, "size": size}


def stack(name, damage, size):
    """As craft_monitor.lua reports a CPU's item."""
    return {"name": name, "mod": "gregtech", "internal": "gt.metaitem.01", "damage": damage, "size": size}


GEAR, PLATE, BOLT, INGOT, DUST = ("Gear", 1), ("Plate", 2), ("Bolt", 3), ("Ingot", 4), ("Dust", 5)
MOLTEN = {"kind": "fluid", "internal": "molten.iron", "name": "Molten Iron", "size": 144}


def pattern(provider, slot, outputs, inputs):
    return {"provider": {"name": provider, "x": slot, "y": 0, "z": 0, "dim": 0}, "slot": slot,
            "crafting": False, "outputs": outputs, "inputs": inputs}


# Gear <- 4 plates + a bolt. Plates two ways: bent from an ingot (slot 1)
# or solidified from molten iron (slot 2, which AE2 would try first);
# bolts from an ingot.
PATTERNS = [
    pattern("Assembler", 0, [item(*GEAR)], [item(*PLATE, 4), item(*BOLT)]),
    pattern("Bender", 1, [item(*PLATE)], [item(*INGOT)]),
    pattern("Solidifier", 2, [item(*PLATE)], [dict(MOLTEN)]),
    pattern("Lathe", 3, [item(*BOLT, 4)], [item(*INGOT)]),
]


def report(client, api_headers, pending, active=(), stored=(), busy=True):
    job = {"name": "A1", "busy": busy}
    if busy:
        job.update(final_output="Gear", final_output_mod="gregtech", final_output_internal="gt.metaitem.01",
                   final_output_damage=GEAR[1], pending=list(pending), active=list(active), stored=list(stored))
    client.post("/api/crafts", headers=api_headers, json={"source": "me_controller", "jobs": [job]})


def start_job(client, api_headers):
    run_pattern_scan(client, api_headers, [PATTERNS])
    client.post("/api/crafts", headers=api_headers, json={"source": "me_controller", "jobs": [{"name": "A1", "busy": False}]})
    # 2 gears: 8 plates (the job bends them: it pulled ingots), 1 bolt
    # being made.
    report(client, api_headers,
           pending=[stack("Gear", GEAR[1], 2), stack("Plate", PLATE[1], 8)],
           active=[stack("Bolt", BOLT[1], 4)],
           stored=[stack("Ingot", INGOT[1], 9)])


def key(name_damage):
    return f"gregtech|gt.metaitem.01|{name_damage[1]}|item"


def child(node, name):
    return next(c for c in node["children"] if c["name"] == name)


class TestTree:
    def test_rebuilt_from_patterns_and_the_jobs_items(self, client, api_headers):
        start_job(client, api_headers)
        data = client.get("/api/crafts/A1/tree").get_json()
        root = data["tree"]["root"]
        assert (root["name"], root["craft"], root["step"]) == ("Gear", 2, key(GEAR))
        plate = child(root, "Plate")
        # The job's own pattern (its inputs are what it pulled), not AE2's first.
        assert (plate["status"], plate["pattern"]["provider"], plate["craft"]) == ("craft", "Bender", 8)
        assert child(plate, "Ingot")["status"] == "stock"  # from storage
        assert child(root, "Bolt")["pattern"]["provider"] == "Lathe"
        assert data["tree"]["at"][key(PLATE)] == ["0"]
        assert key(INGOT) not in data["tree"]["at"]  # never crafted here
        assert data["version"]

    def test_branches_page_like_the_plan(self, client, api_headers):
        start_job(client, api_headers)
        version = client.get("/api/crafts/A1/tree").get_json()["version"]
        node = client.get("/api/crafts/A1/tree", query_string={"path": "0", "version": version}).get_json()["node"]
        assert node["name"] == "Plate" and node["children"][0]["name"] == "Ingot"
        assert client.get("/api/crafts/A1/tree", query_string={"path": "0", "version": "old"}).get_json() == {"stale": True}
        assert client.get("/api/crafts/A1/tree", query_string={"path": "x"}).status_code == 400

    def test_nothing_to_show(self, client, api_headers):
        assert client.get("/api/crafts/A1/tree").get_json()["tree"] is None
        report(client, api_headers, pending=[stack("Gear", GEAR[1], 2)])
        assert "No pattern scan" in client.get("/api/crafts/A1/tree").get_json()["reason"]
        run_pattern_scan(client, api_headers, [PATTERNS[1:]])  # nothing makes gears
        assert "None of the network's patterns" in client.get("/api/crafts/A1/tree").get_json()["reason"]

    def test_fluid_steps_match_fluid_nodes(self):
        assert job_tree.step_item_key((None, "molten.iron", None, "Molten Iron")) == "|molten.iron||fluid"
        assert job_tree.base_key("cropsnh|genericSeed|0|item|Labc") == "cropsnh|genericSeed|0|item"


class TestSteps:
    def test_counts_and_states(self, client, api_headers, monkeypatch):
        start_job(client, api_headers)
        steps = client.get("/api/crafts/A1/steps").get_json()["steps"]
        assert steps[key(PLATE)] == {**steps[key(PLATE)], "total": 8, "left": 8, "crafting": 0, "state": "waiting"}
        assert steps[key(BOLT)]["state"] == "active"
        # Plates under way; the bolts done.
        report(client, api_headers, pending=[stack("Gear", GEAR[1], 2), stack("Plate", PLATE[1], 3)],
               active=[stack("Plate", PLATE[1], 1)])
        steps = client.get("/api/crafts/A1/steps").get_json()["steps"]
        assert (steps[key(PLATE)]["left"], steps[key(PLATE)]["crafting"], steps[key(PLATE)]["state"]) == (4, 1, "active")
        assert (steps[key(BOLT)]["left"], steps[key(BOLT)]["state"]) == (0, "done")

    def test_a_crafting_step_that_stops_moving_is_stuck(self, client, api_headers, monkeypatch):
        start_job(client, api_headers)
        moved = job_tree.steps("A1")[key(BOLT)]["moved_at"]
        # The same numbers again: nothing moved.
        report(client, api_headers, pending=[stack("Gear", GEAR[1], 2), stack("Plate", PLATE[1], 8)],
               active=[stack("Bolt", BOLT[1], 4)])
        steps = job_tree.steps("A1", now=moved + config.CRAFT_STALL_SECONDS - 1)
        assert steps[key(BOLT)]["moved_at"] == moved and steps[key(BOLT)]["state"] == "active"
        steps = job_tree.steps("A1", now=moved + config.CRAFT_STALL_SECONDS)
        assert steps[key(BOLT)]["state"] == "stuck"
        assert steps[key(PLATE)]["state"] == "waiting"  # not crafting: waiting, not stuck

    def test_idle_cpu(self, client, api_headers):
        start_job(client, api_headers)
        report(client, api_headers, pending=[], busy=False)
        assert client.get("/api/crafts/A1/steps").get_json()["steps"] is None


def test_tracking_keeps_the_first_stored_items(client, api_headers):
    start_job(client, api_headers)
    entry = tracking.state.cpu_last_known["A1"]
    assert entry["first_stored"] == {("gregtech", "gt.metaitem.01", INGOT[1], "Ingot"): 9}
    report(client, api_headers, pending=[stack("Gear", GEAR[1], 2)], stored=[stack("Plate", PLATE[1], 8)])
    assert entry["first_stored"] == {("gregtech", "gt.metaitem.01", INGOT[1], "Ingot"): 9}
