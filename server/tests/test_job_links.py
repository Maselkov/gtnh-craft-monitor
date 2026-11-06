"""Links to one crafting job (gcm/job_links.py): /api/crafts/job, the
page's link preview, and the link in webhooks and MCP results."""

import json
import re
import time

import pytest

from gcm import auth, job_links, state

START = 1_800_000_000.0


@pytest.fixture
def clock(monkeypatch):
    """time.time() at START plus whatever a test moves it on by."""
    now = [START]
    monkeypatch.setattr(time, "time", lambda: now[0])

    def advance(seconds):
        now[0] += seconds
    return advance


def post(client, api_headers, output="Iron Ingot", busy=True, cpu="W01", seen_idle=True, **extra):
    """Reports the CPU. One the server hasn't seen yet is seen idle
    first, unless not seen_idle, so its job is seen from its start."""
    if seen_idle and cpu not in state.cpu_last_busy:
        client.post("/api/crafts", json={"jobs": [{"name": cpu, "busy": False}]}, headers=api_headers)
    job = {"name": cpu, "busy": busy, **extra}
    if output and busy:
        job.update(final_output=output, final_output_mod="minecraft",
                   final_output_internal=output.lower().replace(" ", "_"), final_output_damage=0)
    client.post("/api/crafts", json={"source": "me_controller", "jobs": [job]}, headers=api_headers)


def lookup(client, cpu, t):
    return client.get("/api/crafts/job", query_string={"cpu": cpu, "job": t}).get_json()


def restart():
    with state.tracking_lock:
        state.cpu_last_busy.clear()
        state.cpu_last_known.clear()


def embed(html):
    payload = re.search(r'<script id="discord:component-embed" type="application/json">(.*?)</script>', html)
    return json.loads(payload.group(1))["component"]


def embed_text(html):
    first = embed(html)["components"][0]
    return first["content"] if "content" in first else first["components"][0]["content"]


class TestLookup:
    def test_a_running_job_is_live(self, client, api_headers, clock):
        post(client, api_headers)
        assert lookup(client, "W01", int(START)) == {"state": "live", "cpu": "W01", "started_at": START}

    def test_a_finished_job_is_its_history_row(self, client, api_headers, clock):
        post(client, api_headers)
        clock(125)
        post(client, api_headers, busy=False)
        found = lookup(client, "W01", int(START))
        assert found["state"] == "ended"
        assert found["event"]["itemName"] == "Iron Ingot"
        assert found["event"]["finishedAt"] - found["event"]["startedAt"] == 125

    def test_the_next_job_on_the_cpu_doesnt_answer_for_it(self, client, api_headers, clock):
        post(client, api_headers)
        clock(60)
        post(client, api_headers, "Gold Ingot")  # back to back: never seen idle
        assert lookup(client, "W01", int(START))["event"]["itemName"] == "Iron Ingot"
        assert lookup(client, "W01", int(START) + 60)["state"] == "live"

    def test_a_link_to_nothing_is_gone(self, client, api_headers, clock):
        post(client, api_headers)
        assert lookup(client, "W01", int(START) - 500)["state"] == "gone"
        assert lookup(client, "W02", int(START))["state"] == "gone"

    def test_a_job_seen_anew_after_a_restart_is_still_the_links(self, client, api_headers, clock):
        post(client, api_headers)
        clock(30)
        restart()
        post(client, api_headers, seen_idle=False)  # first seen again, not from its start
        assert lookup(client, "W01", int(START))["state"] == "live"
        clock(30)
        post(client, api_headers, busy=False)
        assert lookup(client, "W01", int(START))["state"] == "ended"

    def test_a_job_seen_from_its_start_after_the_link_is_another(self, client, api_headers, clock):
        # The link's job ended while the server was down, and the CPU was
        # idle when it came back: the next job is someone else's.
        post(client, api_headers)
        clock(30)
        restart()
        post(client, api_headers, busy=False)
        clock(30)
        post(client, api_headers, "Gold Ingot")
        assert lookup(client, "W01", int(START))["state"] == "gone"
        clock(30)
        post(client, api_headers, busy=False)
        assert lookup(client, "W01", int(START))["state"] == "gone"

    def test_a_job_held_for_its_outcome_is_ending(self, client, api_headers, clock):
        post(client, api_headers)
        with state.tracking_lock:
            state.cpu_last_known["W01"]["request_id"] = 7
        post(client, api_headers, busy=False)
        assert lookup(client, "W01", int(START))["state"] == "ending"

    @pytest.mark.parametrize("query", [{}, {"cpu": "W01"}, {"cpu": "W01", "job": "soon"}, {"job": "5"},
                                       {"cpu": "W01", "job": "-5"}])
    def test_a_link_missing_a_part_is_refused(self, client, query):
        assert client.get("/api/crafts/job", query_string=query).status_code == 400


class TestLinkPath:
    def test_odd_cpu_names_survive_the_query(self):
        path = job_links.path("A/B & C?", START + 0.9)
        assert path == "/crafts?cpu=A%2FB+%26+C%3F&job=1800000000"

    def test_url_uses_the_public_address(self, monkeypatch):
        monkeypatch.setattr("gcm.config.PUBLIC_URL", "https://gtnh.example.test")
        assert job_links.url("W01", START) == "https://gtnh.example.test/crafts?cpu=W01&job=1800000000"


class TestPreview:
    def page(self, client, t=int(START), cpu="W01"):
        return client.get("/crafts", query_string={"cpu": cpu, "job": t}).get_data(as_text=True)

    def test_a_running_job(self, client, api_headers, clock):
        post(client, api_headers)
        html = self.page(client)
        assert '<meta property="og:title" content="Iron Ingot">' in html
        assert '<meta property="og:description" content="Crafting on CPU W01' in html
        text = embed_text(html)
        assert text.startswith("## Iron Ingot\nCrafting on CPU W01")
        assert f"Started <t:{int(START)}:R>" in text

    def test_a_running_jobs_estimate_is_a_discord_countdown(self, client, api_headers, clock):
        def report(ingots, ore):
            post(client, api_headers, active=[{"name": "Iron Ingot", "size": ingots}],
                 pending=[{"name": "Iron Ore", "size": ore}], stored=[])
        report(5, 10)
        clock(150)  # the server estimates after two minutes
        report(3, 6)
        [job] = [j for j in client.get("/api/crafts").get_json()["jobs"] if j["busy"]]
        assert job["eta_seconds"]
        html = self.page(client)
        due = int(START + 150 + job["eta_seconds"])
        assert f"Crafting on CPU W01 · 40% · due <t:{due}:R>" in embed_text(html)
        assert f"40% · ~{job_links.duration(job['eta_seconds'])} left\">" in html  # OG: plain text

    def test_a_finished_job(self, client, api_headers, clock):
        post(client, api_headers)
        clock(250)
        post(client, api_headers, busy=False)
        html = self.page(client)
        assert '<meta property="og:description" content="Finished on CPU W01, took 4m 10s">' in html
        assert f"Finished on CPU W01 <t:{int(START) + 250}:R>, took 4m 10s" in embed_text(html)

    def test_a_job_that_is_gone(self, client):
        html = self.page(client)
        assert '<meta property="og:description" content="This craft on CPU W01 has ended">' in html

    def test_game_text_cant_make_markdown(self, client, api_headers, clock):
        post(client, api_headers, output="[x](https://evil)", cpu="**W**")
        text = embed_text(self.page(client, cpu="**W**"))
        assert "\\[x\\]" in text and "CPU \\*\\*W\\*\\*" in text

    def test_a_private_server_previews_nothing(self, client, api_headers, clock):
        post(client, api_headers)
        auth.set_private(True)
        html = self.page(client)
        assert "Iron Ingot" not in html
        assert "discord:component-embed" not in html

    def test_without_a_job_crafts_is_the_cpu_list(self, client, api_headers, clock):
        post(client, api_headers)
        html = client.get("/crafts?cpu=W01").get_data(as_text=True)
        assert '<meta property="og:title" content="Crafts">' in html


class TestAssistant:
    def test_current_crafts_and_history_link_the_job(self, client, api_headers, clock, monkeypatch):
        from gcm.mcp import tools
        monkeypatch.setattr("gcm.config.PUBLIC_URL", "https://gtnh.example.test")
        post(client, api_headers)
        link = "https://gtnh.example.test/crafts?cpu=W01&job=1800000000"
        assert tools.current_crafts(None, {})["busy_cpus"][0]["link"] == link
        clock(10)
        post(client, api_headers, busy=False)
        assert tools.craft_history(None, {})["events"][0]["link"] == link
