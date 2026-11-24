import pytest

from gcm import db, eta, eta_report, state
from tests.test_crafts_api import item, post_job, shift_samples, w01


def fed(reports, started_at=0.0, seen_from_start=True):
    """An eta state fed (time, percent) reports."""
    s = eta.new_state()
    for at, percent in reports:
        eta.note_progress(s, started_at, at, percent, seen_from_start)
    return s


class TestMarks:
    def test_a_mark_passed_between_reports_is_put_on_the_line_between_them(self):
        s = fed([(0, 0), (100, 20), (300, 40)])
        assert s["marks"] == {10: 50, 20: 100, 30: 200, 40: 300}

    def test_a_job_not_seen_from_its_start_keeps_none(self):
        assert fed([(0, 30), (100, 60)], seen_from_start=False)["marks"] == {}

    def test_text_round_trip_leaves_out_marks_not_reached(self):
        marks = {10: 5, 20: 12.5, 40: 90}
        text = eta.marks_text(marks)
        assert text == "5,12.5,,90,,,,,"
        assert eta.parse_marks(text) == marks
        assert eta.marks_text({}) is None


class TestHistory:
    def test_time_left_from_where_past_runs_were_at_this_progress(self):
        # Front-loaded: 90% after 100s of 1000.
        runs = [(1000, 10, {m: 100 * m / 90 for m in eta.MARKS})]
        assert eta.history_eta(runs, 10, 90, 0) == pytest.approx(900)
        assert eta.history_eta(runs, 10, 45, 0) == pytest.approx(950)

    def test_median_of_runs_scaled_to_the_amount(self):
        runs = [(100, 10, {}), (200, 10, {}), (900, 20, {})]
        # At 0%: 100, 200 and 900 * 10/20.
        assert eta.history_eta(runs, 10, 0, 0) == pytest.approx(200)

    def test_time_already_spent_at_this_progress_counts(self):
        runs = [(1000, 10, {})]
        assert eta.history_eta(runs, 10, 50, 100) == pytest.approx(400)
        assert eta.history_eta(runs, 10, 50, 600) is None

    def test_none_without_runs(self):
        assert eta.history_eta([], 10, 50, 0) is None


class TestPace:
    def test_from_the_recent_pace_not_the_quick_start(self):
        # 60% in the first minute, then 10% in the next 15.
        s = fed([(0, 0), (60, 60), (500, 65), (960, 70)])
        left = eta.pace_eta(s, 0, 0, 960, 70)
        # Over the last 600s (from the 60% at 60s): 10% in 900s.
        assert left == pytest.approx(900 * 30 / 10)
        # Since the start it would have said 960 * 30 / 70.
        assert left > 5 * 960 * 30 / 70

    def test_none_until_watched_long_enough(self):
        s = fed([(0, 0), (60, 50)])
        assert eta.pace_eta(s, 0, 0, 60, 50) is None

    def test_none_with_no_progress_in_the_window(self):
        s = fed([(0, 0), (100, 50), (900, 50)])
        assert eta.pace_eta(s, 0, 0, 900, 50) is None


class TestEstimate:
    def test_the_longer_estimate_wins(self):
        s = fed([(0, 0), (300, 50)])
        s["history"], s["history_for"] = [(10_000, None, {})], (None, None)
        assert eta.estimate(s, 0, 0, 300, 50) == (pytest.approx(5000), "history")
        s["history"] = [(400, None, {})]
        assert eta.estimate(s, 0, 0, 300, 50) == (pytest.approx(300), "pace")

    def test_nothing_to_say(self):
        assert eta.estimate(fed([(0, 0)]), 0, 0, 0, 0) == (None, None)


def gear(left, *others):
    return [item("Gear", left)] + list(others)


class TestFromPastJobs:
    def run_gear_job(self, client, api_headers):
        post_job(client, api_headers, busy=False)
        post_job(client, api_headers, "Gear", final_output_damage=0, pending=gear(2, item("Plate", 4)))
        shift_samples(100)
        post_job(client, api_headers, "Gear", final_output_damage=0, pending=gear(2, item("Plate", 2)))
        shift_samples(300)
        post_job(client, api_headers, "Gear", final_output_damage=0, pending=gear(2))
        shift_samples(100)
        post_job(client, api_headers, "Gear", final_output_damage=0, active=gear(1))
        post_job(client, api_headers, busy=False)

    def test_a_finished_job_keeps_its_amount_and_marks(self, client, api_headers):
        self.run_gear_job(client, api_headers)
        with db.transaction(db.app_db) as conn:
            status, amount, marks, seconds = conn.execute(
                "SELECT status, amount, progress_marks, occurred_at - started_at FROM craft_events"
            ).fetchone()
        assert (status, amount) == ("finished", 2)
        parsed = eta.parse_marks(marks)
        assert parsed[10] == pytest.approx(40, abs=2)
        assert parsed[50] == pytest.approx(400, abs=2)
        assert seconds == pytest.approx(500, abs=5)

    def test_the_next_job_of_that_item_gets_a_time_left_right_away(self, client, api_headers):
        self.run_gear_job(client, api_headers)
        post_job(client, api_headers, "Gear", final_output_damage=0, pending=gear(2, item("Plate", 4)))
        job = w01(client)
        assert job["eta_basis"] == "history"
        assert job["eta_seconds"] == pytest.approx(500, abs=5)

    def test_not_from_a_job_of_a_much_different_amount(self, client, api_headers):
        self.run_gear_job(client, api_headers)
        post_job(client, api_headers, "Gear", final_output_damage=0, pending=gear(64, item("Plate", 4)))
        assert w01(client)["eta_seconds"] is None
        assert state.cpu_last_known["W01"]["eta"]["history"] == []


class TestReport:
    def test_replays_the_methods_over_finished_jobs(self, client, api_headers):
        runner = TestFromPastJobs()
        runner.run_gear_job(client, api_headers)
        runner.run_gear_job(client, api_headers)
        entries = eta_report.replay(10)
        # The second job, read against the first one's identical run, at
        # 30% and 50% (at 70% it had too little left to judge).
        history = [e["ratios"]["history"] for e in entries if e["ratios"]["history"] is not None]
        assert history == [pytest.approx(1, abs=0.05)] * 2
        assert all(e["seconds"] == pytest.approx(500, abs=5) for e in entries)

    def test_compares_the_methods_on_the_same_jobs_by_length(self, client, api_headers):
        runner = TestFromPastJobs()
        runner.run_gear_job(client, api_headers)
        runner.run_gear_job(client, api_headers)
        text = eta_report.report(10)
        # The first job has no history, so only the second's checkpoints
        # where the pace had been watched long enough count.
        same = [e for e in eta_report.replay(10) if all(v is not None for v in e["ratios"].values())]
        assert same
        assert f"Only where every method gave one, on the same {len(same)}:" in text
        assert "jobs that took under 5 min (0):" in text
        assert f"jobs that took 5-30 min ({len(same)}):" in text
        assert "jobs that took over 30 min (0):" in text
