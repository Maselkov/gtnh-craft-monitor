# GTNH Craft Monitor

Watch AE2 crafting CPU status from a webpage outside Minecraft.

Two halves:

- `oc/craft_monitor.lua` — runs on an OpenComputers computer in-game.
  Reads crafting CPU state via an Adapter, POSTs it out via an Internet Card.
- `server/` — a small Flask server that receives those POSTs and serves a
  live-updating status page.

All the `oc/*.lua` scripts (`craft_monitor.lua`, `power_monitor.lua`,
`network_browser.lua`) share three files, loaded two different ways.
`oc/config.lua` — set your server URL and API key there **once**, not
separately in each script — is `dofile()`'d by absolute path from
`/home/config.lua`, the same place `item_catalog.txt`/`craft_keys.txt`
live if you're using the network browser / craft requests. `oc/json.lua`
(JSON encode/decode - OpenComputers has no JSON library built in) and
`oc/http.lua` (the HTTP request-with-timeout helper every script needs)
are real libraries instead: deploy both to `/usr/lib/` and they're
loaded with `require("json")`/`require("http")`, which works reliably
even for a script living in `/etc/rc.d/` since `/usr/lib/?.lua` is a
fixed, absolute entry in OpenOS's own `package.path`, not resolved
relative to wherever the calling script happens to be. Edit
`CONFIG_PATH` near the top of a script if you'd rather keep `config.lua`
somewhere other than `/home/`.

## 1. Server setup

Before the first startup, create the service and administrator secrets file:

```bash
printf 'API_KEY=%s\n' "$(openssl rand -hex 32)" > .env
printf 'GCM_BOOTSTRAP_ADMIN_TOKEN=gcm_tok-%s_%s\n' \
  "$(openssl rand -hex 12)" "$(openssl rand -hex 32)" >> .env
printf 'GCM_BOOTSTRAP_ADMIN_NAME=Administrator\n' >> .env
chmod 600 .env
```

Then start the service:

```bash
docker compose up -d --build
```

This starts the server on port 8420. Open `http://<your-host>:8420/` in a
browser — it'll say "waiting for data" until the game side is running.

The generated `API_KEY` must match the value in `oc/config.lua`. Copy it to
the OpenComputers configuration before starting any game-side monitor scripts.

### Browser accounts

Browser access uses personal bearer tokens that are exchanged for an
`HttpOnly` session cookie. A token is not used as a user ID: the server
stores an immutable user ID, role, and only a SHA-256 hash of each token.
Pins, completions, and requests belong to that user ID, so a token can be
revoked or replaced without losing the user's data.

Keep the generated token private. Start the stack and enter it once in the
page's **Access token** field. The page requires the bootstrap token to be
rotated immediately: copy the replacement admin token shown in the modal, then
confirm that it was saved. Only then is **Manage users** available to create
viewer and operator accounts. Every generated token is shown once; distribute
it securely to its owner because the server retains only its hash. The same
panel lists each user's active token IDs (revoked tokens are removed from the
list, not just hidden) and can revoke one, immediately ending sessions created
with it. **New token** replaces a user's current token in one step - useful if
a token was lost before it was copied - and **Delete** permanently removes a
user along with their tokens and sessions.

The bootstrap token is used only when no users exist. Once you have signed in
and created another administrator, remove `GCM_BOOTSTRAP_ADMIN_TOKEN` from
`.env` and restart the service. If a new installation starts with no users and
no bootstrap token, the server stops with an explicit setup error instead of
running without an administrator.

The supplied Compose setup is intentionally convenient for a trusted LAN and
sets `SESSION_COOKIE_SECURE=0` because it exposes plain HTTP directly. It is
not suitable for internet exposure: place the service behind a TLS reverse
proxy, stop publishing port `8420` publicly, and set
`SESSION_COOKIE_SECURE=1`. Use an `https://` `SERVER_URL` in `oc/config.lua`
as well so the OpenComputers service API key is not sent in cleartext.

Chart PNG endpoints use a 60-second in-process cache and allow 30 requests per
minute per client by default. Set `TRUSTED_PROXIES` to Caddy's private IP or
CIDR (for example, `TRUSTED_PROXIES=10.0.1.20/32`) so forwarded client IP,
host, and HTTPS headers are honored only from Caddy. Firewall port 8420 so only
that proxy can reach it; direct callers cannot supply trusted forwarded headers.
For a Caddy deployment, also set `SESSION_COOKIE_SECURE=1` in `.env`.

Without Docker:

```bash
cd server
pip install flask
API_KEY="$(openssl rand -hex 32)" python app.py
```

## 2. In-game setup

1. Place an **Adapter** touching your ME Controller (a multiblock, so any
   face works) or an ME Interface if you don't have a Controller.
2. Put an **Internet Card** in the computer running this script.
3. Check with your server admin (or `OpenComputers.cfg` if it's your
   server) that `allowInternet` is enabled and your host isn't blocked by
   a whitelist. This is the most common reason the script silently fails
   to POST — it's a server-side permission, not something fixable from Lua.
4. Copy `oc/craft_monitor.lua` to `/etc/rc.d/craft_monitor.lua` on the
   computer, `oc/config.lua` to `/home/config.lua`, and both
   `oc/json.lua` and `oc/http.lua` to `/usr/lib/json.lua` and
   `/usr/lib/http.lua`
   (floppy disk + `edit`, or an OpenOS `wget` if your server allows it).
   These live in different places for different reasons -
   `craft_monitor.lua` is managed as an OpenOS `rc` service;
   `config.lua` (and `item_catalog.txt`/`craft_keys.txt`, if
   you're using those) stays in `/home/`, loaded by absolute path; and
   `json.lua`/`http.lua` are real libraries, found via `require("json")`/
   `require("http")` because `/usr/lib/` is on OpenOS's own `package.path`.
   If you'd rather keep `config.lua` somewhere else, edit `CONFIG_PATH` near
   the top of each script.
5. Edit `oc/config.lua`:
   ```lua
   SERVER_URL = "http://YOUR-SERVER-HOST:8420",
   API_KEY    = "change-me",  -- same as server
   ```
   `craft_monitor.lua`'s own `CONFIG` block still has its script-specific
   settings (`POLL_SECONDS`, debug flags, etc.) — just no `URL`/`API_KEY`
   to set there anymore, those come from the shared file.
6. (Optional) set `DEBUG_DUMP = true` and run once (`rc craft_monitor
   start`, then check the output) to print the raw `getCpus()` structure
   and each CPU's active/pending/stored items, to sanity-check against
   your build before trusting the derived numbers.
7. Set `DEBUG_DUMP = false` (default). By default it draws a compact,
   self-overwriting status screen each cycle (component used, busy/idle
   counts, last POST result, error count) rather than scrolling a line
   per CPU - redrawing 30+ lines every cycle was the actual source of
   any lag, not the AE2 calls themselves. Set `CONFIG.VERBOSE = true` if
   you want the old scrolling per-CPU log back (useful when diagnosing a
   specific CPU's errors in detail).

### Running it

Each script is an OpenOS `rc` service now, not something you run
directly:

```
rc craft_monitor start      # start now, this boot only
rc craft_monitor enable     # also auto-start on every future boot
rc craft_monitor stop       # stop it
rc craft_monitor restart    # stop then start
rc craft_monitor status     # is it currently running
```

`enable` is what you want for normal, persistent use - `start` alone
only lasts until the computer next reboots. `power_monitor` and
`network_browser` (below) work the same way, each under their own
service name.

### The actual API (confirmed from source)

GTNH's AE2↔OpenComputers bridge does **not** have `getCraftingCPUs()` —
that was wrong in the first version of this script (hence the "attempt
to call a nil value" error). Confirmed from `GTNewHorizons/OpenComputers`,
`NetworkControl.scala`, the real method is:

```
me.getCpus() -> array of { name, storage, coprocessors, busy, cpu }
```

Each row's `cpu` is a proxy object with its own methods: `isActive()`,
`isBusy()`, `activeItems()`, `pendingItems()`, `storedItems()`,
`finalOutput()` (needs a Crafting Monitor tile in that CPU's cluster),
and `cancel()`.

The script sums `size` across `storedItems()` vs `pendingItems()` to
derive a real progress percentage — items already produced vs. items
still needed, per crafting CPU — not a guess.

It also calls `cpu.finalOutput()` to get the actual name of the item
being crafted, shown as the card's headline on the page. This only
works if that CPU's cluster has an **AE2 Crafting Monitor** tile placed
in it — without one, the call errors/returns nothing and the card falls
back to showing "Crafting job (no monitor tile)" instead of guessing.

### Item icons

Icons are matched against a lookup table (`server/data/icons_lookup.json`,
already bundled) built from a NESQL export
(https://github.com/ShadowTheAge/nesql-exporter). If you ever need to
regenerate it (a modpack update, a different pack entirely):

1. Export in-game with the exporter mod (`/nesql`), producing
   `.minecraft/nesql/<repo>/nesql-db.*` and `.minecraft/nesql/<repo>/image.zip`.
2. Run the whole rest of the pipeline in one command:
   ```bash
   python3 tools/generate_icons_lookup.py --nesql-db "/path/to/nesql-repository/nesql-db"
   ```
   (that's the path to your `nesql-db.*` files WITHOUT any extension).
   This compiles and runs `tools/ExportItems.java`/`tools/ExportFluids.java`
   (small, standalone, read-only JDBC dumps of the `Item`/`Fluid` tables
   to CSV) and builds `server/data/icons_lookup.json` directly from the
   result — needs a JDK on PATH (`javac`/`java`), nothing else; it'll
   locate or download the `hsqldb` jar itself. `icons_lookup.json` ends
   up with three lookup tables:
   - `by_key`: `modid:internalname:damage` → icon, for ordinary items.
     AE2 reports `name` as `"modid:internalname"` for these (colon present).
   - `fluids_by_key`: bare Forge fluid registry name → icon. GT/GTNH's
     Fluid Discretizer pseudo-items report `name` with **no colon at all**
     — it's just the raw fluid registry name (e.g.
     `name="molten.mutatedlivingsolder"`), confirmed empirically rather
     than assumed from an NBT tag (the original theory — that the fluid
     was NBT-encoded on a shared dummy item — turned out to be wrong;
     these are `hasTag=false` with the fluid baked straight into `name`).
     These items were never in NESQL's `Item` export at all (confirmed:
     zero matching rows), since the Discretizer generates them
     dynamically from whatever's registered at runtime rather than as
     static NEI-visible entries — hence needing the separate `Fluid` table.
   - `by_label`: plain item label → icon, weakest fallback, used only
     when neither of the above matched (labels collide across mods ~8%
     of the time, so this alone isn't reliable).

   When multiple NESQL rows share the same key (an item with several
   NBT-tagged variants, say), the script prefers whichever row has no
   NBT at all as the representative icon — this project's own OC-side
   data never carries NBT to disambiguate by anyway, so a plain variant
   is the best available default.

**You still need to add the actual images separately.** Drop NESQL's
`image.zip` *unmodified* into `server/data/images.zip`, then rebuild:
```bash
docker compose up -d --build
```
The server reads icons directly out of that zip on demand (via Python's
`zipfile`) — it's never unpacked to disk, so the ~150k images in there cost
nothing until an item that needs one is actually seen.

If `server/data/images.zip` is missing, the page still works fine — items
just render without icons, and the server logs a one-line notice.

## 3. Power monitor (optional)

A separate tab on the page ("Power") charts a GregTech machine's stored
energy over time — built for a Lapotronic Super Capacitor, but works for
any GT multiblock reachable via Adapter.

### In-game setup

1. Place an **Adapter** touching the multiblock (any casing block on an
   LSC works, same as any other GT multiblock ↔ Adapter hookup).
2. Copy `oc/power_monitor.lua` to `/etc/rc.d/power_monitor.lua` on a
   computer (same one as the craft monitor, or a separate one — they
   don't know about each other). `config.lua` stays wherever you
   already put it for craft_monitor.lua (default `/home/`), and
   `http.lua` stays at `/usr/lib/http.lua` — power_monitor.lua needs
   that one too (every script does), just not `json.lua` (its payload
   is always a flat object of plain numbers it builds directly, never
   needing a general encoder).
3. `power_monitor.lua`'s own `CONFIG` block now only has script-specific
   settings (`POLL_SECONDS`, `SHOW_STATUS`, etc.) — `URL`/`API_KEY` come
   from `config.lua`, already set once for all three scripts.
4. `rc power_monitor enable` (or `start` for this boot only). Default
   poll interval is 60s — energy doesn't need the 5s resolution crafting
   does, so this runs as its own independent service with its own
   timing rather than being bolted onto craft_monitor.lua.

The actual API, confirmed from a real bug report rather than assumed:
`getEUStored()` / `getEUMaxStored()` on the `gt_machine` component. There
was a known integer-overflow bug for values above 2^32 EU
(GTNewHorizons/GT-New-Horizons-Modpack#8619), fixed upstream via
GTNewHorizons/OpenComputers#78 — the script still defensively falls back
to parsing `getSensorInformation()`'s text (not subject to the same
overflow) if the direct numeric call fails or looks implausible (stored
> capacity), in case you're on an older build.

### Storage

Readings land in `server/data/power.db` (SQLite, auto-created on first
run — nothing to set up). The `docker-compose.yml` volume mount is what
makes this survive rebuilds; without it, `power.db` would live only in
the container's writable layer and vanish on `docker compose up -d --build`.

The chart's five range buttons (Hour/Day/Week/Month/Lifetime) each hit
`GET /api/power?range=...`, which downsamples server-side to at most 800
points via bucket-averaging — so "Lifetime" stays fast to render even
after months of history, rather than shipping tens of thousands of raw
rows to the browser on every view.

### Running more than one service on one computer

`craft_monitor.lua`, `power_monitor.lua`, and `network_browser.lua` are
each their own OpenOS `rc` service - `rc <name> enable` for each one you
want, and OpenOS handles running them as independent background threads
and starting them again on every future boot. There's no separate
launcher script to run anymore (`start_monitors.lua` is retired - if
you have an old copy still sitting on a computer, running it just
prints a pointer to this section instead of doing anything).

Set `power_monitor.lua`'s `CONFIG.SHOW_STATUS = false` (the default) if
you're running it on the same screen as `craft_monitor.lua` - both
scripts' status screens call `term.clear()` on their own poll cycle, so
two screen-owning scripts sharing one terminal fight over it and
flicker constantly. With `SHOW_STATUS = false`, `power_monitor.lua`
still polls and posts normally, it just doesn't touch the screen,
leaving `craft_monitor.lua` as the one visible display. (If you have a
second GPU+screen on the computer and want both visible, you'd need to
bind each script to a different screen - not something `rc` sets up
for you.)

## 4. Network browser (optional)

A third tab, "Network" — a searchable, sortable browse of everything
currently stored in your ME network (icons, name, quantity), not just
crafting-related items.

### Why this needed its own investigation

The obvious approaches don't work at real GTNH network scale:

- `getItemsInNetwork()` (the plain bulk call) converts the *entire*
  network before returning anything — one huge table, no way to bound
  its size. This repeatedly ran the computer itself out of memory on a
  real ~6,000-item network (confirmed: OpenComputers itself OOMing, not
  a server/Java crash).
- `allItems()` (the iterator OC added specifically to avoid that) trades
  the memory problem for a different one: it's a stateful Java iterator
  held open across thousands of individual calls, and on a *live*
  network — anything with ongoing crafting, import/export buses, etc. —
  the network changing mid-walk corrupts it. Confirmed via repeated
  testing: wildly inconsistent item counts between runs (as low as 22%
  of the true total), sometimes with *zero* reported errors — it doesn't
  always fail loudly, it can silently claim to be done when it isn't.

`getItemsInNetworkById(idList)` — found in a third-party exporter
([uncountablyinfinite1056/oc-influxdb-exporter](https://gitlab.com/uncountablyinfinite1056/oc-influxdb-exporter))
and confirmed against the real Scala source — filters on raw item
identity *before* converting anything, and is stateless per call (no
iterator to invalidate). Tested twice on a real network: matched the
confirmed true count both times, zero batch errors either run.

The remaining risk was memory for the *candidate ID list* itself — an
early version held the whole ~10,885-entry known-item catalog in memory
via `require()`, leaving only ~850KB free at the low point on a ~4MB
computer. Streaming the catalog from a plain text file too (one ID per
line, read in small batches, same discipline already applied to the
results coming back) roughly doubled that margin in testing — real
headroom, not just "happened not to crash that time."

### Setup

1. Copy `oc/network_browser.lua` to `/etc/rc.d/network_browser.lua`, and
   `oc/item_catalog.txt` to `/home/item_catalog.txt` (or edit
   `CONFIG.CATALOG_PATH` in the script to point wherever you put the
   catalog file). The catalog is ~10,885 known base item IDs
   (`modid:internalname`, damage variants collapsed since this method
   matches on base item type only) generated from the same NESQL export
   used for icons — see "Item icons" above for how that export works if
   you ever need to regenerate it for a different modpack/version.
   `config.lua` stays wherever you already put it for
   craft_monitor.lua (default `/home/`), and `json.lua`/`http.lua` stay
   at `/usr/lib/json.lua`/`/usr/lib/http.lua` — every script needs
   `http.lua`, but only `craft_monitor.lua` and `network_browser.lua`
   need `json.lua` (each parses a JSON response body back, not
   just sends one); `power_monitor.lua` is the only one that doesn't,
   since its payload is always a flat object of plain numbers it builds
   directly.
2. `URL`/`API_KEY` already come from `config.lua` — nothing to
   set here unless you want to override `BATCH_SIZE`, `RESULT_CHUNK_SIZE`,
   or `SCAN_INTERVAL_SECONDS`.
3. `rc network_browser enable` (or `start` for this boot only).

A full scan (37 batches, ~0.1s apart by default) runs on its own long
interval — `CONFIG.SCAN_INTERVAL_SECONDS`, default 10 minutes. This
data doesn't need anywhere near crafting status's freshness, and a scan
is real, non-trivial work, so it's deliberately infrequent rather than
continuously polled.

### How it works, server-side

A scan is a `start` → `batch` (× however many) → `finish` sequence, not
one big POST — `POST /api/network/scan/start` clears an in-progress
buffer, `POST /api/network/scan/batch` appends each batch's items to it
(resolving icons immediately, reusing the exact same lookup pipeline
crafting items already use), and `POST /api/network/scan/finish`
promotes that buffer to the live snapshot `GET /api/network` serves.
That means a browser reading mid-scan always sees the last *complete*
snapshot, never a half-built one. Kept in memory only (no SQLite) —
unlike power/craft history, there's no reason for "what's in the
network right now" to survive a server restart.

Fluids appear in the same grid alongside items (tagged "Fluid" in the
hover tooltip, amounts shown in mB) - `network_browser.lua` also calls
`getFluidsInNetwork()`, a single bulk call rather than the batched
approach items need, since real GTNH networks have far fewer distinct
fluids than items (confirmed safe at the scale actually tested).

## 5. Requesting crafts from the website (optional)

The Network tab's craftable items (marked with a small Blank Pattern icon,
top-right corner) can be requested directly from the browser, not just
viewed - fluids too, confirmed via a real successful request during
development, though they need a different `getCraftables()` filter shape
under the hood (by label, not name+damage) - handled automatically,
nothing to configure.

### Setup

Sign in with an access token. Viewer accounts can keep their own pins and
completion history; operator and administrator accounts may also submit or
cancel crafts. The game-side OpenComputers API key remains separate from
browser accounts and authorizes only game-to-server reporting and polling.

### Interaction, matching the real AE2 terminal

- **Left-click** a craftable item with **no current stock** → opens the
  request popup (there's nothing to extract either way, so this is
  repurposed the same way AE2's own UI does).
- **Middle-click** any craftable item, **stock or not** → always opens
  the popup (AE2's own "autocraft" gesture).
- Left-click on a craftable item that already has stock does nothing —
  this page has no way to actually extract items into an inventory the
  way in-game left-click does.

### What happens after you submit

A lightweight "waiting for acknowledgement" card appears — not a
blocking spinner, since a genuinely complex craft can take anywhere from
under a second to several minutes to resolve (confirmed through direct
testing), and closing the tab or navigating away shouldn't lose the
request. Two outcomes:

- **Accepted** — the request card disappears and a real pin appears in
  its place, automatically, on the CPU AE2 assigned. From that point on
  it's an ordinary tracked craft, using the exact same pin/completion
  infrastructure everything else on the Crafts tab already relies on —
  there's no separate "did my request finish" tracking to build or
  maintain.
- **Failed** — the card turns red with AE2's own reason text (usually
  `request failed (missing resources?)`, confirmed from a real failed
  request) and stays until you dismiss it.

The actual signal used to decide accepted-vs-failed is `isComputing()`
becoming `false`, not `isDone()` — confirmed from the real OC/AE2 source
that `isDone()` only tracks whether a crafting *link* was created, which
never happens on a rejected request, so it can stay `false` forever on
exactly the case that matters most for showing an error.

## Notes / limitations

- This is polling, not push: the webpage polls the server every 3s, and
  the game polls AE2 + posts every `POLL_SECONDS`. There's no way for the
  website to reach back into the game.
- The progress percentage is derived from item counts (stored vs.
  pending), not from AE2's own internal job timer, so it's an
  approximation weighted by how many of each ingredient are needed —
  a job needing one expensive item and 63 cheap ones will look mostly
  "done" once the 63 are in, even if the expensive one is still crafting.
- If `allowInternet` is off on your server, there's no workaround from
  OpenComputers alone — you'd be limited to in-game display (monitor +
  GPU) or triggering an external signal via other mod bridges.

### Pinning + desktop notifications

Click the pin icon on a card to move it to a "Pinned" row at the top.
A pinned CPU that transitions from busy to idle (i.e. its job finished)
triggers a browser desktop notification, if you've granted permission
via the "Enable notifications" button.

**Important:** browsers only allow the notification permission prompt
on a *secure context* — `https://` or `http://localhost`. Serving this
page over plain `http://` from a LAN hostname or IP (which is what
`docker compose` gives you by default) means the "Enable notifications"
button will do nothing in most browsers — no prompt, no error, it just
silently won't work. To actually get notifications, either:
- access the page via `http://localhost:8420` (works if the browser
  is on the same machine as the server), or
- put the server behind a reverse proxy with a real TLS certificate
  (e.g. Caddy, Traefik, nginx + Let's Encrypt) if you want it reachable
  from other devices on your LAN.

Pin state and permission state both live only in that browser tab's
memory — they reset on page reload and aren't shared between devices.

## Running the backend test suite

```
cd server
pip install -r tests/requirements-test.txt
pytest tests/
```

Tests are fully isolated from real data - `conftest.py` points the
server at a temporary directory before `app.py` is ever imported, so
nothing touches `server/data/` or a real `power.db`/`craft_history.db`/
`item_history.db`. No live server or Docker container needs to be
running; `app.test_client()` talks to the real Flask routes directly,
in-process.

Currently covers `server/app.py`'s endpoints and pure helper functions
(item history, network scanning, craft requests/cancellation, power
readings including the schema migration, and OpenGraph tag generation)
- not the `oc/*.lua` scripts, which have no equivalent harness yet.
