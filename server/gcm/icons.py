"""Item/fluid/essentia icon lookup and on-demand reads out of images.zip (never
unpacked to disk). Which lookup and zip are live is decided by
gcm/gamedata.py. Both are read through the bundle's index
(gcm/gamedata_index.py), not loaded into memory: the lookup's tables are
looked up in it, and it says where in the zip each icon is."""

import functools
import io
import logging
import os
import re
import threading
import zlib
from urllib.parse import quote

log = logging.getLogger(__name__)


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
# by_key_label: "modid:internalname:damage|label" -> image path, from NBT
# stacks only. by_key holds the stack WITHOUT NBT, so every NBT variant
# of an item (each crop's seeds, each bee species) resolved to that one
# generic icon; their labels are what tell them apart.
# by_key_material: "modid:internalname:damage|material" -> image path, for
# GregTech tools. Every material's Huge Turbine shares one id, damage and
# label; only GT.ToolStats' PrimaryMaterial differs, which gcm/nbt.py
# reads into the stack's variant_name.
# aspects_by_key: Thaumcraft aspect tag ("ordo") -> image path, for
# essentia. Only that: an aspect's label would find namesake items
# ("Ordo" -> some block of it), so essentia never falls back to by_label.
# Each is a _Table over the live index, or {} with none: anything with
# .get().
_icons_by_key = {}
_icons_by_key_label = {}
_icons_by_key_material = {}
_icons_by_label = {}
_fluids_by_key = {}
_aspects_by_key = {}
# The GTNH version of the live bundle, or None for the pre-bundle files.
# Resolved icon paths are prefixed with it, so switching versions changes
# every icon URL and browsers don't keep showing the old version's images
# (they're served with an immutable cache header).
_version = None
_images_zip_path = None
# Image path -> how far the icon is drawn past the item box on each side, in
# 1/16ths of it (a cosmic halo, for one): the lookup's "bleed" table. Those
# images are (16 + 2 * bleed) / 16 times the box's size, the box in their
# middle, and their resolved paths say so (_prefix()) for the page to size
# them by.
_bleed = {}

# The live bundle's image files' suffix: ".webp", or ".png" for bundles
# made before they were WebP (data.json's "image_format").
_image_suffix = ".png"
_IMAGE_SUFFIXES = (".png", ".webp")

_index = None
_images_zip_name = None
_images_zip_fd = None
_images_zip_missing_logged = False
_zip_lock = threading.Lock()

# Top-level folders inside images.zip. A path starting with anything else
# carries a version prefix.
_ZIP_ROOTS = ("item", "fluid", "aspect")


class _Table:
    """One of the lookup's tables, read from the index."""

    def __init__(self, index, name):
        self._index = index
        self._name = name

    def get(self, key, default=None):
        value = self._index.icon(self._name, key)
        return default if value is None else value


def load(index, images_zip_path, version=None, image_format="png"):
    """Makes the given index (a gamedata_index.Index, or None for no
    icons) and the zip it describes the live ones. image_format: "webp"
    or "png", what the zip's images are."""
    global _icons_by_key, _icons_by_key_label, _icons_by_key_material, _icons_by_label, _fluids_by_key, _aspects_by_key
    global _index, _images_zip_fd, _images_zip_missing_logged, _images_zip_path, _images_zip_name, _version, _bleed
    global _image_suffix

    def table(name):
        return _Table(index, name) if index else {}

    with _zip_lock:
        if _images_zip_fd is not None:
            os.close(_images_zip_fd)
        _images_zip_fd = None
        _images_zip_missing_logged = False
        _images_zip_path = images_zip_path
        _images_zip_name = os.path.basename(images_zip_path) if images_zip_path else None
        _icons_by_key = table("by_key")
        _icons_by_key_label = table("by_key_label")
        _icons_by_key_material = table("by_key_material")
        _icons_by_label = table("by_label")
        _fluids_by_key = table("fluids_by_key")
        _aspects_by_key = table("aspects_by_key")
        _bleed = table("bleed")
        _version = version
        _image_suffix = ".webp" if image_format == "webp" else ".png"
        _index = index


def data_version():
    return _version


def _zip_fd():
    global _images_zip_fd, _images_zip_missing_logged
    with _zip_lock:
        if _images_zip_fd is None and _images_zip_path:
            try:
                _images_zip_fd = os.open(_images_zip_path, os.O_RDONLY)
            except OSError:
                if not _images_zip_missing_logged:
                    log.warning("%s can't be read - icons will be blank until it can.", _images_zip_path)
                    _images_zip_missing_logged = True
        return _images_zip_fd, _index, _images_zip_name


def _read_entry(fd, entry):
    """An icon's bytes, from its place in the zip as the index records it:
    a local file header (30 bytes, then its name and extra field), then
    the data, stored or deflated."""
    offset, size, method, crc = entry
    header = os.pread(fd, 30, offset)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        return None
    start = offset + 30 + int.from_bytes(header[26:28], "little") + int.from_bytes(header[28:30], "little")
    data = os.pread(fd, size, start)
    if method == 8:
        data = zlib.decompress(data, -15)
    elif method != 0:
        return None
    return data if zlib.crc32(data) == crc else None


def read_image(path):
    """The image (WebP or PNG) at path inside images.zip, or None if it
    isn't there (or there's no images.zip). Accepts paths with or without
    a version prefix: craft history stores whatever path was current when
    it was recorded."""
    head, sep, rest = path.partition("/")
    if sep and head not in _ZIP_ROOTS:
        path = rest
    fd, index, zip_name = _zip_fd()
    if fd is None or index is None:
        return None
    entry = index.zip_entry(zip_name, path)
    if entry is None:
        return None
    try:
        return _read_entry(fd, entry)
    except (OSError, zlib.error):
        # load() closed this zip for a new version since _zip_fd().
        return None


def is_webp(data):
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def is_animated(data):
    """An animated WebP has a VP8X chunk first with its animation flag
    set; an APNG carries an acTL chunk ahead of the image data."""
    if is_webp(data):
        return data[12:16] == b"VP8X" and bool(data[20] & 0x02)
    return b"acTL" in data[:4096].split(b"IDAT", 1)[0]


def image_mimetype(data):
    return "image/webp" if is_webp(data) else "image/png"


@functools.lru_cache(maxsize=4096)
def _still(data):
    from PIL import Image

    # An animated image opens at its first frame.
    out = io.BytesIO()
    if is_webp(data):
        Image.open(io.BytesIO(data)).save(out, format="WEBP", lossless=True)
    else:
        Image.open(io.BytesIO(data)).save(out, format="PNG")
    return out.getvalue()


def read_still_image(path):
    """read_image(), but an animated icon's first frame only: for viewers
    who've asked their browser for reduced motion."""
    data = read_image(path)
    if data is None or not is_animated(data):
        return data
    return _still(data)


# Notification icons are shown at roughly 64-96dp, so ~192 device pixels
# on a phone. The browser would scale a 16px icon up with smoothing,
# blurring pixel art; scaled here by a whole factor it stays crisp.
NOTIFICATION_ICON_PIXELS = 192


@functools.lru_cache(maxsize=512)
def _notification_icon(png):
    from PIL import Image

    image = Image.open(io.BytesIO(png))  # an animated one opens at its first frame
    factor = max(1, NOTIFICATION_ICON_PIXELS // max(image.size))
    image = image.convert("RGBA").resize(
        (image.width * factor, image.height * factor), Image.Resampling.NEAREST
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def read_notification_image(path):
    """read_image() as a notification icon: still, and scaled up crisply
    to about NOTIFICATION_ICON_PIXELS."""
    data = read_image(path)
    return None if data is None else _notification_icon(data)


def notification_icon_url(path):
    """URL of a stored icon path as a notification icon, or None."""
    path = current_path(path)
    return f"/icons?path={quote(path, safe='')}&notification=1" if path else None


def damage_str(damage):
    if damage is None:
        return None
    if isinstance(damage, float) and damage.is_integer():
        return str(int(damage))
    return str(damage)


def resolve_icon(mod, internal, damage, label, variant=None, kind=None, variant_name=None):
    """Returns the image path inside images.zip for an item, fluid or
    essentia, prefixed with the data version if a bundle is live, or
    None. variant: set for an NBT variant (gcm/inventory.py), whose own
    icon is found by its variant_name (a GregTech tool's material) or
    label before falling back to its item id's generic one."""
    if kind == "essentia":
        return _prefixed(_aspects_by_key.get(internal)) if internal else None
    return _prefixed(
        (variant and _lookup_material(mod, internal, damage, variant_name))
        or (variant and _lookup_variant(mod, internal, damage, label))
        or _lookup(mod, internal, damage, label)
    )


def current_path(path):
    """A stored icon path (craft history keeps whatever was current when it
    was recorded) with the live bundle's prefix, so it's sized and cached
    like a freshly resolved one, and its suffix: a bundle with WebP icons
    names them .webp, one with PNGs .png, and the path may have been
    stored under either."""
    if not path:
        return path
    head, sep, rest = path.partition("/")
    path = rest if sep and head not in _ZIP_ROOTS else path
    for suffix in _IMAGE_SUFFIXES:
        if path.endswith(suffix):
            path = path.removesuffix(suffix) + _image_suffix
            break
    return _prefixed(path)


def icon_for_key(key):
    """The prefixed image path for an item's "modid:internalname:damage",
    or None: what the page asks for when it shows a fixed item's icon (the
    favicon's ME Interface), rather than a path that names a file."""
    mod, _, rest = (key or "").partition(":")
    internal, _, damage = rest.rpartition(":")
    if not (mod and internal and damage):
        return None
    return _prefixed(_lookup(mod, internal, damage, None))


def _prefixed(path):
    # <version>[~<textures>]~<build>[~bleed<n>]/<path in the zip>
    if not path or not _version:
        return path
    bleed = _bleed.get(path)
    return f"{_version}~bleed{bleed}/{path}" if bleed else f"{_version}/{path}"


def _lookup_material(mod, internal, damage, variant_name):
    dmg = damage_str(damage)
    if not (mod and internal and dmg is not None and variant_name):
        return None
    # merge_variants() tells same-named variants apart with a " #1a2b"
    # suffix: two Neutronium turbines worn differently, say.
    material = re.sub(r" #[0-9A-Za-z]{4}$", "", variant_name)
    return _icons_by_key_material.get(f"{mod}:{internal}:{dmg}|{material}")


def _lookup_variant(mod, internal, damage, label):
    dmg = damage_str(damage)
    if not (mod and internal and dmg is not None and label):
        return None
    path = _icons_by_key_label.get(f"{mod}:{internal}:{dmg}|{label}")
    if path:
        return path
    # Lookups exported before by_key_label: by_label, but only an NBT
    # stack of this same item id (the exporter's image path scheme,
    # item/<mod>/<name>~<damage>~<nbt>.png), never another mod's
    # namesake.
    path = _icons_by_label.get(label)
    prefix = "item/" + re.sub(r'[<>:"/\\|?*]', "", f"{mod}~{internal}").replace("~", "/", 1)
    if path and path.startswith(f"{prefix}~{dmg}~"):
        return path
    return None


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
            item.get("mod"),
            item.get("internal"),
            item.get("damage"),
            item.get("name"),
            item.get("variant"),
            item.get("kind"),
            item.get("variant_name"),
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
