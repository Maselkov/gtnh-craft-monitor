"""The bundle index (gcm/gamedata_index.py) that icons and the ore
dictionary are read through instead of holding them in memory."""

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile

from gcm import config, gamedata, gamedata_index, icons
from test_gamedata import PNG, admin, github, install, make_bundle  # noqa: F401 (fixtures)

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def index_file(version):
    return os.path.join(config.GAMEDATA_DIR, version, gamedata_index.FILE)


def test_the_index_is_built_once_and_again_when_textures_are_added(admin, github):
    github.bundles["2.9.0"] = make_bundle("2.9.0")
    install(admin, "2.9.0")
    built = os.stat(index_file("2.9.0")).st_mtime_ns

    gamedata.activate()
    assert os.stat(index_file("2.9.0")).st_mtime_ns == built

    install(admin, "2.9.0", "faithful32")
    assert os.stat(index_file("2.9.0")).st_mtime_ns != built
    assert icons.read_image(icons.resolve_icon("test", "thing", 0, None)) is not None


def test_icons_read_from_the_zip_match_zipfiles_own(admin, github):
    # Stored and deflated entries both, as a texture zip may hold either.
    big = PNG + bytes(range(256)) * 64
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("item/test/thing~0.png", big, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("fluid/test/goo.png", PNG, compress_type=zipfile.ZIP_STORED)
    github.bundles["2.9.0"] = rehashed(make_bundle("2.9.0") | {"images.zip": buf.getvalue()})
    install(admin, "2.9.0")

    assert icons.read_image(icons.resolve_icon("test", "thing", 0, None)) == big
    assert icons.read_image(icons.resolve_icon(None, "goo", None, None)) == PNG
    assert icons.read_image("item/test/missing.png") is None


def test_a_corrupt_icon_reads_as_none(admin, github):
    github.bundles["2.9.0"] = make_bundle("2.9.0")
    install(admin, "2.9.0")
    path = os.path.join(config.GAMEDATA_DIR, "2.9.0", "images.zip")
    with open(path, "r+b") as f:
        data = f.read()
        at = data.index(PNG)
        f.seek(at)
        f.write(b"X")

    assert icons.read_image("item/test/thing~0.png") is None


def test_a_failed_index_build_means_no_icons_not_a_crash(admin, github, monkeypatch, caplog):
    github.bundles["2.9.0"] = make_bundle("2.9.0")
    install(admin, "2.9.0")
    os.remove(index_file("2.9.0"))
    monkeypatch.setattr(gamedata_index, "__file__", "/nonexistent/gamedata_index.py")

    gamedata.activate()

    assert icons.resolve_icon("test", "thing", 0, "Thing") is None
    assert "Indexing the 2.9.0 game data failed" in caplog.text


def test_a_closed_index_finds_nothing(tmp_path):
    (tmp_path / "data.json").write_text("{}")
    (tmp_path / "ore_dict.json").write_text('{"gemDiamond": ["minecraft:diamond:0"]}')
    gamedata_index.build(str(tmp_path), str(tmp_path / "index.sqlite"))
    index = gamedata_index.Index(str(tmp_path / "index.sqlite"))
    assert index.ore_names("minecraft", "diamond", 0) == ["gemDiamond"]
    index.close()
    # A request that started before another bundle went live.
    assert index.ore_names("minecraft", "diamond", 0) == []
    assert index.icon("by_key", "x") is None


def test_startup_leaves_charts_and_push_libraries_unloaded(tmp_path):
    # They're ~70 MB, and most servers never draw a chart or send a push.
    code = (
        "import sys; from gcm import create_app; create_app(sys.argv[1]); "
        "print(sorted(m for m in ('matplotlib', 'numpy', 'pywebpush', 'cryptography') if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], cwd=SERVER_DIR, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip().splitlines()[-1] == "[]"


def rehashed(files):
    """files with data.json's sizes and checksums brought up to date."""
    meta = json.loads(files["data.json"])
    for name, data in files.items():
        if name != "data.json":
            meta["files"][name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return files | {"data.json": json.dumps(meta).encode()}
