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
Bundles from before it existed have none of it. It's read from the
bundle's index (gcm/gamedata_index.py), not held in memory (it was ~24
MB), except the mod names: a few hundred, and the page wants them all."""

import re

from gcm import oredict
from gcm.icons import damage_str

_index = None  # the live bundle's gamedata_index.Index, or None
_loaded = False
_mods = {}
_mods_by_lower = {}


def load(index):
    """Makes the item info in index (a gamedata_index.Index) the live one;
    none if index is None."""
    global _index, _loaded, _mods, _mods_by_lower
    mods = index.mods() if index else {}
    _index = index
    _mods = mods
    _mods_by_lower = {mod.lower(): name for mod, name in mods.items()}
    _loaded = bool(mods or (index and index.has_item_text()))


def loaded():
    return _loaded


def _texts(key, item):
    """{section: text} for an item in each item section: an NBT variant's
    own (a GregTech tool's material, else its label), else its item id's."""
    index = _index
    if index is None:
        return {}
    # The keys to look under, in order of preference.
    wanted = []
    if item.get("variant"):
        if item.get("variant_name"):
            # merge_variants()'s " #1a2b" isn't part of the material.
            material = re.sub(r" #[0-9A-Za-z]{4}$", "", item["variant_name"])
            wanted.append(("by_key_material", f"{key}|{material}"))
        if item.get("name"):
            wanted.append(("by_key_label", f"{key}|{item['name']}"))
    wanted.append(("by_key", key))
    by_place = {}
    for k, table, section, text in index.item_texts([k for _, k in wanted]):
        by_place.setdefault((table, k), {})[section] = text
    texts = {}
    for place in wanted:
        for section, text in by_place.get(place, {}).items():
            texts.setdefault(section, text)
    return texts


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
        index = _index
        fluid = item.get("internal") and index and index.fluid(item["internal"])
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
    texts = _texts(f"{mod}:{internal}:{damage}", item)
    for section, field in (("tooltips", "description"), ("shift_tooltips", "shift_description"),
                           ("rarity", "rarity"), ("formulas", "formula")):
        if texts.get(section):
            info[field] = texts[section]
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
