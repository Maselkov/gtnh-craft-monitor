"""Local dev server with a fake game feeding it.

    python dev.py               (from server/, with requirements.txt installed)

Starts the real app on a throwaway data directory with:
- a small generated game data bundle, so every fake item has an icon;
- an admin user, whose access token is printed at startup;
- a week of backfilled power (two machines) and item history, so the
  charts have lines;
- a fake game in a background thread that talks to the real API endpoints
  the way the oc/ scripts do: crafting CPUs whose jobs progress, finish
  and restart, network scans, power readings, and craft/cancel requests
  from the page accepted (or refused when every CPU is busy).

Its favicon and app icons are red (ICON_COLOR), so a dev tab doesn't
pass for a real server's.

Options: --port, --host (0.0.0.0 to try it from a phone), --data-dir to
keep the data between runs, --no-icons, --open to open a browser.
Nothing here touches server/data/.
"""

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import random
import re
import secrets
import shutil
import signal
import struct
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile


# A deployment's settings (a sourced .env, say) mustn't leak in: a real
# API key, data directory or GTNH version would override the dev ones,
# or refuse to start.
for _name in ("API_KEY", "DATA_DIR", "GTNH_VERSION", "GTNH_TEXTURES", "GAMEDATA_API_URL",
              "GAMEDATA_REPO", "TRUSTED_PROXIES", "VAPID_SUBJECT", "STALE_AFTER_SECONDS", "SESSION_LIFETIME_SECONDS",
              "AUTOCRAFT_KEEP_IDLE_CPUS", "AUTOCRAFT_RETRY_SECONDS", "CRAFT_STALL_SECONDS",
              "CRAFT_BLOCK_GRACE_SECONDS",
              "WEBHOOK_URLS", "WEBHOOK_EVENTS", "WEBHOOK_HOSTS", "PUBLIC_URL", "SCRIPTS_REPO",
              "HISTORY_HOURLY_AFTER_DAYS", "HISTORY_DAILY_AFTER_DAYS"):
    os.environ.pop(_name, None)
# Read by gcm.config at import: session cookies over plain http.
os.environ["SESSION_COOKIE_SECURE"] = "0"
# The crafting tree calls a step stalled after this long with nothing
# back: well within T00's held bolts (CHAIN_JOB), so the dev page shows
# one. Blocked and busy steps (gcm/bottlenecks.py) count after the grace.
os.environ["CRAFT_STALL_SECONDS"] = "30"
os.environ["CRAFT_BLOCK_GRACE_SECONDS"] = "10"
# As a published image would have it: the first fake computer runs older
# scripts than this, so admins see the notice asking to update it. And
# the real latest release is newer than this server, so Status says so
# (and auto-update holds back, as it never updates past the latest).
os.environ["GCM_SCRIPTS_VERSION"] = "v1.1.0"
os.environ["GCM_VERSION"] = "v1.1.0"
# The favicon and app icons in red (gcm/app_icons.py), so a dev tab
# doesn't pass for a real server's.
os.environ["ICON_COLOR"] = "#ff6b6b"

from gcm import bottlenecks, create_app, db, icons, patterns, store  # noqa: E402
from gcm.routes.crafts import name_cpus  # noqa: E402

API_KEY = "dev-" + secrets.token_hex(24)
TICK_SECONDS = 2
SCAN_EVERY_TICKS = 15
# Between scans, the stock rules' items are checked this often, as
# network_browser.lua does every WATCH_INTERVAL_SECONDS.
WATCH_EVERY_TICKS = 5
PATTERN_SCAN_EVERY_TICKS = 900  # half an hour, as patterns rarely change
ADMIN_NAME = "Admin"

# ---------------------------------------------------------------- items

# (name, shape, colour). Items get mod gregtech and a made-up damage value;
# fluids have no mod, which is how the game reports them, and neither
# does essentia, named by its aspect tag. Essentia gets no icon, as
# game data before aspect icons has none.
ITEMS = [
    ("Tungstensteel Ingot", "ingot", (70, 70, 120)),
    ("Kanthal Dust", "dust", (190, 160, 110)),
    ("Inconel-625 Plate", "plate", (110, 190, 110)),
    ("Inconel-625 Ingot", "ingot", (110, 190, 110)),
    ("Inconel-625 Gear", "gear", (110, 190, 110)),
    ("Any IV Circuit", "circuit", (60, 60, 70)),
    ("Nichrome Ingot", "ingot", (200, 190, 230)),
    ("Tungstensteel Dust", "dust", (70, 70, 120)),
    ("Glass Tube", "rod", (200, 230, 240)),
    ("Fine Platinum Wire", "wire", (230, 230, 170)),
    ("Europium Foil", "plate", (240, 170, 220)),
    ("Iridium Screw", "screw", (220, 220, 235)),
    ("Steel Bolt", "rod", (130, 130, 140)),
    ("Osmium Rod", "rod", (90, 110, 230)),
    ("Naquadah Ring", "ring", (40, 50, 40)),
    ("Titanium Gear", "gear", (220, 160, 240)),
    ("Neutronium Plate", "plate", (240, 240, 240)),
    ("Silicon Wafer", "plate", (60, 60, 80)),
    ("Polybenzimidazole Sheet", "plate", (40, 40, 40)),
    ("SMD Capacitor", "circuit", (200, 180, 90)),
    ("SMD Transistor", "circuit", (120, 90, 60)),
    ("Rubber Ring", "ring", (30, 30, 30)),
    ("Lapotron Crystal", "gem", (60, 90, 220)),
    ("Graphene Sheet", "plate", (100, 100, 100)),
    ("Draconium Ingot", "ingot", (150, 70, 200)),
    ("Niobium-Titanium Wire", "wire", (50, 40, 60)),
    ("HSS-G Frame", "frame", (150, 140, 60)),
    ("Ultimate Circuit", "circuit", (80, 40, 110)),
    ("Electrum Foil", "plate", (240, 230, 110)),
    ("Stainless Steel Rotor", "gear", (200, 200, 220)),
    ("Tantalum Screw", "screw", (100, 100, 120)),
    ("Molybdenum Dust", "dust", (180, 180, 220)),
    ("Integral Framework I", "frame", (100, 100, 110)),
    ("Dangote Distillus", "block", (80, 80, 90)),
    ("Radon Plasma", "fluid", (240, 0, 240)),
    ("Molten Inconel-625", "fluid", (100, 170, 100)),
    ("Bacterial Sludge", "fluid", (20, 70, 20)),
    ("Water", "fluid", (50, 80, 230)),
    ("Glass Dust", "dust", (230, 230, 230)),
    ("Iron Ingot", "ingot", (215, 215, 215)),
    # A real GTNH name, long enough to wrap on a crafting card.
    ("Exquisite Cerium-doped Lutetium Aluminium Garnet (Ce:LuAG)", "gem", (130, 220, 60)),
    ("Diamond", "gem", (110, 230, 230)),
    ("Industrial Diamond", "gem", (200, 240, 240)),
    ("Flawless Diamond", "gem", (150, 250, 250)),
    ("Diamond Dust", "dust", (110, 230, 230)),
    ("Block of Diamond", "block", (110, 230, 230)),
    ("Dimensionally Transcendent Plasma Forge", "block", (90, 40, 140)),
    # What it's built from (STRUCTURES), for its multiblock kit.
    ("Dimensionally Transcendent Casing", "block", (60, 30, 90)),
    ("Dimensional Injection Casing", "block", (120, 60, 160)),
    ("Dimensional Bridge", "block", (200, 120, 240)),
    ("Cupronickel Coil Block", "block", (200, 120, 60)),
    ("Kanthal Coil Block", "block", (190, 160, 110)),
    ("Nichrome Coil Block", "block", (200, 190, 230)),
    # A real name with formatting codes in it, as AE2's storage parts have.
    ("\u00a7a16k\u00a7r ME Storage Component", "circuit", (60, 140, 60)),
    # Newer than the selected game data: see NEWER_ITEM.
    ("Reinforced Neutronium Casing", "block", (70, 70, 80)),
    ("Ordo", "essentia", None),
    ("Metallum", "essentia", None),
    ("Praecantatio", "essentia", None),
]


# An item of GTNH_VERSION's that only NEXT_VERSION's catalog has (a new
# casing block, as 2.9.0's gt.blockcasings14:6, but an id no real version
# has, so the published catalogs the check also reads never have it), as
# if the game had been updated since its game data was picked. A pattern makes it, so admins
# see the notice that the game data looks older than the game
# (gcm/gamedata_check.py), and the scan skips it, as the real one would.
NEWER_ITEM = "Reinforced Neutronium Casing"


def _slug(name):
    return name.lower().replace(" ", "_").replace("-", "_")


def _item(index, name, shape, colour):
    if shape == "essentia":
        return {"name": name, "mod": None, "internal": name.lower(), "damage": None,
                "kind": "essentia", "shape": shape, "colour": colour}
    if shape == "fluid":
        return {"name": name, "mod": None, "internal": _slug(name), "damage": None,
                "kind": "fluid", "shape": shape, "colour": colour}
    if name == NEWER_ITEM:
        return {"name": name, "mod": "gregtech", "internal": "gt.blockcasings.dev", "damage": 6,
                "kind": "item", "shape": shape, "colour": colour}
    return {"name": name, "mod": "gregtech", "internal": "gt.metaitem.01", "damage": 30000 + index,
            "kind": "item", "shape": shape, "colour": colour}


CATALOG = [_item(i, *entry) for i, entry in enumerate(ITEMS)]
BY_NAME = {it["name"]: it for it in CATALOG}

# Forestry bees: one item id per kind (princess, drone, queen), the species
# only in their NBT, as the game has them. Recipes and icons know each
# species (game data's display stacks and by_key_label); the network has a
# few, with their NBT. Illustrative mutations, for the recipe book's
# breeding cards and Bee Better At Bees' tree: (species, colour, parents,
# chance, conditions).
BEE_KINDS = {"Princess": "beePrincessGE", "Drone": "beeDroneGE", "Queen": "beeQueenGE"}
BEES = [
    ("Forest", (60, 120, 220), None, None, []),
    ("Meadows", (220, 60, 60), None, None, []),
    ("Common", (180, 180, 180), ("Forest", "Meadows"), 15, []),
    ("Cultivated", (90, 200, 90), ("Common", "Forest"), 12, []),
    ("Diligent", (200, 70, 200), ("Cultivated", "Common"), 10,
     ["Requires Warm temperature.", "Requires Block of Diamond as a foundation."]),
]
BEE_SPECIES = {name: (f"forestry.species{name}", colour) for name, colour, *_ in BEES}
# What the fake network holds: (species, kind, amount).
NETWORK_BEES = [("Diligent", "Princess", 1), ("Cultivated", "Drone", 12), ("Forest", "Drone", 30)]


def bee_key(kind):
    return f"Forestry:{BEE_KINDS[kind]}:0"


def bee_snbt(species):
    """A bee's NBT as the exporter writes it (NBTTagCompound.toString())."""
    uid = BEE_SPECIES[species][0]
    return ('{MaxH:20,Health:20,IsAnalyzed:1b,Genome:{Chromosomes:[0:{Slot:0b,UID0:"%s",UID1:"%s"}]}}' % (uid, uid))


def bee_tag(species):
    """The same NBT as network_browser.lua sends it: gzipped binary, hex."""
    def name(text):
        raw = text.encode()
        return struct.pack(">H", len(raw)) + raw
    uid = BEE_SPECIES[species][0]
    chromosome = (b"\x01" + name("Slot") + b"\x00" + b"\x08" + name("UID0") + name(uid)
                  + b"\x08" + name("UID1") + name(uid) + b"\x00")
    genome = b"\x09" + name("Chromosomes") + b"\x0a" + struct.pack(">i", 1) + chromosome + b"\x00"
    body = b"\x01" + name("IsAnalyzed") + b"\x01" + b"\x0a" + name("Genome") + genome + b"\x00"
    return gzip.compress(b"\x0a\x00\x00" + body).hex()


def bee_icon_path(species, kind):
    return f"item/Forestry/{BEE_KINDS[kind]}~0~{species.lower()}.webp"


def bee_display_id(species, kind):
    return f"dev-bee-{species.lower()}-{kind.lower()}"


def bee_display_stacks():
    return {bee_display_id(species, kind): {"key": bee_key(kind), "name": f"{species} {kind}",
                                            "nbt": bee_snbt(species), "icon": bee_icon_path(species, kind)}
            for species in BEE_SPECIES for kind in BEE_KINDS}


def stack(name, size):
    """An item as the game reports it in a craft or scan."""
    it = BY_NAME[name]
    return {"name": it["name"], "mod": it["mod"], "internal": it["internal"],
            "damage": it["damage"], "size": int(size)}


# ------------------------------------------------------------- patterns

# What the fake network's patterns make, for the craft dialog's plan
# (gcm/planner.py): (provider, crafting?, outputs, inputs), each a list
# of (name, size). Covers a chain (gear <- plate <- ingot), a step with
# two patterns (plates), a cycle (ingot <-> molten), an output made in
# batches (bolts), an input nothing makes and the network never has,
# essentia (the framework), and ore-dictionary substitutes: Block of
# Diamond takes Industrial Diamonds as diamonds too, the way the real
# Substitute-ticked pattern does. A fifth field holds a pattern's NBT
# flags.
PATTERNS = [
    ("Molecular Assembler", True, [("Inconel-625 Gear", 1)], [("Inconel-625 Plate", 4), ("Steel Bolt", 1)]),
    ("Bending Machine T4:EV", False, [("Inconel-625 Plate", 1)], [("Inconel-625 Ingot", 1)]),
    ("Fluid Solidifier {Plate}", False, [("Inconel-625 Plate", 1)], [("Molten Inconel-625", 144)]),
    ("Fluid Solidifier {Ingot}", False, [("Inconel-625 Ingot", 1)], [("Molten Inconel-625", 144)]),
    ("Fluid Extractor", False, [("Molten Inconel-625", 144)], [("Inconel-625 Ingot", 1)]),
    ("Lathe T3:HV", False, [("Steel Bolt", 4)], [("Iron Ingot", 1)]),
    ("Circuit Assembler", False, [("Ultimate Circuit", 1)],
     [("Draconium Ingot", 2), ("SMD Capacitor", 4), ("Lapotron Crystal", 1), ("Molten Inconel-625", 288)]),
    ("Molecular Assembler", True, [("Titanium Gear", 1)], [("Neutronium Plate", 4), ("Iridium Screw", 1)]),
    ("Infusion Altar", False, [("Integral Framework I", 1)], [("Iron Ingot", 2), ("Ordo", 64), ("Praecantatio", 16)]),
    ("Molecular Assembler", True, [("Block of Diamond", 1)], [("Diamond", 9)], {"substitute": True}),
    ("Industrial Sledgehammer", False, [("Diamond", 2)], [("Flawless Diamond", 1)]),
    ("Electric Implosion Compressor", False, [("Industrial Diamond", 3)], [("Diamond Dust", 4)],
     {"beSubstitute": True}),
    ("Circuit Assembler", False, [("\u00a7a16k\u00a7r ME Storage Component", 1)],
     [("SMD Capacitor", 4), ("Silicon Wafer", 1), ("Glass Dust", 2)]),
    ("Molecular Assembler", True, [(NEWER_ITEM, 1)], [("Neutronium Plate", 6), ("Titanium Gear", 1)]),
    # The DTPF's blocks, for its multiblock kit's plan: the casings share
    # Neutronium Plates, so asking for many of both comes up short.
    ("Assembler", False, [("Dimensionally Transcendent Casing", 1)], [("Neutronium Plate", 4), ("Iridium Screw", 2)]),
    ("Assembler", False, [("Dimensional Injection Casing", 1)], [("Neutronium Plate", 2), ("Europium Foil", 4)]),
    ("Assembler", False, [("Cupronickel Coil Block", 1)], [("Fine Platinum Wire", 8), ("Steel Bolt", 2)]),
    ("Assembler", False, [("Kanthal Coil Block", 1)], [("Kanthal Dust", 8), ("Steel Bolt", 2)]),
    ("Assembler", False, [("Nichrome Coil Block", 1)], [("Nichrome Ingot", 8), ("Steel Bolt", 2)]),
]
# Some items' tooltips below their name, as the bundle's item_info.json
# has them (gcm/item_info.py): formatting codes and all, so the page's
# colours, multi-line tooltips and #search show. Made up, if GTNH-like,
# except the DTPF's: GTNH 2.9.0-RC-1's own, a long multiblock tooltip.
DESCRIPTIONS = {
    "Iron Ingot": "\u00a7eFe",
    # GregTech's font glyph for subscript 0 (\ue01a), as the real one has.
    "Tungstensteel Ingot": "\u00a7e(Fe\u2085\ue01aC)W",
    "Kanthal Dust": "\u00a7eFeAlCr",
    "Nichrome Ingot": "\u00a7eNi\u2084Cr",
    "Lapotron Crystal": "\u00a77Stores \u00a7e10,000,000 EU\n\u00a77Tier: \u00a7bIV",
    "Dimensionally Transcendent Plasma Forge": (
        "Machine Type: \u00a7ePlasma Forge, DTPF\u00a7r\n"
        "Transcending Dimensional Boundaries.\n"
        "Takes \u00a7c8\u00a77 hours of continuous run time to fully breach dimensional\n"
        "boundaries and achieve maximum efficiency, reducing fuel consumption by up to \u00a7c50%\n"
        "When no recipe is running, fuel discount decays x\u00a7c100\u00a77 as fast as it builds up, draining\n"
        "the total amount of stored runtime\n"
        "\u00a77\u00a7m-----------------------------------------\n"
        "Multidimensional spaces can be perfectly aligned and synchronized in this state, \n"
        "allowing \u00a76Dimensional Convergence \u00a77to occur. To reach the required stability threshold,\n"
        "a \u00a7bTransdimensional Alignment Matrix \u00a77must be placed in the controller\n"
        "When \u00a76Convergence \u00a77is active, it allows the forge to perform \u00a7cPerfect Overclocks\u00a77,\n"
        "but the extra power cost is instead added in form of increased catalyst amounts\n"
        "This machine can run recipes regardless of tier, if given enough energy\n"
        "\u00a7aSupports Multi-Amp and Laser Hatches!\n"
        "\u00a77\u00a7m-----------------------------------------\n"
        "Added by: \u00a7a\u00a74\u00a7l\u00a7o\u00a7nC\u00a76\u00a7l\u00a7o\u00a7no\u00a7a\u00a7l\u00a7o\u00a7nl\u00a73\u00a7l\u00a7o\u00a7ne\u00a75\u00a7l\u00a7o\u00a7nn\u00a7r\u00a77"
    ),
}
# The same with Shift held, where that's different.
SHIFT_DESCRIPTIONS = {
    "Dimensionally Transcendent Plasma Forge": (
        "\u00a7fDimensions: \u00a7633\u00a77x\u00a7633\u00a77x\u00a7624\u00a77 (\u00a76L\u00a77x\u00a76W\u00a77x\u00a76H\u00a77) \n"
        "\u00a7fStructure: \n"
        "   \u00a7fController: \u00a77Middle of the structure, 3rd layer\n"
        "   \u00a762121 \u00a7fDimensionally Transcendent Casing\n"
        "   \u00a762112 \u00a7fHeating Coil \u00a7b(Tiered)\n"
        "   \u00a761250-1270 \u00a7fDimensional Injection Casing\n"
        "   \u00a76120 \u00a7fDimensional Bridge\n"
        "   \u00a7e1-2 \u00a7fEnergy Hatch: \u00a77Any injection casing\n"
        "   \u00a7e1+ \u00a7fInput Bus/Hatch: \u00a77Any injection casing\n"
        "   \u00a7e1+ \u00a7fOutput Bus/Hatch: \u00a77Any injection casing\n"
        "\n"
        "Subchannel \u00a7bcoil\u00a77 determines \u00a7bHeating Coil Tier\n"
        "\u00a77\u00a7m------------------------------\n"
        "\u00a7fUse the Multiblock Structure \u00a79Hologram \u00a71Projector\u00a7f to visualize/build the structure!"
    ),
}
# Chemical formulas, as GregTech's materials have them.
FORMULAS = {
    "Iron Ingot": "Fe", "Tungstensteel Ingot": "(Fe\u2085\ue01aC)W", "Kanthal Dust": "FeAlCr",
    "Nichrome Ingot": "Ni\u2084Cr", "Glass Dust": "SiO\u2082", "Osmium Rod": "Os",
}
# Rarity colours (formatting codes) of the uncommon items.
RARITY = {"Lapotron Crystal": "b", "Ultimate Circuit": "d"}
# The fluids' temperature in kelvin, whether each is a gas, and its
# formula if it has one.
FLUID_INFO = {"Radon Plasma": (10000, True, "Rn"), "Molten Inconel-625": (1700, False, "Ni\u2083CrMoNb"),
              "Bacterial Sludge": (300, False, None), "Water": (295, False, "H\u2082O")}
# The DTPF's structure, as the bundle's structures.json.gz and
# layouts.json.gz have it (tools/icon-export's Structures.java and
# Layouts.java): a small stand-in for the real one, its coil channel
# swapping the coils. The network has patterns for the casings and coils
# (KIT_CRAFTABLE), not the bridges, which the kit shows as having none.
DTPF = "Dimensionally Transcendent Plasma Forge"
DTPF_COILS = ["Cupronickel Coil Block", "Kanthal Coil Block", "Nichrome Coil Block"]
KIT_CRAFTABLE = {"Dimensionally Transcendent Casing", "Dimensional Injection Casing", *DTPF_COILS}


def _game_key(name):
    it = BY_NAME[name]
    return f"{it['mod']}:{it['internal']}:{it['damage']}"


def dtpf_layout():
    """{(x, y, z): block name} relative to the controller, which is in the
    middle of the front wall's bottom layer: an injection casing floor
    and roof, casing walls, a ring of coils inside and a bridge pillar in
    each corner."""
    blocks = {}
    for y in range(0, 7):
        for x in range(-4, 5):
            for z in range(-8, 1):
                edge_x, edge_z = abs(x) == 4, z in (-8, 0)
                if y in (0, 6):
                    blocks[(x, y, z)] = "Dimensional Injection Casing"
                elif edge_x and edge_z:
                    blocks[(x, y, z)] = "Dimensional Bridge"
                elif edge_x or edge_z:
                    blocks[(x, y, z)] = "Dimensionally Transcendent Casing"
                elif 2 <= y <= 4 and (abs(x) == 2 and -6 <= z <= -2 or z in (-6, -2) and abs(x) <= 2):
                    blocks[(x, y, z)] = DTPF_COILS[0]
    blocks[(0, 0, 0)] = DTPF
    return blocks


def _pack(x, y, z):
    return (x + 512) << 20 | (y + 512) << 10 | (z + 512)


def _deltas(positions):
    out, last = [], 0
    for p in sorted(positions):
        out.append(p - last)
        last = p
    return out


def _block_key(name):
    return "mte:" + _game_key(name) if name == DTPF else "gregtech:dev.block:" + str(BY_NAME[name]["damage"])


def structures():
    layout = dtpf_layout()
    counts = {}
    for name in layout.values():
        if name != DTPF:
            counts[_game_key(name)] = counts.get(_game_key(name), 0) + 1
    coil, n = _game_key(DTPF_COILS[0]), counts[_game_key(DTPF_COILS[0])]
    tiers = {str(i + 1): ({} if i == 0 else {coil: -n, _game_key(name): n}) for i, name in enumerate(DTPF_COILS)}
    names = [DTPF, "Dimensionally Transcendent Casing", "Dimensional Injection Casing", "Dimensional Bridge",
             *DTPF_COILS]
    return {
        "format": 1,
        "names": {_game_key(n): n for n in names},
        "multiblocks": {_game_key(DTPF): {
            "channels": [{"name": "coil", "min": 1, "max": len(DTPF_COILS)}],
            "base": counts,
            "tiers": {"coil": tiers},
        }},
    }


def layouts():
    """layouts.json.gz's contents, and its atlas: one texture per block."""
    from PIL import Image

    layout = dtpf_layout()
    textured = [n for n in dict.fromkeys(layout.values()) if n != DTPF] + DTPF_COILS[1:]
    textured = list(dict.fromkeys(textured))
    atlas = Image.new("RGBA", (16 * len(textured), 16))
    blocks = {_block_key(DTPF): {"item": _game_key(DTPF)}}
    for i, name in enumerate(textured):
        atlas.paste(_frame("block", BY_NAME[name]["colour"]), (16 * i, 0))
        blocks[_block_key(name)] = {"item": _game_key(name), "faces": [i] * 6}
    by_key = {}
    for (x, y, z), name in layout.items():
        by_key.setdefault(_block_key(name), []).append(_pack(x, y, z))
    coil = _block_key(DTPF_COILS[0])
    tiers = {str(i + 1): ({} if i == 0 else {"swap": {coil: _block_key(name)}}) for i, name in enumerate(DTPF_COILS)}
    data = {
        "format": 1, "tile": 16, "columns": len(textured), "blocks": blocks,
        "multiblocks": {_game_key(DTPF): {"base": {k: _deltas(v) for k, v in by_key.items()},
                                          "tiers": {"coil": tiers}}},
    }
    out = io.BytesIO()
    atlas.save(out, format="PNG")
    return data, out.getvalue()


# What the ore dictionary says about CATALOG (gcm/oredict.py).
ORE_DICT = {"gemDiamond": ["Diamond", "Industrial Diamond"], "ingotIron": ["Iron Ingot"],
            "dustKanthal": ["Kanthal Dust"], "ingotTungstenSteel": ["Tungstensteel Ingot"]}
PATTERN_OUTPUTS = {name for _, _, outputs, *_ in PATTERNS for name, _ in outputs}
# Kept at none, so a plan for the Ultimate Circuit comes up short.
ALWAYS_EMPTY = {"Draconium Ingot"}
# Kept at this many, so the network always has one to look at.
ALWAYS_STOCKED = {"Dimensionally Transcendent Plasma Forge": 1}


def pattern_entry(name, size):
    """One side of a pattern, as network_browser.lua sends it."""
    it = BY_NAME[name]
    return {"kind": it["kind"], "mod": it["mod"], "internal": it["internal"], "damage": it["damage"],
            "name": name, "size": size}


def ore_dict():
    """ORE_DICT as the bundle's ore_dict.json has it."""
    return {name: [f"{BY_NAME[n]['mod']}:{BY_NAME[n]['internal']}:{BY_NAME[n]['damage']}" for n in members]
            for name, members in ORE_DICT.items()}


def item_info():
    """DESCRIPTIONS and the rest as the bundle's item_info.json has them."""
    def by_key(values):
        return {"by_key": {f"{BY_NAME[n]['mod']}:{BY_NAME[n]['internal']}:{BY_NAME[n]['damage']}": v
                           for n, v in values.items()}}
    return {
        "tooltips": by_key(DESCRIPTIONS),
        "shift_tooltips": by_key(SHIFT_DESCRIPTIONS),
        "rarity": by_key(RARITY),
        "formulas": by_key(FORMULAS),
        "fluids": {BY_NAME[n]["internal"]: {"temperature": t, "gaseous": gas, **({"formula": f} if f else {})}
                   for n, (t, gas, f) in FLUID_INFO.items()},
        "mods": {"gregtech": "GregTech", "minecraft": "Minecraft"},
    }


def flags_tag(flags):
    """A pattern's NBT holding only byte flags (substitute, beSubstitute),
    gzipped and hex-encoded, as network_browser.lua sends a pattern's."""
    body = b"".join(b"\x01" + struct.pack(">H", len(n)) + n.encode() + bytes([1 if v else 0]) for n, v in flags.items())
    return gzip.compress(b"\x0a\x00\x00" + body + b"\x00").hex()


def pattern_scan():
    scanned = []
    for slot, (provider, crafting, outputs, inputs, *flags) in enumerate(PATTERNS):
        p = {"provider": {"name": provider, "x": 10 * slot, "y": 64, "z": 0, "dim": 0}, "slot": slot,
             "crafting": crafting,
             "inputs": [pattern_entry(*i) for i in inputs], "outputs": [pattern_entry(*o) for o in outputs]}
        if flags:
            p["tag"] = flags_tag(flags[0])
        scanned.append(p)
    return scanned


# ---------------------------------------------------------------- icons

def _mask(shape, x, y):
    """Whether pixel (x, y) of a 16x16 icon is inside the shape."""
    dx, dy = x - 7.5, y - 7.5
    r = (dx * dx + dy * dy) ** 0.5
    if shape == "ingot":
        return 5 <= y <= 10 and (2 + (10 - y) // 2) <= x <= (13 - (10 - y) // 2)
    if shape == "dust":
        return 6 <= y <= 13 and abs(dx) <= (y - 5) * 0.9
    if shape == "fluid":
        return True
    if shape == "plate":
        return 2 <= x <= 13 and 2 <= y <= 13
    if shape == "gear":
        teeth = r <= 7.2 and (abs(dx) < 1.5 or abs(dy) < 1.5 or abs(abs(dx) - abs(dy)) < 1.5)
        return (2.5 <= r <= 5.5) or (5.5 < r and teeth)
    if shape == "ring":
        return 3.5 <= r <= 6.5
    if shape == "wire":
        return 2.5 <= r <= 6.5 and int(r * 2) % 2 == 0
    if shape == "rod":
        return abs(x - (15 - y)) <= 1 and 1 <= y <= 14
    if shape == "screw":
        return (abs(dx) <= 1.5 and 5 <= y <= 14) or (3 <= y <= 5 and abs(dx) <= 3.5)
    if shape == "circuit":
        return 2 <= x <= 13 and 3 <= y <= 12
    if shape == "gem":
        return abs(dx) + abs(dy) <= 6.5
    if shape == "bee":
        body = ((dx + 1) / 6) ** 2 + ((dy - 2) / 3.6) ** 2 <= 1
        wing = ((dx - 1) / 3) ** 2 + ((dy + 3) / 2.5) ** 2 <= 1
        return body or wing
    if shape == "frame":
        edge = x in (2, 3, 12, 13) or y in (2, 3, 12, 13)
        return 2 <= x <= 13 and 2 <= y <= 13 and (edge or x == y or x == 15 - y)
    return 1 <= x <= 14 and 1 <= y <= 14  # block


def _pixel(shape, colour, x, y):
    if not _mask(shape, x, y):
        neighbours = ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        if any(0 <= a < 16 and 0 <= b < 16 and _mask(shape, a, b) for a, b in neighbours):
            return (20, 20, 25, 255)  # outline
        return (0, 0, 0, 0)
    # Lit from the top left, like Minecraft's item art.
    shade = 1.25 - (x + y) / 40
    if shape == "circuit" and (x + y) % 4 == 0 and 4 <= y <= 11:
        return (230, 200, 80, 255)
    if shape == "block" and (x in (1, 14) or y in (1, 14)):
        shade *= 0.7
    if shape == "bee":
        if y < 6:
            return (235, 240, 250, 255)  # wings
        if x % 3 == 0:
            return (30, 30, 30, 255)  # stripes
    return tuple(max(0, min(255, int(c * shade))) for c in colour) + (255,)


def _frame(shape, colour, brightness=1.0):
    from PIL import Image

    tinted = tuple(min(255, int(c * brightness)) for c in colour)
    image = Image.new("RGBA", (16, 16))
    image.putdata([_pixel(shape, tinted, x, y) for y in range(16) for x in range(16)])
    return image


def _webp(shape, colour):
    """An icon as the exporter writes it, lossless WebP. Fluids pulse, an
    animated WebP like the game's animated textures."""
    frames = [_frame(shape, colour, 1 + 0.15 * math.sin(i * math.pi / 4)) for i in range(8)] \
        if shape == "fluid" else [_frame(shape, colour)]
    out = io.BytesIO()
    frames[0].save(out, format="WEBP", lossless=True, save_all=len(frames) > 1,
                   append_images=frames[1:], duration=150, loop=0)
    return out.getvalue()


# Drawn with a halo past its box, as Avaritia's cosmic items are: the
# image is (16 + 2 * HALO_BLEED) / 16 times the box, the item in its
# middle, and the lookup's "bleed" table says so (gcm/icons.py).
HALOED = {"Neutronium Plate"}
HALO_BLEED = 12


def _haloed_webp(shape, colour):
    from PIL import Image

    size = 16 + 2 * HALO_BLEED
    centre = (size - 1) / 2
    image = Image.new("RGBA", (size, size))
    image.putdata([(255, 200, 255, int(160 * max(0.0, 1 - ((x - centre) ** 2 + (y - centre) ** 2) ** 0.5 / centre)))
                   for y in range(size) for x in range(size)])
    item = _frame(shape, colour)
    image.alpha_composite(item, (HALO_BLEED, HALO_BLEED))
    out = io.BytesIO()
    image.save(out, format="WEBP", lossless=True)
    return out.getvalue()


def _write_catalog(bundle, newer):
    with open(os.path.join(bundle, "item_catalog.txt"), "w", encoding="utf-8") as f:
        f.write("".join(sorted({f"{it['mod']}:{it['internal']}\n" for it in CATALOG
                                if it["mod"] and (newer or it["name"] != NEWER_ITEM)})))


def install_icons(data_dir, prerelease=False):
    """Writes a game data bundle for CATALOG and selects it, the same
    layout gcm/gamedata.py installs from a release. With prerelease, the
    selected version is a pre-release whose final release is installed
    too, and NEWER_ITEM is in neither."""
    version, next_version = (PRERELEASE_VERSION, GTNH_VERSION) if prerelease else (GTNH_VERSION, NEXT_VERSION)
    bundle = os.path.join(data_dir, "gamedata", version)
    os.makedirs(bundle, exist_ok=True)
    by_key, fluids_by_key, bleed = {}, {}, {}
    with zipfile.ZipFile(os.path.join(bundle, "images.zip"), "w") as zf:
        # Recipe glyph only; network essentia still exercises its missing-icon fallback.
        zf.writestr("aspect/ordo.webp", _webp("ring", (180, 180, 210)))
        for it in CATALOG:
            if it["kind"] == "essentia":
                continue
            if it["kind"] == "fluid":
                path = f"fluid/{it['internal']}.webp"
                fluids_by_key[it["internal"]] = path
            else:
                path = f"item/{it['mod']}/{it['internal']}~{it['damage']}.webp"
                by_key[f"{it['mod']}:{it['internal']}:{it['damage']}"] = path
            if it["name"] in HALOED:
                bleed[path] = HALO_BLEED
                zf.writestr(path, _haloed_webp(it["shape"], it["colour"]))
            else:
                zf.writestr(path, _webp(it["shape"], it["colour"]))
        by_key_label = {}
        for species, (_, colour) in BEE_SPECIES.items():
            for kind in BEE_KINDS:
                path = bee_icon_path(species, kind)
                zf.writestr(path, _webp("bee", colour))
                by_key_label[f"{bee_key(kind)}|{species} {kind}"] = path
                by_key.setdefault(bee_key(kind), path)
    with open(os.path.join(bundle, "icons_lookup.json"), "w", encoding="utf-8") as f:
        json.dump({"by_key": by_key, "by_key_label": by_key_label, "fluids_by_key": fluids_by_key, "by_label": {}, "bleed": bleed}, f)
    with open(os.path.join(bundle, "ore_dict.json"), "w", encoding="utf-8") as f:
        json.dump(ore_dict(), f)
    with open(os.path.join(bundle, "item_info.json"), "w", encoding="utf-8") as f:
        json.dump(item_info(), f)
    with gzip.open(os.path.join(bundle, "structures.json.gz"), "wt", encoding="utf-8") as f:
        json.dump(structures(), f)
    layout_data, atlas = layouts()
    with gzip.open(os.path.join(bundle, "layouts.json.gz"), "wt", encoding="utf-8") as f:
        json.dump(layout_data, f)
    with open(os.path.join(bundle, "layouts.png"), "wb") as f:
        f.write(atlas)
    _write_catalog(bundle, newer=False)
    # Its hash is the bundle's build id, which busts icon caches: new
    # icons after a change here get new URLs.
    digest = hashlib.sha256(open(os.path.join(bundle, "images.zip"), "rb").read()).hexdigest()
    with open(os.path.join(bundle, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"format": 1, "image_format": "webp", "files": {}, "generated_at": "dev", "images": digest}, f)
    # The same bundle as the version before it, with the recipes it had,
    # for the pattern check after an upgrade (gcm/recipes.py).
    before = os.path.join(data_dir, "gamedata", PREVIOUS_VERSION)
    shutil.rmtree(before, ignore_errors=True)
    shutil.copytree(bundle, before)
    write_recipes(os.path.join(bundle, "recipes.json.gz"), recipe_maps(changed=True))
    write_recipes(os.path.join(before, "recipes.json.gz"), recipe_maps(changed=False))
    # And a newer one, with NEWER_ITEM in its catalog.
    after = os.path.join(data_dir, "gamedata", next_version)
    shutil.rmtree(after, ignore_errors=True)
    shutil.copytree(bundle, after)
    _write_catalog(after, newer=not prerelease)
    with open(os.path.join(data_dir, "gamedata", "selected.json"), "w", encoding="utf-8") as f:
        json.dump({"version": version, "textures": "default", "previous": PREVIOUS_VERSION}, f)


# ---------------------------------------------------------------- recipes

# Named like GTNH's own versions, so they go in order
# (gamedata.version_key()).
GTNH_VERSION = "2.9.0-dev"
PREVIOUS_VERSION = "2.8.4-dev"
NEXT_VERSION = "2.9.1-dev"
PRERELEASE_VERSION = "2.9.0-RC-1-dev"
# How GTNH_VERSION's recipes differ from PREVIOUS_VERSION's, by the pattern
# provider whose recipe changes: two patterns broken (one by an item
# removed, which only an Unpackager takes apart now), one moved and two
# changed outside the pattern.
RECIPE_CHANGES = {
    "Bending Machine T4:EV": {"inputs": [("Inconel-625 Ingot", 2)]},
    "Circuit Assembler": {"removed": "Ultimate Circuit"},
    "Lathe T3:HV": {"machine": "Cutting Machine"},
    "Fluid Extractor": {"eut": 1920},
    "Electric Implosion Compressor": {"fluids": [("Water", 1000)]},
}


def _machine(provider):
    """What GregTech calls the machine a provider is named after: "Bending
    Machine T4:EV" is a Bending Machine."""
    return re.split(r" T\d| \{", provider)[0]


def _recipe_key(name):
    it = BY_NAME[name]
    return it["internal"] if it["kind"] == "fluid" else f"{it['mod']}:{it['internal']}:{it['damage']}"


def recipe_maps(changed):
    """{map: {"machines": [names], "recipes": [...]}} with item keys in
    place of indices: a GregTech recipe for each processing pattern, as
    the old version had it or, changed, the new one."""
    maps = {}
    for provider, crafting, outputs, inputs, *_ in PATTERNS:
        if crafting or any(BY_NAME[n]["kind"] == "essentia" for n, _ in inputs + outputs):
            continue
        change = RECIPE_CHANGES.get(provider, {}) if changed else {}
        if change.get("removed") in [n for n, _ in outputs]:
            # As GTNH removes an item: only taking it apart is left.
            inputs, outputs, provider = outputs, [e for e in inputs if BY_NAME[e[0]]["kind"] != "fluid"], "Unpackager"
            change = {}
        inputs = change.get("inputs", inputs)
        machine = change.get("machine", _machine(provider))
        recipe = {"e": change.get("eut", 120), "d": 200}
        for side, fluid_side, entries in (("i", "f", inputs + change.get("fluids", [])), ("o", "g", outputs)):
            for name, size in entries:
                kind = fluid_side if BY_NAME[name]["kind"] == "fluid" else side
                recipe.setdefault(kind, []).append([_recipe_key(name), size])
        entry = maps.setdefault(f"gt.recipe.{_slug(machine)}", {"machines": [machine], "recipes": []})
        entry["recipes"].append(recipe)
    # A small viewer chain, plus enough uses of a circuit to exercise paging.
    # These are illustrative fake-game recipes, not GTNH recipe knowledge.
    circuit, wafer = _recipe_key("Any IV Circuit"), _recipe_key("Silicon Wafer")
    foil, wire = _recipe_key("Electrum Foil"), _recipe_key("Fine Platinum Wire")
    crafting = [{"type": "shaped", "w": 3, "h": 3, "mirrored": True,
                 "slots": [{"alts": [[foil, 1]]}, {"alts": [[wire, 1]]}, {"alts": [[foil, 1]]},
                           None, {"alts": [[wafer, 1]]}, None,
                           {"alts": [[_recipe_key("SMD Capacitor"), 1], [_recipe_key("SMD Transistor"), 1]],
                            "od": "componentCircuit"}, {"alts": [[wire, 1]]}, None], "o": [[circuit, 1]]}]
    for _, is_crafting, outputs, inputs, *_ in PATTERNS:
        if is_crafting:
            crafting.append({"type": "shapeless", "slots": [{"alts": [[_recipe_key(n), 1]]} for n, count in inputs for _ in range(count)],
                             "o": [[_recipe_key(n), count] for n, count in outputs]})
    maps["crafting"] = {"machines": [{"name": "Crafting Table", "key": "minecraft:crafting_table:0"}], "recipes": crafting}
    maps["furnace"] = {"machines": [{"name": "Furnace", "key": "minecraft:furnace:0"}], "recipes": [
        {"type": "furnace", "i": [[_recipe_key("Tungstensteel Dust"), 1]], "o": [[_recipe_key("Inconel-625 Ingot"), 1]], "d": 200}]}
    maps["gt.recipe.wafer_cutter"] = {"machines": ["Wafer Cutter"], "recipes": [
        {"i": [[_recipe_key("Inconel-625 Plate"), 1]], "o": [[wafer, 4]], "e": 120, "d": 160}]}
    maps["gt.recipe.viewer_assembler"] = {"machines": ["Assembler"], "recipes": [
        {"i": [[circuit, 1], [_recipe_key("Iron Ingot"), 2]], "o": [[_recipe_key(it["name"]), 1]], "e": 30, "d": 20 + i}
        for i, it in enumerate(CATALOG[:40]) if it["kind"] == "item" and it["name"] not in ("Any IV Circuit", "Ultimate Circuit")]}
    maps["assembly_line"] = {"machines": ["Assembly Line"], "recipes": [
        {"type": "assembly_line", "groups": [{"alts": [[circuit, 4]]}, {"alts": [[wafer, 16]]}],
         "o": [[_recipe_key("Ultimate Circuit"), 1]], "e": 32768, "d": 400,
         "research": {"method": "research_station", "item": [wafer, 1], "eut": 8192,
                      "computation": 1000000, "computation_per_second": 128, "amperage": 4}}]}
    maps["nei.dev"] = {"machines": ["Illustrative NEI Machine"], "recipes": [
        {"type": "nei", "groups": [{"alts": [[foil, 2], [wire, 2]]}], "o": [[_recipe_key("Ultimate Circuit"), 1]],
         "requirements": {"mana": 2000}, "approximate": ["nei_display"]}]}
    def slot(role, index, x, y):
        return {"role": role, "index": index, "x": x, "y": y, "w": 18, "h": 18}
    maps["assembly_line"]["recipes"][0]["preview"] = {
        "family": "assembly_line", "width": 170, "height": 84, "layers": [],
        "slots": [slot("groups", n, 16 + 18 * (n % 4), 8 + 18 * (n // 4)) for n in range(16)]
                 + [slot("f", n, 106, 8 + 18 * n) for n in range(4)]
                 + [slot("o", 0, 142, 8)],
    }
    import math
    maps["nei.infusion.dev"] = {"machines": ["Arcane Infusion"], "recipes": [{
        "type": "nei", "groups": [{"alts": [[key, 1]]} for key in [wafer, foil, wire, foil, wire]],
        "o": [[_recipe_key("Ultimate Circuit"), 1]], "requirements": {
            "instability": 0, "research": "DEV_INFUSION", "essentia": {"ordo": 25}},
        "preview": {"family": "infusion", "width": 166, "height": 130, "layers": [], "item_frames": False,
                    "slots": [slot("o", 0, 74, 4), slot("groups", 0, 74, 62)] + [
                        slot("groups", n + 1, 74 + round(40 * math.cos(math.radians(-90 + 90*n))),
                             62 + round(40 * math.sin(math.radians(-90 + 90*n)))) for n in range(4)]},
        "approximate": ["nei_display"],
    }]}
    maps["nei.dev::thaumcraft.arcane.shaped"] = {"machines": ["Shaped Arcane Crafting"], "recipes": [{
        "type": "nei", "groups": [{"alts": [[foil if n % 2 else wafer, 1]]} for n in range(9)],
        "o": [[_recipe_key("Ultimate Circuit"), 1]],
        "requirements": {"aspects": {"ordo": 10},
                         "prereqs": [{"research": "Dev Arcane Crafting", "isResearched": False, "prevX": 0}],
                         "vis": [{"relx": 75, "rely": 122, "item": {"nbt": '{Aspect:"ordo"}'}}]},
        "preview": {"family": "positioned", "width": 166, "height": 142, "layers": [],
                    "slots": [slot("groups", n, 46+28*(n % 3), 37+27*(n // 3)) for n in range(9)]
                             + [slot("o", 0, 74, 4)]},
        "approximate": ["nei_display"],
    }]}
    maps.update(bee_maps())
    return maps


def bee_maps():
    """NEI Addons' bee breeding (one card per mutation) and Bee Better At
    Bees' tree for the last species, as the exporter captures them."""
    def bee(species, kind):
        return [bee_key(kind), 1, bee_display_id(species, kind)]

    def slot(role, index, x, y):
        return {"role": role, "index": index, "x": x, "y": y, "w": 18, "h": 18}
    breeding = []
    for species, _, parents, chance, conditions in BEES:
        if parents:
            breeding.append({
                "type": "nei", "groups": [{"alts": [bee(parents[0], "Princess")]}, {"alts": [bee(parents[1], "Drone")]}],
                "o": [bee(species, "Queen")], "requirements": {"chance": chance, "requirements": conditions},
                "preview": {"family": "positioned", "width": 166, "height": 80, "layers": [], "graphics": "slots_only",
                            "slots": [slot("groups", 0, 21, 18), slot("groups", 1, 74, 18), slot("o", 0, 128, 18)]},
                "approximate": ["nei_display"]})
    # The tree: the species at the top, each bee's parents on the row
    # below, halving the spacing each row; a foundation block under the
    # bee that needs one.
    parents = {species: p for species, _, p, *_ in BEES}
    target = BEES[-1][0]
    groups, slots, frontier = [], [slot("o", 0, 73, 0)], [(target, 73)]
    groups.append({"alts": [[_recipe_key("Block of Diamond"), 1]]})
    slots.append(slot("groups", 0, 73, 23))
    for row, spread in enumerate((40, 20, 10)):
        below = []
        for species, x in frontier:
            for parent, dx in zip(parents[species] or (), (-spread, spread), strict=False):
                slots.append(slot("groups", len(groups), x + dx, 49 + 50 * row))
                groups.append({"alts": [bee(parent, "Drone")]})
                below.append((parent, x + dx))
        frontier = below
    tree = {"type": "nei", "groups": groups, "o": [bee(target, "Princess")], "approximate": ["nei_display"],
            "preview": {"family": "positioned", "width": 166, "height": 194, "layers": [], "graphics": "slots_only",
                        "slots": slots}}
    # What the last species makes, each product with its chance, as NEI
    # Addons draws it (out of 10,000, as GregTech's chances are).
    produce = {"type": "nei", "groups": [{"alts": [bee(target, "Queen")]}], "approximate": ["nei_display"],
               "o": [[_recipe_key("Glass Dust"), 1, 3000], [_recipe_key("Diamond Dust"), 1, 1250], [_recipe_key("Diamond"), 1, 500]],
               "preview": {"family": "positioned", "width": 166, "height": 80, "layers": [], "graphics": "slots_only",
                           "slots": [slot("groups", 0, 21, 18)] + [slot("o", n, 95 + 18 * n, 7) for n in range(3)]}}
    return {
        # The icon NEI draws on their tabs, as the export records it.
        "nei.net.bdew.neiaddons.forestry.bees.BeeProduceHandler::": {
            "machines": ["Bee Produce"], "recipes": [produce], "icon": bee_key("Queen")},
        "nei.net.bdew.neiaddons.forestry.bees.BeeBreedingHandler::": {
            "machines": ["Bee Breeding"], "recipes": breeding, "icon": bee_key("Queen")},
        "nei.hellfirepvp.beebetteratbees.client.gui.BBABGuiRecipeTreeHandler::": {
            "machines": ["Bee Breeding Tree"], "recipes": [tree], "icon": bee_key("Queen")},
    }


def write_recipes(path, maps):
    """A bundle's recipes.json.gz as tools/icon-export's Recipes.java
    writes it: items and fluids in tables, recipes pointing into them."""
    tables = {"items": {}, "fluids": {}}

    def index(table, key):
        return tables[table].setdefault(key, len(tables[table]))

    def stack(table, alt):
        # (key, size), (key, size, chance out of 10,000) or, for a stack the
        # game shows as one NBT variant (a bee's species), (key, size,
        # display stack id).
        key, size, *rest = alt
        if rest and isinstance(rest[0], str):
            return [index(table, key), size, None, rest]
        return [index(table, key), size, *rest]

    out = {}
    for map_name, entry in maps.items():
        recipes = []
        for r in entry["recipes"]:
            recipe = {k: v for k, v in r.items() if k not in ("i", "o", "f", "g", "slots", "groups", "research")}
            recipe.setdefault("type", "gt")
            if "slots" in r:
                recipe["slots"] = [None if slot is None else {**slot, "alts": [[index("items", key), size] for key, size in slot["alts"]]} for slot in r["slots"]]
            if "groups" in r:
                recipe["groups"] = [{**group, "alts": [stack("fluids" if group.get("kind") == "fluid" else "items", alt)
                                                      for alt in group["alts"]]} for group in r["groups"]]
            if "research" in r:
                key, size = r["research"]["item"]
                recipe["research"] = {**r["research"], "item": [index("items", key), size]}
            for side, table in (("i", "items"), ("o", "items"), ("f", "fluids"), ("g", "fluids")):
                if side in r:
                    recipe[side] = [stack(table, alt) for alt in r[side]]
            recipes.append(recipe)
        out[map_name] = {"machines": [m if isinstance(m, dict) else {"name": m, "key": None} for m in entry["machines"]], "recipes": recipes}
        if entry.get("icon"):
            out[map_name]["icon"] = entry["icon"]
    names = {_recipe_key(it["name"]): it["name"] for it in CATALOG if it["kind"] != "essentia"}
    names.update({bee_key(kind): kind for kind in BEE_KINDS})
    data = {
        "format": 4,
        "display_stacks": bee_display_stacks(), "ui_assets": {},
        "research_info": {"DEV_INFUSION": {"name": "Infusion", "category_name": "Dev research"}},
        "aspects": {"ordo": {"name": "Ordo", "icon": "aspect/ordo.webp"}},
        "coverage": {"handlers": ["gregtech", "crafting", "furnace", "assembly_line", "nei"], "identity": "item_id_damage",
                     "limitations": ["Illustrative dev recipes; NBT and custom matching may differ"]},
        "items": list(tables["items"]), "item_names": [names[k] for k in tables["items"]],
        "fluids": list(tables["fluids"]), "fluid_names": [names[k] for k in tables["fluids"]],
        "maps": out,
    }
    data.update(catalog_order(data["items"], data["item_names"]))
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f)


def catalog_order(keys, names):
    """NEI's order and item groups as CatalogOrder.java exports them: vanilla
    first, then by mod, item and damage; a group for each kind of part
    (ingots, dusts...) that several items are."""
    def sort_key(i):
        mod, internal, damage = keys[i].rsplit(":", 2)
        return mod != "minecraft", mod, internal, int(damage) if damage.isdigit() else -1
    rank = [0] * len(keys)
    for position, i in enumerate(sorted(range(len(keys)), key=sort_key)):
        rank[i] = position
    kinds = [name.split()[-1] if name else None for name in names]
    counts = {kind: kinds.count(kind) for kind in kinds if kind}
    grouped = sorted(kind for kind, n in counts.items() if n > 1)
    return {"item_rank": rank, "item_group": [grouped.index(k) if k in grouped else -1 for k in kinds],
            "item_groups": [{"name": f"{kind}s", "expanded": False} for kind in grouped]}


# ---------------------------------------------------------------- power and history

CAPACITY = 1e10
# A second machine (power_monitor.lua's MACHINES): name, capacity, and
# how far its daily cycle runs behind the main one's.
BATTERIES = ("Batteries", 2e8, 6 * 3600)
# The wireless EU network, read through the LSC: no capacity, and far
# bigger than any machine.
WIRELESS = "Wireless"


# An LSC in wireless mode, with one UEV capacitor: every 5 minutes it's
# set back to its 6P threshold, and in between it fills or drains.
LSC = "LSC"
LSC_CAPACITY = 9.2e18
LSC_CAPACITORS = {"UHV": 0, "UEV": 1, "UIV": 0, "UMV": 0}
LSC_THRESHOLD = 6e15


def lsc_reading(ts):
    """(stored, trend) of the wireless LSC: a sawtooth around its
    threshold, rising by day and draining by night. Its averages are
    EU/t, as a real LSC's are."""
    phase = (ts % 86400) / 86400 * 2 * math.pi
    per_second = 2e12 * math.sin(phase) - 5e11 + random.uniform(-1e11, 1e11)
    stored = max(0.0, LSC_THRESHOLD + per_second * (ts % 300))
    avg_in = 1.1e11 + random.uniform(0, 5e9)
    return stored, {"avg_eu_in_5s": avg_in, "avg_eu_out_5s": max(0.0, avg_in - per_second / 20)}


def wireless_reading(ts):
    """Wireless EU climbing slowly, swinging on a daily cycle."""
    phase = (ts % 86400) / 86400 * 2 * math.pi
    return 4e18 + (ts - 1.7e9) * 1e9 + 3e16 * math.sin(phase)


def power_reading(ts, capacity=CAPACITY):
    """Stored EU drifting on a daily cycle, with noise - the same curve
    for the backfill and the live readings, so they join up."""
    phase = (ts % 86400) / 86400 * 2 * math.pi
    stored = capacity * (0.45 + 0.3 * math.sin(phase) + 0.03 * random.uniform(-1, 1))
    # The trend arrow follows the slope of the cycle. EU/t, as an LSC's
    # averages are; the page shows them per second.
    slope = math.cos(phase) * 4000
    return stored, {"avg_eu_in_5s": 10000 + max(0, slope) + random.uniform(0, 250),
                    "avg_eu_out_5s": 10000 + max(0, -slope) + random.uniform(0, 250)}


def backfill_history(days=7):
    """A week of power readings, network stock and craft history, written
    straight to the stores, so the charts and lists aren't empty on a
    fresh data directory.
    Returns each item's last backfilled stock, for the live scans to
    carry on from."""
    now = time.time()
    for i in range(days * 24 * 6):  # every 10 minutes
        ts = now - (days * 24 * 6 - i) * 600
        stored, trend = power_reading(ts)
        store.power.add_reading(ts, stored, CAPACITY, trend)
        name, capacity, lag = BATTERIES
        stored, trend = power_reading(ts - lag, capacity)
        store.power.add_reading(ts, stored, capacity, trend, name)
        store.power.add_reading(ts, wireless_reading(ts), 0, {}, WIRELESS)
        stored, trend = lsc_reading(ts)
        store.power.add_reading(ts, stored, LSC_CAPACITY, trend, LSC, threshold=LSC_THRESHOLD, wireless_mode=True)

    rows, stock = [], {}
    for it in CATALOG:
        key = store.items.item_key(it["mod"], it["internal"], it["damage"], it["kind"])
        fixed = ALWAYS_STOCKED.get(it["name"])
        size = random.randint(0, 5000) if fixed is None else fixed
        for step in range(days * 12):  # every 2 hours
            if fixed is None:
                size = max(0, size + random.randint(-400, 450))
            rows.append((key, it["name"], size, now - (days * 12 - step) * 7200))
        stock[it["name"]] = size
    store.items.add_rows(rows)

    # Past job ends for the Crafts tab's history, oldest first like the
    # real log. A few were already running when the server first saw
    # them (no start), a few stopped early, and some small ones were a
    # keep-in-stock target's.
    events, ended = [], now - days * 86400
    while True:
        ended += random.uniform(600, 5400)
        if ended >= now - 60:
            break
        name, cpu = random.choice([(BIG_JOB[0], "M00")] + [(n, f"a0{i}") for i, n in enumerate(SMALL_JOBS[:4])])
        it = BY_NAME[name]
        took = random.uniform(1800, 7200) if cpu == "M00" else random.uniform(5, 900)
        stopped = random.random() < 0.08
        # Seen from its start, or first seen part way through.
        seen_at = ended - took if random.random() >= 0.05 else ended - took / 2
        events.append((cpu, name, icons.resolve_icon(it["mod"], it["internal"], it["damage"], name),
                       "incomplete" if stopped else "finished", random.randint(5, 90) if stopped else 100,
                       ended, seen_at if seen_at == ended - took else None,
                       it["mod"], it["internal"], it["damage"],
                       int(cpu != "M00" and random.random() < 0.25), seen_at))
    with db.transaction(db.app_db) as conn:
        conn.executemany(
            "INSERT INTO craft_events (cpu_name, item_label, item_icon, status, progress_at_end, occurred_at, "
            "started_at, item_mod, item_internal, item_damage, auto, first_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", events
        )

    # Time jobs were held up at some of the dev patterns' machines, for
    # the statistics' Held up at list: the lathe most, as T00 and T01
    # share it.
    held = {"Lathe T3:HV": 0.5, "Bending Machine T4:EV": 0.2, "Fluid Solidifier {Plate}": 0.1,
            "Circuit Assembler": 0.05}
    providers = {p["provider"]["name"]: p["provider"] for p in pattern_scan()}
    for hour in range(days * 24):
        at = (now // 3600 - days * 24 + hour) * 3600
        rows = []
        for name, chance in held.items():
            if random.random() < chance:
                where = providers[name]
                minutes = random.uniform(1, 25)
                rows.append((bottlenecks.key_text(patterns.provider_key(where)), where, {
                    "blocked": minutes * 60 * random.random() * (name != "Lathe T3:HV"),
                    "busy": minutes * 60 * random.random() * (name == "Lathe T3:HV"),
                    "stalled": minutes * 60 * random.random() * 0.5,
                    "critical": minutes * 60 * random.random() * 0.6, "jobs": random.randint(1, 3)}))
        if rows:
            store.bottlenecks.add(at, rows)
    return stock


# ---------------------------------------------------------------- fake game

class Cpu:
    def __init__(self, name, storage, coprocessors, monitor=True):
        # name is what AE2 calls it: "" for a CPU nobody named, or one
        # another CPU shares too, which FakeGame.name_cpus() makes a name
        # from its size for, as craft_monitor.lua does, so the page shows
        # them with their (?).
        self.ae2_name, self.storage, self.coprocessors = name, storage, coprocessors
        self.name, self.auto_named = name, False
        # Without a Crafting Monitor, finalOutput() gives nothing. With
        # one, a job's first report can still come without it.
        self.monitor = monitor
        self.job = None           # {"output": name, "items": {name: [scheduled, crafting, stored]}}
        self.idle_ticks = 0
        # craft_monitor.lua's watch-tick look at a job that just ended,
        # sent once with the next idle report as last_busy.
        self.last_look = None
        # The browser request running here, watched for its outcome the
        # way craft_monitor.lua watches its CraftingStatus.
        self.request_id = None
        # What finishing the job adds to the network: (name, amount),
        # for a request's job.
        self.delivers = None

    def start(self, output, ingredients):
        self.job = {"output": output, "items": {
            name: [amount, 0, random.choice((0, 0, 0, random.randint(1, 20)))]
            for name, amount in ingredients
        }}

    def start_chain(self, output, stages, raw, stuck=None):
        """A job that follows the dev patterns, for the live crafting
        tree: `stages` [[(name, amount)]], crafted in turn (a step only
        starts once the stage before is done); `raw` [(name, amount)] in
        the CPU's storage from the start, used up as the first stage
        goes; `stuck` (name, ticks) holds that step still a while,
        crafting but not moving, once some of it has come back."""
        items = {name: [0, 0, amount] for name, amount in raw}
        for stage in stages:
            for name, amount in stage:
                items[name] = [amount, 0, 0]
        self.job = {"output": output, "items": items, "stages": [[n for n, _ in st] for st in stages],
                    "raw": [n for n, _ in raw], "stuck": list(stuck) if stuck else None}

    def _tick_chain(self):
        items = self.job["items"]
        stage = next((st for st in self.job["stages"] if any(items[n][0] or items[n][1] for n in st)), None)
        if stage is None:
            return
        for name in stage:
            counts = items[name]
            stuck = self.job["stuck"]
            if stuck and stuck[0] == name and counts[1] and counts[2]:
                stuck[1] -= 1
                if stuck[1] > 0:
                    continue
                self.job["stuck"] = None
            if counts[1]:
                # Given back to the CPU, as a machine's output is: so a
                # later stage's inputs show up in its storage.
                back = min(counts[1], random.randint(1, max(1, counts[1] // 2 + 1)))
                counts[1] -= back
                counts[2] += back
            if counts[0] and random.random() < 0.6:
                moved = random.randint(1, max(1, counts[0] // 3 + 1))
                counts[0] -= moved
                counts[1] += moved
        if stage is self.job["stages"][0]:
            for name in self.job["raw"]:
                items[name][2] = max(0, items[name][2] - random.randint(0, 6))

    def tick(self):
        """Moves some scheduled items to crafting and finishes some
        crafting ones. Returns True when the job just finished."""
        if not self.job:
            self.idle_ticks += 1
            return False
        before = self._lists()
        if "stages" in self.job:
            self._tick_chain()
        for counts in ([] if "stages" in self.job else self.job["items"].values()):
            if counts[1] and random.random() < 0.35:
                counts[1] = max(0, counts[1] - random.randint(1, max(1, counts[1] // 2 + 1)))
            elif counts[0] and random.random() < 0.3:
                moved = random.randint(1, max(1, counts[0] // 3 + 1))
                counts[0] -= moved
                counts[1] += moved
        if all(s == 0 and c == 0 for s, c, _ in self.job["items"].values()):
            self.stop(before)
            return True
        return False

    def stop(self, look=None):
        """Ends the job, as finishing or a cancel does. `look` is what the
        last watch tick saw of it - by default, the job as it is now."""
        look = look or self._lists()
        self.last_look = {"pending": look["pending"], "active": look["active"],
                          "age": round(random.uniform(0.1, 1), 2)}
        self.job = None
        self.idle_ticks = 0

    def _lists(self):
        active, pending, stored = [], [], []
        for name, (scheduled, crafting, kept) in self.job["items"].items():
            if crafting:
                active.append(stack(name, crafting))
            if scheduled:
                pending.append(stack(name, scheduled))
            if kept:
                stored.append(stack(name, kept))
        return {"active": active, "pending": pending, "stored": stored}

    def report(self):
        named = {"name": self.name, **({"auto_named": True} if self.auto_named else {})}
        if not self.job:
            idle = {**named, "busy": False, "storage": self.storage,
                    "coprocessors": self.coprocessors}
            if self.last_look:
                idle["last_busy"], self.last_look = self.last_look, None
            return idle
        report = {**named, "busy": True, "storage": self.storage,
                  "coprocessors": self.coprocessors, **self._lists()}
        reports = self.job.get("reports", 0)
        self.job["reports"] = reports + 1
        if self.monitor and reports:
            out = BY_NAME.get(self.job["output"], {})
            report.update(final_output=self.job["output"], final_output_mod=out.get("mod"),
                          final_output_internal=out.get("internal"), final_output_damage=out.get("damage"))
        return report


def kit_blocks(inputs):
    """A kit request's blocks ({name: "mod:internal", damage, size}) as
    catalog items [{name, size}], leaving out any the catalog lacks."""
    by_id = {(f"{it['mod']}:{it['internal']}", it["damage"]): it["name"] for it in CATALOG if it["kind"] == "item"}
    return [{"name": by_id[(i["name"], i["damage"])], "size": i["size"]}
            for i in inputs if (i["name"], i["damage"]) in by_id]


def random_ingredients(count, exclude):
    names = [it["name"] for it in CATALOG if it["name"] != exclude and it["kind"] != "essentia"]
    return [(name, random.randint(1, 64) * random.choice((1, 1, 4, 16)))
            for name in random.sample(names, count)]


# The big job restarts on M00 forever, so there's always a long
# ingredient list to look at.
BIG_JOB = ("Dangote Distillus", 30)
# The job that restarts on T00 forever: Inconel-625 Gears over the dev
# PATTERNS chain, for the live crafting tree - plates and bolts first,
# then the gears, from ingots in the CPU's storage. Its bolts are held a
# while: busy while T01's bolts (BOLT_JOB) come off the same lathe,
# stalled once those are done, and the gears blocked meanwhile, their
# plates and bolts ready but not sent.
CHAIN_JOB = ("Inconel-625 Gear", [[("Inconel-625 Plate", 64), ("Steel Bolt", 16)], [("Inconel-625 Gear", 16)]],
             [("Inconel-625 Ingot", 64), ("Iron Ingot", 4)], ("Steel Bolt", 40))
# Restarts on T01 forever: bolts on the Lathe T3:HV, which T00 needs too.
BOLT_JOB = ("Steel Bolt", [[("Steel Bolt", 96)]], [("Iron Ingot", 24)])
# Requests the fake game turns down, and why - so a failed keep-in-stock
# target has something to show.
FAILS = {"Inconel-625 Plate": "request failed (missing resources?)"}
SMALL_JOBS = ["Radon Plasma", "Bacterial Sludge", "Glass Dust", "Lapotron Crystal",
              "Titanium Gear", "Ultimate Circuit",
              "Exquisite Cerium-doped Lutetium Aluminium Garnet (Ce:LuAG)",
              "\u00a7a16k\u00a7r ME Storage Component"]


class FakeComputer:
    """A computer running gcm_agent.lua: heartbeats, and carries out the
    Scripts dialog's commands a couple of ticks later."""

    def __init__(self, address, version, services, total_memory):
        self.address = address
        self.version = version
        self.services = services
        self.total_memory = total_memory
        self.booted_at = time.time() - random.randint(600, 90000)
        self.command = None
        self.command_ticks = 0

    def tick(self, game):
        if self.command:
            self.command_ticks += 1
            if self.command_ticks < 3:
                return
            cmd, self.command = self.command, None
            if cmd["action"] == "update":
                before = self.version
                self.version = "main" if cmd["ref"] == "main" else "v9.9.9"
                message = f"Updated {before} -> {self.version}, rebooting."
            else:
                message = "Rebooting."
            game.call("POST", f"/api/agent/commands/{cmd['id']}/result",
                      {"ok": True, "message": message, "version": self.version, "rebooting": True})
            self.booted_at = time.time()
        reply = game.call("POST", "/api/agent/heartbeat", {
            "address": self.address, "version": self.version,
            "components": [name.split("_")[0] for name in self.services],
            # network_browser falls over now and then, and gcm_agent restarts it.
            # power_monitor ran out of memory a while ago and was restarted.
            "services": [{"name": name, "running": name != "network_browser" or random.random() < 0.9,
                          "restarts": int(name in ("network_browser", "power_monitor")),
                          **({"last_error": {"message": "not enough memory",
                                             "ago": time.time() - self.booted_at - 300}}
                             if name == "power_monitor" else {})}
                         for name in self.services],
            "free_memory": int(self.total_memory * random.uniform(0.2, 0.6)),
            "total_memory": self.total_memory, "uptime": time.time() - self.booted_at,
            "poll_seconds": TICK_SECONDS,
        })
        if reply.get("command"):
            self.command, self.command_ticks = reply["command"], 0


class FakeGame:
    def __init__(self, app, stock=None, terminal=True):
        self.client = app.test_client()
        # Without an ME Interface Terminal no patterns are read, and the
        # network scan says so.
        self.terminal = terminal
        self.cpus = [Cpu("M00", 131072, 256), Cpu("a00", 16384, 16), Cpu("a01", 16384, 16),
                     Cpu("a02", 16384, 16, monitor=False), Cpu("a01", 16384, 16), Cpu("B01", 65536, 64),
                     Cpu("T00", 65536, 64), Cpu("T01", 65536, 64), Cpu("", 4096, 4), Cpu("", 4096, 1)]
        self.name_cpus()
        self.cpus[0].start(BIG_JOB[0], random_ingredients(BIG_JOB[1], BIG_JOB[0]))
        self.cpus[-2].start_chain(*CHAIN_JOB)
        self.cpus[-1].start_chain(*BOLT_JOB)
        for cpu in self.cpus[1:4]:
            out = random.choice(SMALL_JOBS)
            cpu.start(out, random_ingredients(random.randint(1, 6), out))
        self.stock = stock or {it["name"]: random.randint(0, 5000) for it in CATALOG}
        self.stock.update(dict.fromkeys(ALWAYS_EMPTY, 0))
        self.stock.update(ALWAYS_STOCKED)
        self.computers = [
            FakeComputer("1f3a9c20-5b1e-4d2a-9e77-0c4b8f2d6a11", "v1.0.0",
                         ["craft_monitor", "network_browser"], 4 * 1024 * 1024),
            FakeComputer("77b0e1d4-2c9a-4f6b-8d03-5e1a7c9b2f40", "v1.1.0", ["power_monitor"], 1024 * 1024),
        ]
        self.ticks = 0

    def call(self, method, path, body=None):
        response = self.client.open(path, method=method, json=body, headers={"X-API-Key": API_KEY})
        return response.get_json(silent=True) or {}

    def tick(self):
        for cpu in self.cpus:
            if cpu.tick():
                self.report_outcome(cpu, "finished")
                if cpu.delivers:
                    name, amount = cpu.delivers
                    self.stock[name] = self.stock.get(name, 0) + amount
                    cpu.delivers = None
            if cpu.job is None and cpu.name == "M00" and cpu.idle_ticks >= 5:
                cpu.start(BIG_JOB[0], random_ingredients(BIG_JOB[1], BIG_JOB[0]))
            elif cpu.job is None and cpu.name == "T00" and cpu.idle_ticks >= 5:
                cpu.start_chain(*CHAIN_JOB)
            elif cpu.job is None and cpu.name == "T01" and cpu.idle_ticks >= 20:
                cpu.start_chain(*BOLT_JOB)
            elif cpu.job is None and cpu.name not in ("B01", "T00", "T01") and cpu.idle_ticks >= 8 and random.random() < 0.1:
                out = random.choice(SMALL_JOBS)
                cpu.start(out, random_ingredients(random.randint(1, 6), out))
        self.name_cpus()
        self.answer_requests()
        # Two kit slots: multiblock kits can be requested as one job.
        self.call("POST", "/api/crafts", {"source": "me_controller", "poll_seconds": TICK_SECONDS,
                                          "kit_slots": 2, "jobs": [cpu.report() for cpu in self.cpus]})
        stored, trend = power_reading(time.time())
        self.call("POST", "/api/power", {"stored": stored, "capacity": CAPACITY, "poll_seconds": TICK_SECONDS, **trend})
        name, capacity, lag = BATTERIES
        stored, trend = power_reading(time.time() - lag, capacity)
        self.call("POST", "/api/power", {"stored": stored, "capacity": capacity, "machine": name, **trend})
        self.call("POST", "/api/power", {"stored": wireless_reading(time.time()), "capacity": 0, "machine": WIRELESS})
        stored, trend = lsc_reading(time.time())
        self.call("POST", "/api/power", {"stored": stored, "capacity": LSC_CAPACITY, "machine": LSC,
                                         "wireless_mode": True, "capacitors": LSC_CAPACITORS, **trend})
        if self.terminal and self.ticks % PATTERN_SCAN_EVERY_TICKS == 0:
            self.scan_patterns()
        if self.ticks % SCAN_EVERY_TICKS == 0:
            self.scan()
        elif self.ticks % WATCH_EVERY_TICKS == 0:
            self.check_watched()
        for computer in self.computers:
            computer.tick(self)
        self.ticks += 1

    def name_cpus(self):
        """Names the CPUs without a name of their own after their size
        ("4k #1", "a01 16k #2"), as craft_monitor.lua's get_cpus() does
        before each look."""
        named = name_cpus([{"name": c.ae2_name, "storage": c.storage, "coprocessors": c.coprocessors}
                           for c in self.cpus])
        for cpu, job in zip(self.cpus, named, strict=True):
            cpu.name, cpu.auto_named = job["name"], job.get("auto_named") is True

    def answer_requests(self):
        for req in self.call("GET", "/api/craft/requests/pending").get("requests", []):
            cpu = next((c for c in self.cpus if c.job is None), None)
            if cpu is None:
                self.call("POST", f"/api/craft/requests/{req['id']}/result",
                          {"status": "failed", "reason": "no free crafting CPU"})
                continue
            if req["label"] in FAILS:
                self.call("POST", f"/api/craft/requests/{req['id']}/result",
                          {"status": "failed", "reason": FAILS[req["label"]]})
                continue
            named = {}
            if req.get("kit_names"):
                # As craft_monitor.lua does: the first name no running kit has.
                running = {c.job["output"] for c in self.cpus if c.job}
                req["label"] = next((n["label"] for n in req["kit_names"] if n["label"] not in running),
                                    req["label"])
                named = {"label": req["label"]}
            label = req["label"] if req["label"] in BY_NAME else "Iron Ingot"
            if req.get("kit_inputs"):
                # A multiblock kit: its blocks, all on the one CPU.
                cpu.start(label, [(it["name"], it["size"]) for it in kit_blocks(req["kit_inputs"])])
            else:
                cpu.start(label, random_ingredients(random.randint(2, 8), label))
            cpu.job["output"] = req["label"]
            cpu.request_id = req["id"]
            cpu.delivers = (req["label"], int(req["amount"])) if req["label"] in BY_NAME else None
            self.call("POST", f"/api/craft/requests/{req['id']}/result",
                      {"status": "accepted", "cpu_name": cpu.name, "watching": True, **named})
        for req in self.call("GET", "/api/craft/cancel/pending").get("requests", []):
            cpu = next((c for c in self.cpus if c.name == req["cpu_name"]), None)
            ok = bool(cpu and cpu.job)
            if ok:
                cpu.stop()
                self.report_outcome(cpu, "cancelled")
            self.call("POST", f"/api/craft/cancel/{req['id']}/result",
                      {"success": ok, "reason": None if ok else "CPU is not crafting"})

    def report_outcome(self, cpu, outcome):
        if cpu.request_id is not None:
            self.call("POST", f"/api/craft/requests/{cpu.request_id}/outcome",
                      {"outcome": outcome, "cpu_name": cpu.name})
            cpu.request_id = None

    def _scanned(self, it):
        """`it` as a scan reports it, or None when AE2 wouldn't list it."""
        size = self.stock[it["name"]]
        craftable = (it["name"] in SMALL_JOBS or it["name"] in PATTERN_OUTPUTS or it["name"] in KIT_CRAFTABLE
                     or it["shape"] in ("ingot", "plate", "gear"))
        if not size and not craftable:
            return None
        entry = {k: it[k] for k in ("name", "mod", "internal", "damage", "kind")}
        return {**entry, "size": size, "isCraftable": craftable}

    def scan(self):
        items = []
        for it in CATALOG:
            if it["name"] == NEWER_ITEM:
                continue
            if it["name"] not in ALWAYS_EMPTY and it["name"] not in ALWAYS_STOCKED:
                self.stock[it["name"]] = max(0, self.stock[it["name"]] + random.randint(-50, 60))
            entry = self._scanned(it)
            if entry:
                items.append(entry)
        for species, kind, size in NETWORK_BEES:
            items.append({"name": f"{species} {kind}", "mod": "Forestry", "internal": BEE_KINDS[kind], "damage": 0,
                          "kind": "item", "size": size, "isCraftable": False, "hasTag": True, "tag": bee_tag(species)})
        token = self.call("POST", "/api/network/scan/start",
                          {"scan_interval_seconds": SCAN_EVERY_TICKS * TICK_SECONDS}).get("scan_token")
        self.call("POST", "/api/network/scan/batch", {"scan_token": token, "items": items})
        self.call("POST", "/api/network/scan/finish",
                  {"scan_token": token, "chunks_sent": 1, "total_errors": 0, "interface_terminal": self.terminal})

    def scan_patterns(self):
        token = self.call("POST", "/api/network/patterns/start").get("scan_token")
        self.call("POST", "/api/network/patterns/batch", {"scan_token": token, "patterns": pattern_scan()})
        self.call("POST", "/api/network/patterns/finish",
                  {"scan_token": token, "chunks_sent": 1, "total_errors": 0})

    def check_watched(self):
        """network_browser.lua's check of the stock rules' items between
        full scans. Those items get used up a little faster here, so the
        checks have something to show."""
        watch = self.call("GET", "/api/network/watch")
        wanted = {(w["name"], w["damage"]) for w in watch.get("items", [])} | {
            (f, None) for f in watch.get("fluids", []) + watch.get("essentia", [])}
        found = []
        for it in CATALOG:
            name = f"{it['mod']}:{it['internal']}" if it["mod"] else it["internal"]
            if (name, it["damage"]) in wanted:
                self.stock[it["name"]] = max(0, self.stock[it["name"]] - random.randint(0, 120))
                entry = self._scanned(it)
                if entry:
                    found.append(entry)
        if wanted:
            self.call("POST", "/api/network/levels", {"checked": watch, "items": found, "elapsed": 0.2})

    def run_forever(self):
        while True:
            try:
                self.tick()
            except Exception as e:  # keep feeding the page whatever one tick did
                print(f"[dev] fake game tick failed: {e!r}", flush=True)
            time.sleep(TICK_SECONDS)


# ---------------------------------------------------------------- main

def seed_stock_rules(stock):
    """A few stock rules for the dev admin, some already low: targets
    the fake game fills (Iron Ingot), turns down (Inconel-625 Plate) or
    has plenty of (Titanium Gear), and three alerts, one on essentia."""
    user_id = store.users.id_for_display_name(ADMIN_NAME)

    def item(name):
        it = BY_NAME[name]
        return {"label": name, "mod": it["mod"], "internal": it["internal"], "damage": it["damage"],
                "kind": it["kind"], "variant": None}

    store.stock.set_target(user_id, item("Iron Ingot"), stock["Iron Ingot"] + 300, stock["Iron Ingot"] + 1500, True)
    store.stock.set_target(user_id, item("Inconel-625 Plate"), stock["Inconel-625 Plate"] + 200,
                           stock["Inconel-625 Plate"] + 1000, True)
    store.stock.set_target(user_id, item("Titanium Gear"), max(1, stock["Titanium Gear"] // 4),
                           max(2, stock["Titanium Gear"] // 2), True)
    store.stock.set_alert(user_id, item("Glass Dust"), stock["Glass Dust"] + 200)
    store.stock.set_alert(user_id, item("Osmium Rod"), max(1, stock["Osmium Rod"] // 3))
    store.stock.set_alert(user_id, item("Ordo"), stock["Ordo"] + 100)


def admin_token():
    """A fresh access token for the dev admin, creating it if needed."""
    user_id = store.users.id_for_display_name(ADMIN_NAME)
    if user_id:
        return store.users.replace_credentials(user_id)
    return store.users.create(ADMIN_NAME, "admin")[1]


def run(args, data_dir):
    fresh = not os.path.exists(os.path.join(data_dir, "app.db"))
    if not args.no_icons:
        install_icons(data_dir, prerelease=args.prerelease)
    else:
        shutil.rmtree(os.path.join(data_dir, "gamedata"), ignore_errors=True)

    app = create_app(data_dir=data_dir, api_key=API_KEY)
    stock = backfill_history() if fresh else None
    token = admin_token()
    if fresh:
        seed_stock_rules(stock)

    game = FakeGame(app, stock, terminal=not args.no_terminal)
    game.tick()  # data in place before the first page load
    threading.Thread(target=game.run_forever, daemon=True).start()

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}/"
    print(f"\n  GTNH Monitor dev server: {url}\n"
          f"  Admin access token:      {token}\n"
          f"  MCP endpoint:            {url}mcp (Authorization: Bearer <the token>)\n"
          f"  Data directory:          {data_dir}{'' if args.data_dir else ' (deleted on exit)'}\n",
          flush=True)
    if args.open:
        webbrowser.open(url)

    from waitress import serve
    serve(app, host=args.host, port=args.port, threads=8)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=8421)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--data-dir", help="keep data here between runs (default: a temp dir)")
    parser.add_argument("--no-icons", action="store_true", help="run without game data")
    parser.add_argument("--open", action="store_true", help="open the page in a browser")
    parser.add_argument("--no-terminal", action="store_true",
                        help="no ME Interface Terminal: no patterns, so no crafting plan or job tree")
    parser.add_argument("--prerelease", action="store_true",
                        help="game data for a pre-release whose final release is out, not an older version")
    args = parser.parse_args()

    # A plain kill skips finally blocks - exit normally instead, so the
    # temp data directory still gets cleaned up.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    data_dir = args.data_dir or tempfile.mkdtemp(prefix="gcm-dev-")
    try:
        run(args, data_dir)
    finally:
        # Also after a failed startup, which would otherwise leave one
        # directory behind per attempt.
        if not args.data_dir:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
