import hashlib
import io
import json
import os
import shutil
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gcm import config, gamedata, icons, state
from conftest import login_as

PNG = b"\x89PNG\r\n\x1a\nnot really, but the server doesn't care"
FAITHFUL_PNG = b"\x89PNG\r\n\x1a\nthe same thing, in 32x"


def images_zip(png):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("item/test/thing~0.png", png)
        zf.writestr("fluid/test/goo.png", png)
    return zip_buffer.getvalue()


def make_bundle(version, catalog="test:thing\n", faithful=True, generated_at="2026-09-25T00:00:00Z"):
    """The files CI publishes for one GTNH version, as {name: bytes}."""
    files = {
        "images.zip": images_zip(PNG),
        "icons_lookup.json": json.dumps(
            {
                "by_key": {"test:thing:0": "item/test/thing~0.png"},
                "fluids_by_key": {"goo": "fluid/test/goo.png"},
                "by_label": {"Thing": "item/test/thing~0.png"},
            }
        ).encode(),
        "item_catalog.txt": catalog.encode(),
    }
    if faithful:
        files["images-faithful32.zip"] = images_zip(FAITHFUL_PNG)
    files["data.json"] = json.dumps(
        {
            "format": 1,
            "gtnh_version": version,
            "generated_at": generated_at,
            "files": {
                name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in files.items()
            },
        }
    ).encode()
    return files


class FakeGitHub:
    """Serves a releases listing plus asset downloads, like GitHub does."""

    def __init__(self):
        self.bundles = {}
        self.releases_requests = 0
        self.downloads = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/repos/"):
                    fake.releases_requests += 1
                    body = json.dumps(fake.listing()).encode()
                else:
                    _, version, name = self.path.split("/", 2)
                    fake.downloads.append(name)
                    body = fake.bundles.get(version, {}).get(name)
                    if body is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def listing(self):
        return [
            {"tag_name": "v1.3.1", "assets": []},
        ] + [
            {
                "tag_name": f"gtnh-data-{version}",
                "published_at": f"2026-09-{10 + i:02d}T00:00:00Z",
                "assets": [
                    {
                        "name": name,
                        "size": len(data),
                        "browser_download_url": f"{self.url}/{version}/{name}",
                        # A new id for every upload, as GitHub does.
                        "id": int(hashlib.sha256(data).hexdigest()[:8], 16),
                    }
                    for name, data in files.items()
                ],
            }
            for i, (version, files) in enumerate(self.bundles.items())
        ]


@pytest.fixture()
def github(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(config, "GAMEDATA_API_URL", fake.url)
    monkeypatch.setattr(config, "GAMEDATA_REPO", "owner/repo")
    yield fake
    fake.server.shutdown()
    shutil.rmtree(config.GAMEDATA_DIR, ignore_errors=True)
    gamedata.activate()


@pytest.fixture()
def admin(client):
    return login_as(client, "admin-user", role="admin")


def prefix(version, textures=None):
    """The icon path prefix: version, texture set and build."""
    parts = [version] + ([textures] if textures else []) + [gamedata.build_id(version)]
    return "~".join(parts)


def wait_for_install():
    deadline = time.time() + 10
    while gamedata.status()["state"] == "installing":
        assert time.time() < deadline, "install didn't finish"
        time.sleep(0.02)
    return gamedata.status()


def test_lists_published_bundles_newest_first(admin, github):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    github.bundles["2.9.0-RC-1"] = make_bundle("2.9.0-RC-1")

    data = admin.get("/api/admin/gamedata").get_json()

    assert [r["version"] for r in data["available"]] == ["2.9.0-RC-1", "2.9.0-beta-3"]
    assert set(data["available"][0]["textures"]) == {"default", "faithful32"}
    assert [t["id"] for t in data["textures"]] == ["default", "faithful32"]
    assert "Ethryan" in data["textures"][1]["credit"]
    assert data["selected"] is None
    assert data["available_error"] is None


def test_release_listing_is_cached(admin, github):
    admin.get("/api/admin/gamedata")
    admin.get("/api/admin/gamedata")
    assert github.releases_requests == 1
    admin.get("/api/admin/gamedata?refresh=1")
    assert github.releases_requests == 2


def test_listing_error_is_reported_not_raised(admin, monkeypatch):
    monkeypatch.setattr(config, "GAMEDATA_API_URL", "http://127.0.0.1:1")
    data = admin.get("/api/admin/gamedata").get_json()
    assert data["available"] == []
    assert "Couldn't list" in data["available_error"]


def test_game_data_is_admin_only(client, github):
    assert client.get("/api/admin/gamedata").status_code == 403
    login_as(client, "op", role="operator")
    assert client.get("/api/admin/gamedata").status_code == 403
    assert client.post("/api/admin/gamedata/install", json={"version": "x"}).status_code == 403


def test_install_makes_the_bundle_live(admin, github, api_headers):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")

    response = admin.post("/api/admin/gamedata/install", json={"version": "2.9.0-beta-3"})
    assert response.status_code == 202
    assert wait_for_install()["state"] == "done"

    assert gamedata.selected_version() == "2.9.0-beta-3"
    path = icons.resolve_icon("test", "thing", 0, "Thing")
    assert path == f"{prefix('2.9.0-beta-3')}/item/test/thing~0.png"
    assert admin.get(f"/icons?path={path}").data == PNG
    assert icons.resolve_icon(None, "goo", None, None) == f"{prefix('2.9.0-beta-3')}/fluid/test/goo.png"

    catalog = admin.get("/api/network/catalog", headers=api_headers)
    assert catalog.data == b"test:thing\n"
    assert catalog.headers["X-Catalog-Version"] == prefix("2.9.0-beta-3")
    start = admin.post("/api/network/scan/start", headers=api_headers, json={})
    assert start.get_json()["catalog_version"] == prefix("2.9.0-beta-3")


def test_checksum_mismatch_fails_and_keeps_the_old_data(admin, github):
    bundle = make_bundle("2.9.0-beta-3")
    bundle["images.zip"] = bundle["images.zip"][:-1] + b"X"
    github.bundles["2.9.0-beta-3"] = bundle

    admin.post("/api/admin/gamedata/install", json={"version": "2.9.0-beta-3"})
    status = wait_for_install()

    assert status["state"] == "error"
    assert "checksum" in status["error"]
    assert gamedata.selected_version() is None
    assert not os.path.exists(os.path.join(config.GAMEDATA_DIR, "2.9.0-beta-3.part"))


def test_switching_back_to_an_installed_version_needs_no_download(admin, github):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    github.bundles["2.9.0-RC-1"] = make_bundle("2.9.0-RC-1")
    for version in ("2.9.0-beta-3", "2.9.0-RC-1"):
        admin.post("/api/admin/gamedata/install", json={"version": version})
        wait_for_install()
    github.bundles.clear()

    response = admin.post("/api/admin/gamedata/install", json={"version": "2.9.0-beta-3"})

    assert response.status_code == 202
    assert response.get_json()["selected"] == "2.9.0-beta-3"
    assert icons.data_version() == prefix("2.9.0-beta-3")


def test_only_the_selected_and_previous_bundles_are_kept(admin, github):
    versions = ("2.8.4", "2.9.0-beta-3", "2.9.0-RC-1")
    for version in versions:
        github.bundles[version] = make_bundle(version)
    for version in versions:
        admin.post("/api/admin/gamedata/install", json={"version": version})
        wait_for_install()

    assert sorted(v["version"] for v in gamedata.installed()) == ["2.9.0-RC-1", "2.9.0-beta-3"]


def test_unknown_and_unsafe_versions_are_rejected(admin, github):
    for version in ("9.9.9", "../../etc", "", None):
        response = admin.post("/api/admin/gamedata/install", json={"version": version})
        assert response.status_code == 409


def test_install_refreshes_icons_on_the_live_snapshot(admin, github):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    state.network["items"] = [{"mod": "test", "internal": "thing", "damage": 0, "name": "Thing"}]
    etag_before = admin.get("/api/network").headers["ETag"]

    admin.post("/api/admin/gamedata/install", json={"version": "2.9.0-beta-3"})
    wait_for_install()

    response = admin.get("/api/network")
    assert response.get_json()["items"][0]["icon"] == f"{prefix('2.9.0-beta-3')}/item/test/thing~0.png"
    assert response.headers["ETag"] != etag_before


def test_without_a_bundle_the_catalog_is_404_and_paths_are_unprefixed(client, api_headers, monkeypatch):
    monkeypatch.setattr(icons, "_icons_by_key", {"a:b:0": "item/a/b~0.png"})
    assert client.get("/api/network/catalog", headers=api_headers).status_code == 404
    assert icons.resolve_icon("a", "b", 0, None) == "item/a/b~0.png"
    start = client.post("/api/network/scan/start", headers=api_headers, json={})
    assert start.get_json()["catalog_version"] is None


def test_catalog_needs_the_api_key(client, github):
    assert client.get("/api/network/catalog").status_code == 401


def test_read_image_accepts_old_unprefixed_paths(admin, github):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    admin.post("/api/admin/gamedata/install", json={"version": "2.9.0-beta-3"})
    wait_for_install()

    # Craft history keeps whatever path was current when it was recorded.
    assert icons.read_image("item/test/thing~0.png") == PNG
    assert icons.read_image("2.8.4/item/test/thing~0.png") == PNG


def test_configured_version_is_installed_at_startup(github, monkeypatch):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    monkeypatch.setattr(config, "GTNH_VERSION", "2.9.0-beta-3")

    gamedata.install_configured_version()

    assert wait_for_install()["state"] == "done"
    assert gamedata.selected_version() == "2.9.0-beta-3"


def install(admin, version, textures="default"):
    response = admin.post(
        "/api/admin/gamedata/install", json={"version": version, "textures": textures}
    )
    assert response.status_code == 202, response.get_json()
    return wait_for_install()


def test_faithful_textures_are_served(admin, github):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")

    assert install(admin, "2.9.0-beta-3", "faithful32")["state"] == "done"

    assert gamedata.selected() == ("2.9.0-beta-3", "faithful32")
    assert "images.zip" not in github.downloads
    path = icons.resolve_icon("test", "thing", 0, "Thing")
    assert path == f"{prefix('2.9.0-beta-3', 'faithful32')}/item/test/thing~0.png"
    assert admin.get(f"/icons?path={path}").data == FAITHFUL_PNG


def test_adding_textures_to_an_installed_version_fetches_only_their_zip(admin, github):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    install(admin, "2.9.0-beta-3")
    github.downloads.clear()

    status = install(admin, "2.9.0-beta-3", "faithful32")

    assert status["state"] == "done"
    assert github.downloads == ["data.json", "images-faithful32.zip"]
    assert gamedata.installed()[0]["textures"] == ["default", "faithful32"]
    # Both are on disk now, so switching back is instant.
    github.bundles.clear()
    response = admin.post(
        "/api/admin/gamedata/install", json={"version": "2.9.0-beta-3", "textures": "default"}
    )
    assert response.get_json()["selected_textures"] == "default"
    assert icons.data_version() == prefix("2.9.0-beta-3")


def test_a_rebuilt_release_is_downloaded_again_in_full(admin, github):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    install(admin, "2.9.0-beta-3")
    github.bundles["2.9.0-beta-3"] = make_bundle(
        "2.9.0-beta-3", catalog="test:thing\ntest:new\n", generated_at="2026-09-26T00:00:00Z"
    )
    github.downloads.clear()

    install(admin, "2.9.0-beta-3", "faithful32")

    assert sorted(github.downloads) == sorted(
        ["data.json", "icons_lookup.json", "item_catalog.txt", "images-faithful32.zip"]
    )
    # The old default zip went with the old lookup.
    assert gamedata.installed()[0]["textures"] == ["faithful32"]
    with open(gamedata.catalog_path(), encoding="utf-8") as f:
        assert f.read() == "test:thing\ntest:new\n"


def test_textures_a_release_lacks_are_refused(admin, github):
    github.bundles["2.8.4"] = make_bundle("2.8.4", faithful=False)
    response = admin.post(
        "/api/admin/gamedata/install", json={"version": "2.8.4", "textures": "faithful32"}
    )
    assert response.status_code == 409
    assert "no Faithful 32x icons" in response.get_json()["error"]
    response = admin.post(
        "/api/admin/gamedata/install", json={"version": "2.8.4", "textures": "../x"}
    )
    assert response.status_code == 409


def test_configured_textures_are_installed_at_startup(github, monkeypatch):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    monkeypatch.setattr(config, "GTNH_VERSION", "2.9.0-beta-3")
    monkeypatch.setattr(config, "GTNH_TEXTURES", "faithful32")

    gamedata.install_configured_version()

    assert wait_for_install()["state"] == "done"
    assert gamedata.selected() == ("2.9.0-beta-3", "faithful32")


def test_a_pinned_version_keeps_the_textures_picked_on_the_page(admin, github, monkeypatch):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    install(admin, "2.9.0-beta-3", "faithful32")
    monkeypatch.setattr(config, "GTNH_VERSION", "2.9.0-beta-3")
    monkeypatch.setattr(config, "GTNH_TEXTURES", "")

    gamedata.install_configured_version()

    assert gamedata.status()["state"] != "installing"
    assert gamedata.selected() == ("2.9.0-beta-3", "faithful32")


def apng():
    from PIL import Image

    frames = [Image.new("RGBA", (4, 4), colour) for colour in ((255, 0, 0, 255), (0, 0, 255, 255))]
    out = io.BytesIO()
    frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return out.getvalue()


def test_animated_icons_can_be_fetched_as_a_still_frame(admin, github, monkeypatch):
    from PIL import Image

    bundle = make_bundle("2.9.0-beta-3")
    animated = apng()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("item/test/thing~0.png", animated)
        zf.writestr("fluid/test/goo.png", PNG)
    bundle["images.zip"] = zip_buffer.getvalue()
    meta = json.loads(bundle["data.json"])
    meta["files"]["images.zip"] = {
        "size": len(bundle["images.zip"]),
        "sha256": hashlib.sha256(bundle["images.zip"]).hexdigest(),
    }
    bundle["data.json"] = json.dumps(meta).encode()
    github.bundles["2.9.0-beta-3"] = bundle
    install(admin, "2.9.0-beta-3")
    path = icons.resolve_icon("test", "thing", 0, "Thing")

    assert admin.get(f"/icons?path={path}").data == animated
    still = Image.open(io.BytesIO(admin.get(f"/icons?path={path}&still=1").data))
    assert getattr(still, "n_frames", 1) == 1
    assert still.convert("RGBA").getpixel((0, 0)) == (255, 0, 0, 255)
    # Plain PNGs come back untouched.
    goo = icons.resolve_icon(None, "goo", None, None)
    assert admin.get(f"/icons?path={goo}&still=1").data == PNG


def test_a_rebuilt_release_is_offered_as_an_update_and_reinstalled(admin, github, api_headers):
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3")
    install(admin, "2.9.0-beta-3")
    old_prefix = prefix("2.9.0-beta-3")
    listing = admin.get("/api/admin/gamedata").get_json()
    assert listing["available"][0]["update_available"] is False

    # CI rebuilt the version: same name, new upload.
    github.bundles["2.9.0-beta-3"] = make_bundle(
        "2.9.0-beta-3", catalog="test:thing\ntest:new\n", generated_at="2026-09-26T00:00:00Z"
    )
    listing = admin.get("/api/admin/gamedata?refresh=1").get_json()
    assert listing["available"][0]["update_available"] is True
    github.downloads.clear()

    # Picking it again downloads it again rather than switching to the old copy.
    assert install(admin, "2.9.0-beta-3")["state"] == "done"

    assert "images.zip" in github.downloads
    listing = admin.get("/api/admin/gamedata").get_json()
    assert listing["available"][0]["update_available"] is False
    # New build, new icon URLs and catalog version, so browsers and the
    # scanner fetch them again.
    assert prefix("2.9.0-beta-3") != old_prefix
    assert icons.resolve_icon("test", "thing", 0, None).startswith(prefix("2.9.0-beta-3") + "/")
    start = admin.post("/api/network/scan/start", headers=api_headers, json={})
    assert start.get_json()["catalog_version"] == prefix("2.9.0-beta-3")
    assert admin.get("/api/network/catalog", headers=api_headers).data == b"test:thing\ntest:new\n"


def test_locally_built_bundles_are_never_flagged_as_outdated(admin, github):
    # run.sh --install writes a bundle without a release record.
    bundle = make_bundle("2.9.0-beta-3")
    target = os.path.join(config.GAMEDATA_DIR, "2.9.0-beta-3")
    os.makedirs(target)
    for name, data in bundle.items():
        with open(os.path.join(target, name), "wb") as f:
            f.write(data)
    github.bundles["2.9.0-beta-3"] = make_bundle("2.9.0-beta-3", generated_at="2026-09-27T00:00:00Z")

    listing = admin.get("/api/admin/gamedata").get_json()
    assert listing["available"][0]["update_available"] is False
    github.downloads.clear()
    response = admin.post("/api/admin/gamedata/install", json={"version": "2.9.0-beta-3"})
    assert response.get_json()["selected"] == "2.9.0-beta-3"
    assert github.downloads == []
