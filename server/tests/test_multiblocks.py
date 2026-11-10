import gzip
import json

import pytest

from gcm import commands, gamedata, multiblocks, nbt, state, store
from conftest import login_as

EBF = "gregtech:gt.blockmachines:1000"
ASSLINE = "gregtech:gt.blockmachines:1170"
CUPRONICKEL = "gregtech:gt.blockcasings5:0"
KANTHAL = "gregtech:gt.blockcasings5:1"
HEATPROOF = "gregtech:gt.blockcasings:11"
BORO = "bartworks:BW_TieredGlass:0"
TITANIUM_GLASS = "bartworks:BW_TieredGlass:1"
CASING = "gregtech:gt.blockcasings2:0"
NANO_FORGE = "gregtech:gt.blockmachines:357"
NANO_CASING = "gregtech:gt.blockcasings8:10"
LSC = "gregtech:gt.blockmachines:13106"
LAPOTRONIC = "kekztech:kekztech_lapotronicenergyunit_block:6"

# As Structures.java writes it: the EBF's coil channel swaps blocks; the
# assembly line's glass comes with its length, so it has every combo; the
# Nano Forge's tier is the projector's stack size, and adds a part.
STRUCTURES = {
    "format": 1,
    "names": {
        EBF: "Electric Blast Furnace", ASSLINE: "Assembly Line", CUPRONICKEL: "Cupronickel Coil Block",
        KANTHAL: "Kanthal Coil Block", HEATPROOF: "Heat Proof Machine Casing", BORO: "Borosilicate Glass Block",
        TITANIUM_GLASS: "Titanium Reinforced Borosilicate Glass Block", CASING: "Solid Steel Machine Casing",
        NANO_FORGE: "Nano Forge", NANO_CASING: "Radiation Proof Machine Casing",
        LSC: "Lapotronic Supercapacitor", LAPOTRONIC: "Empty Capacitor",
    },
    "multiblocks": {
        EBF: {
            "channels": [{"name": "coil", "min": 1, "max": 2}],
            "base": {CUPRONICKEL: 16, HEATPROOF: 16},
            "tiers": {"coil": {"1": {}, "2": {CUPRONICKEL: -16, KANTHAL: 16}}},
        },
        ASSLINE: {
            "channels": [{"name": "glass", "min": 1, "max": 2}, {"name": "length", "min": 5, "max": 6}],
            "base": {BORO: 1, CASING: 2},
            "tiers": {
                "glass": {"1": {}, "2": {BORO: -1, TITANIUM_GLASS: 1}},
                "length": {"5": {BORO: 8, CASING: 13}, "6": {BORO: 10, CASING: 16}},
            },
            "combos": {
                "glass=1,length=5": {BORO: 9, CASING: 15},
                "glass=1,length=6": {BORO: 11, CASING: 18},
                "glass=2,length=5": {TITANIUM_GLASS: 9, CASING: 15},
                "glass=2,length=6": {TITANIUM_GLASS: 11, CASING: 18},
            },
        },
        NANO_FORGE: {
            "channels": [{"name": "tier", "min": 1, "max": 2, "stack": True}],
            "base": {NANO_CASING: 100},
            "tiers": {"tier": {"1": {}, "2": {NANO_CASING: 40, CASING: 12}}},
        },
        # Too many combinations to build: its height adds glass, which the
        # glass channel's swap has to cover too.
        LSC: {
            "channels": [{"name": "tier", "min": 1, "max": 2, "stack": True}, {"name": "glass", "min": 1, "max": 2}],
            "base": {BORO: 4, LAPOTRONIC: 2},
            "tiers": {
                "tier": {"1": {}, "2": {BORO: 4, LAPOTRONIC: 2}},
                "glass": {"1": {}, "2": {BORO: -4, TITANIUM_GLASS: 4}},
            },
            "approximate": True,
        },
    },
}


@pytest.fixture()
def structures(tmp_path, monkeypatch):
    path = tmp_path / multiblocks.FILE
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(STRUCTURES, f)
    monkeypatch.setattr(gamedata, "selected_version", lambda: "2.9.0")
    monkeypatch.setattr(gamedata, "bundle_file",
                        lambda version, name: str(tmp_path / name) if (tmp_path / name).exists() else None)
    multiblocks.reset()
    with state.network_lock:
        state.network["items"] = [
            {"mod": "gregtech", "internal": "gt.blockcasings5", "damage": 1, "kind": "item", "name": "Kanthal Coil Block",
             "size": 5, "isCraftable": True},
            {"mod": "gregtech", "internal": "gt.blockcasings", "damage": 11, "kind": "item",
             "name": "Heat Proof Machine Casing", "size": 0, "isCraftable": True},
        ]
    return path


def rows(kit):
    return {r["key"]: r["need"] for r in kit["rows"]}


def test_lowest_tiers_without_a_choice(structures):
    kit = multiblocks.kit(EBF)
    # The controller too, though the builds leave it out, and first.
    assert rows(kit) == {EBF: 1, CUPRONICKEL: 16, HEATPROOF: 16}
    assert kit["rows"][0]["key"] == EBF and kit["rows"][0]["name"] == "Electric Blast Furnace"
    assert kit["controller"]["name"] == "Electric Blast Furnace"
    [coil] = kit["channels"]
    assert coil["kind"] == "block" and coil["tier"] == 1
    assert [o["label"] for o in coil["options"]] == ["Cupronickel Coil Block", "Kanthal Coil Block"]


def test_a_tier_swaps_the_block_and_count_multiplies(structures):
    kit = multiblocks.kit(EBF, {"coil": "2"}, 3)
    assert rows(kit) == {EBF: 3, KANTHAL: 48, HEATPROOF: 48}
    kanthal = next(r for r in kit["rows"] if r["key"] == KANTHAL)
    assert (kanthal["have"], kanthal["craftable"], kanthal["damage"]) == (5, True, 1)
    assert kanthal["item"] == "gregtech|gt.blockcasings5|1|item"


def test_tiers_out_of_range_are_clamped(structures):
    assert rows(multiblocks.kit(EBF, {"coil": 99})) == {EBF: 1, KANTHAL: 16, HEATPROOF: 16}
    assert rows(multiblocks.kit(EBF, {"coil": "x"})) == {EBF: 1, CUPRONICKEL: 16, HEATPROOF: 16}


def test_interacting_channels_come_from_combos(structures):
    kit = multiblocks.kit(ASSLINE, {"glass": 2, "length": 6})
    # Base plus the changes would leave Borosilicate glass in.
    assert rows(kit) == {ASSLINE: 1, TITANIUM_GLASS: 11, CASING: 18}
    glass, length = kit["channels"]
    assert glass["kind"] == "block" and length["kind"] == "size"
    assert [o["label"] for o in length["options"]] == ["5", "6"]


def test_a_stack_size_tier_is_numbered_not_named_after_what_it_adds(structures):
    kit = multiblocks.kit(NANO_FORGE, {"tier": 2})
    assert rows(kit) == {NANO_FORGE: 1, NANO_CASING: 140, CASING: 12}
    [tier] = kit["channels"]
    assert tier["kind"] == "size" and tier["tier"] == 2
    assert [o["label"] for o in tier["options"]] == ["1", "2"]
    saved = {"tiers": {"tier": 2}}
    assert multiblocks.preferred_tiers(NANO_FORGE, saved) == {"tier": 2}


def test_without_combos_a_swap_covers_what_the_other_channels_add(structures):
    kit = multiblocks.kit(LSC, {"tier": 2, "glass": 2})
    assert rows(kit) == {LSC: 1, TITANIUM_GLASS: 8, LAPOTRONIC: 4}
    assert kit["approximate"]
    assert rows(multiblocks.kit(LSC, {"tier": 2})) == {LSC: 1, BORO: 8, LAPOTRONIC: 4}


def test_no_kit_without_the_game_data(structures, monkeypatch):
    assert multiblocks.kit("gregtech:gt.blockmachines:1") is None
    monkeypatch.setattr(gamedata, "bundle_file", lambda version, name: None)
    assert multiblocks.kit(EBF) is None
    assert not multiblocks.available("gregtech", "gt.blockmachines", 1000)


def test_kit_route(client, structures):
    res = client.get(f"/api/multiblocks/kit?key={EBF}&tier.coil=2&count=2")
    assert res.status_code == 200
    assert rows(res.get_json()) == {EBF: 2, KANTHAL: 32, HEATPROOF: 32}
    assert client.get("/api/multiblocks/kit?key=nope").status_code == 404
    assert client.get(f"/api/multiblocks/kit?key={EBF}&count=x").status_code == 400
    listed = client.get("/api/multiblocks").get_json()["multiblocks"]
    assert [m["name"] for m in listed] == ["Assembly Line", "Electric Blast Furnace", "Lapotronic Supercapacitor", "Nano Forge"]


def test_item_history_says_which_items_have_a_kit(client, structures):
    res = client.get("/api/network/history?mod=gregtech&internal=gt.blockmachines&damage=1000")
    assert res.get_json()["multiblock"] is True
    res = client.get("/api/network/history?mod=gregtech&internal=gt.blockmachines&damage=1001")
    assert res.get_json()["multiblock"] is False


def test_kit_reports_whether_a_plan_preview_is_available(client, structures):
    def fetched():
        return client.get(f"/api/multiblocks/kit?key={EBF}").get_json()

    with state.patterns_lock:
        state.patterns["terminal"] = False
        state.patterns["updated_at"] = None
    kit = fetched()
    assert not kit["plan_available"] and kit["needs_terminal"]
    assert "needs an ME Interface Terminal" in kit["plan_unavailable_reason"]
    with state.patterns_lock:
        state.patterns["terminal"] = True
    assert "Reading your patterns" in fetched()["plan_unavailable_reason"]
    with state.patterns_lock:
        state.patterns["updated_at"] = 123
    kit = fetched()
    assert kit["plan_available"] and kit["plan_unavailable_reason"] is None
    assert not kit["needs_terminal"]


def test_picker_lists_only_stored_or_craftable_controllers(client, structures):
    def listed():
        return client.get("/api/multiblocks?network=1").get_json()["multiblocks"]

    assert listed() == []
    ebf = {"mod": "gregtech", "internal": "gt.blockmachines", "damage": 1000, "size": 1}
    assembly = dict(ebf, damage=1170, size=0, isCraftable=True)
    with state.network_lock:
        state.network["items"] += [ebf, assembly]
    assert [m["key"] for m in listed()] == [ASSLINE, EBF]

    # Empty entries, fluids and NBT variants don't provide a plain controller.
    with state.network_lock:
        state.network["items"] = [dict(ebf, size=0), dict(assembly, isCraftable=False),
                                  dict(ebf, kind="fluid"), dict(ebf, variant="named")]
    assert listed() == []
    # The full catalog and direct kit links still work without a controller in stock.
    assert len(client.get("/api/multiblocks").get_json()["multiblocks"]) == 4
    assert client.get(f"/api/multiblocks/kit?key={EBF}").status_code == 200


BATCH = {"requests": [
    {"label": "Kanthal Coil Block", "mod": "gregtech", "internal": "gt.blockcasings5", "damage": 1, "amount": 11},
    {"label": "Heat Proof Machine Casing", "mod": "gregtech", "internal": "gt.blockcasings", "damage": 11, "amount": 16},
]}


def test_saved_kit_choices_are_per_account_and_multiblock(client, flask_app, structures):
    login_as(client, "alice", role="operator")
    payload = {"key": EBF, "tiers": {"coil": 2}, "separate": True}
    assert client.put("/api/multiblocks/preferences", json=payload).status_code == 200
    # A different browser session for the same account gets the same defaults.
    other = flask_app.test_client()
    login_as(other, "alice", role="operator")
    kit = other.get(f"/api/multiblocks/kit?key={EBF}").get_json()
    assert kit["count"] == 1 and kit["channels"][0]["tier"] == 2 and kit["preferred_separate"]
    assert "count" not in store.kit_preferences.get("alice", EBF)
    assert other.get(f"/api/multiblocks/kit?key={ASSLINE}").get_json()["count"] == 1
    # Explicit configuration changes override the saved defaults.
    kit = other.get(f"/api/multiblocks/kit?key={EBF}&tier.coil=1&count=2").get_json()
    assert kit["count"] == 2 and kit["channels"][0]["tier"] == 1
    login_as(other, "bob", role="operator")
    kit = other.get(f"/api/multiblocks/kit?key={EBF}").get_json()
    assert kit["count"] == 1 and kit["channels"][0]["tier"] == 1 and not kit["preferred_separate"]
    anonymous = flask_app.test_client().get(f"/api/multiblocks/kit?key={EBF}").get_json()
    assert anonymous["count"] == 1 and anonymous["channels"][0]["tier"] == 1
    assert store.users.delete("alice")
    assert store.kit_preferences.get("alice", EBF) is None


def test_saved_materials_follow_block_identity_and_removed_materials_fall_back(structures):
    saved = {"tiers": {"coil": 1}, "blocks": {"coil": KANTHAL}}
    assert multiblocks.kit(EBF, multiblocks.preferred_tiers(EBF, saved))["channels"][0]["tier"] == 2
    saved["blocks"]["coil"] = "removed:coil:0"
    assert multiblocks.kit(EBF, multiblocks.preferred_tiers(EBF, saved))["channels"][0]["tier"] == 1
    assert multiblocks.preferred_tiers("missing:controller:0", saved) == {}
    sizes = {"tiers": {"length": 9000}}
    line = multiblocks.kit(ASSLINE, multiblocks.preferred_tiers(ASSLINE, sizes))
    assert line["channels"][1]["tier"] == 6


def test_saving_kit_choices_requires_an_operator_and_valid_configuration(client, structures):
    payload = {"key": EBF, "tiers": {"coil": 1}, "separate": False}
    assert client.put("/api/multiblocks/preferences", json=payload).status_code == 401
    login_as(client, "viewer", role="viewer")
    assert client.put("/api/multiblocks/preferences", json=payload).status_code == 403
    login_as(client, "operator", role="operator")
    for change in ({"tiers": []}, {"tiers": {"coil": "2"}}, {"separate": "yes"}, {"key": []}):
        assert client.put("/api/multiblocks/preferences", json={**payload, **change}).status_code == 400
    assert client.put("/api/multiblocks/preferences", json={**payload, "key": "missing:block:0"}).status_code == 404
    assert store.kit_preferences.get("operator", EBF) is None


def test_batch_request_queues_each(client):
    login_as(client, "usr_operator", role="operator")
    res = client.post("/api/craft/request-batch", json=BATCH)
    assert res.status_code == 200
    queued = commands.craft_requests.select(lambda r: True)
    assert sorted((r["label"], r["amount"]) for r in queued) == [
        ("Heat Proof Machine Casing", 16), ("Kanthal Coil Block", 11)]
    assert len(res.get_json()["ids"]) == 2


def test_batch_request_is_all_or_nothing(client):
    login_as(client, "usr_operator", role="operator")
    bad = {"requests": [BATCH["requests"][0], dict(BATCH["requests"][1], amount=0)]}
    res = client.post("/api/craft/request-batch", json=bad)
    assert res.status_code == 400
    assert "Heat Proof Machine Casing" in res.get_json()["error"]
    assert commands.craft_requests.select(lambda r: True) == []
    assert client.post("/api/craft/request-batch", json={"requests": []}).status_code == 400


def test_batch_request_needs_an_operator(client):
    assert client.post("/api/craft/request-batch", json=BATCH).status_code == 401
    login_as(client, "usr_viewer", role="viewer")
    assert client.post("/api/craft/request-batch", json=BATCH).status_code == 403


def test_a_display_name_tag_reads_back_and_is_always_the_same():
    tag = nbt.display_name_hex("Electric Blast Furnace kit")
    assert nbt.parse_hex(tag) == {"display": (nbt.COMPOUND, {"Name": (nbt.STRING, "Electric Blast Furnace kit")})}
    assert tag == nbt.display_name_hex("Electric Blast Furnace kit")


def kit_payload(**amounts):
    return {"key": EBF, "count": 1, "tiers": {"coil": 2}, "amounts": amounts}


@pytest.fixture()
def kit_slots():
    with state.crafts_lock:
        state.crafts["kit_slots"] = 1


def test_a_kit_is_queued_as_one_request(client, structures, kit_slots, api_headers):
    login_as(client, "usr_operator", role="operator")
    # Kanthal: 5 in stock of 16, 11 to craft. AE2 takes a pattern's inputs
    # from stock first, so the input is 5 + 11. The casings: 10 of 16, a
    # wall shared.
    res = client.post("/api/craft/request-kit", json=kit_payload(**{KANTHAL: 11, HEATPROOF: 10}))
    assert res.status_code == 200
    [req] = commands.craft_requests.select(lambda r: True)
    assert (req["label"], req["amount"], req["mod"], req["internal"], req["damage"]) == (
        "Electric Blast Furnace kit", 1, "gregtech", "gt.blockmachines", 1000)
    assert nbt.parse_hex(req["tag"])["display"][1]["Name"][1] == "Electric Blast Furnace kit"
    assert req["kit_inputs"] == [
        {"name": "gregtech:gt.blockcasings", "damage": 11, "size": 10},
        {"name": "gregtech:gt.blockcasings5", "damage": 1, "size": 16},
    ]
    # The game gets the blocks; the page doesn't need them.
    [pending] = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
    assert pending["kit_inputs"] == req["kit_inputs"] and pending["tag"] == req["tag"]
    client.post(f"/api/craft/requests/{pending['id']}/result", headers=api_headers,
                json={"status": "failed", "reason": "no kit slot"})
    [mine] = client.get("/api/craft/requests").get_json()["requests"]
    assert "kit_inputs" not in mine and "tag" not in mine


def test_a_block_with_more_in_stock_than_the_kit_needs_is_still_crafted(client, structures, kit_slots):
    # 40 Heat Proof Casings in stock, 16 needed, 4 more asked for: the kit
    # asks for all 40 and 4, so AE2 takes the 40 and crafts the 4.
    with state.network_lock:
        state.network["items"][1]["size"] = 40
    login_as(client, "usr_operator", role="operator")
    assert client.post("/api/craft/request-kit", json=kit_payload(**{HEATPROOF: 4})).status_code == 200
    [req] = commands.craft_requests.select(lambda r: True)
    assert req["kit_inputs"] == [{"name": "gregtech:gt.blockcasings", "damage": 11, "size": 44}]


def test_a_kit_offers_a_name_per_kit_slot_and_takes_the_one_the_game_used(client, structures, api_headers):
    with state.crafts_lock:
        state.crafts["kit_slots"] = 3
    login_as(client, "usr_operator", role="operator")
    assert client.post("/api/craft/request-kit", json=kit_payload(**{HEATPROOF: 16})).status_code == 200
    [req] = commands.craft_requests.select(lambda r: True)
    labels = ["Electric Blast Furnace kit", "Electric Blast Furnace kit #2", "Electric Blast Furnace kit #3"]
    assert [n["label"] for n in req["kit_names"]] == labels
    assert [nbt.parse_hex(n["tag"])["display"][1]["Name"][1] for n in req["kit_names"]] == labels
    assert req["tag"] == req["kit_names"][0]["tag"]

    [pending] = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
    client.post(f"/api/craft/requests/{pending['id']}/result", headers=api_headers,
                json={"status": "failed", "reason": "missing blocks", "label": "Electric Blast Furnace kit #2"})
    [mine] = client.get("/api/craft/requests").get_json()["requests"]
    assert mine["label"] == "Electric Blast Furnace kit #2" and "kit_names" not in mine
    assert store.requests.find("craft", pending["id"], "usr_operator")["label"] == "Electric Blast Furnace kit #2"


def test_a_label_the_kit_didnt_offer_is_ignored(client, structures, kit_slots, api_headers):
    login_as(client, "usr_operator", role="operator")
    client.post("/api/craft/request-kit", json=kit_payload(**{HEATPROOF: 16}))
    [pending] = client.get("/api/craft/requests/pending", headers=api_headers).get_json()["requests"]
    client.post(f"/api/craft/requests/{pending['id']}/result", headers=api_headers,
                json={"status": "failed", "reason": "no", "label": "Something else"})
    [mine] = client.get("/api/craft/requests").get_json()["requests"]
    assert mine["label"] == "Electric Blast Furnace kit"
    assert store.requests.find("craft", pending["id"], "usr_operator")["label"] == "Electric Blast Furnace kit"


def test_a_block_left_at_zero_stays_out_of_the_kit(client, structures, kit_slots):
    login_as(client, "usr_operator", role="operator")
    assert client.post("/api/craft/request-kit", json=kit_payload(**{HEATPROOF: 16})).status_code == 200
    [req] = commands.craft_requests.select(lambda r: True)
    assert req["kit_inputs"] == [{"name": "gregtech:gt.blockcasings", "damage": 11, "size": 16}]


def test_a_kit_needs_a_kit_slot_in_the_game(client, structures):
    login_as(client, "usr_operator", role="operator")
    res = client.post("/api/craft/request-kit", json=kit_payload(**{HEATPROOF: 16}))
    assert res.status_code == 409
    assert "on its own" in res.get_json()["error"]
    assert commands.craft_requests.select(lambda r: True) == []


def test_bad_kit_requests(client, structures, kit_slots):
    def post(payload):
        return client.post("/api/craft/request-kit", json=payload)
    assert post(kit_payload(**{HEATPROOF: 16})).status_code == 401
    login_as(client, "usr_viewer", role="viewer")
    assert post(kit_payload(**{HEATPROOF: 16})).status_code == 403
    login_as(client, "usr_operator", role="operator")
    assert post(kit_payload()).status_code == 400
    assert post(kit_payload(**{HEATPROOF: -1})).status_code == 400
    assert post(kit_payload(**{HEATPROOF: 1.5})).status_code == 400
    # Cupronickel (coil tier 1) isn't in the network, so it has no pattern.
    assert post({**kit_payload(**{CUPRONICKEL: 16}), "tiers": {}}).status_code == 400
    assert post({**kit_payload(**{HEATPROOF: 16}), "key": "nope"}).status_code == 404
    assert post({"key": EBF}).status_code == 400
    assert commands.craft_requests.select(lambda r: True) == []


def test_the_game_reports_its_kit_slots(client, structures, api_headers):
    def report(**extra):
        client.post("/api/crafts", headers=api_headers, json={"jobs": [], **extra})
        return client.get(f"/api/multiblocks/kit?key={EBF}").get_json()["kit_slots"]
    assert report() == 0
    assert report(kit_slots=2) == 2
    assert report(kit_slots="2") == 0
    assert state.kit_slots() == 0


def test_mcp_kit_by_name_with_a_block_tier(client, structures):
    from test_mcp import ok, make_token
    token = make_token("mia", role="viewer")
    kit = ok(client, token, "multiblock_kit", multiblock="electric blast furnace",
             tiers={"coil": "Kanthal Coil Block"}, count=2)
    assert kit["multiblock"] == "Electric Blast Furnace"
    assert {b["name"]: b["need"] for b in kit["blocks"]} == {
        "Electric Blast Furnace": 2, "Kanthal Coil Block": 32, "Heat Proof Machine Casing": 32}
    [coil] = kit["channels"]
    assert coil["chosen"] == "Kanthal Coil Block"
    assert coil["options"] == ["1: Cupronickel Coil Block", "2: Kanthal Coil Block"]
    kanthal = next(b for b in kit["blocks"] if b["name"] == "Kanthal Coil Block")
    assert (kanthal["item"], kanthal["in_network"], kanthal["craftable"]) == ("gregtech|gt.blockcasings5|1|item", 5, True)


def test_mcp_kit_by_item_key_and_size(client, structures):
    from test_mcp import ok, make_token
    token = make_token("mia", role="viewer")
    kit = ok(client, token, "multiblock_kit", multiblock="gregtech|gt.blockmachines|1170|item", tiers={"length": 6})
    assert {b["name"]: b["need"] for b in kit["blocks"]} == {
        "Assembly Line": 1, "Borosilicate Glass Block": 11, "Solid Steel Machine Casing": 18}
    assert kit["channels"][1] == {"name": "length", "kind": "size", "tier": 6, "chosen": "6", "range": [5, 6]}


def test_mcp_kit_errors(client, structures):
    from test_mcp import call, make_token
    token = make_token("mia", role="viewer")
    answer, is_error = call(client, token, "multiblock_kit", multiblock="bl")
    assert is_error and "Assembly Line; Electric Blast Furnace" in answer
    answer, is_error = call(client, token, "multiblock_kit", multiblock="Electric Blast Furnace", tiers={"heat": 2})
    assert is_error and "no channel heat" in answer
    answer, is_error = call(client, token, "multiblock_kit", multiblock="Electric Blast Furnace",
                            tiers={"coil": "Gold"})
    assert is_error and "no 'Gold'" in answer


def pack(x, y, z):
    """Layouts.pack()."""
    return (x + 512) << 20 | (y + 512) << 10 | (z + 512)


def deltas(*positions):
    out, last = [], 0
    for p in sorted(pack(*xyz) for xyz in positions):
        out.append(p - last)
        last = p
    return out


# As Layouts.java writes it: the EBF's coil tier is a swap; the assembly
# line's combos are each a change from base.
LAYOUTS = {
    "format": 1, "tile": 16, "columns": 2,
    "blocks": {
        "mte:" + EBF: {"item": EBF},
        "gregtech:gt.blockcasings5:0": {"item": CUPRONICKEL, "mesh": {
            "t": [0], "p": [0, 0, 0, 256, 0, 0, 256, 256, 0, 0, 256, 0], "uv": [0, 0, 256, 0, 256, 256, 0, 256],
            "c": [0xcccccc] * 4}},
        "gregtech:gt.blockcasings5:1": {"item": KANTHAL, "faces": [1] * 6},
        "gregtech:gt.blockcasings:11": {"item": HEATPROOF, "faces": [0, 1, 0, 0, 0, 0], "tint": 0x808080},
        "bartworks:BW_TieredGlass:0": {"item": BORO, "faces": [1] * 6, "translucent": True},
        "bartworks:BW_TieredGlass:1": {"item": TITANIUM_GLASS, "faces": [1] * 6, "translucent": True},
        "gregtech:gt.blockcasings2:0": {"item": CASING, "faces": [0] * 6},
    },
    "ctm": [
        {"tiles": [2, 3, 4, 5, 6], "sprites": [0], "connect": "block"},
        {"tiles": [7, 8, 9, 10, 11], "blocks": ["bartworks:BW_TieredGlass:9"], "connect": "block"},
    ],
    "multiblocks": {
        EBF: {
            "base": {"mte:" + EBF: deltas((0, 0, 0)),
                     "gregtech:gt.blockcasings5:0": deltas((0, 1, -1), (1, 1, -1)),
                     "gregtech:gt.blockcasings:11": deltas((0, 2, -1))},
            "tiers": {"coil": {"1": {}, "2": {"swap": {"gregtech:gt.blockcasings5:0": "gregtech:gt.blockcasings5:1"}}}},
        },
        ASSLINE: {
            "base": {"bartworks:BW_TieredGlass:0": deltas((0, 1, -1)), "gregtech:gt.blockcasings2:0": deltas((0, 0, -1))},
            "tiers": {
                "glass": {"1": {}, "2": {"swap": {"bartworks:BW_TieredGlass:0": "bartworks:BW_TieredGlass:1"}}},
                "length": {"5": {}, "6": {"set": {"bartworks:BW_TieredGlass:0": deltas((0, 1, -2)),
                                                  "gregtech:gt.blockcasings2:0": deltas((0, 0, -2))}}},
            },
            "combos": {
                "glass=1,length=5": {},
                "glass=2,length=6": {"set": {"bartworks:BW_TieredGlass:1": deltas((0, 1, -1), (0, 1, -2)),
                                             "gregtech:gt.blockcasings2:0": deltas((0, 0, -2))}},
                "glass=2,length=5": {"set": {"bartworks:BW_TieredGlass:1": deltas((0, 1, -1))},
                                     "clear": deltas((0, 0, -1))},
            },
        },
    },
}


@pytest.fixture()
def layouts(structures):
    with gzip.open(structures.parent / multiblocks.LAYOUTS, "wt", encoding="utf-8") as f:
        json.dump(LAYOUTS, f)
    (structures.parent / multiblocks.ATLAS).write_bytes(b"\x89PNG\r\n\x1a\nan atlas")


def placed(layout):
    """{block key: [(x, y, z)]} of a layout."""
    return {b["key"]: [tuple(b["positions"][i:i + 3]) for i in range(0, len(b["positions"]), 3)]
            for b in layout["blocks"]}


def test_layout_at_the_lowest_tiers(layouts):
    layout = multiblocks.layout(EBF)
    assert placed(layout) == {
        "mte:" + EBF: [(0, 0, 0)],
        "gregtech:gt.blockcasings5:0": [(0, 1, -1), (1, 1, -1)],
        "gregtech:gt.blockcasings:11": [(0, 2, -1)],
    }
    by_key = {b["key"]: b for b in layout["blocks"]}
    controller = by_key["mte:" + EBF]
    assert controller["controller"] is True and "faces" not in controller
    assert controller["name"] == "Electric Blast Furnace"
    assert by_key["gregtech:gt.blockcasings:11"]["tint"] == 0x808080
    # The game's own geometry, as captured.
    assert by_key["gregtech:gt.blockcasings5:0"]["mesh"]["c"] == [0xcccccc] * 4
    # Only the connected textures for what it's built from: the coils'.
    assert [d["tiles"] for d in layout["ctm"]] == [[2, 3, 4, 5, 6]]
    assert multiblocks.layout(ASSLINE)["ctm"] == []
    assert (layout["tile"], layout["columns"]) == (16, 2)
    assert multiblocks.kit(EBF)["layout"] is True


def test_layout_tier_swaps_blocks(layouts):
    assert placed(multiblocks.layout(EBF, {"coil": 2}))["gregtech:gt.blockcasings5:1"] == [(0, 1, -1), (1, 1, -1)]


def test_layout_set_then_swap_and_combos(layouts):
    # A combo the file has: exactly that.
    assert placed(multiblocks.layout(ASSLINE, {"glass": 2, "length": 5})) == {
        "bartworks:BW_TieredGlass:1": [(0, 1, -1)]}
    # One it hasn't: each channel's set and clear, then the swaps.
    assert placed(multiblocks.layout(ASSLINE, {"glass": 1, "length": 6})) == {
        "bartworks:BW_TieredGlass:0": [(0, 1, -2), (0, 1, -1)],
        "gregtech:gt.blockcasings2:0": [(0, 0, -2), (0, 0, -1)]}
    layout = multiblocks.layout(ASSLINE, {"glass": 2, "length": 6})
    assert placed(layout)["bartworks:BW_TieredGlass:1"] == [(0, 1, -2), (0, 1, -1)]
    assert next(b for b in layout["blocks"] if b["key"].startswith("bartworks"))["translucent"] is True


def test_no_layout_without_its_file(structures):
    assert multiblocks.layout(EBF) is None
    assert multiblocks.kit(EBF)["layout"] is False


def test_layout_and_atlas_routes(client, layouts):
    res = client.get(f"/api/multiblocks/layout?key={ASSLINE}&tier.glass=2&tier.length=6")
    assert res.status_code == 200
    data = res.get_json()
    assert data["atlas"].startswith("/api/multiblocks/atlas?v=")
    assert "bartworks:BW_TieredGlass:1" in placed(data)
    assert client.get("/api/multiblocks/layout?key=nope").status_code == 404
    atlas = client.get(data["atlas"])
    assert atlas.status_code == 200 and atlas.mimetype == "image/png"
    assert atlas.data.startswith(b"\x89PNG")
