import pytest

from gcm import commands, db, state, tracking
from conftest import login_as


class TestCraftsEndpoint:
    def test_unauthenticated_post_rejected(self, client):
        res = client.post("/api/crafts", json={"jobs": []})
        assert res.status_code == 401

    def test_post_then_get_round_trip(self, client, api_headers):
        payload = {
            "source": "me_controller",
            "jobs": [{"name": "W01", "busy": True, "final_output": "Neutronium Ingot"}],
        }
        res = client.post("/api/crafts", json=payload, headers=api_headers)
        assert res.status_code == 200
        assert res.get_json()["ok"] is True

        res = client.get("/api/crafts")
        data = res.get_json()
        assert data["source"] == "me_controller"
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["name"] == "W01"
        assert data["stale"] is False

    def test_no_data_yet_is_stale(self, client):
        res = client.get("/api/crafts")
        data = res.get_json()
        assert data["stale"] is True
        assert data["age_seconds"] is None

    def test_invalid_payload_rejected(self, client, api_headers):
        res = client.post(
            "/api/crafts",
            data="not json",
            content_type="application/json",
            headers=api_headers,
        )
        assert res.status_code == 400

    def test_missing_jobs_defaults_to_empty_list(self, client, api_headers):
        res = client.post(
            "/api/crafts", json={"source": "me_controller"}, headers=api_headers
        )
        assert res.status_code == 200
        assert client.get("/api/crafts").get_json()["jobs"] == []


class TestCpuPins:
    def _post_busy_job(self, client, api_headers, cpu_name="W01", busy=True):
        client.post(
            "/api/crafts",
            json={
                "source": "me_controller",
                "jobs": [{"name": cpu_name, "busy": busy}],
            },
            headers=api_headers,
        )

    def test_pin_requires_sign_in(self, client):
        res = client.post("/api/pins", json={"cpu_name": "W01"})
        assert res.status_code == 401

    def test_cannot_pin_unknown_cpu(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        res = client.post("/api/pins", json={"cpu_name": "NoSuchCPU"})
        assert res.status_code == 404

    def test_cannot_pin_idle_cpu(self, client, api_headers):
        self._post_busy_job(client, api_headers, busy=False)
        login_as(client, "alice")
        res = client.post("/api/pins", json={"cpu_name": "W01"})
        assert res.status_code == 400
        assert "not currently busy" in res.get_json()["error"]

    def test_pin_busy_cpu_then_list_it(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        res = client.post("/api/pins", json={"cpu_name": "W01"})
        assert res.status_code == 200

        res = client.get("/api/pins")
        assert res.get_json()["pins"] == ["W01"]

    def test_pins_are_per_user(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})

        login_as(client, "bob")
        res = client.get("/api/pins")
        assert res.get_json()["pins"] == []

    def test_double_pin_does_not_duplicate(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        client.post("/api/pins", json={"cpu_name": "W01"})
        res = client.get("/api/pins")
        assert res.get_json()["pins"] == ["W01"]

    def test_unpin_removes_it(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        res = client.post("/api/pins/unpin", json={"cpu_name": "W01"})
        assert res.status_code == 200
        assert client.get("/api/pins").get_json()["pins"] == []

    def test_pin_on_cpu_first_seen_idle_is_dropped(self, client, api_headers):
        # Simulates a server restart: the pin survives in SQLite, but the
        # job finished while the server was down, so the first poll sees
        # the CPU idle with no remembered busy state.
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        state.cpu_last_busy.clear()

        self._post_busy_job(client, api_headers, busy=False)
        assert client.get("/api/pins").get_json()["pins"] == []

        # ...and the CPU's next job finishing doesn't notify alice.
        self._post_busy_job(client, api_headers)
        self._post_busy_job(client, api_headers, busy=False)
        assert client.get("/api/completions").get_json()["completions"] == []

    def test_pin_on_busy_cpu_survives_restart(self, client, api_headers):
        self._post_busy_job(client, api_headers)
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        state.cpu_last_busy.clear()

        self._post_busy_job(client, api_headers)
        assert client.get("/api/pins").get_json()["pins"] == ["W01"]
        self._post_busy_job(client, api_headers, busy=False)
        assert len(client.get("/api/completions").get_json()["completions"]) == 1


def post_job(client, api_headers, output=None, busy=True, cpu_name="W01", pending=None, **extra):
    job = {"name": cpu_name, "busy": busy, "pending": pending or [], **extra}
    if output:
        job.update(final_output=output, final_output_mod="gregtech", final_output_internal=output)
    client.post(
        "/api/crafts",
        json={"source": "me_controller", "jobs": [job]},
        headers=api_headers,
    )


def completion_names(client):
    return [c["itemName"] for c in client.get("/api/completions").get_json()["completions"]]


class TestBackToBackJobs:
    # A CPU that finishes and starts its next job between two polls is
    # never seen idle.
    def test_new_output_on_a_busy_cpu_ends_the_old_job(self, client, api_headers):
        post_job(client, api_headers, "Iron Ingot")
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})

        post_job(client, api_headers, "Gold Ingot")

        assert completion_names(client) == ["Iron Ingot"]
        assert client.get("/api/pins").get_json()["pins"] == []
        post_job(client, api_headers, busy=False)
        assert completion_names(client) == ["Iron Ingot"]

    def test_unknown_output_is_not_a_new_job(self, client, api_headers):
        # No Crafting Monitor on the CPU: final_output is simply absent.
        post_job(client, api_headers, "Iron Ingot")
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})

        post_job(client, api_headers, None)
        post_job(client, api_headers, "Iron Ingot")

        assert completion_names(client) == []
        assert client.get("/api/pins").get_json()["pins"] == ["W01"]


class TestAcceptedRequestTracking:
    def _accept(self, client, api_headers, poll_sees_job_first=False):
        login_as(client, "bob", role="operator")
        req_id = client.post(
            "/api/craft/request",
            json={"label": "Gold Ingot", "internal": "gold", "amount": 1},
        ).get_json()["id"]
        commands.craft_requests.requests[req_id]["created_at"] -= 10
        client.get("/api/craft/requests/pending", headers=api_headers)
        if poll_sees_job_first:
            post_job(client, api_headers, "Gold Ingot")
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "accepted", "cpu_name": "W01"},
            headers=api_headers,
        )

    def test_job_tracked_from_before_the_request_is_closed(self, client, api_headers):
        # The game only starts a request on an idle CPU, so a job we
        # still think runs there ended between polls.
        post_job(client, api_headers, "Iron Ingot")
        state.cpu_last_known["W01"]["started_at"] -= 60
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})

        self._accept(client, api_headers)

        login_as(client, "alice")
        assert completion_names(client) == ["Iron Ingot"]
        assert client.get("/api/pins").get_json()["pins"] == []
        login_as(client, "bob", role="operator")
        assert client.get("/api/pins").get_json()["pins"] == ["W01"]

    def test_job_already_seen_after_the_request_is_kept(self, client, api_headers):
        # The status poll can see the requested job before the game
        # reports the result; that job is the request's own.
        self._accept(client, api_headers, poll_sees_job_first=True)
        assert completion_names(client) == []
        assert client.get("/api/pins").get_json()["pins"] == ["W01"]

        post_job(client, api_headers, busy=False)
        assert completion_names(client) == ["Gold Ingot"]


def fluid(amount):
    return {"name": "Molten Iron", "internal": "molten.iron", "size": amount}


def item(name, amount):
    return {"name": name, "mod": "gregtech", "internal": name, "damage": 0, "size": amount}


def w01(client):
    return client.get("/api/crafts").get_json()["jobs"][0]


def craft_statuses(client):
    with db.transaction(db.app_db) as conn:
        return [r[0] for r in conn.execute("SELECT status FROM craft_events ORDER BY id")]


class TestJobProgress:
    def test_first_report_is_the_baseline(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Plate", 8)], progress_percent=77)
        job = w01(client)
        assert (job["progress_percent"], job["steps_done"], job["steps_total"]) == (0, 0, 1)

    def test_fluid_amounts_do_not_outweigh_items(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[fluid(144000), item("Plate", 1)])
        post_job(client, api_headers, "Gear", pending=[fluid(144000)])
        job = w01(client)
        assert (job["progress_percent"], job["steps_done"], job["steps_total"]) == (50, 1, 2)

    def test_stored_is_ignored(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Plate", 4)], stored=[item("Ingot", 100)])
        post_job(client, api_headers, "Gear", pending=[item("Plate", 4)], stored=[item("Ingot", 1)])
        assert w01(client)["progress_percent"] == 0

    def test_active_counts_as_remaining(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Plate", 4)])
        post_job(client, api_headers, "Gear", pending=[item("Plate", 1)], active=[item("Plate", 3)])
        assert w01(client)["progress_percent"] == 0
        post_job(client, api_headers, "Gear", active=[item("Plate", 1)])
        assert w01(client)["progress_percent"] == 75

    def test_never_goes_backwards(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Plate", 4)])
        post_job(client, api_headers, "Gear", pending=[item("Plate", 2)])
        post_job(client, api_headers, "Gear", pending=[item("Plate", 8)])
        assert w01(client)["progress_percent"] == 50

    def test_new_job_starts_over(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Plate", 4)])
        post_job(client, api_headers, "Gear", pending=[item("Plate", 1)])
        post_job(client, api_headers, busy=False)
        assert w01(client)["progress_percent"] is None
        post_job(client, api_headers, "Gear", pending=[item("Plate", 2)])
        assert w01(client)["progress_percent"] == 0

    def test_new_output_starts_over(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Plate", 4)])
        post_job(client, api_headers, "Gear", pending=[item("Plate", 1)])
        post_job(client, api_headers, "Rotor", pending=[item("Plate", 2)])
        assert w01(client)["progress_percent"] == 0

    def test_ending_with_only_the_final_step_left_is_finished(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1), item("Plate", 4)])
        post_job(client, api_headers, "Gear", active=[item("Gear", 1)])
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["finished"]



def shift_samples(seconds, cpu_name="W01", last=None):
    """Moves the job's recorded sample times `seconds` into the past, as
    if it had been running that long; `last` moves only the latest one,
    as if that many seconds passed since it."""
    entry = state.cpu_last_known[cpu_name]
    for key in ("started_at", "first_sample_at", "last_sample_at"):
        entry[key] -= seconds
    if last is not None:
        entry["last_sample_at"] -= last


def plates(n):
    return [item("Gear", 1), item("Plate", 4), item("Rod", 4), item("Bolt", 4)][: n + 1]


class TestJobEnd:
    # Only the steps left at the last busy sample are known, and that
    # sample is seconds old by the time the CPU is seen idle.
    def test_slow_job_ending_with_steps_left_is_incomplete(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=plates(3))
        shift_samples(600)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1), item("Plate", 2), item("Rod", 4), item("Bolt", 4)])
        shift_samples(0, last=5)
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["incomplete"]

    def test_job_seen_once_is_finished(self, client, api_headers):
        # One sample is only the baseline - the job most likely finished
        # in the gap.
        post_job(client, api_headers, "Gear", pending=plates(3))
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["finished"]

    def test_fast_job_ending_with_steps_left_is_finished(self, client, api_headers):
        # Half done within 5s: the other half fits in the next 5s.
        post_job(client, api_headers, "Gear", pending=plates(3))
        shift_samples(5)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1), item("Plate", 2), item("Rod", 2), item("Bolt", 2)])
        shift_samples(0, last=5)
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["finished"]

    def test_fresh_last_busy_snapshot_decides(self, client, api_headers):
        # A slow job whose 5s-old sample had steps left, but Lua looked
        # again a second before it went idle: only the final step left.
        post_job(client, api_headers, "Gear", pending=plates(3))
        shift_samples(600)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1), item("Plate", 2), item("Rod", 4), item("Bolt", 4)])
        shift_samples(0, last=5)
        post_job(client, api_headers, busy=False,
                 last_busy={"pending": [], "active": [item("Gear", 1)], "age": 1})
        assert craft_statuses(client) == ["finished"]

    def test_last_busy_is_not_served(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=plates(1))
        post_job(client, api_headers, busy=False, last_busy={"pending": [], "active": [], "age": 1})
        assert "last_busy" not in w01(client)

    @pytest.mark.parametrize("last_busy", [
        "junk",
        {"pending": [], "active": []},
        {"pending": [], "active": [], "age": "1"},
        {"pending": [], "active": [], "age": True},
        {"pending": "junk", "active": 3, "age": 1},
    ])
    def test_malformed_last_busy_is_ignored(self, client, api_headers, last_busy):
        post_job(client, api_headers, "Gear", pending=plates(3))
        shift_samples(600)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1), item("Plate", 2), item("Rod", 4), item("Bolt", 4)])
        shift_samples(0, last=5)
        res = client.post(
            "/api/crafts",
            json={"jobs": [{"name": "W01", "busy": False, "last_busy": last_busy}]},
            headers=api_headers,
        )
        assert res.status_code == 200
        assert craft_statuses(client) == ["incomplete"]

    def _cancel(self, client, api_headers, report=True):
        login_as(client, "bob", role="operator")
        req_id = client.post("/api/craft/cancel", json={"cpu_name": "W01"}).get_json()["id"]
        client.get("/api/craft/cancel/pending", headers=api_headers)
        if report:
            client.post(f"/api/craft/cancel/{req_id}/result", json={"success": True}, headers=api_headers)

    @pytest.mark.parametrize("result_first", [True, False])
    def test_cancel_from_the_page_is_incomplete(self, client, api_headers, result_first):
        # Even a job with only its final step left - and whichever of the
        # cancel's result and the idle report reaches us first.
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1)])
        self._cancel(client, api_headers, report=result_first)
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["incomplete"]

    def test_refused_cancel_does_not_count(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1)])
        login_as(client, "bob", role="operator")
        req_id = client.post("/api/craft/cancel", json={"cpu_name": "W01"}).get_json()["id"]
        client.get("/api/craft/cancel/pending", headers=api_headers)
        client.post(f"/api/craft/cancel/{req_id}/result", json={"success": False}, headers=api_headers)
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["finished"]

    def test_cancel_of_an_earlier_job_does_not_count(self, client, api_headers):
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1)])
        self._cancel(client, api_headers)
        post_job(client, api_headers, busy=False)
        post_job(client, api_headers, "Rotor", pending=[item("Rotor", 1)])
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["incomplete", "finished"]


class TestRequestOutcome:
    # craft_monitor.lua watching a browser request's own crafting link
    # for how its job ended.
    def _accept(self, client, api_headers, watching=True):
        login_as(client, "bob", role="operator")
        req_id = client.post(
            "/api/craft/request",
            json={"label": "Gear", "internal": "Gear", "amount": 1},
        ).get_json()["id"]
        client.get("/api/craft/requests/pending", headers=api_headers)
        client.post(
            f"/api/craft/requests/{req_id}/result",
            json={"status": "accepted", "cpu_name": "W01", "watching": watching},
            headers=api_headers,
        )
        return req_id

    def _outcome(self, client, api_headers, req_id, outcome, cpu_name="W01"):
        return client.post(
            f"/api/craft/requests/{req_id}/outcome",
            json={"outcome": outcome, "cpu_name": cpu_name},
            headers=api_headers,
        )

    def _slow_job_with_steps_left(self, client, api_headers):
        # The pace check alone would call this incomplete.
        post_job(client, api_headers, "Gear", pending=plates(3))
        shift_samples(600)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1), item("Plate", 2), item("Rod", 4), item("Bolt", 4)])
        shift_samples(0, last=5)

    def test_finished_outcome_before_idle_decides(self, client, api_headers):
        req_id = self._accept(client, api_headers)
        self._slow_job_with_steps_left(client, api_headers)
        assert self._outcome(client, api_headers, req_id, "finished").get_json()["applied"] is True
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["finished"]

    def test_cancelled_outcome_after_idle_decides(self, client, api_headers):
        req_id = self._accept(client, api_headers)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1)])
        post_job(client, api_headers, busy=False)
        # Held until the outcome arrives.
        assert craft_statuses(client) == []
        assert client.get("/api/pins").get_json()["pins"] == ["W01"]
        assert self._outcome(client, api_headers, req_id, "cancelled").get_json()["applied"] is True
        assert craft_statuses(client) == ["incomplete"]
        assert completion_names(client) == ["Gear"]

    def test_no_outcome_is_judged_after_the_wait(self, client, api_headers):
        self._accept(client, api_headers)
        self._slow_job_with_steps_left(client, api_headers)
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == []
        state.cpu_ending["W01"]["ended_at"] -= tracking.OUTCOME_WAIT_SECONDS
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["incomplete"]

    def test_new_job_releases_the_held_end_first(self, client, api_headers):
        req_id = self._accept(client, api_headers)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1)])
        post_job(client, api_headers, busy=False)
        post_job(client, api_headers, "Rotor", pending=[item("Rotor", 1)])
        assert craft_statuses(client) == ["finished"]
        login_as(client, "alice")
        client.post("/api/pins", json={"cpu_name": "W01"})
        # Too late for the ended job, and not this one's to decide.
        assert self._outcome(client, api_headers, req_id, "cancelled").get_json()["applied"] is False
        assert client.get("/api/pins").get_json()["pins"] == ["W01"]

    def test_unwatched_request_is_not_held(self, client, api_headers):
        self._accept(client, api_headers, watching=False)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1)])
        post_job(client, api_headers, busy=False)
        assert craft_statuses(client) == ["finished"]

    def test_outcome_for_another_cpu_is_not_applied(self, client, api_headers):
        req_id = self._accept(client, api_headers)
        post_job(client, api_headers, "Gear", pending=[item("Gear", 1)])
        assert self._outcome(client, api_headers, req_id, "cancelled", cpu_name="W02").get_json()["applied"] is False

    @pytest.mark.parametrize("body", [
        {"outcome": "exploded", "cpu_name": "W01"},
        {"outcome": "finished"},
        {"outcome": "finished", "cpu_name": 3},
    ])
    def test_malformed_outcome_rejected(self, client, api_headers, body):
        res = client.post("/api/craft/requests/1/outcome", json=body, headers=api_headers)
        assert res.status_code == 400

    def test_outcome_needs_the_api_key(self, client):
        res = client.post("/api/craft/requests/1/outcome", json={"outcome": "finished", "cpu_name": "W01"})
        assert res.status_code == 401


def history(client, **params):
    return client.get("/api/crafts/history", query_string=params).get_json()


class TestCraftHistory:
    def test_lists_job_ends_newest_first(self, client, api_headers):
        post_job(client, api_headers, busy=False)
        for output in ("Gear", "Rotor"):
            post_job(client, api_headers, output)
            post_job(client, api_headers, busy=False)
        data = history(client)
        assert [e["itemName"] for e in data["events"]] == ["Rotor", "Gear"]
        assert data["more"] is False
        event = data["events"][0]
        assert (event["cpu"], event["status"]) == ("W01", "finished")
        assert (event["mod"], event["internal"]) == ("gregtech", "Rotor")

    def test_public(self, client):
        assert client.get("/api/crafts/history").status_code == 200

    def test_records_how_long_a_job_ran(self, client, api_headers):
        post_job(client, api_headers, busy=False)
        post_job(client, api_headers, "Gear", pending=plates(1))
        shift_samples(90)
        post_job(client, api_headers, busy=False)
        event = history(client)["events"][0]
        assert event["finishedAt"] - event["startedAt"] == pytest.approx(90, abs=5)

    def test_start_unknown_for_a_job_running_before_the_server_saw_it(self, client, api_headers):
        post_job(client, api_headers, "Gear")
        post_job(client, api_headers, busy=False)
        assert history(client)["events"][0]["startedAt"] is None

    def test_back_to_back_job_start_is_known(self, client, api_headers):
        post_job(client, api_headers, "Gear")
        post_job(client, api_headers, "Rotor")
        post_job(client, api_headers, busy=False)
        rotor, gear = history(client)["events"]
        assert gear["startedAt"] is None
        assert rotor["startedAt"] is not None

    def test_pages_with_before_and_after(self, client, api_headers):
        post_job(client, api_headers, busy=False)
        for n in range(5):
            post_job(client, api_headers, f"Item{n}")
            post_job(client, api_headers, busy=False)
        first = history(client, limit=2)
        assert [e["itemName"] for e in first["events"]] == ["Item4", "Item3"]
        assert first["more"] is True
        older = history(client, limit=2, before=first["events"][-1]["id"])
        assert [e["itemName"] for e in older["events"]] == ["Item2", "Item1"]
        newer = history(client, after=older["events"][0]["id"])
        assert [e["itemName"] for e in newer["events"]] == ["Item4", "Item3"]
        assert newer["more"] is False

    def test_bad_params_fall_back_to_defaults(self, client, api_headers):
        post_job(client, api_headers, "Gear")
        post_job(client, api_headers, busy=False)
        data = history(client, limit="lots", before="x")
        assert len(data["events"]) == 1
        assert len(history(client, limit=0)["events"]) == 1
