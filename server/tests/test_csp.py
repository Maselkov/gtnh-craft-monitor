import base64
import hashlib
import os
import re

from gcm.routes import pages


def csp(client):
    header = client.get("/").headers["Content-Security-Policy"]
    return dict(d.strip().split(" ", 1) for d in header.split(";"))


def test_page_sends_a_strict_policy(client):
    policy = csp(client)
    assert policy["default-src"] == "'self'"
    assert policy["object-src"] == "'none'"
    assert policy["base-uri"] == "'none'"
    assert policy["frame-ancestors"] == "'none'"
    for directive in policy.values():
        assert "'unsafe-inline'" not in directive
        assert "'unsafe-eval'" not in directive


def test_scripts_only_from_self_and_exact_cdn_files(client):
    sources = csp(client)["script-src"].split()
    assert sources[0] == "'self'"
    # Whole-CDN sources would let an injected <script> load any package
    # hosted there - every external entry must be one specific file.
    for source in sources[1:]:
        assert re.fullmatch(r"https://cdn\.jsdelivr\.net/npm/\S+\.js", source), source
    with open(pages.INDEX_HTML_PATH, encoding="utf-8") as f:
        external = set(re.findall(r'<script\b[^>]*\bsrc="(https://[^"]+)"', f.read()))
    assert set(sources[1:]) == external


def test_inline_styles_allowed_only_by_hash_of_index_html_values(client):
    sources = csp(client)["style-src"].split()
    assert sources[:2] == ["'self'", "'unsafe-hashes'"]
    with open(pages.INDEX_HTML_PATH, encoding="utf-8") as f:
        values = set(re.findall(r'\sstyle="([^"]*)"', f.read()))
    expected = {
        "'sha256-%s'" % base64.b64encode(hashlib.sha256(v.encode()).digest()).decode()
        for v in values
    }
    assert set(sources[2:]) == expected


def test_generated_markup_has_no_inline_styles():
    # Hashes only cover index.html's fixed values; a style="..." built by
    # the JS would be blocked (set el.style.* instead).
    js_dir = os.path.join(pages.STATIC_DIR, "js")
    for name in os.listdir(js_dir):
        with open(os.path.join(js_dir, name), encoding="utf-8") as f:
            assert not re.search(r'\sstyle="', f.read()), name


def test_vendored_scripts_are_version_named():
    # Vendored files are served immutable, so the version must be in the
    # filename for an upgrade to bust caches.
    with open(pages.INDEX_HTML_PATH, encoding="utf-8") as f:
        srcs = re.findall(r'<script\b[^>]*\bsrc="/static/(vendor/[^"]+)"', f.read())
    assert srcs
    for src in srcs:
        assert re.search(r"-\d+\.\d+\.\d+[.-]", src), src
        assert os.path.exists(os.path.join(pages.STATIC_DIR, src)), src
