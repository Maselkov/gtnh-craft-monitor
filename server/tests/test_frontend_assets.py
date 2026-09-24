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


IMPORT = re.compile(r"""^import\s[^;]*?from\s+'\./([\w-]+\.js)';""", re.MULTILINE | re.DOTALL)


def test_main_is_the_only_script_and_reaches_every_module(client):
    html = client.get("/").get_data(as_text=True)
    local_scripts = re.findall(r'<script([^>]*)src="/static/([^"?]+)', html)
    assert local_scripts == [(' type="module" ', "js/main.js")]

    # Walk the import graph from main.js: a module nothing imports would
    # never load, and would only fail in the browser.
    reachable, todo = set(), ["main.js"]
    while todo:
        name = todo.pop()
        if name in reachable:
            continue
        reachable.add(name)
        with open(os.path.join(STATIC_DIR, "js", name), encoding="utf-8") as f:
            todo.extend(IMPORT.findall(f.read()))
    on_disk = set(os.listdir(os.path.join(STATIC_DIR, "js")))
    assert reachable == on_disk


def test_static_files_are_served_and_revalidated(client):
    response = client.get("/static/js/main.js")
    assert response.status_code == 200
    assert "javascript" in response.content_type
    # Modules import each other without ?v=, so they must never be
    # reused from cache without checking.
    assert response.headers["Cache-Control"] == "no-cache"


def test_no_inline_event_handlers():
    # All handlers are registered from static/js (data-action + listeners);
    # an inline on*="..." attribute would also break a script-src CSP.
    inline = re.compile(r"""\son[a-z]+\s*=\s*["']""", re.IGNORECASE)
    paths = [pages.INDEX_HTML_PATH] + [
        os.path.join(STATIC_DIR, "js", name) for name in os.listdir(os.path.join(STATIC_DIR, "js"))
    ]
    offenders = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            hits = [line.strip() for line in f if inline.search(line)]
        if hits:
            offenders[os.path.basename(path)] = hits
    assert offenders == {}
