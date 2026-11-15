"""GregTech ore processing diagrams, composed from the recipe index's own
GT recipes as NEI Custom Diagram's "GregTech Ore Processing" page draws
them: ore and raw ore through the macerator, ore washer, chemical baths,
thermal centrifuge, sifter, centrifuge and electromagnetic separator to
dust, each step with its byproducts.

NEI Custom Diagram builds its pages when the game first asks for them,
after the export has run, so the bundle has none; but it derives them
from the same GT recipe maps the index already has. One diagram per
crushed ore (an item both the ore washer and the thermal centrifuge
take), with the geometry of NEI Custom Diagram's LayoutHandler: a grid
whose point (x, y) is a slot centred on (9 + 12x, 9 + 12y), in the
recipe page's pixels.

Standard library only: recipe_index.py imports it in its isolated build
process."""

import re

MAP = "ore_processing"
TITLE = "GregTech Ore Processing"
# The maps a diagram reads, by step.
MACERATOR = "gt.recipe.macerator"
ORE_WASHER = "gt.recipe.orewasher"
THERMAL_CENTRIFUGE = "gt.recipe.thermalcentrifuge"
CHEMICAL_BATH = "gt.recipe.chemicalbath"
SIFTER = "gt.recipe.sifter"
CENTRIFUGE = "gt.recipe.centrifuge"
SEPARATOR = "gt.recipe.electromagneticseparator"
MAPS = (MACERATOR, ORE_WASHER, THERMAL_CENTRIFUGE, CHEMICAL_BATH, SIFTER, CENTRIFUGE, SEPARATOR)
# Without these there is no diagram; the others only add branches.
REQUIRED_MAPS = (MACERATOR, ORE_WASHER, THERMAL_CENTRIFUGE)
BATH_FLUIDS = (("mercury", "Mercury"), ("sodiumpersulfate", "Sodium Persulfate"))
# NEI Custom Diagram leaves out the stone dust a step also gives.
STONE_DUST = "gregtech:gt.metaitem.01:2299"
# Raw ore items (GT's rawOre, bartworks', GT++'s), told from ore blocks.
_RAW_ORE_KEY = re.compile(r"^(?:gregtech:gt\.metaitem\.03:|bartworks:gt\.bwMetaGeneratedrawOre:|miscutils:oreRaw)")
WIDTH = 166


def grid(x, y):
    return 9 + 12 * x, 9 + 12 * y


def _edge(x, y, direction):
    cx, cy = grid(x, y)
    dx, dy = {"N": (0, -9), "S": (0, 9), "E": (9, 0), "W": (-9, 0)}[direction]
    return cx + dx, cy + dy


def _order(key):
    """GregTech's own items first, then by item and numeric damage: a GT
    ore's plain stone variant (damage 69) before its nether one (1069)."""
    item, _, damage = key.rpartition(":")
    return (not item.startswith("gregtech:"), item, int(damage) if damage.isdigit() else -1, key)


def is_raw_ore(key, name):
    return bool(_RAW_ORE_KEY.match(key)) or (name or "").startswith("Raw ")


class _Recipes:
    """Each map's recipes by their first item input, in index order."""

    def __init__(self, rows):
        self.by_input = {name: {} for name in MAPS}
        self.making = {}
        for map_name, body in rows:
            if map_name not in self.by_input or not body.get("i") or not body.get("o"):
                continue
            self.by_input[map_name].setdefault(body["i"][0][0], []).append(body)
            if map_name == MACERATOR:
                self.making.setdefault(body["o"][0][0], []).append(body)

    def first(self, map_name, key, fluid=None):
        """The recipe taking key with the most outputs (some machines have a
        plain variant beside one with byproducts), or None."""
        found = [b for b in self.by_input[map_name].get(key, [])
                 if fluid is None or any(f[0] == fluid for f in b.get("f", []))]
        return max(found, key=lambda b: len(b["o"]), default=None)


def _outputs(body, limit):
    """A step's outputs as [key, n, chance], main product first, less stone dust."""
    out = [[o[0], o[1], o[2] if len(o) > 2 else None] for o in body["o"] if o[0] != STONE_DUST]
    return out[:limit]


class _Diagram:
    def __init__(self, machines):
        self.machines = machines
        self.slots = []     # {"x", "y", "alts": [[key, n, chance]], "framed"}
        self.lines = []
        self.arrows = []
        self.steps = []     # {"map", "in": key, "out": [[key, n, chance]], "fluid"?}

    def slot(self, x, y, alts, framed=True):
        self.slots.append({"x": x - 9, "y": y - 9, "alts": alts, "framed": framed})

    def machine(self, map_name, gx, gy, label=None):
        machine = self.machines.get(map_name)
        if machine:
            cx, cy = grid(gx, gy)
            self.slots.append({"x": cx - 9, "y": cy - 9, "alts": [[machine["key"], 1, None]], "framed": False,
                               "machine": label or machine["name"]})

    def group(self, gx, gy, stacks, direction="S", cols=1):
        cx, cy = grid(gx, gy)
        step = 18 if direction == "S" else -18
        for k, stack in enumerate(stacks):
            row, col = divmod(k, cols)
            self.slot(round(cx + (col - (cols - 1) / 2) * 18), cy + row * step, [stack])

    def path(self, *points, arrow=None):
        """A line through grid points, ending in an arrow at a slot's edge
        ((x, y, direction)) when given."""
        pts = [grid(*p) for p in points]
        if arrow:
            pts.append(_edge(*arrow))
            self.arrows.append({"x": pts[-1][0], "y": pts[-1][1], "dir": {"N": "S", "S": "N", "E": "W", "W": "E"}[arrow[2]]})
        for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
            self.lines.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})


def _machines(machines_by_map):
    """Each map's machine to draw: its first electric one (the LV
    machine NEI Custom Diagram shows), not a steam one."""
    out = {}
    for map_name, machines in machines_by_map.items():
        keyed = [m for m in machines if m.get("key")]
        chosen = next((m for m in keyed if "steam" not in m["name"].lower()), keyed[0] if keyed else None)
        if chosen:
            out[map_name] = chosen
    return out


def compose(rows, machines_by_map, names):
    """[(map name, body)] of the index's GT recipes, {map: [machine]},
    {item key: name} -> [diagram body] for the ore_processing map."""
    recipes = _Recipes(rows)
    machines = _machines(machines_by_map)
    washed = set(recipes.by_input[ORE_WASHER])
    crushed_ores = sorted(k for k in washed if k in recipes.by_input[THERMAL_CENTRIFUGE] and k in recipes.making)
    out = []
    for crushed in crushed_ores:
        body = _diagram(crushed, recipes, machines, names)
        if body:
            out.append(body)
    return out


def _diagram(crushed, recipes, machines, names):
    sources = [b for b in recipes.making[crushed] if len(b["i"]) == 1]
    keys = {b["i"][0][0] for b in sources}
    ores = sorted((k for k in keys if not is_raw_ore(k, names.get(k))), key=_order)
    raws = sorted((k for k in keys if is_raw_ore(k, names.get(k))), key=_order)
    if not ores and not raws:
        return None
    d = _Diagram(machines)
    inputs, outputs = set(ores) | set(raws), set()

    def step(map_name, key, label_at, path, arrow, group_at, size, direction="S", cols=1, fluid=None, label=None):
        recipe = recipes.first(map_name, key, fluid)
        if not recipe:
            return None
        stacks = _outputs(recipe, size)
        if not stacks:
            return None
        d.path(*path, arrow=arrow)
        d.machine(map_name, *label_at, label=label)
        d.group(*group_at, stacks, direction=direction, cols=cols)
        d.steps.append({"map": map_name, "in": key, "out": stacks, **({"fluid": fluid} if fluid else {})})
        outputs.update(s[0] for s in stacks)
        return stacks[0][0]

    # Ore blocks of every stone type cycle in one slot, as in NEI; the raw ore beside them.
    if ores:
        d.slot(*grid(4, 2), [[k, 1, None] for k in ores])
        step(MACERATOR, ores[0], (4, 5), [(4, 2)], (4, 7, "N"), (4, 7), 2)
    if raws:
        d.slot(*grid(6, 2), [[k, 1, None] for k in raws])
        step(MACERATOR, raws[0], (6, 5), [(6, 2)], (6, 7, "N"), (6, 7), 2)
        if ores:
            d.path((4, 7), (6, 7))
    inputs.add(crushed)

    impure = step(MACERATOR, crushed, (2, 12), [(4, 7), (3, 7), (3, 10), (2, 10)], (2, 14, "N"), (2, 14), 2)
    purified = step(ORE_WASHER, crushed, (8, 8), [(4, 7), (8, 7)], (8, 10, "N"), (8, 10), 2)
    for (fluid, label), x in zip(BATH_FLUIDS, (0, 2), strict=True):
        step(CHEMICAL_BATH, crushed, (x, 8), [(4, 7), (3, 7), (3, 10), (x, 10)], (x, 6, "S"), (x, 6), 3,
             direction="N", fluid=fluid, label=f"Chemical Bath ({label})")
    if impure:
        inputs.add(impure)
        step(CENTRIFUGE, impure, (0, 14), [(2, 14), (0, 14)], (0, 16, "N"), (0, 16), 2)

    purified_dust = None
    if purified:
        inputs.add(purified)
        purified_dust = step(MACERATOR, purified, (10, 12), [(8, 10), (10, 10)], (10, 14, "N"), (10, 14), 2)
        step(SIFTER, purified, (10, 8), [(8, 10), (10, 10)], (10, 5, "S"), (10, 5), 9, direction="N", cols=3)
    if purified_dust:
        inputs.add(purified_dust)
        step(CENTRIFUGE, purified_dust, (8, 14), [(10, 14), (8, 14)], (8, 16, "N"), (8, 16), 2)
        step(SEPARATOR, purified_dust, (12, 14), [(10, 14), (12, 14)], (12, 12, "S"), (12, 12), 3, direction="N")

    # The thermal centrifuge: one, fed by both crushed and purified ore, when
    # both give the same; otherwise one each.
    from_crushed = recipes.first(THERMAL_CENTRIFUGE, crushed)
    from_purified = recipes.first(THERMAL_CENTRIFUGE, purified) if purified else None
    centrifuged = []
    same = (from_crushed and from_purified
            and _outputs(from_crushed, 2) == _outputs(from_purified, 2))
    if same or (from_crushed and not from_purified):
        path = [(4, 7), (5, 7), (5, 12), (7, 12), (7, 10), (8, 10)] if same else [(4, 7), (5, 7), (5, 12), (6, 12)]
        d.path(*path)
        main = step(THERMAL_CENTRIFUGE, crushed, (6, 12), [(6, 12)], (6, 14, "N"), (6, 14), 2)
        if main:
            centrifuged.append(main)
            inputs.add(main)
            step(MACERATOR, main, (4, 14), [(6, 14), (4, 14)], (4, 16, "N"), (4, 16), 2)
    elif from_purified:
        a = step(THERMAL_CENTRIFUGE, crushed, (4, 10), [(4, 7), (5, 7), (5, 10), (4, 10)], (4, 12, "N"), (4, 12), 2) \
            if from_crushed else None
        b = step(THERMAL_CENTRIFUGE, purified, (6, 10), [(8, 10), (6, 10)], (6, 12, "N"), (6, 12), 2)
        for key in (a, b):
            if key:
                inputs.add(key)
        main = b or a
        if main:
            if a:
                d.path((4, 12), (6, 12))
            step(MACERATOR, main, (6, 16), [(5, 12), (5, 15), (6, 15), (6, 16)], (4, 16, "E"), (4, 16), 2)

    slots = d.slots
    height = max(s["y"] + 18 for s in slots) + 2
    groups = [{"kind": "item", "alts": s["alts"]} for s in slots]
    layout = {"family": "ore_tree", "width": WIDTH, "height": height, "graphics": "slots_only",
              "slots": [{"role": "groups", "index": i, "x": s["x"], "y": s["y"], "w": 18, "h": 18,
                         **({"framed": False, "label": s["machine"]} if not s["framed"] else {})}
                        for i, s in enumerate(slots)],
              "lines": d.lines, "arrows": d.arrows}
    machine_keys = {s["alts"][0][0] for s in slots if not s["framed"]}
    return {"type": "ore_tree", "i": [[k, 1, None] for k in sorted(inputs)], "f": [],
            "o": [[k, 1, None] for k in sorted(outputs - machine_keys)], "g": [],
            "groups": groups, "preview": layout, "steps": d.steps, "crushed": crushed}
