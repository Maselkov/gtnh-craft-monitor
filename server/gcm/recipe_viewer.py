"""Recipe identity and bounded presentation for items outside the ME network."""
import re

from gcm import icons, item_info, mctext, recipes, state
from gcm.recipe_index import FLUID, ITEM

ALTERNATIVES = 16
RESEARCH_MAPS = {"research.scanner": "scanner", "research.research_station": "research_station"}
ARCANE_MAPS = ("::thaumcraft.arcane.shaped", "::thaumcraft.arcane.shapeless")


def arcane_layout(layout, *, shaped=True):
    """TemplateThaumHandler's research-book crops; slots retain NEI's positions."""
    atlas = "thaumcraft:textures/gui/gui_researchbook_overlay.png"
    layers = [
        {"asset": atlas, "x": 68.775, "y": -1.05, "w": 28, "h": 29.75,
         "uv": [20/256, 3/256, 36/256, 20/256]},
    ]
    if shaped:
        layers.extend([
            {"asset": atlas, "x": 35, "y": 28, "w": 91, "h": 91,
             "uv": [112/256, 15/256, 164/256, 67/256]},
            {"asset": atlas, "x": 0, "y": 113.75, "w": 26.25, "h": 26.25, "opacity": .4,
             "uv": [68/256, 76/256, 80/256, 88/256]},
        ])
    return {**layout, "family": "arcane", "item_frames": False, "layers": layers,
            "slots": [s for s in layout.get("slots", []) if s.get("role") != "extras"]}


def research_map_info(idx, method):
    station = method == "research_station"
    source = "nei.gt.gt.recipe.researchStation" if station else "nei.gt.gt.recipe.scanner"
    name = "Research Station" if station else "Scanner"
    info = map_info(idx, source)
    if not info["machines"]:
        key = "gregtech:gt.blockmachines:15331" if station else "gregtech:gt.blockmachines:341"
        info["machines"] = [{"name": name, "key": key, "icon": icons.icon_for_key(key)}]
    return {**info, "id": f"research.{method}", "name": name, "mod_name": "TecTech" if station else "GregTech"}


def research_recipes(rows, idx):
    def single_stack(value):
        out = {**value, "size": 1}
        if value.get("display_variants"):
            out["display_variants"] = [{**variant, "size": 1} for variant in value["display_variants"]]
        return out

    out = []
    for production in present_recipes(rows, idx):
        scan = production["research"]
        method = scan["method"]
        info = research_map_info(idx, method)
        stick = next((e for e in production["auxiliary"] if e["key"] == "gregtech:gt.metaitem.01:32708"), None)
        row = {"id": f"research-{production['id']}", "map": info["id"], "type": "research",
               "machines": info["machines"], "scan": scan,
               "inputs": [scan["item"]], "outputs": [single_stack(e) for e in production["outputs"]],
               "reference_only": True, "auxiliary": [single_stack(stick)] if stick else []}
        layout = idx.layout("nei.gt.gt.recipe.researchStation" if method == "research_station" else "nei.gt.gt.recipe.scanner")
        if not layout:
            y = 33 if method == "research_station" else 24
            layout = {"width": 170, "height": 82, "slots": [
                {"role": "i", "index": 0, "x": 52, "y": y, "w": 18, "h": 18},
                {"role": "o", "index": 0, "x": 106, "y": y, "w": 18, "h": 18},
                {"role": "aux", "index": 0, "x": 124, "y": 62, "w": 18, "h": 18}], "layers": []}
        layout = {**layout, "family": method}
        if method == "research_station":
            layout["layers"] = [layer for layer in layout.get("layers", []) if "progressbar" not in layer.get("asset", "")]
        row["preview"] = present_layout(layout, {"i": [[scan["item"]]], "o": [[e] for e in row["outputs"]],
                                               "aux": [[e] for e in row["auxiliary"]]}, {}, idx)
        out.append(row)
    return out


def listing(idx, kind, key, direction, map_name=None, *, limit=20, offset=0):
    if map_name is not None and map_name not in idx.maps() and map_name not in RESEARCH_MAPS:
        raise ValueError("Unknown recipe category.")
    research_counts = idx.research_counts(kind, key, direction)
    if map_name in RESEARCH_MAPS:
        method = RESEARCH_MAPS[map_name]
        rows = idx.research_page(kind, key, direction, method, limit=limit, offset=offset)
        return research_recipes(rows, idx), research_counts.get(method, 0)
    query = idx.made_by if direction == "making" else idx.used_by
    rows, total = query(kind, key, map_name, limit=limit, offset=offset)
    shown = present_recipes(rows, idx)
    if map_name is None:
        if len(shown) < limit:
            extra = idx.research_page(kind, key, direction, limit=limit-len(shown), offset=max(0, offset-total))
            shown.extend(research_recipes(extra, idx))
        total += sum(research_counts.values())
    return shown, total


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
    catalysts = idx.machines(name)
    # Older exports omitted the native infusion catalyst entirely.
    if name == "nei.thaumcraft.infusion" and not any(m.get("key") for m in catalysts):
        catalysts = [{"name": "Arcane Infusion", "key": "Thaumcraft:blockStoneDevice:2"}]
    machines = [{**m, "name": mctext.plain(m["name"]), "icon": icons.icon_for_key(m.get("key"))} for m in catalysts]
    key = next((m["key"] for m in machines if m.get("key")), None)
    mod = key.partition(":")[0] if key else None
    title = mctext.plain(machines[0]["name"]) if machines else name
    if name.startswith("gt.") and key:
        machine = machine_entry(next(m for m in machines if m.get("key")))
        description = mctext.plain(machine["info"].get("description", ""))
        match = re.search(r"^Machine Type:[ \t]*([^\r\n]+)", description, re.MULTILINE)
        if match:
            title = match[1].strip()
    title = (idx.layout(name) or {}).get("title") or title
    return {"id": name, "name": mctext.plain(title), "machines": machines,
            "mod_name": (item_info.mod_name(mod) or mod) if mod else None}


def machine_entry(machine):
    """A catalyst's concrete identity and the same item info as the network."""
    unknown = {**machine, "key": None, "kind": "item", "info": {}}
    try:
        kind, key = parse_key(machine.get("key"))
    except ValueError:
        return unknown
    if kind != ITEM:
        return unknown
    value = entry(ITEM, key, 1, {(ITEM, key): machine["name"]})
    value["info"] = {**item_info.describe(value), "mod_name": item_info.mod_name(value["mod"])}
    return value


def machine_summaries(rows):
    """One machine list per category, shared by every recipe on the page."""
    out = {}
    for row in rows:
        if row["map"] in out:
            continue
        machines = row["machines"]
        # Generic NEI handlers put a name-only category label before their
        # real catalysts. Show it only when no concrete machines are known.
        candidates = [m for m in machines if m.get("key")] or machines
        out[row["map"]] = [machine_entry(machine) for machine in candidates]
    return out


def present_recipes(rows, idx):
    wanted = {ITEM: set(), FLUID: set()}
    for _, _, body in rows:
        for side in ("i", "o"):
            wanted[ITEM].update(s[0] for s in body[side])
            wanted[ITEM].update(s[0] for s in body.get("preview_stacks", {}).get(side, []))
        for side in ("f", "g"):
            wanted[FLUID].update(s[0] for s in body[side])
            wanted[FLUID].update(s[0] for s in body.get("preview_stacks", {}).get(side, []))
        for slot in body.get("slots", []):
            if slot:
                wanted[ITEM].update(s[0] for s in slot["alts"][:ALTERNATIVES])
        for group in [*body.get("groups", []), *body.get("extras", [])]:
            wanted[FLUID if group["kind"] == "fluid" else ITEM].update(s[0] for s in group["alts"][:ALTERNATIVES])
        if body.get("research"):
            wanted[ITEM].add(body["research"]["item"][0])
    refs = set()
    for _, _, body in rows:
        for stack in recipe_stacks(body):
            if len(stack) > 3:
                refs.update(stack[3])
        wanted[ITEM].update(s[0] for s in body.get("aux", []))
    variants = idx.display_stacks(refs)
    names = {(kind, key): name for kind, keys in wanted.items() for key, name in idx.names(kind, keys).items()}
    tooltip_cache = {}
    def tooltip_info(value, display=None):
        identity = (value["key"], display.get("key"), display.get("name")) if display else (value["key"],)
        if identity not in tooltip_cache:
            lookup = value
            if display:
                mod, _, rest = display["key"].partition(":")
                internal, _, damage = rest.rpartition(":")
                lookup = {**value, "mod": mod, "internal": internal,
                          "damage": None if damage == "*" else int(damage),
                          "name": display.get("name") or value["name"], "variant": bool(display.get("nbt"))}
            tooltip_cache[identity] = {**item_info.describe(lookup), "mod_name": item_info.mod_name(lookup.get("mod"))}
        return tooltip_cache[identity]

    def stack_entry(kind, stack):
        value = entry(kind, stack[0], stack[1], names, stack[2] if len(stack) > 2 else None)
        value["info"] = tooltip_info(value)
        choices = []
        for ref in stack[3] if len(stack) > 3 else []:
            display = variants.get(ref)
            if not display:
                continue
            choice = {**value, "display_id": ref,
                      "name": mctext.plain(display.get("name") or value["name"]),
                      "icon": icons.recipe_image(display.get("icon")) or value.get("icon"),
                      "info": tooltip_info(value, display)}
            choices.append(choice)
        if choices:
            value.update(name=choices[0]["name"], icon=choices[0]["icon"], info=choices[0]["info"], display_variants=choices)
        return value

    out = []
    for rid, map_name, body in rows:
        row = {"id": rid, "map": map_name, "type": body["type"], "machines": map_info(idx, map_name)["machines"],
               "inputs": [stack_entry(ITEM, s) for s in body["i"]]
               + [stack_entry(FLUID, s) for s in body["f"]],
               "outputs": [stack_entry(ITEM, s) for s in body["o"]]
               + [stack_entry(FLUID, s) for s in body["g"]]}
        if body["type"] in ("gt", "assembly_line"):
            row.update(eut=body["e"], tier=recipes._tier(body["e"]), special=body["s"], heat=map_name in recipes.HEAT_MAPS)
        if "d" in body:
            row["ticks"] = body["d"]
        if body.get("approximate"):
            row["approximate"] = body["approximate"]
        if "slots" in body:
            slots = []
            for slot in body["slots"]:
                slots.append(None if slot is None else {
                    "alternatives": [stack_entry(ITEM, s) for s in slot["alts"][:ALTERNATIVES]],
                    "alternatives_total": len(slot["alts"]), "ore": slot.get("od"),
                })
            row["grid"] = {"slots": slots, "shapeless": body["type"] == "shapeless",
                           "w": body.get("w", 3), "h": body.get("h", (len(slots) + 2) // 3),
                           "mirrored": body.get("mirrored", False)}
        if "groups" in body:
            row["groups"] = [{"alternatives": [stack_entry(FLUID if group["kind"] == "fluid" else ITEM, s)
                                               for s in group["alts"][:ALTERNATIVES]],
                              "alternatives_total": len(group["alts"])} for group in body["groups"]]
        if body.get("research"):
            research = body["research"]
            row["research"] = {**research, "item": stack_entry(ITEM, research["item"])}
            scan = row["research"]
            station = research["method"] == "research_station"
            scan["name"] = "Research Station" if station else "Scanner"
            scan["tier"] = recipes._tier(research["eut"]) if research.get("eut") is not None else None
            map_id = "nei.gt.gt.recipe.researchStation" if station else "nei.gt.gt.recipe.scanner"
            scan["machines"] = map_info(idx, map_id)["machines"]
            if research.get("eut") is not None:
                scan["usage"] = research["eut"] * (research.get("amperage", 1) if station else 1)
                if station and research.get("computation_per_second") and research.get("computation"):
                    seconds = max(1, research["computation"] // research["computation_per_second"])
                    # NEI uses whole seconds at the minimum computation rate.
                    # A decimal string preserves totals past JS's precision.
                    scan["max_eu"] = str(seconds * 20 * scan["usage"])
                elif not station and research.get("ticks") is not None:
                    scan["total_eu"] = str(research["ticks"] * scan["usage"])
        if body.get("requirements"):
            row["requirements"] = body["requirements"]
            handled = {"condensate_input"} if "f" in body.get("preview_stacks", {}) and map_name == "gt.recipe.assemble-condensate" else set()
            if body["requirements"].get("max_nanite_tier"):
                handled.add("nanite_tiers")
            costs = body["requirements"]
            if map_name.endswith(ARCANE_MAPS):
                costs = {k: v for k, v in costs.items() if k not in ("aspects", "vis", "prereqs")}
            row["requirement_notes"] = requirement_notes(costs, handled_metadata=handled)
        row["extras"] = [{"alternatives": [stack_entry(FLUID if g["kind"] == "fluid" else ITEM, s)
                                             for s in g["alts"][:ALTERNATIVES]]}
                         for g in body.get("extras", [])]
        row["auxiliary"] = [stack_entry(ITEM, s) for s in body.get("aux", [])]
        layout = body.get("preview") or idx.layout(map_name)
        if layout:
            if map_name.endswith(ARCANE_MAPS):
                layout = arcane_layout(layout, shaped=map_name.endswith(".shaped"))
            sources = {side: [[stack_entry(kind, s)] for s in body[side]]
                       for side, kind in (("i", ITEM), ("o", ITEM), ("f", FLUID), ("g", FLUID))}
            for side, stacks in body.get("preview_stacks", {}).items():
                sources[side] = [[stack_entry(FLUID if side in ("f", "g") else ITEM, s)] for s in stacks]
            sources["extras"] = [g["alternatives"] for g in row["extras"]]
            sources["aux"] = [[e] for e in row["auxiliary"]]
            sources["groups"] = [group["alternatives"] for group in row.get("groups", [])]
            row["preview"] = present_layout(layout, sources, body, idx)
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
    for direction, counts in (("making", making), ("using", using)):
        counts.update({f"research.{method}": n for method, n in idx.research_counts(kind, key, direction).items()})
    maps = [research_map_info(idx, RESEARCH_MAPS[name]) if name in RESEARCH_MAPS else map_info(idx, name)
            for name in making.keys() | using.keys()]
    maps.sort(key=lambda m: (0 if m["id"] == "crafting" else 1 if m["id"] == "furnace" else 2, m["name"]))
    return {"item": it, "info": item_info.describe(it), "making": making, "using": using, "maps": maps,
            "coverage": recipes.coverage_summary(idx)}


def catalog_items(rows):
    """Small named results with the same identities and tooltips as recipe slots."""
    names = {(kind, key): name for kind, key, name in rows}
    out = []
    for kind, key, _ in rows:
        value = entry(kind, key, 1, names)
        value["info"] = {**item_info.describe(value), "mod_name": item_info.mod_name(value.get("mod"))}
        out.append(value)
    return out


def requirement_notes(value, *, handled_metadata=()):
    """Readable, bounded labels for native/NEI costs; raw data stays available to MCP."""
    labels = {"mSpecialItems": "Special items", "mAltFluidInputs": "Fluid alternatives",
              "mInputChances": "Input consumption chances (out of 10,000)",
              "mFluidInputChances": "Fluid consumption chances (out of 10,000)",
              "mFluidOutputChances": "Fluid output chances (out of 10,000)",
              "nbt_sensitive": "NBT-sensitive inputs", "lp": "Life points", "lp_amount": "Life points", "manaUsage": "Mana",
              "prereqs": "Research", "eut": "EU/t", "max_nanite_tier": "Max Nanite Tier"}
    def readable(v):
        if v is None:
            return ""
        if isinstance(v, dict):
            return ", ".join(f"{labels.get(k, k)}: {text}" for k, x in v.items()
                             if k not in ("class", "unexpanded_class") and (text := readable(x)))
        if isinstance(v, list):
            return ", ".join(text for x in v[:32] if (text := readable(x)))
        if isinstance(v, bool):
            return "Yes" if v else "No"
        return f"{v:,}" if isinstance(v, (int, float)) else str(v)
    out = []
    for key, val in value.items():
        if key in ("nbt_inputs", "nbt_outputs") or val is False or val is None:
            continue
        if key == "metadata" and isinstance(val, dict):
            out.extend(requirement_notes({k: v for k, v in val.items() if k not in handled_metadata}))
            continue
        if val is True and key in ("cleanroom", "low_gravity"):
            out.append("Needs Cleanroom" if key == "cleanroom" else "Needs Low Gravity")
            continue
        label = labels.get(key) or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key.replace("_", " ")).capitalize()
        text = readable(val)
        if text:
            if key == "max_nanite_tier":
                out.extend([f"{label}:", text[:512]])
            else:
                out.append(f"{label}: {text}"[:512])
    return out[:32]


def recipe_stacks(body):
    for stacks in body.get("preview_stacks", {}).values():
        yield from stacks
    for side in ("i", "o", "f", "g", "aux"):
        yield from body.get(side, [])
    for group in [*body.get("groups", []), *body.get("extras", [])]:
        yield from group["alts"][:ALTERNATIVES]
    for slot in body.get("slots", []):
        if slot:
            yield from slot["alts"][:ALTERNATIVES]
    if body.get("research"):
        yield body["research"]["item"]


def present_layout(layout, sources, body, idx):
    """Only bounded geometry and structured values cross the preview API."""
    import math
    def number(value, default=0):
        return value if type(value) in (int, float) and math.isfinite(value) and -4096 <= value <= 4096 else default
    out = {"family": str(layout.get("family", "positioned")),
           "width": max(1, number(layout.get("width"), 166)),
           "height": max(1, number(layout.get("height"), 100)), "slots": [], "layers": []}
    assets = idx.presentation["ui_assets"]
    def asset(ref):
        return icons.recipe_image(assets.get(ref, {}).get("path"))
    for slot in layout.get("slots", [])[:256]:
        role, index = slot.get("role"), slot.get("index")
        if role not in sources or type(index) is not int or index < 0:
            continue
        indices = body.get("preview_indices", {}).get(role)
        if indices is not None:
            index = indices.index(index) if index in indices else -1
        alternatives = sources[role][index] if 0 <= index < len(sources[role]) else []
        out["slots"].append({**{k: number(slot.get(k), 18 if k in ("w", "h") else 0) for k in ("x", "y", "w", "h")},
                             "role": role, "index": slot["index"], "alternatives": alternatives,
                             "framed": bool(layout.get("item_frames", True)),
                             "frame": asset(slot.get("asset")) if layout.get("item_frames", True) else None})
    for layer in layout.get("layers", [])[:64]:
        uv = layer.get("uv", [0, 0, 1, 1])
        if not isinstance(uv, list) or len(uv) != 4 or not all(type(v) in (int, float) and 0 <= v <= 1 for v in uv):
            continue
        if asset(layer.get("asset")):
            out["layers"].append({**{k: number(layer.get(k)) for k in ("x", "y", "w", "h")},
                                  "image": asset(layer["asset"]), "uv": uv,
                                  "opacity": min(1, max(0, number(layer.get("opacity"), 1)))})
    requirements = body.get("requirements", {})
    out["aspects"] = []
    arcane = out["family"] == "arcane"
    aspect_costs = requirements.get("aspects" if arcane else "essentia", {})
    for tag, amount in sorted(aspect_costs.items(), key=lambda e: -(e[1] if type(e[1]) is int else 0)):
        if type(amount) is not int:
            continue
        info = idx.presentation["aspects"].get(tag, {})
        out["aspects"].append({"tag": tag, "name": info.get("name", tag), "amount": amount,
                               "icon": icons.recipe_image(info.get("icon", f"aspect/{tag}.webp"))})
    if arcane:
        positions = {}
        for vis in requirements.get("vis", [])[:64]:
            if not isinstance(vis, dict):
                continue
            stack = vis.get("item", {})
            match = re.search(r'Aspect:\s*"([^"\\]+)"', stack.get("nbt", "")) if isinstance(stack, dict) else None
            if match:
                positions[match[1]] = {"x": number(vis.get("relx")), "y": number(vis.get("rely")), "w": 16, "h": 16}
        for i, aspect in enumerate(out["aspects"]):
            aspect.update(positions.get(aspect["tag"], {"x": 83 - len(out["aspects"])*9.5 + i*19, "y": 122, "w": 16, "h": 16}))
        prereqs = [p for p in requirements.get("prereqs", [])[:16] if isinstance(p, dict) and isinstance(p.get("research"), str)]
        if prereqs:
            # Cached isResearched belongs to the headless export, not the viewer's player.
            out["research"] = {"name": "; ".join(dict.fromkeys(p["research"] for p in prereqs)), "state": "unknown"}
    research = requirements.get("research")
    if isinstance(research, str) and research:
        info = idx.presentation["research_info"].get(research, {})
        out["research"] = {"key": research, "name": mctext.plain(info.get("name", research)),
                           "category": mctext.plain(info["category_name"]) if info.get("category_name") else None, "icon": asset(info.get("icon")), "state": "unknown"}
    return out
