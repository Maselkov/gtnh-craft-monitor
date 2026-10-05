"""Local dev server with a fake game feeding it.

    python dev.py               (from server/, with requirements.txt installed)

Starts the real app on a throwaway data directory with:
- a small generated game data bundle, so every fake item has an icon;
- an admin user, whose access token is printed at startup;
- a week of backfilled power and item history, so the charts have lines;
- a fake game in a background thread that talks to the real API endpoints
  the way the oc/ scripts do: crafting CPUs whose jobs progress, finish
  and restart, network scans, power readings, and craft/cancel requests
  from the page accepted (or refused when every CPU is busy).

Options: --port, --host (0.0.0.0 to try it from a phone), --data-dir to
keep the data between runs, --no-icons, --open to open a browser.
Nothing here touches server/data/.
"""

import argparse
import hashlib
import json
import math
import os
import random
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
import zlib

# A deployment's settings (a sourced .env, say) mustn't leak in: a real
# bootstrap token, data directory or GTNH version would override the dev
# ones, or refuse to start.
for _name in ("API_KEY", "DATA_DIR", "GCM_BOOTSTRAP_ADMIN_TOKEN", "GCM_BOOTSTRAP_ADMIN_NAME",
              "GTNH_VERSION", "GTNH_TEXTURES", "GAMEDATA_API_URL", "GAMEDATA_REPO",
              "TRUSTED_PROXIES", "VAPID_SUBJECT", "STALE_AFTER_SECONDS", "SESSION_LIFETIME_SECONDS",
              "AUTOCRAFT_KEEP_IDLE_CPUS", "AUTOCRAFT_RETRY_SECONDS"):
    os.environ.pop(_name, None)
# Read by gcm.config at import: session cookies over plain http.
os.environ["SESSION_COOKIE_SECURE"] = "0"

from gcm import create_app, db, icons, store  # noqa: E402

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
# fluids have no mod, which is how the game reports them.
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
]


def _slug(name):
    return name.lower().replace(" ", "_").replace("-", "_")


def _item(index, name, shape, colour):
    if shape == "fluid":
        return {"name": name, "mod": None, "internal": _slug(name), "damage": None,
                "kind": "fluid", "shape": shape, "colour": colour}
    return {"name": name, "mod": "gregtech", "internal": "gt.metaitem.01", "damage": 30000 + index,
            "kind": "item", "shape": shape, "colour": colour}


CATALOG = [_item(i, *entry) for i, entry in enumerate(ITEMS)]
BY_NAME = {it["name"]: it for it in CATALOG}


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
# batches (bolts) and an input nothing makes and the network never has.
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
]
PATTERN_OUTPUTS = {name for _, _, outputs, _ in PATTERNS for name, _ in outputs}
# Kept at none, so a plan for the Ultimate Circuit comes up short.
ALWAYS_EMPTY = {"Draconium Ingot"}


def pattern_entry(name, size):
    """One side of a pattern, as network_browser.lua sends it."""
    it = BY_NAME[name]
    return {"kind": it["kind"], "mod": it["mod"], "internal": it["internal"], "damage": it["damage"],
            "name": name, "size": size}


def pattern_scan():
    return [
        {"provider": {"name": provider, "x": 10 * slot, "y": 64, "z": 0, "dim": 0}, "slot": slot,
         "crafting": crafting,
         "inputs": [pattern_entry(*i) for i in inputs], "outputs": [pattern_entry(*o) for o in outputs]}
        for slot, (provider, crafting, outputs, inputs) in enumerate(PATTERNS)
    ]


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
    return tuple(max(0, min(255, int(c * shade))) for c in colour) + (255,)


def _png(shape, colour):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(
        b"\x00" + b"".join(bytes(_pixel(shape, colour, x, y)) for x in range(16)) for y in range(16)
    )
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def install_icons(data_dir):
    """Writes a game data bundle for CATALOG and selects it, the same
    layout gcm/gamedata.py installs from a release."""
    bundle = os.path.join(data_dir, "gamedata", "dev")
    os.makedirs(bundle, exist_ok=True)
    by_key, fluids_by_key = {}, {}
    with zipfile.ZipFile(os.path.join(bundle, "images.zip"), "w") as zf:
        for it in CATALOG:
            if it["kind"] == "fluid":
                path = f"fluid/{it['internal']}.png"
                fluids_by_key[it["internal"]] = path
            else:
                path = f"item/{it['mod']}/{it['internal']}~{it['damage']}.png"
                by_key[f"{it['mod']}:{it['internal']}:{it['damage']}"] = path
            zf.writestr(path, _png(it["shape"], it["colour"]))
    with open(os.path.join(bundle, "icons_lookup.json"), "w", encoding="utf-8") as f:
        json.dump({"by_key": by_key, "fluids_by_key": fluids_by_key, "by_label": {}}, f)
    with open(os.path.join(bundle, "item_catalog.txt"), "w", encoding="utf-8") as f:
        f.write("".join(f"{it['mod']}:{it['internal']}\n" for it in CATALOG if it["mod"]))
    # Its hash is the bundle's build id, which busts icon caches: new
    # icons after a change here get new URLs.
    digest = hashlib.sha256(open(os.path.join(bundle, "images.zip"), "rb").read()).hexdigest()
    with open(os.path.join(bundle, "data.json"), "w", encoding="utf-8") as f:
        json.dump({"format": 1, "files": {}, "generated_at": "dev", "images": digest}, f)
    with open(os.path.join(data_dir, "gamedata", "selected.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "dev", "textures": "default", "previous": None}, f)


# ---------------------------------------------------------------- power and history

CAPACITY = 1e10


def power_reading(ts):
    """Stored EU drifting on a daily cycle, with noise - the same curve
    for the backfill and the live readings, so they join up."""
    phase = (ts % 86400) / 86400 * 2 * math.pi
    stored = CAPACITY * (0.45 + 0.3 * math.sin(phase) + 0.03 * random.uniform(-1, 1))
    # The trend arrow follows the slope of the cycle.
    slope = math.cos(phase) * 80000
    return stored, {"avg_eu_in_5s": 200000 + max(0, slope) + random.uniform(0, 5000),
                    "avg_eu_out_5s": 200000 + max(0, -slope) + random.uniform(0, 5000)}


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

    rows, stock = [], {}
    for it in CATALOG:
        key = store.items.item_key(it["mod"], it["internal"], it["damage"], it["kind"])
        size = random.randint(0, 5000)
        for step in range(days * 12):  # every 2 hours
            size = max(0, size + random.randint(-400, 450))
            rows.append((key, it["name"], size, now - (days * 12 - step) * 7200))
        stock[it["name"]] = size
    with db.transaction(db.item_history_db) as conn:
        conn.executemany(
            "INSERT INTO item_history (item_key, label, size, recorded_at) VALUES (?, ?, ?, ?)", rows
        )

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
        events.append((cpu, name, icons.resolve_icon(it["mod"], it["internal"], it["damage"], name),
                       "incomplete" if stopped else "finished", random.randint(5, 90) if stopped else 100,
                       ended, None if random.random() < 0.05 else ended - took,
                       it["mod"], it["internal"], it["damage"],
                       int(cpu != "M00" and random.random() < 0.25)))
    with db.transaction(db.app_db) as conn:
        conn.executemany(
            "INSERT INTO craft_events (cpu_name, item_label, item_icon, status, progress_at_end, occurred_at, "
            "started_at, item_mod, item_internal, item_damage, auto) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", events
        )
    return stock


# ---------------------------------------------------------------- fake game

class Cpu:
    def __init__(self, name, storage, coprocessors):
        self.name, self.storage, self.coprocessors = name, storage, coprocessors
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

    def tick(self):
        """Moves some scheduled items to crafting and finishes some
        crafting ones. Returns True when the job just finished."""
        if not self.job:
            self.idle_ticks += 1
            return False
        before = self._lists()
        for counts in self.job["items"].values():
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
        if not self.job:
            idle = {"name": self.name, "busy": False, "storage": self.storage,
                    "coprocessors": self.coprocessors}
            if self.last_look:
                idle["last_busy"], self.last_look = self.last_look, None
            return idle
        out = BY_NAME.get(self.job["output"], {})
        return {
            "name": self.name, "busy": True, "storage": self.storage,
            "coprocessors": self.coprocessors, "final_output": self.job["output"],
            "final_output_mod": out.get("mod"), "final_output_internal": out.get("internal"),
            "final_output_damage": out.get("damage"),
            **self._lists(),
        }


def random_ingredients(count, exclude):
    names = [it["name"] for it in CATALOG if it["name"] != exclude]
    return [(name, random.randint(1, 64) * random.choice((1, 1, 4, 16)))
            for name in random.sample(names, count)]


# The big job restarts on M00 forever, so there's always a long
# ingredient list to look at.
BIG_JOB = ("Dangote Distillus", 30)
# Requests the fake game turns down, and why - so a failed keep-in-stock
# target has something to show.
FAILS = {"Inconel-625 Plate": "request failed (missing resources?)"}
SMALL_JOBS = ["Radon Plasma", "Bacterial Sludge", "Glass Dust", "Lapotron Crystal",
              "Titanium Gear", "Ultimate Circuit",
              "Exquisite Cerium-doped Lutetium Aluminium Garnet (Ce:LuAG)"]


class FakeGame:
    def __init__(self, app, stock=None):
        self.client = app.test_client()
        self.cpus = [Cpu("M00", 131072, 256), Cpu("a00", 16384, 16), Cpu("a01", 16384, 16),
                     Cpu("a02", 16384, 16), Cpu("a03", 16384, 16), Cpu("B01", 65536, 64)]
        self.cpus[0].start(BIG_JOB[0], random_ingredients(BIG_JOB[1], BIG_JOB[0]))
        for cpu in self.cpus[1:4]:
            out = random.choice(SMALL_JOBS)
            cpu.start(out, random_ingredients(random.randint(1, 6), out))
        self.stock = stock or {it["name"]: random.randint(0, 5000) for it in CATALOG}
        self.stock.update(dict.fromkeys(ALWAYS_EMPTY, 0))
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
            elif cpu.job is None and cpu.name != "B01" and cpu.idle_ticks >= 8 and random.random() < 0.1:
                out = random.choice(SMALL_JOBS)
                cpu.start(out, random_ingredients(random.randint(1, 6), out))
        self.answer_requests()
        self.call("POST", "/api/crafts", {"source": "me_controller",
                                          "jobs": [cpu.report() for cpu in self.cpus]})
        stored, trend = power_reading(time.time())
        self.call("POST", "/api/power", {"stored": stored, "capacity": CAPACITY, **trend})
        if self.ticks % PATTERN_SCAN_EVERY_TICKS == 0:
            self.scan_patterns()
        if self.ticks % SCAN_EVERY_TICKS == 0:
            self.scan()
        elif self.ticks % WATCH_EVERY_TICKS == 0:
            self.check_watched()
        self.ticks += 1

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
            label = req["label"] if req["label"] in BY_NAME else "Iron Ingot"
            cpu.start(label, random_ingredients(random.randint(2, 8), label))
            cpu.job["output"] = req["label"]
            cpu.request_id = req["id"]
            cpu.delivers = (req["label"], int(req["amount"])) if req["label"] in BY_NAME else None
            self.call("POST", f"/api/craft/requests/{req['id']}/result",
                      {"status": "accepted", "cpu_name": cpu.name, "watching": True})
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
        craftable = it["name"] in SMALL_JOBS or it["name"] in PATTERN_OUTPUTS or it["shape"] in ("ingot", "plate", "gear")
        if not size and not craftable:
            return None
        entry = {k: it[k] for k in ("name", "mod", "internal", "damage", "kind")}
        return {**entry, "size": size, "isCraftable": craftable}

    def scan(self):
        items = []
        for it in CATALOG:
            if it["name"] not in ALWAYS_EMPTY:
                self.stock[it["name"]] = max(0, self.stock[it["name"]] + random.randint(-50, 60))
            entry = self._scanned(it)
            if entry:
                items.append(entry)
        token = self.call("POST", "/api/network/scan/start").get("scan_token")
        self.call("POST", "/api/network/scan/batch", {"scan_token": token, "items": items})
        self.call("POST", "/api/network/scan/finish",
                  {"scan_token": token, "chunks_sent": 1, "total_errors": 0})

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
            (f, None) for f in watch.get("fluids", [])}
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
    has plenty of (Titanium Gear), and two alerts."""
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


def admin_token():
    """A fresh access token for the dev admin, creating it if needed."""
    user_id = store.users.id_for_display_name(ADMIN_NAME)
    if user_id:
        return store.users.replace_credentials(user_id)
    return store.users.create(ADMIN_NAME, "admin")[1]


def run(args, data_dir):
    fresh = not os.path.exists(os.path.join(data_dir, "app.db"))
    if not args.no_icons:
        install_icons(data_dir)
    else:
        shutil.rmtree(os.path.join(data_dir, "gamedata"), ignore_errors=True)

    app = create_app(data_dir=data_dir, api_key=API_KEY)
    stock = backfill_history() if fresh else None
    token = admin_token()
    if fresh:
        seed_stock_rules(stock)

    game = FakeGame(app, stock)
    game.tick()  # data in place before the first page load
    threading.Thread(target=game.run_forever, daemon=True).start()

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}/"
    print(f"\n  GTNH Monitor dev server: {url}\n"
          f"  Admin access token:      {token}\n"
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
