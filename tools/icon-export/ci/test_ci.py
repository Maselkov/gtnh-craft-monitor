"""Tests for the gtnh-data workflow's helper scripts. Run with
python -m pytest tools/icon-export/ci (CI does, in ci.yml)."""

import json
import os
import sys
import zipfile
from io import BytesIO

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import detect  # noqa: E402
import export  # noqa: E402
import validate  # noqa: E402


def release(tag, published="2026-01-01T00:00:00Z", draft=False):
    return {"tag_name": tag, "published_at": published, "draft": draft}


GTNH = [
    release("2.9.0-nightly-2026-09-22", "2026-09-22T00:00:00Z"),
    release("2.9.0-RC-1", "2026-09-24T00:00:00Z"),
    release("2.9.0-beta-3", "2026-09-06T00:00:00Z"),
    release("2.8.4", "2026-06-01T00:00:00Z"),
    release("2.9.0-draft", "2026-09-25T00:00:00Z", draft=True),
]


def exists_only(*urls):
    return lambda url: url in urls


def url(version, java="17-26", betas=True):
    folder = "betas/" if betas else ""
    return f"{detect.DOWNLOADS}{folder}GT_New_Horizons_{version}_Java_{java}.zip"


def test_versions_newest_first_without_nightlies_or_drafts():
    assert detect.gtnh_versions(GTNH) == ["2.9.0-RC-1", "2.9.0-beta-3", "2.8.4"]


def test_skips_published_and_unavailable_versions():
    ours = [release("v1.3.1"), release("gtnh-data-2.9.0-beta-3")]
    exists = exists_only(url("2.8.4", "17-25", betas=False))
    assert detect.plan(GTNH, ours, exists) == [
        {"version": "2.8.4", "pack_url": url("2.8.4", "17-25", betas=False)}
    ]


def test_prereleases_look_in_betas_first():
    assert detect.candidate_urls("2.9.0-RC-1")[0].startswith(
        detect.DOWNLOADS + "betas/")
    assert not detect.candidate_urls("2.8.4")[0].startswith(
        detect.DOWNLOADS + "betas/")


def test_caps_builds_per_run():
    many = [release(f"2.{i}.0", f"2026-0{i}-01T00:00:00Z") for i in range(1, 7)]
    builds = detect.plan(many, [], lambda url: True)
    assert len(builds) == detect.MAX_BUILDS_PER_RUN
    assert builds[0]["version"] == "2.6.0"


def test_manual_version_respects_force_and_pack_url():
    ours = [release("gtnh-data-2.8.4")]
    assert detect.plan(GTNH, ours, version="2.8.4") == []
    builds = detect.plan(GTNH, ours, version="2.8.4",
                         pack_url="https://example.com/p.zip", force=True)
    assert builds == [{"version": "2.8.4",
                       "pack_url": "https://example.com/p.zip"}]


def test_manual_version_without_a_zip_fails():
    with pytest.raises(SystemExit):
        detect.plan(GTNH, [], lambda url: False, version="9.9.9")


def png(pixels, size=4):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for (x, y), colour in pixels.items():
        image.putpixel((x, y), colour)
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def test_flat_silhouette_is_flat():
    assert validate.is_flat(png({(x, 1): (20, 40, 60, 255) for x in range(4)}))


def test_textured_icon_is_not_flat():
    assert not validate.is_flat(png({(0, 0): (200, 10, 10, 255),
                                     (1, 0): (10, 200, 10, 255),
                                     (2, 0): (10, 10, 200, 255)}))


def test_blank_icon_is_not_counted_as_flat():
    assert not validate.is_flat(png({}))


RED = png({(0, 0): (200, 10, 10, 255), (1, 0): (10, 200, 10, 255)})
BLUE = png({(0, 0): (10, 10, 200, 255), (1, 1): (10, 200, 10, 255)})


def write_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)


def test_faithful_zip_falls_back_to_default_renders(tmp_path):
    lookup = {"by_key": {"a:b:0": "item/a/b~0.png", "a:c:0": "item/a/c~0.png"},
              "fluids_by_key": {"goo": "fluid/a/goo.png"},
              "by_label": {"B": "item/a/b~0.png"}}
    (tmp_path / "lookup.json").write_text(json.dumps(lookup))
    write_zip(tmp_path / "default.zip", {"item/a/b~0.png": RED,
                                         "item/a/c~0.png": RED,
                                         "fluid/a/goo.png": RED})
    # The textured pass rendered an extra stack and missed one.
    write_zip(tmp_path / "textured.zip", {"item/a/b~0.png": BLUE,
                                          "fluid/a/goo.png": BLUE,
                                          "item/a/extra~0.png": BLUE})

    fell_back = export.merge_textures(
        tmp_path / "default.zip", tmp_path / "textured.zip",
        tmp_path / "lookup.json", tmp_path / "out.zip", "Thanks\n")

    assert fell_back == 1
    with zipfile.ZipFile(tmp_path / "out.zip") as zf:
        assert sorted(zf.namelist()) == ["CREDITS.txt", "fluid/a/goo.png",
                                         "item/a/b~0.png", "item/a/c~0.png"]
        assert zf.read("item/a/b~0.png") == BLUE
        assert zf.read("item/a/c~0.png") == RED
        assert zf.read("CREDITS.txt") == b"Thanks\n"


def faithful_export(tmp_path, textured_png):
    paths = [f"item/a/i{n}~0.png" for n in range(10)]
    write_zip(tmp_path / "images.zip", {p: RED for p in paths})
    write_zip(tmp_path / "images-faithful32.zip",
              {"CREDITS.txt": "Thanks", **{p: textured_png for p in paths}})
    (tmp_path / "icons_lookup.json").write_text(json.dumps(
        {"by_key": {f"a:i{n}:0": p for n, p in enumerate(paths)}}))
    (tmp_path / "export-report.json").write_text(json.dumps(
        {"by_keyEntries": 50000, "fluids_by_keyEntries": 2000,
         "catalogIds": 9000, "itemsRendered": 100, "itemsFailed": 0}))
    (tmp_path / "data.json").write_text(json.dumps(
        {"textures": {"faithful32": {"default_fallbacks": 0}}}))
    return tmp_path


def test_faithful_icons_that_changed_pass(tmp_path):
    problems, _, _ = validate.check(faithful_export(tmp_path, BLUE))
    assert problems == []


def test_faithful_icons_identical_to_default_fail(tmp_path):
    problems, _, _ = validate.check(faithful_export(tmp_path, RED))
    assert any("resource pack applied" in p for p in problems)


def test_animation_period_finds_the_loop():
    assert export.animation_period(list("ABCABCABCA")) == list("ABC")
    assert export.animation_period(list("AABBAABB")) == list("AABB")
    # Not repeated twice within the capture: keep all of it.
    assert export.animation_period(list("ABCDEFA")) == list("ABCDEFA")


def test_finish_images_writes_apngs_and_drops_unused_icons(tmp_path):
    from PIL import Image, ImageSequence
    still = png({(0, 0): (200, 10, 10, 255), (1, 0): (10, 200, 10, 255)})
    write_zip(tmp_path / "images.zip", {"item/a/anim~0.png": still,
                                        "item/a/flat~0.png": still,
                                        "item/a/other~0.png": BLUE})
    write_zip(tmp_path / "animations.zip", {
        "item/a/anim~0.png/f0.png": still, "item/a/anim~0.png/f1.png": BLUE,
        "item/a/anim~0.png/f2.png": RED,
        # Only ever one frame after all: stays the still icon.
        "item/a/flat~0.png/f0.png": still})
    (tmp_path / "animations.json").write_text(json.dumps({
        "tickMillis": 50,
        "icons": {
            # f0 for 2 ticks, f1 for 1, f2 for 3, twice over.
            "item/a/anim~0.png": [["f0", 2], ["f1", 1], ["f2", 3],
                                  ["f0", 2], ["f1", 1], ["f2", 3]],
            "item/a/flat~0.png": [["f0", 12]],
        }}))

    (tmp_path / "icons_lookup.json").write_text(json.dumps({
        "by_key": {"a:anim:0": "item/a/anim~0.png", "a:flat:0": "item/a/flat~0.png"},
        "fluids_by_key": {}, "by_label": {"Other": "item/a/other~0.png"}}))
    write_zip(tmp_path / "images.zip", {"item/a/anim~0.png": still,
                                        "item/a/flat~0.png": still,
                                        "item/a/other~0.png": BLUE,
                                        # An NBT variant no lookup entry uses.
                                        "item/a/anim~0~nbt.png": still})

    assert export.finish_images(tmp_path) == 1

    assert not (tmp_path / "animations.zip").exists()
    with zipfile.ZipFile(tmp_path / "images.zip") as zf:
        assert "item/a/anim~0~nbt.png" not in zf.namelist()
        assert zf.read("item/a/flat~0.png") == still
        assert zf.read("item/a/other~0.png") == BLUE
        image = Image.open(BytesIO(zf.read("item/a/anim~0.png")))
        assert image.n_frames == 3
        durations = [frame.info["duration"]
                     for frame in ImageSequence.Iterator(image)]
        assert durations == [100, 50, 150]
        image.seek(0)
        assert (image.convert("RGBA").tobytes()
                == Image.open(BytesIO(still)).convert("RGBA").tobytes())


def test_near_loop_ends_where_a_spin_comes_back_round():
    from PIL import Image
    # Frame brightness stands in for the angle: out to 200, back to ~0.
    # f8 is already close enough, but f9 is closer still: the loop ends
    # there so the seam is smallest.
    levels = [0, 50, 100, 150, 200, 150, 100, 50, 3, 1, 50]
    ticks = [f"f{i}" for i in range(len(levels))]
    images = {f"f{i}": Image.new("RGBA", (4, 4), (int(v), 0, 0, 255))
              for i, v in enumerate(levels)}
    assert export.near_loop(ticks, images.__getitem__) == ticks[:9]


def test_near_loop_needs_the_animation_to_move_away_first():
    from PIL import Image
    # Barely changing at all: no loop point, keep the whole capture.
    images = {f"f{i}": Image.new("RGBA", (4, 4), (i % 2, 0, 0, 255))
              for i in range(6)}
    assert export.near_loop(list(images), images.__getitem__) is None


def test_merge_runs_keeps_the_speed_at_lower_frame_rates():
    ticks = list("AABBCCDD")
    assert export.merge_runs(ticks) == [["A", 2], ["B", 2], ["C", 2], ["D", 2]]
    assert export.merge_runs(ticks, 2) == [["A", 2], ["B", 2], ["C", 2], ["D", 2]]
    assert export.merge_runs(list("ABCDEFG"), 2) == [["A", 2], ["C", 2], ["E", 2], ["G", 1]]


def test_oversized_animations_drop_frames_until_they_fit(tmp_path, monkeypatch):
    from PIL import Image
    ticks = [f"f{i}" for i in range(16)]
    frames = {f"p/{t}.png": png({(i % 4, i // 4): (255, 0, 0, 255)})
              for i, t in enumerate(ticks)}
    write_zip(tmp_path / "a.zip", frames)
    with zipfile.ZipFile(tmp_path / "a.zip") as zf:
        full = export.build_within_budget(zf, "p", ticks, 50)
        assert Image.open(BytesIO(full)).n_frames == 16
        # Budget that only a quarter of the frames fit in.
        monkeypatch.setattr(export, "APNG_MAX_BYTES", len(full) // 3)
        smaller = export.build_within_budget(zf, "p", ticks, 50)
        image = Image.open(BytesIO(smaller))
        assert image.n_frames < 16
        # Same total length: the animation keeps its speed.
        total = 0
        for i in range(image.n_frames):
            image.seek(i)
            total += image.info["duration"]
        assert total == 16 * 50


def test_faithful_icon_of_another_size_falls_back(tmp_path):
    # Drawn past the item box in one pass only: the lookup's bleed table
    # describes the default render, so that's the one kept.
    lookup = {"by_key": {"a:b:0": "item/a/b~0.png"},
              "bleed": {"item/a/b~0.png": 12}}
    (tmp_path / "lookup.json").write_text(json.dumps(lookup))
    halo = png({(0, 0): (200, 10, 10, 255)}, size=10)
    write_zip(tmp_path / "default.zip", {"item/a/b~0.png": halo})
    write_zip(tmp_path / "textured.zip", {"item/a/b~0.png": BLUE})

    fell_back = export.merge_textures(
        tmp_path / "default.zip", tmp_path / "textured.zip",
        tmp_path / "lookup.json", tmp_path / "out.zip", "Thanks\n")

    assert fell_back == 1
    with zipfile.ZipFile(tmp_path / "out.zip") as zf:
        assert zf.read("item/a/b~0.png") == halo


def test_lookup_paths_leaves_out_the_bleed_table(tmp_path):
    (tmp_path / "lookup.json").write_text(json.dumps(
        {"by_key": {"a:b:0": "item/a/b~0.png"}, "fluids_by_key": {},
         "by_label": {}, "bleed": {"item/a/b~0.png": 12}}))
    assert export.lookup_paths(tmp_path / "lookup.json") == {"item/a/b~0.png"}


def test_animation_with_frames_of_different_sizes_stays_still(tmp_path):
    write_zip(tmp_path / "a.zip", {
        "p/f0.png": png({(0, 0): (255, 0, 0, 255)}),
        "p/f1.png": png({(0, 0): (0, 255, 0, 255)}, size=10),
    })
    with zipfile.ZipFile(tmp_path / "a.zip") as zf:
        assert export.build_within_budget(zf, "p", ["f0", "f1"], 50) is None


def test_bleed_entries_missing_from_the_zip_fail(tmp_path):
    export_dir = faithful_export(tmp_path, BLUE)
    lookup = json.loads((export_dir / "icons_lookup.json").read_text())
    lookup["bleed"] = {"item/a/gone~0.png": 12}
    (export_dir / "icons_lookup.json").write_text(json.dumps(lookup))
    problems, _, _ = validate.check(export_dir)
    assert any("bleed table" in p for p in problems)
