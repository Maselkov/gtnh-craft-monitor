#!/usr/bin/env python3
"""
What changed between two icon exports, and how.

    python3 compare_exports.py <base> <new> [--base2 <base>] [--limit N]

Each argument is an export's out directory or its images.zip. Every icon
whose bytes differ is decoded and put in one class:

- still pixels: a still icon drew differently
- became animated / became still: the animation stages classed it the
  other way
- animation timing: the frames' count or durations differ (a different
  loop or frame rate was picked, or the capture ran a different length)
- animation pixels: the same timing, but some frames drew differently
- same pixels: different bytes for the same image (the encoder)
- only in base / only in new

Two runs of the same code aren't identical (see CONTEXT.md, "Icon
export"), so a change can't be judged from one before/after diff. With
--base2, a second run of the base code, only icons both base runs agree
on are counted; the ones they disagree on are summed up as noise.

Needs Pillow.
"""
import argparse
import collections
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageSequence

TICK_MILLIS = 50
CLASSES = ("still pixels", "became animated", "became still",
           "animation timing", "animation pixels", "same pixels",
           "only in base", "only in new")


def images_zip(path):
    path = Path(path)
    return path / "images.zip" if path.is_dir() else path


def read_all(path):
    with zipfile.ZipFile(images_zip(path)) as z:
        return {name: z.read(name) for name in z.namelist()
                if not name.endswith("/") and name != "CREDITS.txt"}


def frames(data):
    """[(RGBA image, ms)] for an image, one entry for a still."""
    image = Image.open(BytesIO(data))
    if getattr(image, "n_frames", 1) == 1:
        return [(image.convert("RGBA"), 0)]
    return [(frame.convert("RGBA"), frame.info.get("duration", 0))
            for frame in ImageSequence.Iterator(image)]


def pixel_difference(a, b):
    """(pixels that differ, largest channel difference); None if the
    sizes differ."""
    if a.size != b.size:
        return None
    diff = ImageChops.difference(a, b)
    bands = diff.split()
    largest = max(band.getextrema()[1] for band in bands)
    if not largest:
        return 0, 0
    mask = bands[0]
    for band in bands[1:]:
        mask = ImageChops.lighter(mask, band)
    changed = sum(mask.point(lambda v: 255 if v else 0).histogram()[255:])
    return changed, largest


def classify(old, new):
    """(class, detail) for two different encodings of one icon."""
    a, b = frames(old), frames(new)
    if len(a) == 1 and len(b) == 1:
        diff = pixel_difference(a[0][0], b[0][0])
        if diff is None:
            return "still pixels", f"size {a[0][0].size} -> {b[0][0].size}"
        if diff == (0, 0):
            return "same pixels", ""
        return "still pixels", f"{diff[0]} px, up to {diff[1]}"
    if len(a) == 1:
        return "became animated", f"{len(b)} frames"
    if len(b) == 1:
        return "became still", f"was {len(a)} frames"
    times_a = [ms for _, ms in a]
    times_b = [ms for _, ms in b]
    if times_a != times_b:
        return "animation timing", (
            f"{len(a)} frames, {sum(times_a)} ms -> "
            f"{len(b)} frames, {sum(times_b)} ms")
    changed, largest, pixels = [], 0, 0
    for i, ((x, _), (y, _)) in enumerate(zip(a, b, strict=True)):
        diff = pixel_difference(x, y)
        if diff is None:
            return "animation pixels", f"frame {i} size {x.size} -> {y.size}"
        if diff != (0, 0):
            changed.append(i)
            pixels = max(pixels, diff[0])
            largest = max(largest, diff[1])
    if not changed:
        return "same pixels", ""
    shown = ",".join(map(str, changed[:8])) + ("..." if len(changed) > 8 else "")
    return "animation pixels", (
        f"{len(changed)}/{len(a)} frames ({shown}), "
        f"up to {pixels} px, up to {largest}")


def compare(base, new, names=None):
    """{name: (class, detail)} for every icon that differs."""
    found = {}
    for name in sorted(names if names is not None else base.keys() | new.keys()):
        if name not in new:
            found[name] = ("only in base", "")
        elif name not in base:
            found[name] = ("only in new", "")
        elif base[name] != new[name]:
            found[name] = classify(base[name], new[name])
    return found


def mod_of(name):
    parts = name.split("/")
    return parts[1] if len(parts) > 2 else parts[0]


def report(found, limit, out):
    by_class = collections.Counter(cls for cls, _ in found.values())
    for cls in CLASSES:
        if by_class[cls]:
            print(f"  {by_class[cls]:6}  {cls}", file=out)
    by_mod = collections.defaultdict(collections.Counter)
    for name, (cls, _) in found.items():
        by_mod[mod_of(name)][cls] += 1
    if by_mod:
        print("  by mod:", file=out)
        for mod, counts in sorted(by_mod.items(),
                                  key=lambda item: -sum(item[1].values())):
            detail = ", ".join(f"{n} {cls}" for cls, n in counts.most_common())
            print(f"    {sum(counts.values()):5}  {mod}: {detail}", file=out)
    for cls in CLASSES:
        names = [name for name, (c, _) in found.items() if c == cls]
        if not names:
            continue
        print(f"  {cls}:", file=out)
        for name in names[:limit]:
            detail = found[name][1]
            print(f"    {name}" + (f"  ({detail})" if detail else ""), file=out)
        if len(names) > limit:
            print(f"    ... and {len(names) - limit} more", file=out)


def main(argv=None, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("base", help="out directory or images.zip")
    parser.add_argument("new", help="out directory or images.zip")
    parser.add_argument("--base2", help="a second run of the base code")
    parser.add_argument("--limit", type=int, default=40,
                        help="icons listed per class (default 40)")
    args = parser.parse_args(argv)
    base, new = read_all(args.base), read_all(args.new)
    names = None
    if args.base2:
        base2 = read_all(args.base2)
        noise = compare(base, base2)
        print(f"{len(noise)} icons differ between the two base runs (noise):",
              file=out)
        report(noise, args.limit, out)
        names = (base.keys() | new.keys()) - noise.keys()
    found = compare(base, new, names)
    print(f"{len(found)} icons differ"
          + (" where the base runs agree" if args.base2 else "") + ":",
          file=out)
    report(found, args.limit, out)
    return found


if __name__ == "__main__":
    main()
