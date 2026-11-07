"""Recipe identity and bounded presentation for items outside the ME network."""
import re

from gcm import icons, item_info, mctext, recipes, state
from gcm.recipe_index import FLUID, ITEM

ALTERNATIVES = 16


def parse_key(key):
    """HTTP form: mod:internal:damage (including *) or fluid:registry-name.
    Item damage remains None for wildcard identity; icons may use a fallback."""
    if not isinstance(key, str) or not key or len(key) > 1024 or re.search(r"[\x00-\x20\x7f]", key):
        raise ValueError("Choose a valid item or fluid key.")
    if key.startswith("fluid:"):
        name = key[6:]
        if not name:
            raise ValueError("A fluid key needs its registry name.")
        return FLUID, name
    mod, sep, rest = key.partition(":")
    internal, sep2, damage = rest.rpartition(":")
    if not (mod and sep and internal and sep2 and (damage == "*" or re.fullmatch(r"[0-9]+", damage))):
        raise ValueError("An item key must be mod:internal:damage (or * for any damage).")
    if damage != "*" and int(damage) > 2147483647:
        raise ValueError("Item damage is too large.")
    return ITEM, f"{mod}:{internal}:{damage if damage == '*' else int(damage)}"


def entry(kind, key, size, names, chance=None):
    out = recipes._entry(kind, key, size, chance, names)
    # Resolve an icon before restoring wildcard identity: a concrete display
    # fallback must never turn navigation into damage 0.
    icons.attach_item_icons([out])
    out["key"] = f"fluid:{key}" if kind == FLUID else key
    out["name"] = mctext.plain(out["name"])
    if kind == ITEM and key.endswith(":*"):
        out["damage"] = None
        out["wildcard"] = True
    return out


def map_info(idx, name):
    machines = [{**m, "name": mctext.plain(m["name"]), "icon": icons.icon_for_key(m.get("key"))} for m in idx.machines(name)]
    return {"id": name, "name": mctext.plain(machines[0]["name"]) if machines else name, "machines": machines}


def present_recipes(rows, idx):
    wanted = {ITEM: set(), FLUID: set()}
    for _, _, body in rows:
        for side in ("i", "o"):
            wanted[ITEM].update(s[0] for s in body[side])
        for side in ("f", "g"):
            wanted[FLUID].update(s[0] for s in body[side])
        for slot in body.get("slots", []):
            if slot:
                wanted[ITEM].update(s[0] for s in slot["alts"][:ALTERNATIVES])
    names = {(kind, key): name for kind, keys in wanted.items() for key, name in idx.names(kind, keys).items()}
    out = []
    for rid, map_name, body in rows:
        row = {"id": rid, "map": map_name, "type": body["type"], "machines": map_info(idx, map_name)["machines"],
               "inputs": [entry(ITEM, s[0], s[1], names) for s in body["i"]]
               + [entry(FLUID, s[0], s[1], names) for s in body["f"]],
               "outputs": [entry(ITEM, s[0], s[1], names, s[2] if len(s) > 2 else None) for s in body["o"]]
               + [entry(FLUID, s[0], s[1], names) for s in body["g"]]}
        if body["type"] == "gt":
            row.update(eut=body["e"], tier=recipes._tier(body["e"]), special=body["s"], heat=map_name in recipes.HEAT_MAPS)
        if "d" in body:
            row["ticks"] = body["d"]
        if body.get("approximate"):
            row["approximate"] = body["approximate"]
        if "slots" in body:
            slots = []
            for slot in body["slots"]:
                slots.append(None if slot is None else {
                    "alternatives": [entry(ITEM, s[0], s[1], names) for s in slot["alts"][:ALTERNATIVES]],
                    "alternatives_total": len(slot["alts"]), "ore": slot.get("od"),
                })
            row["grid"] = {"slots": slots, "shapeless": body["type"] == "shapeless",
                           "w": body.get("w", 3), "h": body.get("h", (len(slots) + 2) // 3),
                           "mirrored": body.get("mirrored", False)}
        out.append(row)
    return out


def item_summary(idx, kind, key):
    names = {(kind, k): v for k, v in idx.names(kind, [key]).items()}
    if not names:
        with state.network_lock:
            for known in state.network["items"]:
                if known.get("variant"):
                    continue
                k = known.get("kind", "item")
                identity = known.get("internal") if k == "fluid" else f"{known.get('mod')}:{known.get('internal')}:{known.get('damage')}"
                if (kind == FLUID) == (k == "fluid") and key == identity and known.get("name"):
                    names[(kind, key)] = known["name"]
                    break
    it = entry(kind, key, 1, names)
    making, using = idx.counts_making(kind, key), idx.counts_using(kind, key)
    maps = [map_info(idx, name) for name in making.keys() | using.keys()]
    maps.sort(key=lambda m: (0 if m["id"] == "crafting" else 1 if m["id"] == "furnace" else 2, m["name"]))
    return {"item": it, "info": item_info.describe(it), "making": making, "using": using, "maps": maps,
            "coverage": recipes.coverage_summary(idx)}
