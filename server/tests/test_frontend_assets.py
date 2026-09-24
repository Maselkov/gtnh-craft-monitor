import os
import re

from gcm.routes import pages

STATIC_DIR = pages.STATIC_DIR


def asset_paths(html):
    return re.findall(r'(?:src|href)="/static/([^"?]+)\?v=([0-9a-f]+)"', html)


def test_index_references_existing_versioned_assets(client):
    html = client.get("/").get_data(as_text=True)
    assert "__ASSET_VERSION__" not in html
    assets = asset_paths(html)
    assert assets
    for path, version in assets:
        assert os.path.isfile(os.path.join(STATIC_DIR, path)), path
        assert version == pages._asset_version()


def test_every_script_is_loaded_and_main_is_last(client):
    html = client.get("/").get_data(as_text=True)
    scripts = [p for p, _ in asset_paths(html) if p.startswith("js/")]
    on_disk = {f"js/{name}" for name in os.listdir(os.path.join(STATIC_DIR, "js"))}
    # A new file under static/js/ that index.html forgets to load would
    # only fail in the browser, at the first call into it.
    assert set(scripts) == on_disk
    assert scripts[-1] == "js/main.js"


def test_static_files_are_served(client):
    response = client.get("/static/js/main.js")
    assert response.status_code == 200
    assert "javascript" in response.content_type
