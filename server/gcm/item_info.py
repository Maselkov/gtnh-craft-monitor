"""What the game says about items beyond their name and icon, from the
game data bundle's item_info.json (written by tools/icon-export/): each
item's description (its tooltip below the name, and again with Shift
held where that's different) and rarity colour, each fluid's
temperature and state, and each mod's name - plus, from gcm/oredict.py,
an item's ore-dictionary names. For the Network tab's tooltips, item
dialog and search.

item_info.json has:
- tooltips, shift_tooltips, rarity, formulas: the icon lookup's item
  tables (by_key, by_key_label, by_key_material; see gcm/icons.py), each
  to the text - lines joined with newlines, Minecraft's formatting codes
  (§e) kept for the page to colour - the rarity's colour code ("e"), or
  the chemical formula, which keeps GregTech's font glyphs (the page
  maps them). Never by label alone: a namesake from another mod would
  get the wrong text.
- fluids: {fluid name: {temperature: kelvin, gaseous: bool, formula}},
  which describe() words the way GregTech's fluid tooltips do.
- mods: {mod id: name}.
Bundles from before it existed have none of it."""

import json
import logging
import os
import re

from gcm import oredict
from gcm.icons import damage_str

log = logging.getLogger(__name__)

_sections = {}
_fluids = {}
_mods = {}
_mods_by_lower = {}


def load(path):
    """Makes the item info at path the live one; none if path is None or
    missing."""
    global _sections, _fluids, _mods, _mods_by_lower
    data = {}
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("unreadable item info %s: %s", path, e)
        if not isinstance(data, dict):
            data = {}
    _sections = {
        name: data.get(name) if isinstance(data.get(name), dict) else {}
        for name in ("tooltips", "shift_tooltips", "rarity", "formulas")
    }
    _fluids = data.get("fluids") if isinstance(data.get("fluids"), dict) else {}
    _mods = data.get("mods") if isinstance(data.get("mods"), dict) else {}
    _mods_by_lower = {mod.lower(): name for mod, name in _mods.items()}


def loaded():
    return bool(_sections.get("tooltips", {}).get("by_key") or _mods)


def _find(section, key, item):
    """An item's entry in one of the item sections: an NBT variant's own
    (a GregTech tool's material, or its label), else its item id's."""
    tables = _sections.get(section) or {}
    if item.get("variant"):
        if item.get("variant_name"):
            # merge_variants()'s " #1a2b" isn't part of the material.
            material = re.sub(r" #[0-9A-Za-z]{4}$", "", item["variant_name"])
            found = (tables.get("by_key_material") or {}).get(f"{key}|{material}")
            if found:
                return found
        found = item.get("name") and (tables.get("by_key_label") or {}).get(f"{key}|{item['name']}")
        if found:
            return found
    return (tables.get("by_key") or {}).get(key)


def _fluid_description(fluid, name):
    if not isinstance(fluid.get("temperature"), (int, float)):
        return None
    if name.startswith("plasma."):
        state = "Plasma"
    else:
        state = "Gas" if fluid.get("gaseous") else "Liquid"
    return f"§7Temperature: §e{fluid['temperature']:,} K\n§7State: §e{state}"


def _with_formula(description, formula):
    """The description with the formula as its first line, in GregTech's
    yellow, unless it's already in there (a GregTech item's tooltip has
    it)."""
    if not formula:
        return description
    if description and re.sub("§.", "", formula) in re.sub("§.", "", description):
        return description
    return f"§e{formula}" + (f"\n{description}" if description else "")


def describe(item):
    """What's known about an item as the network snapshot has it (mod,
    internal, damage, kind, and for an NBT variant its variant, name and
    variant_name): {description, shift_description, rarity, formula, ore -
    its ore-dictionary names}, each only when there is one. The
    description starts with the formula where the game's tooltip didn't
    have it (a fluid's never does)."""
    kind = item.get("kind") or "item"
    info = {}
    if kind == "fluid":
        fluid = item.get("internal") and _fluids.get(item["internal"])
        if isinstance(fluid, dict):
            formula = fluid.get("formula") if isinstance(fluid.get("formula"), str) else None
            description = _with_formula(_fluid_description(fluid, item["internal"]), formula)
            if description:
                info["description"] = description
            if formula:
                info["formula"] = formula
        return info
    if kind != "item":
        return info
    mod, internal, damage = item.get("mod"), item.get("internal"), damage_str(item.get("damage"))
    if not (mod and internal and damage is not None):
        return info
    key = f"{mod}:{internal}:{damage}"
    for section, field in (("tooltips", "description"), ("shift_tooltips", "shift_description"),
                           ("rarity", "rarity"), ("formulas", "formula")):
        found = _find(section, key, item)
        if found:
            info[field] = found
    if info.get("formula"):
        info["description"] = _with_formula(info.get("description"), info["formula"])
    ore = oredict.names(mod, internal, item.get("damage"))
    if ore:
        info["ore"] = ore
    return info


def mod_names():
    """{mod id: name}, for every mod the game data knows."""
    return dict(_mods)


def mod_name(mod):
    """A mod's name by its id (any case, as AE2 and the registry don't
    always agree on it), or None."""
    if not mod:
        return None
    return _mods.get(mod) or _mods_by_lower.get(mod.lower())
