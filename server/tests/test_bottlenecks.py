"""Held-up steps of running jobs and the machines behind them
(gcm/bottlenecks.py): the live tree's states, and the totals in the
Crafts tab's statistics."""

import time

import pytest

from gcm import bottlenecks, config, store
from test_job_tree import BOLT, GEAR, INGOT, PATTERNS, PLATE, item, key, pattern, stack
from test_patterns_api import run_pattern_scan

GRACE, STALL = 60, 300
T0 = 1_700_000_000.0


@pytest.fixture
def clock(monkeypatch):
    """time.time(), moved on by hand: clock.at(seconds after the start)."""
    class Clock:
        now = T0

        def at(self, seconds):
            self.now = T0 + seconds

    c = Clock()
    monkeypatch.setattr(time, "time", lambda: c.now)
    monkeypatch.setattr(config, "CRAFT_BLOCK_GRACE_SECONDS", GRACE)
    monkeypatch.setattr(config, "CRAFT_STALL_SECONDS", STALL)
    return c


def job(name, pending=(), active=(), stored=()):
    return {"name": name, "busy": True, "final_output": "Gear", "final_output_mod": "gregtech",
            "final_output_internal": "gt.metaitem.01", "final_output_damage": GEAR[1],
            "pending": list(pending), "active": list(active), "stored": list(stored)}


def post(client, api_headers, *jobs):
    client.post("/api/crafts", headers=api_headers, json={"source": "me_controller", "jobs": list(jobs)})


def start(client, api_headers, *names, patterns=PATTERNS):
    run_pattern_scan(client, api_headers, [patterns])
    post(client, api_headers, *({"name": n, "busy": False} for n in names))


class TestStates:
    def test_ready_but_not_sent_is_blocked(self, client, api_headers, clock):
        start(client, api_headers, "A1")
        # 8 plates to bend, the ingots for them in the CPU: nothing sent.
        waiting = job("A1", pending=[stack("Gear", GEAR[1], 2), stack("Plate", PLATE[1], 8)],
                      stored=[stack("Ingot", INGOT[1], 9)])
        clock.at(0)
        post(client, api_headers, waiting)
        clock.at(GRACE - 5)
        post(client, api_headers, waiting)
        assert bottlenecks.steps("A1")[key(PLATE)]["state"] == "waiting"
        clock.at(GRACE)
        post(client, api_headers, waiting)
        plate = bottlenecks.steps("A1")[key(PLATE)]
        assert plate["state"] == "blocked" and plate["since"] == T0
        # Bent from ingots: the Bender, not the Solidifier AE2 would try first.
        assert plate["machines"] == [{"name": "Bender", "x": 1, "y": 0, "z": 0, "dim": 0}]
        # Nothing to make gears from yet: waiting, not blocked.
        assert bottlenecks.steps("A1")[key(GEAR)]["state"] == "waiting"
        # One sent: under way again.
        clock.at(GRACE + 5)
        post(client, api_headers, job("A1", pending=[stack("Gear", GEAR[1], 2), stack("Plate", PLATE[1], 7)],
                                      active=[stack("Plate", PLATE[1], 1)], stored=[stack("Ingot", INGOT[1], 8)]))
        assert bottlenecks.steps("A1")[key(PLATE)]["state"] == "active"

    def test_without_the_inputs_its_waiting_however_long(self, client, api_headers, clock):
        start(client, api_headers, "A1")
        waiting = job("A1", pending=[stack("Gear", GEAR[1], 2), stack("Plate", PLATE[1], 8)])
        for t in (0, GRACE, 2 * GRACE):
            clock.at(t)
            post(client, api_headers, waiting)
        assert bottlenecks.steps("A1")[key(PLATE)]["state"] == "waiting"

    def test_a_machine_giving_another_cpu_its_output_is_busy(self, client, api_headers, clock):
        start(client, api_headers, "A1", "A2")
        mine = job("A1", pending=[stack("Gear", GEAR[1], 1)], active=[stack("Bolt", BOLT[1], 4)])

        def theirs(bolts):
            return job("A2", pending=[stack("Gear", GEAR[1], 3)], active=[stack("Bolt", BOLT[1], bolts)])

        for t, bolts in ((0, 12), (30, 10), (GRACE, 8)):
            clock.at(t)
            post(client, api_headers, mine, theirs(bolts))
        bolt = bottlenecks.steps("A1")[key(BOLT)]
        assert bolt["state"] == "busy" and bolt["since"] == T0
        assert bolt["with"] == {"cpu": "A2", "name": "Bolt"}
        assert bolt["machines"] == [{"name": "Lathe", "x": 3, "y": 0, "z": 0, "dim": 0}]
        assert bottlenecks.steps("A2")[key(BOLT)]["state"] == "active"
        # The other job's bolts done too: nothing on the Lathe moving. Not
        # stalled until CRAFT_STALL_SECONDS, which is longer.
        clock.at(GRACE + 5)
        post(client, api_headers, mine, theirs(8))
        clock.at(GRACE + GRACE)
        post(client, api_headers, mine, theirs(8))
        assert bottlenecks.steps("A1")[key(BOLT)]["state"] == "active"
        clock.at(STALL)
        post(client, api_headers, mine, theirs(8))
        assert bottlenecks.steps("A1")[key(BOLT)]["state"] == "stalled"

    @pytest.mark.parametrize(("x", "busy"), [(4, True), (5, False)])
    def test_two_interfaces_on_one_machine_are_one_machine(self, client, api_headers, clock, x, busy):
        # Bolts from two Lathe interfaces: either side of one lathe block
        # (x=2 and x=4, both touching x=3), or not (x=2 and x=5).
        lathes = PATTERNS[:3] + [
            pattern("Lathe", 3, [item(*BOLT, 4)], [item(*INGOT)]),
            pattern("Lathe", 3, [item(*BOLT, 4)], [item("Rod", 6)]),
        ]
        lathes[3]["provider"]["x"], lathes[4]["provider"]["x"] = 2, x
        start(client, api_headers, "A1", "A2", patterns=lathes)
        mine = job("A1", pending=[stack("Gear", GEAR[1], 1)], active=[stack("Bolt", BOLT[1], 4)],
                   stored=[stack("Ingot", INGOT[1], 1)])
        for t, bolts in ((0, 12), (30, 10), (GRACE, 8)):
            clock.at(t)
            post(client, api_headers, mine, job("A2", pending=[stack("Gear", GEAR[1], 3)],
                                                active=[stack("Bolt", BOLT[1], bolts)],
                                                stored=[stack("Rod", 6, 1)]))
        bolt = bottlenecks.steps("A1")[key(BOLT)]
        if busy:
            assert bolt["state"] == "busy" and bolt["with"]["cpu"] == "A2"
            # The job's own interface: the one taking what it pulled.
            assert [m["x"] for m in bolt["machines"]] == [2]
        else:
            assert bolt["state"] == "active"


def test_mates():
    def p(name, x, y=0, z=0, dim=0):
        return {"name": name, "x": x, "y": y, "z": z, "dim": dim}

    providers = {
        "a": p("Lathe", 0), "b": p("Lathe", 2), "c": p("Lathe", 1, 1),  # around (1, 0, 0)
        "d": p("Lathe", 5),  # too far
        "e": p("Bender", 2),  # another machine's name
        "f": p("Lathe", 2, dim=7),  # another world
        "g": {"name": "Lathe"},  # nowhere known
    }
    mates = bottlenecks.mates(providers)
    assert mates["a"] == {"a", "b", "c"}
    assert mates["b"] == {"a", "b", "c"}
    assert mates["d"] == {"d"} and mates["e"] == {"e"} and mates["f"] == {"f"} and mates["g"] == {"g"}


class TestTotals:
    def test_held_up_time_adds_up_per_machine(self, client, api_headers, clock):
        start(client, api_headers, "A1")
        stuck = job("A1", pending=[stack("Gear", GEAR[1], 2), stack("Plate", PLATE[1], 8)],
                    active=[stack("Bolt", BOLT[1], 4)], stored=[stack("Ingot", INGOT[1], 9)])
        for t in range(0, STALL + 31, 10):
            clock.at(t)
            post(client, api_headers, stuck)
        rows = {r["name"]: r for r in bottlenecks.summary(None)}
        bender, lathe = rows["Bender"], rows["Lathe"]
        # Plates blocked from GRACE on, bolts stalled from STALL on; the
        # whole job still from GRACE on, waiting on both.
        assert bender["blocked"] == pytest.approx(STALL + 30 - GRACE)
        assert lathe["stalled"] == pytest.approx(30)
        assert bender["critical"] + lathe["critical"] == pytest.approx(STALL + 30 - GRACE)
        assert bender["critical"] == pytest.approx(lathe["critical"])
        assert (bender["jobs"], lathe["jobs"], bender["x"]) == (1, 1, 1)
        assert "Assembler" not in rows  # gears: waiting on the others

    def test_a_job_that_keeps_moving_adds_nothing(self, client, api_headers, clock):
        start(client, api_headers, "A1")
        for i, t in enumerate(range(0, 300, 10)):
            clock.at(t)
            post(client, api_headers, job("A1", pending=[stack("Gear", GEAR[1], 2)],
                                          active=[stack("Bolt", BOLT[1], 100 - i)]))
        assert bottlenecks.summary(None) == []

    def test_gaps_in_the_reports_count_only_so_long(self, client, api_headers, clock):
        start(client, api_headers, "A1")
        stuck = job("A1", pending=[stack("Gear", GEAR[1], 2)], active=[stack("Bolt", BOLT[1], 4)])
        clock.at(0)
        post(client, api_headers, stuck)
        clock.at(STALL)
        post(client, api_headers, stuck)  # stalled from now on
        clock.at(STALL + 3600)
        post(client, api_headers, stuck)  # the first report after a gap
        [lathe] = bottlenecks.summary(None)
        assert lathe["stalled"] == pytest.approx(bottlenecks.MAX_STEP_SECONDS)

    def test_ranked_by_how_long_jobs_waited_on_them(self, client):  # client: a fresh app.db
        hour = 3600 * 100
        where = {"name": "Lathe", "x": 3, "y": 0, "z": 0, "dim": 0}
        store.bottlenecks.add(hour, [
            ("0|3|0|0|Lathe", where, {"blocked": 0, "busy": 50, "stalled": 0, "critical": 10, "jobs": 1}),
            ("0|1|0|0|Bender", {**where, "name": "Bender", "x": 1},
             {"blocked": 500, "busy": 0, "stalled": 0, "critical": 0, "jobs": 2}),
        ])
        store.bottlenecks.add(hour + 3600, [
            ("0|3|0|0|Lathe", {**where, "name": "Lathe (renamed)"},
             {"blocked": 5, "busy": 0, "stalled": 0, "critical": 20, "jobs": 1}),
        ])
        rows = store.bottlenecks.totals(None, 10)
        assert [r["name"] for r in rows] == ["Lathe (renamed)", "Bender"]
        assert (rows[0]["critical"], rows[0]["busy"], rows[0]["blocked"], rows[0]["jobs"]) == (30, 50, 5, 2)
        assert [r["name"] for r in store.bottlenecks.totals(hour + 3600, 10)] == ["Lathe (renamed)"]
        assert len(store.bottlenecks.totals(None, 1)) == 1

    def test_in_the_crafts_stats(self, client, api_headers, clock):
        start(client, api_headers, "A1")
        stuck = job("A1", pending=[stack("Gear", GEAR[1], 2)], active=[stack("Bolt", BOLT[1], 4)])
        for t in (0, STALL, STALL + 10):
            clock.at(t)
            post(client, api_headers, stuck)
        data = client.get("/api/crafts/stats?range=day").get_json()
        assert [(b["name"], b["stalled"]) for b in data["bottlenecks"]] == [("Lathe", pytest.approx(10))]
