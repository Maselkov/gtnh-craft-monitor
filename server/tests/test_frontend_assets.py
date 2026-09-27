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


def test_main_is_the_only_module_and_reaches_every_module(client):
    html = client.get("/").get_data(as_text=True)
    local_scripts = re.findall(r'<script([^>]*)src="/static/([^"?]+)', html)
    # boot.js only reports a module graph that failed to load.
    assert [s for s in local_scripts if not s[1].startswith("vendor/")] == [
        (" ", "boot.js"),
        (' type="module" ', "js/main.js"),
    ]

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


def test_served_modules_import_this_release(client):
    # Every import is versioned like main.js, so a browser can't pair a
    # new module with an old one it has cached.
    version = pages._asset_version()
    for name in os.listdir(os.path.join(STATIC_DIR, "js")):
        with open(os.path.join(STATIC_DIR, "js", name), encoding="utf-8") as f:
            source_imports = IMPORT.findall(f.read())
        served = client.get(f"/static/js/{name}").get_data(as_text=True)
        assert re.findall(r"from\s+'\./([\w-]+\.js)\?v=" + version + "';", served) == source_imports, name
        assert not re.search(r"from\s+'\./[\w-]+\.js';", served), name


def test_module_revalidation_returns_304(client):
    first = client.get("/static/js/crafts.js")
    again = client.get("/static/js/crafts.js", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
    assert client.get("/static/js/nope.js").status_code == 404


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
