import os
import time

import pytest

from gcm import config, gamedata, gamedata_check, state
from conftest import login_as
from test_gamedata import admin, github, install, make_bundle, put_bundle, shared_dir  # noqa: F401


def set_patterns(*items):
    """A pattern scan with one pattern per (mod:name, label) given."""
    scanned = []
    for item_id, label in items:
        mod, internal = item_id.split(":", 1)
        scanned.append({"inputs": [{"kind": "item", "mod": mod, "internal": internal, "damage": 0, "name": label,
                                    "size": 1}],
                        "outputs": [{"kind": "item", "mod": "test", "internal": "thing", "damage": 0,
                                     "name": "Thing", "size": 1}]})
    with state.patterns_lock:
        state.patterns["patterns"] = scanned
        state.patterns["updated_at"] = time.time()


def notice(client):
    deadline = time.time() + 10
    while True:
        response = client.get("/api/admin/gamedata-notice")
        assert response.status_code == 200
        body = response.get_json()
        if not body.get("pending"):
            return body
        assert time.time() < deadline, "the check didn't finish"
        time.sleep(0.02)


def catalog_downloads(github):
    return github.downloads.count("item_catalog.txt")


def test_versions_go_in_gtnh_order_not_publish_order():
    order = ["2.8.3", "2.8.4", "2.9.0-beta-1", "2.9.0-beta-2", "2.9.0-RC-1", "2.9.0-RC-2", "2.9.0", "2.10.0"]
    assert sorted(reversed(order), key=gamedata.version_key) == order
    assert gamedata.version_key("2.9.0-mypack") == gamedata.version_key("2.9.0")
    assert gamedata.version_key("2.9.0-rc-1") == gamedata.version_key("2.9.0-RC-1")
    assert gamedata.version_key("nightly-123") is None and gamedata.version_key("dev") is None
    assert gamedata.is_prerelease("2.9.0-RC-1") and gamedata.is_prerelease("2.9.0-beta-2")
    assert not gamedata.is_prerelease("2.9.0") and not gamedata.is_prerelease("dev")


def test_nothing_without_game_data(admin):
    set_patterns(("test:new", "New Thing"))
    assert notice(admin) == {}


def test_items_a_newer_version_has_suggest_it(admin, github):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    github.bundles["2.9.0-RC-1"] = make_bundle("2.9.0-RC-1", catalog="test:thing\ntest:new\n")
    github.bundles["2.9.0"] = make_bundle("2.9.0", catalog="test:thing\ntest:new\n")
    install(admin, "2.8.4")
    set_patterns(("test:new", "§aNew§r Thing"), ("test:hidden", "Hidden Thing"), ("test:thing", "Thing"))

    assert notice(admin) == {
        "kind": "behind", "selected": "2.8.4",
        # The final release over its own RC, which had them first.
        "suggest": "2.9.0", "count": 1, "suggest_count": 1,
        "ids": [{"id": "test:new", "label": "New Thing", "version": "2.9.0-RC-1"}],
        # In no published catalog: NEI-hidden, or a modified pack's.
        "unpublished_count": 1, "unpublished": [{"id": "test:hidden", "label": "Hidden Thing"}],
    }


def test_the_version_with_the_most_of_them_is_suggested(admin, github):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    github.bundles["2.9.0"] = make_bundle("2.9.0", catalog="test:thing\ntest:a\n")
    github.bundles["2.9.1"] = make_bundle("2.9.1", catalog="test:thing\ntest:a\ntest:b\n")
    install(admin, "2.8.4")
    set_patterns(("test:a", "A"), ("test:b", "B"))

    body = notice(admin)
    assert (body["suggest"], body["count"], body["suggest_count"]) == ("2.9.1", 2, 2)
    assert [(i["id"], i["version"]) for i in body["ids"]] == [("test:a", "2.9.0"), ("test:b", "2.9.1")]


def test_items_no_newer_version_has_bring_no_notice(admin, github):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    github.bundles["2.9.0"] = make_bundle("2.9.0")
    install(admin, "2.8.4")
    set_patterns(("test:hidden", "Hidden Thing"))
    assert notice(admin) == {}


def test_crafting_jobs_count_too(admin, github, client, api_headers):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    github.bundles["2.9.0"] = make_bundle("2.9.0", catalog="test:thing\ntest:new\n")
    install(admin, "2.8.4")
    job = {"name": "cpu1", "busy": True, "final_output": "Thing", "final_output_mod": "test",
           "final_output_internal": "thing", "final_output_damage": 0,
           "active": [], "pending": [], "stored": [{"name": "New Thing", "mod": "test", "internal": "new", "size": 1}]}
    assert client.post("/api/crafts", json={"jobs": [job]}, headers=api_headers).status_code == 200
    # Still counted once the job is gone from the reports.
    assert client.post("/api/crafts", json={"jobs": []}, headers=api_headers).status_code == 200

    body = notice(admin)
    assert (body["kind"], body["suggest"]) == ("behind", "2.9.0")
    assert body["ids"] == [{"id": "test:new", "label": "New Thing", "version": "2.9.0"}]


def test_a_prerelease_is_told_its_final_release_is_out(admin, github):
    github.bundles["2.9.0-RC-1"] = make_bundle("2.9.0-RC-1")
    install(admin, "2.9.0-RC-1")
    assert notice(admin) == {}

    github.bundles["2.9.0"] = make_bundle("2.9.0")
    gamedata.available(refresh=True)
    assert gamedata_check.check("2.9.0-RC-1", {}) == {
        "kind": "prerelease", "selected": "2.9.0-RC-1", "suggest": "2.9.0",
        "unpublished_count": 0, "unpublished": [],
    }
    # A later release isn't the RC's own final one.
    assert gamedata_check.check("2.8.4-RC-1", {}) == {}


def test_catalogs_are_downloaded_once_and_again_when_rebuilt(admin, github):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    github.bundles["2.9.0"] = make_bundle("2.9.0", catalog="test:thing\ntest:new\n")
    install(admin, "2.8.4")
    before = catalog_downloads(github)
    unknown = {"test:new": "New Thing"}

    assert gamedata_check.check("2.8.4", unknown)["suggest"] == "2.9.0"
    assert gamedata_check.check("2.8.4", unknown)["suggest"] == "2.9.0"
    assert catalog_downloads(github) == before + 1

    github.bundles["2.9.0"] = make_bundle("2.9.0", catalog="test:thing\ntest:new\ntest:more\n")
    gamedata.available(refresh=True)
    assert gamedata_check.check("2.8.4", unknown)["suggest"] == "2.9.0"
    assert catalog_downloads(github) == before + 2


def test_cached_catalogs_of_versions_no_longer_newer_are_removed(admin, github):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    github.bundles["2.9.0"] = make_bundle("2.9.0", catalog="test:thing\ntest:new\n")
    install(admin, "2.8.4")
    gamedata_check.check("2.8.4", {"test:new": "New Thing"})
    cached = os.path.join(config.DATA_DIR, "catalogs")
    assert sorted(os.listdir(cached)) == ["2.9.0.json", "2.9.0.txt"]

    gamedata_check.check("2.9.0", {"test:new": "New Thing"})
    assert os.listdir(cached) == []


def test_an_installed_bundle_counts_without_a_release(admin, github, shared_dir):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    install(admin, "2.8.4")
    put_bundle(shared_dir, "2.9.0-mypack", make_bundle("2.9.0-mypack", catalog="test:thing\ntest:new\n"))
    before = catalog_downloads(github)
    set_patterns(("test:new", "New Thing"))

    assert notice(admin)["suggest"] == "2.9.0-mypack"
    assert catalog_downloads(github) == before


def test_without_github_there_is_no_notice(admin, github, monkeypatch):
    github.bundles["2.8.4"] = make_bundle("2.8.4")
    install(admin, "2.8.4")
    monkeypatch.setattr(config, "GAMEDATA_API_URL", "http://127.0.0.1:1")
    gamedata.available(refresh=True)
    assert gamedata_check.check("2.8.4", {"test:new": "New Thing"}) == {}


@pytest.mark.parametrize("role", [None, "viewer", "operator"])
def test_the_notice_is_admin_only(client, role):
    if role:
        login_as(client, f"{role}-user", role=role)
    assert client.get("/api/admin/gamedata-notice").status_code in (401, 403)
