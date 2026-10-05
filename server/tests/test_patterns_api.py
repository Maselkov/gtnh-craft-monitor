"""Pattern scans (gcm/patterns.py): what network_browser.lua sends, in
the shapes oc/pattern_dump.lua found on a real GTNH 2.9 network."""

from gcm import inventory, nbt, patterns, state
from conftest import login_as
from nbt_fixtures import seed_tag, tag_hex


def run_pattern_scan(client, api_headers, batches, total_errors=0):
    token = client.post("/api/network/patterns/start", headers=api_headers).get_json()["scan_token"]
    for batch in batches:
        client.post(
            "/api/network/patterns/batch",
            json={"patterns": batch, "scan_token": token},
            headers=api_headers,
        )
    return client.post(
        "/api/network/patterns/finish",
        json={"scan_token": token, "chunks_sent": len(batches), "total_errors": total_errors},
        headers=api_headers,
    )


def item(internal, label, size=0, damage=0, mod="minecraft", item_id=1, **extra):
    return {"kind": "item", "mod": mod, "internal": internal, "damage": damage, "name": label,
            "size": size, "isCraftable": False, "id": item_id, **extra}


def stack_nbt(item_id, count, damage=0):
    """A stack in a pattern's "in"/"out" list, as vanilla writes it."""
    return [("id", nbt.SHORT, item_id), ("Count", nbt.BYTE, count), ("Damage", nbt.SHORT, damage)]


def pattern_tag(inputs, outputs, crafting=True, substitute=False):
    return tag_hex([
        ("in", nbt.LIST, (nbt.COMPOUND, inputs)),
        ("out", nbt.LIST, (nbt.COMPOUND, outputs)),
        ("crafting", nbt.BYTE, 1 if crafting else 0),
        ("substitute", nbt.BYTE, 1 if substitute else 0),
    ])


PROVIDER = {"name": "Molecular Assembler", "x": -1413, "y": 57, "z": -123, "dim": 0}
LOG = item("log", "Oak Wood", item_id=17)
PLANKS = item("planks", "Oak Wood Planks", item_id=5)

# Logs -> 4 planks, as OC reports a crafting pattern: every size 0.
PLANKS_PATTERN = {
    "provider": PROVIDER,
    "slot": 0,
    "crafting": True,
    "inputs": [LOG],
    "outputs": [PLANKS],
    "tag": pattern_tag([stack_nbt(17, 1)] + [[]] * 8, [stack_nbt(5, 4)]),
}

# A GT pattern hatch's processing pattern: real sizes, item to fluid.
NEODYMIUM_PATTERN = {
    "provider": {"name": "Fluid Extractor p1", "x": 1, "y": 2, "z": 3, "dim": 0},
    "slot": 0,
    "crafting": False,
    "inputs": [item("gt.metaitem.01", "Neodymium Ingot", 16, damage=11067, mod="gregtech", item_id=7444)],
    "outputs": [{"kind": "fluid", "internal": "molten.neodymium", "name": "Molten Neodymium",
                 "size": 2304, "isCraftable": False}],
}


def scanned(client):
    login_as(client, "usr_alice")
    return client.get("/api/network/patterns").get_json()


class TestScan:
    def test_scan_needs_the_api_key(self, client):
        assert client.post("/api/network/patterns/start").status_code == 401
        assert client.post("/api/network/patterns/batch", json={"patterns": []}).status_code == 401

    def test_reading_patterns_needs_a_sign_in(self, client):
        # They say where each machine is in the world.
        assert client.get("/api/network/patterns").status_code == 401

    def test_a_scan_replaces_the_last_one(self, client, api_headers):
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN], [NEODYMIUM_PATTERN]])
        res = run_pattern_scan(client, api_headers, [[NEODYMIUM_PATTERN]])
        assert res.get_json() == {"ok": True, "pattern_count": 1}
        data = scanned(client)
        assert [p["provider"]["name"] for p in data["patterns"]] == ["Fluid Extractor p1"]
        assert data["in_progress"] is False
        assert data["summary"] == {"patterns": 1, "crafting": 0, "processing": 1, "inexact": 0, "providers": 1}

    def test_an_incomplete_scan_keeps_the_last_one(self, client, api_headers):
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])
        res = run_pattern_scan(client, api_headers, [[NEODYMIUM_PATTERN]], total_errors=1)
        assert res.get_json()["rejected"] is True
        token = client.post("/api/network/patterns/start", headers=api_headers).get_json()["scan_token"]
        client.post("/api/network/patterns/batch", json={"patterns": [NEODYMIUM_PATTERN], "scan_token": token},
                    headers=api_headers)
        res = client.post("/api/network/patterns/finish",
                          json={"scan_token": token, "chunks_sent": 2, "total_errors": 0}, headers=api_headers)
        assert res.get_json()["rejected"] is True
        assert [p["provider"]["name"] for p in scanned(client)["patterns"]] == ["Molecular Assembler"]

    def test_batches_from_an_old_scan_are_refused(self, client, api_headers):
        old = client.post("/api/network/patterns/start", headers=api_headers).get_json()["scan_token"]
        client.post("/api/network/patterns/start", headers=api_headers)
        res = client.post("/api/network/patterns/batch", json={"patterns": [PLANKS_PATTERN], "scan_token": old},
                          headers=api_headers)
        assert res.get_json() == {"ok": False, "error": "stale_scan_token"}
        res = client.post("/api/network/patterns/finish",
                          json={"scan_token": old, "chunks_sent": 0, "total_errors": 0}, headers=api_headers)
        assert res.get_json()["ok"] is False

    def test_a_restart_reloads_the_last_scan(self, client, api_headers):
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN, NEODYMIUM_PATTERN]])
        before = scanned(client)
        state.reset()
        patterns.load_snapshot()
        after = scanned(client)
        assert after["patterns"] == before["patterns"]
        assert after["updated_at"] == before["updated_at"]

    def test_unchanged_patterns_answer_304(self, client, api_headers):
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])
        login_as(client, "usr_alice")
        etag = client.get("/api/network/patterns").headers["ETag"]
        assert client.get("/api/network/patterns", headers={"If-None-Match": etag}).status_code == 304
        run_pattern_scan(client, api_headers, [[PLANKS_PATTERN]])
        assert client.get("/api/network/patterns", headers={"If-None-Match": etag}).status_code == 200


def entry(e):
    """An entry without its icon (the test game data may or may not have one)."""
    return {k: v for k, v in e.items() if k != "icon"}


class TestNormalize:
    def test_crafting_sizes_come_from_the_pattern_nbt(self):
        p = patterns.normalize(PLANKS_PATTERN)
        assert p["crafting"] is True
        assert p["exact"] is True
        assert p["substitute"] is False
        assert [entry(e) for e in p["inputs"]] == [
            {"mod": "minecraft", "internal": "log", "damage": 0, "kind": "item", "name": "Oak Wood", "size": 1}
        ]
        assert [(e["name"], e["size"]) for e in p["outputs"]] == [("Oak Wood Planks", 4)]
        assert p["provider"] == PROVIDER

    def test_repeated_inputs_are_merged(self):
        # The LV conveyor module: 6 rubber sheets, 2 motors, 1 cable.
        sheet = item("gt.metaitem.01", "Silicone Rubber Sheet", damage=17471, mod="gregtech", item_id=7444)
        motor = item("gt.metaitem.01", "Electric Motor (LV)", damage=32600, mod="gregtech", item_id=7444)
        cable = item("gt.blockmachines", "1x Tin Cable", damage=1246, mod="gregtech", item_id=2425)
        grid = [sheet, sheet, sheet, motor, cable, motor, sheet, sheet, sheet]
        nbt_grid = [stack_nbt(7444, 1, e["damage"]) if e is not cable else stack_nbt(2425, 1, 1246)
                    for e in grid]
        p = patterns.normalize({
            "provider": PROVIDER, "crafting": True, "inputs": grid,
            "outputs": [item("gt.metaitem.01", "Conveyor Module (LV)", damage=32630, mod="gregtech", item_id=7444)],
            "tag": pattern_tag(nbt_grid, [stack_nbt(7444, 1, 32630)]),
        })
        assert sorted((e["name"], e["size"]) for e in p["inputs"]) == [
            ("1x Tin Cable", 1), ("Electric Motor (LV)", 2), ("Silicone Rubber Sheet", 6),
        ]
        assert p["outputs"][0]["size"] == 1

    def test_a_crafting_pattern_without_its_nbt(self):
        # allowItemStackNBTTags off: inputs still count by grid slot, the
        # output's count is unknown.
        p = patterns.normalize({**PLANKS_PATTERN, "inputs": [LOG, LOG], "tag": None})
        assert [e["size"] for e in p["inputs"]] == [2]
        assert [e["size"] for e in p["outputs"]] == [None]
        assert p["exact"] is False
        assert "substitute" not in p

    def test_an_unreadable_nbt_counts_as_missing(self):
        p = patterns.normalize({**PLANKS_PATTERN, "tag": "not hex"})
        assert p["outputs"][0]["size"] is None

    def test_cnt_is_read_when_count_is_zero(self):
        out = [("id", nbt.SHORT, 5), ("Count", nbt.BYTE, 0), ("Cnt", nbt.INT, 300), ("Damage", nbt.SHORT, 0)]
        p = patterns.normalize({**PLANKS_PATTERN, "tag": pattern_tag([stack_nbt(17, 1)], [out])})
        assert p["outputs"][0]["size"] == 300

    def test_processing_sizes_are_kept(self):
        p = patterns.normalize(NEODYMIUM_PATTERN)
        assert [(e["name"], e["size"], e["kind"]) for e in p["inputs"] + p["outputs"]] == [
            ("Neodymium Ingot", 16, "item"), ("Molten Neodymium", 2304, "fluid"),
        ]
        assert p["exact"] is True

    def test_ae2fc_fluid_drops_become_fluids(self):
        drop = item("fluid_drop", "drop of Molten Glass", 144, mod="ae2fc", item_id=4489, hasTag=True,
                    tag=tag_hex([("Fluid", nbt.STRING, "molten.glass")]))
        p = patterns.normalize({**NEODYMIUM_PATTERN, "inputs": [drop]})
        assert [entry(e) for e in p["inputs"]] == [
            {"mod": None, "internal": "molten.glass", "damage": None, "kind": "fluid",
             "name": "Molten Glass", "size": 144}
        ]

    def test_a_fluid_drop_without_nbt_stays_an_item(self):
        drop = item("fluid_drop", "drop of Molten Glass", 144, mod="ae2fc", item_id=4489, hasTag=True)
        p = patterns.normalize({**NEODYMIUM_PATTERN, "inputs": [drop]})
        assert p["inputs"][0]["kind"] == "item"

    def test_essentia(self):
        ordo = {"kind": "essentia", "internal": "ordo", "name": "Ordo", "size": 1}
        p = patterns.normalize({**NEODYMIUM_PATTERN, "inputs": [ordo]})
        assert entry(p["inputs"][0]) == {"mod": None, "internal": "ordo", "damage": None, "kind": "essentia",
                                         "name": "Ordo", "size": 1}

    def test_nbt_variants_get_the_network_tabs_key(self):
        tag = seed_tag("wheat", 10, 5, 1)
        seed = item("itemCropSeed", "Wheat Seeds", 1, mod="IC2", item_id=4000, hasTag=True, tag=tag)
        p = patterns.normalize({**NEODYMIUM_PATTERN, "inputs": [seed]})
        in_network = {"mod": "IC2", "internal": "itemCropSeed", "damage": 0, "kind": "item",
                      "name": "Wheat Seeds", "hasTag": True, "tag": tag}
        inventory.assign_variant(in_network)
        assert p["inputs"][0]["variant"] == in_network["variant"]
        assert p["inputs"][0]["variant_name"] == "Gr 10 · Ga 5 · Re 1"

    def test_junk_is_dropped_not_raised(self):
        p = patterns.normalize({"provider": "nope", "inputs": [5, {"name": "no internal"}], "outputs": "x"})
        assert p["inputs"] == [] and p["outputs"] == []
        assert p["provider"] == {"name": "?"}
