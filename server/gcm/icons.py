"""Item/fluid icon lookup and on-demand reads out of images.zip (never
unpacked to disk). Which lookup and zip are live is decided by
gcm/gamedata.py: an installed game data bundle, or the files that shipped
before bundles existed."""

import functools
import io
import json
import os
import threading
import zipfile


# Written by tools/icon-export/. Two separate keyspaces:
# - by_key: "modid:internalname:damage" -> image path, for ordinary items.
#   AE2 gives us `name` as "modid:internalname" for these (colon present).
# - fluids_by_key: raw Forge fluid registry name -> image path. Confirmed
#   empirically: GT/GTNH's Fluid Discretizer pseudo-items have a `name`
#   with NO colon at all - it's just the bare fluid registry name itself
#   (e.g. name="molten.mutatedlivingsolder", no mod prefix, no NBT tag
#   involved despite that being the original assumption). Forge fluid
#   names are globally unique, so no further disambiguation is needed.
# by_label is the weakest fallback for either case - plain item labels
# collide across mods ~8% of the time, so only used when nothing else matched.
_icons_by_key = {}
_icons_by_label = {}
_fluids_by_key = {}
# The GTNH version of the live bundle, or None for the pre-bundle files.
# Resolved icon paths are prefixed with it, so switching versions changes
# every icon URL and browsers don't keep showing the old version's images
# (they're served with an immutable cache header).
_version = None
_images_zip_path = None

_images_zip = None
_images_zip_missing_logged = False
_zip_lock = threading.Lock()

# Top-level folders inside images.zip. A path starting with anything else
# carries a version prefix.
_ZIP_ROOTS = ("item", "fluid")


def load(lookup_path, images_zip_path, version=None):
    """Makes the given lookup and zip the live ones."""
    global _icons_by_key, _icons_by_label, _fluids_by_key
    global _images_zip, _images_zip_missing_logged, _images_zip_path, _version
    icon_data = {}
    if lookup_path and os.path.exists(lookup_path):
        with open(lookup_path, "r", encoding="utf-8") as f:
            icon_data = json.load(f)
    with _zip_lock:
        if _images_zip is not None:
            _images_zip.close()
        _images_zip = None
        _images_zip_missing_logged = False
        _images_zip_path = images_zip_path
        _icons_by_key = icon_data.get("by_key", {})
        _icons_by_label = icon_data.get("by_label", {})
        _fluids_by_key = icon_data.get("fluids_by_key", {})
        _version = version


def data_version():
    return _version


def get_images_zip():
    global _images_zip, _images_zip_missing_logged
    if _images_zip is not None:
        return _images_zip
    with _zip_lock:
        if _images_zip is None and _images_zip_path:
            if os.path.exists(_images_zip_path):
                _images_zip = zipfile.ZipFile(_images_zip_path, "r")
            elif not _images_zip_missing_logged:
                print(
                    f"[icons] {_images_zip_path} not found - icons will be blank until it's added."
                )
                _images_zip_missing_logged = True
    return _images_zip


def read_image(path):
    """The PNG at path inside images.zip, or None if it isn't there (or
    there's no images.zip). Accepts paths with or without a version prefix:
    craft history stores whatever path was current when it was recorded."""
    head, sep, rest = path.partition("/")
    if sep and head not in _ZIP_ROOTS:
        path = rest
    zf = get_images_zip()
    if zf is None:
        return None
    # ZipFile reads share one file handle, so they're serialized.
    with _zip_lock:
        try:
            return zf.read(path)
        except KeyError:
            return None
        except ValueError:
            # load() closed this zip for a new version between
            # get_images_zip() and here.
            return None


def is_animated(png):
    """APNGs carry an acTL chunk ahead of the image data; plain PNGs don't."""
    return b"acTL" in png[:4096].split(b"IDAT", 1)[0]


@functools.lru_cache(maxsize=4096)
def _still(png):
    from PIL import Image

    out = io.BytesIO()
    Image.open(io.BytesIO(png)).save(out, format="PNG")
    return out.getvalue()


def read_still_image(path):
    """read_image(), but an animated icon's first frame only: for viewers
    who've asked their browser for reduced motion."""
    data = read_image(path)
    if data is None or not is_animated(data):
        return data
    return _still(data)


def damage_str(damage):
    if damage is None:
        return None
    if isinstance(damage, float) and damage.is_integer():
        return str(int(damage))
    return str(damage)


def resolve_icon(mod, internal, damage, label):
    """Returns the image path inside images.zip for an item or fluid,
    prefixed with the data version if a bundle is live, or None."""
    path = _lookup(mod, internal, damage, label)
    if path and _version:
        return f"{_version}/{path}"
    return path


def _lookup(mod, internal, damage, label):
    if mod and internal:
        dmg = damage_str(damage)
        if dmg is not None:
            path = _icons_by_key.get(f"{mod}:{internal}:{dmg}")
            if path:
                return path
    elif internal:
        # No mod prefix at all in `name` -> treat the whole thing as a
        # bare fluid registry name (see comment above the lookup tables).
        path = _fluids_by_key.get(internal)
        if path:
            return path
    if label:
        return _icons_by_label.get(label)
    return None


def attach_item_icons(items):
    for item in items or []:
        icon = resolve_icon(
            item.get("mod"), item.get("internal"), item.get("damage"), item.get("name")
        )
        if icon:
            item["icon"] = icon
    return items


def attach_job_icons(jobs):
    # Resolved once here, at ingestion, rather than on every /api/crafts
    # poll - the lookup table is static, no point redoing the work every
    # 3s for however many browser tabs happen to be open.
    for job in jobs or []:
        icon = resolve_icon(
            job.get("final_output_mod"),
            job.get("final_output_internal"),
            job.get("final_output_damage"),
            job.get("final_output"),
        )
        if icon:
            job["final_output_icon"] = icon
        attach_item_icons(job.get("active"))
        attach_item_icons(job.get("pending"))
        attach_item_icons(job.get("stored"))
    return jobs
