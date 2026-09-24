"""Item/fluid icon lookup, built from a NESQL export, and on-demand
reads out of images.zip (never unpacked to disk)."""

import json
import os
import threading
import zipfile

from gcm import config

# Built from a NESQL export (see oc/README notes). Two separate keyspaces:
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


def load_lookup():
    global _icons_by_key, _icons_by_label, _fluids_by_key
    global _images_zip, _images_zip_missing_logged
    _images_zip = None
    _images_zip_missing_logged = False
    icon_data = {}
    if os.path.exists(config.ICONS_LOOKUP_PATH):
        with open(config.ICONS_LOOKUP_PATH, "r", encoding="utf-8") as f:
            icon_data = json.load(f)
    _icons_by_key = icon_data.get("by_key", {})
    _icons_by_label = icon_data.get("by_label", {})
    _fluids_by_key = icon_data.get("fluids_by_key", {})


_images_zip = None
_images_zip_missing_logged = False
_zip_lock = threading.Lock()


def get_images_zip():
    global _images_zip, _images_zip_missing_logged
    if _images_zip is not None:
        return _images_zip
    with _zip_lock:
        if _images_zip is None:
            if os.path.exists(config.IMAGES_ZIP_PATH):
                _images_zip = zipfile.ZipFile(config.IMAGES_ZIP_PATH, "r")
            elif not _images_zip_missing_logged:
                print(
                    f"[icons] {config.IMAGES_ZIP_PATH} not found - icons will be blank until it's added."
                )
                _images_zip_missing_logged = True
    return _images_zip


def damage_str(damage):
    if damage is None:
        return None
    if isinstance(damage, float) and damage.is_integer():
        return str(int(damage))
    return str(damage)


def resolve_icon(mod, internal, damage, label):
    """Returns the image path inside images.zip for an item or fluid, or None."""
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
