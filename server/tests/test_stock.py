import pytest

from gcm import commands, config, push, store
from conftest import login_as


IRON = {"label": "Iron Ingot", "mod": "minecraft", "internal": "iron_ingot", "damage": 0, "kind": "item"}
GOLD = {"label": "Gold Ingot", "mod": "minecraft", "internal": "gold_ingot", "damage": 0, "kind": "item"}


def scanned(item, size, craftable=True):
    return {"name": item["label"], "mod": item["mod"], "internal": item["internal"],
            "damage": item["damage"], "size": size, "isCraftable": craftable}


def scan(client, api_headers, *items):
    token = client.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
    client.post("/api/network/scan/batch", json={"items": list(items), "scan_token": token}, headers=api_headers)
    res = client.post("/api/network/scan/finish",
                      json={"scan_token": token, "chunks_sent": 1, "total_errors": 0}, headers=api_headers)
    assert res.get_json().get("rejected") is None


def cpus(client, api_headers, idle=3, busy=()):
    """A crafting-CPU report: `idle` idle CPUs, plus one busy CPU making
    each item in `busy`."""
    jobs = [{"name": f"I{n}", "busy": False} for n in range(idle)]
    for n, item in enumerate(busy):
        jobs.append({"name": f"B{n}", "busy": True, "final_output": item["label"],
                     "final_output_mod": item["mod"], "final_output_internal": item["internal"],
                     "final_output_damage": item["damage"]})
    client.post("/api/crafts", json={"source": "me_controller", "jobs": jobs}, headers=api_headers)


def rules(client):
    return client.get("/api/stock/rules").get_json()


def auto_requests():
    return commands.craft_requests.select(lambda r: r.get("source") == "auto")


@pytest.fixture()
def pushes(monkeypatch):
    calls = []
    monkeypatch.setattr(push, "notify_users", lambda user_ids, payload: calls.append((list(user_ids), payload)))
    return calls


@pytest.fixture()
def operator(client, api_headers):
    """Signed in as an operator, with iron and gold scanned as craftable."""
    scan(client, api_headers, scanned(IRON, 500), scanned(GOLD, 500))
    login_as(client, "olive", role="operator")
    return client


def set_target(client, item=IRON, keep=100, refill=300, **extra):
    return client.post("/api/stock/target", json={**item, "keep_at_least": keep, "refill_to": refill, **extra})


class TestAlerts:
    def test_fires_once_then_rearms_after_recovering(self, client, api_headers, pushes):
        login_as(client, "alice")
        assert client.post("/api/stock/alert", json={**IRON, "below": 100}).status_code == 200
        scan(client, api_headers, scanned(IRON, 50))
        assert len(pushes) == 1
        user_ids, payload = pushes[0]
        assert user_ids == ["alice"]
        assert payload["body"] == "Iron Ingot is at 50 (below 100)."
        scan(client, api_headers, scanned(IRON, 40))
        assert len(pushes) == 1  # still low: no repeat
        scan(client, api_headers, scanned(IRON, 100))
        scan(client, api_headers, scanned(IRON, 10))
        assert len(pushes) == 2

    def test_item_gone_from_the_network_counts_as_none_left(self, client, api_headers, pushes):
        login_as(client, "alice")
        client.post("/api/stock/alert", json={**IRON, "below": 1})
        scan(client, api_headers, scanned(GOLD, 5))
        assert "is at 0" in pushes[0][1]["body"]

    def test_alerts_are_per_user(self, client, api_headers, pushes):
        login_as(client, "alice")
        client.post("/api/stock/alert", json={**IRON, "below": 100})
        login_as(client, "bob")
        assert rules(client)["alerts"] == []
        scan(client, api_headers, scanned(IRON, 50))
        assert [p[0] for p in pushes] == [["alice"]]

    def test_listed_with_current_amount_and_deleted(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 50))
        login_as(client, "alice")
        client.post("/api/stock/alert", json={**IRON, "below": 100})
        alert = rules(client)["alerts"][0]
        assert (alert["label"], alert["current"], alert["below"], alert["low"]) == ("Iron Ingot", 50, 100, True)
        client.post("/api/stock/alert/delete", json=IRON)
        assert rules(client)["alerts"] == []

    def test_deleting_the_user_deletes_their_alerts(self, client):
        login_as(client, "alice")
        client.post("/api/stock/alert", json={**IRON, "below": 100})
        assert store.users.delete("alice")
        assert store.stock.all_alerts() == []

    def test_needs_sign_in_and_a_positive_threshold(self, client):
        assert client.post("/api/stock/alert", json={**IRON, "below": 5}).status_code == 401
        login_as(client, "alice")
        assert client.post("/api/stock/alert", json={**IRON, "below": 0}).status_code == 400
        assert client.post("/api/stock/alert", json={**IRON, "below": True}).status_code == 400
        assert client.post("/api/stock/alert", json={"below": 5}).status_code == 400


class TestTargetEditing:
    def test_operators_only(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500))
        assert set_target(client).status_code == 401
        login_as(client, "vic", role="viewer")
        assert set_target(client).status_code == 403

    def test_set_list_and_delete(self, operator):
        assert set_target(operator).status_code == 200
        target = rules(operator)["targets"][0]
        assert (target["keep_at_least"], target["refill_to"], target["status"], target["current"]) == (100, 300, "ok", 500)
        operator.post("/api/stock/target/delete", json=IRON)
        assert rules(operator)["targets"] == []

    def test_not_shown_as_crafting_while_stocked(self, operator, api_headers):
        set_target(operator)
        cpus(operator, api_headers, busy=[IRON])
        assert rules(operator)["targets"][0]["status"] == "ok"

    def test_shared_with_everyone(self, operator, client):
        set_target(operator)
        client.post("/api/auth/logout")
        assert len(rules(client)["targets"]) == 1

    def test_refill_must_be_above_keep(self, operator):
        assert set_target(operator, keep=100, refill=100).status_code == 400
        assert set_target(operator, keep=-1, refill=100).status_code == 400

    def test_only_craftable_items(self, client, api_headers):
        scan(client, api_headers, scanned(IRON, 500, craftable=False))
        login_as(client, "olive", role="operator")
        assert set_target(client).status_code == 400
        assert set_target(client, GOLD).status_code == 400  # not in the network at all

    def test_not_essentia(self, client, api_headers):
        # OC can't ask AE2 to make essentia, so nothing can keep it stocked.
        ordo = {"label": "Ordo", "mod": None, "internal": "ordo", "damage": None, "kind": "essentia"}
        scan(client, api_headers, {"name": "Ordo", "internal": "ordo", "kind": "essentia", "size": 5})
        login_as(client, "olive", role="operator")
        assert set_target(client, ordo).status_code == 400


class TestRestock:
    def test_low_target_requests_the_refill(self, operator, api_headers):
        set_target(operator)
        cpus(operator, api_headers)
        scan(operator, api_headers, scanned(IRON, 40), scanned(GOLD, 500))
        [req] = auto_requests()
        assert (req["label"], req["amount"], req["user_id"], req["target_key"]) == ("Iron Ingot", 260, "olive", "minecraft|iron_ingot|0|item")
        assert rules(operator)["targets"][0]["status"] == "requested"

    def test_hidden_from_the_requesters_own_list(self, operator, api_headers):
        set_target(operator)
        cpus(operator, api_headers)
        scan(operator, api_headers, scanned(IRON, 40))
        assert operator.get("/api/craft/requests").get_json()["requests"] == []

    def test_not_requested_twice_while_waiting_on_the_game(self, operator, api_headers):
        set_target(operator)
        cpus(operator, api_headers)
        scan(operator, api_headers, scanned(IRON, 40))
        scan(operator, api_headers, scanned(IRON, 40))
        assert len(auto_requests()) == 1

    def test_not_while_a_cpu_is_making_it(self, operator, api_headers):
        set_target(operator)
        cpus(operator, api_headers, busy=[IRON])
        scan(operator, api_headers, scanned(IRON, 40))
        assert auto_requests() == []
        assert rules(operator)["targets"][0]["status"] == "crafting"

    def test_not_when_a_job_made_it_during_the_scan(self, operator, api_headers):
        # The scan may have counted the item before that job's output
        # reached the network.
        set_target(operator)
        cpus(operator, api_headers, busy=[IRON])
        token = operator.post("/api/network/scan/start", headers=api_headers).get_json()["scan_token"]
        operator.post("/api/crafts", json={"source": "me_controller", "jobs": [  # the job ends
            {"name": f"I{n}", "busy": False} for n in range(3)] + [{"name": "B0", "busy": False}]},
            headers=api_headers)
        operator.post("/api/network/scan/batch", json={"items": [scanned(IRON, 40)], "scan_token": token},
                      headers=api_headers)
        operator.post("/api/network/scan/finish", json={"scan_token": token, "chunks_sent": 1, "total_errors": 0},
                      headers=api_headers)
        assert auto_requests() == []
        scan(operator, api_headers, scanned(IRON, 40))
        assert len(auto_requests()) == 1  # the next scan saw it all

    def test_keeps_cpus_idle(self, operator, api_headers, monkeypatch):
        monkeypatch.setattr(config, "AUTOCRAFT_KEEP_IDLE_CPUS", 2)
        set_target(operator)
        cpus(operator, api_headers, idle=2)
        scan(operator, api_headers, scanned(IRON, 40))
        assert auto_requests() == []
        target = rules(operator)["targets"][0]
        assert (target["status"], target["reason"]) == ("waiting", "waiting for a free crafting CPU")

    def test_emptiest_first_when_cpus_are_short(self, operator, api_headers, monkeypatch):
        monkeypatch.setattr(config, "AUTOCRAFT_KEEP_IDLE_CPUS", 1)
        set_target(operator, IRON, keep=100, refill=300)
        set_target(operator, GOLD, keep=100, refill=300)
        cpus(operator, api_headers, idle=2)
        scan(operator, api_headers, scanned(IRON, 60), scanned(GOLD, 10))
        assert [r["label"] for r in auto_requests()] == ["Gold Ingot"]

    def test_nothing_without_a_fresh_cpu_report(self, operator, api_headers):
        set_target(operator)
        scan(operator, api_headers, scanned(IRON, 40))
        assert auto_requests() == []

    def test_disabled_target_does_nothing(self, operator, api_headers):
        set_target(operator, enabled=False)
        cpus(operator, api_headers)
        scan(operator, api_headers, scanned(IRON, 40))
        assert auto_requests() == []
        assert rules(operator)["targets"][0]["status"] == "off"


def game_result(client, api_headers, req_id, **body):
    client.get("/api/craft/requests/pending", headers=api_headers)
    client.post(f"/api/craft/requests/{req_id}/result", json=body, headers=api_headers)


class TestAutoRequestResults:
    @pytest.fixture()
    def requested(self, operator, api_headers):
        set_target(operator)
        cpus(operator, api_headers)
        scan(operator, api_headers, scanned(IRON, 40))
        return auto_requests()[0]["id"]

    def test_accepted_is_tracked_but_pins_nobody(self, operator, api_headers, requested):
        game_result(operator, api_headers, requested, status="accepted", cpu_name="I0")
        assert operator.get("/api/pins").get_json()["pins"] == []
        assert rules(operator)["targets"][0]["last_status"] == "accepted"
        cpus(operator, api_headers, idle=0, busy=[IRON])
        job = next(j for j in operator.get("/api/crafts").get_json()["jobs"] if j["name"] == "B0")
        assert job.get("auto") is None  # a different CPU than the request's
        operator.post("/api/crafts", json={"source": "me_controller", "jobs": [
            {"name": "I0", "busy": True, "final_output": "Iron Ingot", "final_output_mod": "minecraft",
             "final_output_internal": "iron_ingot", "final_output_damage": 0}]}, headers=api_headers)
        assert operator.get("/api/crafts").get_json()["jobs"][0]["auto"] is True

    def test_failure_waits_before_retrying_and_tells_alert_holders(self, operator, api_headers, requested, pushes):
        operator.post("/api/stock/alert", json={**IRON, "below": 10})
        game_result(operator, api_headers, requested, status="failed", reason="missing resources")
        assert pushes[-1][0] == ["olive"]
        assert pushes[-1][1]["body"] == "Couldn't restock Iron Ingot: missing resources"
        target = rules(operator)["targets"][0]
        assert (target["status"], target["reason"]) == ("failed", "missing resources")
        scan(operator, api_headers, scanned(IRON, 40))
        assert len(auto_requests()) == 1  # just the failed one

    def test_retries_after_the_wait(self, operator, api_headers, requested, monkeypatch):
        game_result(operator, api_headers, requested, status="failed", reason="missing resources")
        monkeypatch.setattr(config, "AUTOCRAFT_RETRY_SECONDS", 0)
        scan(operator, api_headers, scanned(IRON, 40))
        assert len([r for r in auto_requests() if r["status"] == "pending"]) == 1

    def test_marked_auto_in_craft_history(self, operator, api_headers, requested):
        game_result(operator, api_headers, requested, status="accepted", cpu_name="I0")
        iron_job = {"final_output": "Iron Ingot", "final_output_mod": "minecraft",
                    "final_output_internal": "iron_ingot", "final_output_damage": 0}
        operator.post("/api/crafts", headers=api_headers, json={"source": "me_controller", "jobs": [
            {"name": "I0", "busy": True, **iron_job}, {"name": "I1", "busy": True, **iron_job}]})
        operator.post("/api/crafts", headers=api_headers, json={"source": "me_controller", "jobs": [
            {"name": "I0", "busy": False}, {"name": "I1", "busy": False}]})
        events = operator.get("/api/crafts/history").get_json()["events"]
        assert {e["cpu"]: e["auto"] for e in events} == {"I0": True, "I1": False}

    def test_listed_as_auto_in_user_history(self, client, operator, api_headers, requested):
        login_as(client, "admin", role="admin")
        events = client.get("/api/admin/users/olive/history").get_json()["events"]
        assert events[0]["source"] == "auto"


def _nbt_string(text):
    data = text.encode()
    return len(data).to_bytes(2, "big") + data


# A Neutronium Huge Turbine: only GT.ToolStats' PrimaryMaterial tells it
# apart from every other material's.
TURBINE_TAG = (
    b"\x0a" + _nbt_string("") + b"\x0a" + _nbt_string("GT.ToolStats")
    + b"\x08" + _nbt_string("PrimaryMaterial") + _nbt_string("Neutronium") + b"\x00\x00"
).hex()
GENERIC_TURBINE = "item/gregtech/gt.metatool.01~176.png"
NEUTRONIUM_TURBINE = "item/gregtech/gt.metatool.01~176~neutronium.png"


class TestToolIcons:
    @pytest.fixture()
    def turbine(self, client, api_headers, monkeypatch):
        """A Neutronium Huge Turbine scanned with its NBT; the rule body
        for it, as the page sends one (variant id, no variant_name)."""
        from gcm import icons
        monkeypatch.setattr(icons, "_icons_by_key", {"gregtech:gt.metatool.01:176": GENERIC_TURBINE})
        monkeypatch.setattr(icons, "_icons_by_key_material", {"gregtech:gt.metatool.01:176|Neutronium": NEUTRONIUM_TURBINE})
        item = {"name": "Huge Turbine", "mod": "gregtech", "internal": "gt.metatool.01", "damage": 176,
                "size": 1, "isCraftable": True, "hasTag": True, "tag": TURBINE_TAG}
        scan(client, api_headers, item)
        [scanned_item] = client.get("/api/network").get_json()["items"]
        assert scanned_item["icon"] == NEUTRONIUM_TURBINE
        return item, {"label": "Huge Turbine", "mod": "gregtech", "internal": "gt.metatool.01", "damage": 176,
                      "kind": "item", "variant": scanned_item["variant"]}

    def test_rules_and_alert_pushes_show_the_material(self, client, api_headers, pushes, turbine):
        item, rule = turbine
        login_as(client, "olive", role="operator")
        assert client.post("/api/stock/alert", json={**rule, "below": 2}).status_code == 200
        assert set_target(client, rule, keep=2, refill=4).status_code == 200
        listed = rules(client)
        assert listed["alerts"][0]["icon"] == NEUTRONIUM_TURBINE
        assert listed["targets"][0]["icon"] == NEUTRONIUM_TURBINE
        cpus(client, api_headers)
        scan(client, api_headers, item)  # 1 < 2: alert and restock
        assert NEUTRONIUM_TURBINE.replace("/", "%2F") in pushes[0][1]["icon"]
        [req] = auto_requests()
        assert req["icon"] == NEUTRONIUM_TURBINE
