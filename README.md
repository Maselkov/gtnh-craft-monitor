# GTNH Craft Monitor

A web dashboard for a GregTech: New Horizons base. OpenComputers scripts
running in-game read your AE2 network and GregTech machines and report to a
small Flask server, which serves a live page you can open from any browser.

Features:

- **Crafts** — every AE2 crafting CPU with its current job, output item, and
  progress. Pin a job to get a desktop notification when it finishes.
- **Power** — stored EU of a GregTech multiblock (e.g. a Lapotronic Super
  Capacitor) charted over time.
- **Network** — searchable, sortable list of every item and fluid in the ME
  network, with per-item stock history charts.
- **Remote crafting** — request or cancel AE2 crafts from the browser.
- **Accounts** — per-user tokens with viewer, operator, and admin roles.

## Contents

- [How it fits together](#how-it-fits-together)
- [Server setup](#server-setup)
- [In-game setup](#in-game-setup)
- [Using the web page](#using-the-web-page)
- [Limitations](#limitations)
- [Technical notes](#technical-notes)
- [Development](#development)

## How it fits together

```
 Minecraft (OpenComputers)                       Server (Docker)
┌───────────────────────────┐    HTTP POST    ┌──────────────────────┐
│ craft_monitor.lua         │ ──────────────▶ │ server/ (Flask)      │ ◀── browser
│ power_monitor.lua         │ ◀────────────── │ SQLite in server/data│
│ network_browser.lua       │  polled requests└──────────────────────┘
└───────────────────────────┘
```

The game always initiates the connection: scripts POST their readings and
poll the server for pending craft requests and cancellations. The server
never connects into the game.

| Path | Purpose |
|---|---|
| `server/` | Flask server (`app.py` entrypoint, `gcm/` package) and the web page (`index.html`, `static/`) |
| `oc/craft_monitor.lua` | Reports crafting CPUs; executes craft requests and cancellations |
| `oc/power_monitor.lua` | Reports a GregTech machine's stored EU |
| `oc/network_browser.lua` | Scans ME network contents |
| `oc/gcm.lua`, `oc/manifest.lua` | Installer and updater, and the list of files it installs |
| `oc/config.lua` | Server URL, API key and per-script settings for all scripts |
| `oc/http.lua`, `oc/json.lua` | Libraries used by the scripts |
| `oc/item_catalog.txt` | Item ID list used by the network scanner |
| `oc/sensor_info_dump.lua` | One-off diagnostic: prints a GT machine's full sensor info |
| `tools/` | Regenerates the icon lookup from a NESQL export |

## Server setup

### Quick start (Docker)

Create the secrets file:

```bash
printf 'API_KEY=%s\n' "$(openssl rand -hex 32)" > .env
printf 'GCM_BOOTSTRAP_ADMIN_TOKEN=gcm_tok-%s_%s\n' \
  "$(openssl rand -hex 12)" "$(openssl rand -hex 32)" >> .env
printf 'GCM_BOOTSTRAP_ADMIN_NAME=Administrator\n' >> .env
chmod 600 .env
```

Start the server:

```bash
docker compose up -d --build
```

The page is served at `http://<your-host>:8420/`. It shows "waiting for data"
until the in-game scripts are running. Sign in with the bootstrap token as
described in [User accounts](#user-accounts).

`API_KEY` is the shared secret the in-game scripts use; `gcm install` asks
for it later and saves it in `/home/config.lua`.

All persistent data (databases, `images.zip`) lives in `server/data/`, which
`docker-compose.yml` mounts into the container, so rebuilding the image
doesn't lose history. The icon lookup table ships inside the image
(`server/reference/`), so each release brings its own.

### Prebuilt image

Tagged releases are published to `ghcr.io/maselkov/gtnh-craft-monitor` for
amd64 and arm64. [`deploy/docker-compose.yml`](deploy/docker-compose.yml)
runs it: copy it and `.env` into a directory of their own, add a `data/`
directory beside them (with `images.zip`, if you have it), and run
`docker compose up -d`. It runs `latest` unless `GCM_TAG` names a version;
`deploy/gcm-deploy.sh` sets it on each release deploy.

### Without Docker

Create `.env` as above, then:

```bash
cd server
pip install -r requirements.txt
set -a; . ../.env; set +a
SESSION_COOKIE_SECURE=0 python app.py
```

`SESSION_COOKIE_SECURE=0` is needed to sign in over plain HTTP; see
[Exposing it beyond your LAN](#exposing-it-beyond-your-lan).

### Configuration

Environment variables (set in `.env` for Docker):

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | — (required) | Shared secret for the in-game scripts. Use 32+ characters. |
| `GCM_BOOTSTRAP_ADMIN_TOKEN` | — | Token for the first admin; only used while no users exist. |
| `GCM_BOOTSTRAP_ADMIN_NAME` | `Administrator` | Display name of the bootstrap admin. |
| `SESSION_COOKIE_SECURE` | `1` (`0` in Compose) | Mark the session cookie HTTPS-only. |
| `TRUSTED_PROXIES` | empty | Comma-separated IPs/CIDRs allowed to send `X-Forwarded-*` headers. |
| `SESSION_LIFETIME_SECONDS` | `604800` (7 days) | Browser session length. |
| `STALE_AFTER_SECONDS` | `30` | How old crafting data can get before the page marks it stale. |
| `CHART_CACHE_TTL_SECONDS` | `60` | Cache time for chart PNGs. |
| `CHART_RATE_LIMIT_PER_MINUTE` | `30` | Chart PNG requests allowed per client per minute. |
| `PORT` | `8420` | Listening port. |
| `DATA_DIR` | `server/data` | Where the databases are written and `images.zip` is read from. |

### Exposing it beyond your LAN

The supplied Compose file serves plain HTTP with `SESSION_COOKIE_SECURE=0`,
which is only appropriate on a trusted LAN. To expose it to the internet:

1. Put it behind a TLS reverse proxy (Caddy, Traefik, nginx).
2. Stop publishing port 8420 publicly, or firewall it so only the proxy can
   reach it.
3. Set `SESSION_COOKIE_SECURE=1`.
4. Set `TRUSTED_PROXIES` to the proxy's address (e.g.
   `TRUSTED_PROXIES=10.0.1.20/32`) so the client IP, host, and HTTPS headers
   it forwards are honored. Forwarded headers from any other source are
   ignored.
5. Use an `https://` `SERVER_URL` in `/home/config.lua` so the API key isn't sent
   in cleartext.

HTTPS is also required for desktop notifications when the page is opened from
another device (see [Pins and notifications](#pins-and-notifications)).

### User accounts

Browser users sign in with a personal access token, which is exchanged for an
`HttpOnly` session cookie. The server stores only a SHA-256 hash of each
token. Pins, completions, and craft history belong to the user, not the token,
so a token can be replaced without losing data.

| Role | Can |
|---|---|
| Viewer | View everything; keep personal pins and completion history |
| Operator | Also request and cancel crafts |
| Admin | Also manage users and tokens |

First sign-in:

1. Enter `GCM_BOOTSTRAP_ADMIN_TOKEN` in the page's **Access token** field.
2. The page immediately issues a replacement admin token. Copy it, confirm
   that you saved it, and use it from now on.
3. **Manage users** is now available for creating accounts. Consider
   creating a second admin as a backup.
4. Once you're signed in with the new token, remove `GCM_BOOTSTRAP_ADMIN_TOKEN`
   from `.env` and restart the service.

Managing users:

- Each generated token is shown once. Send it to its owner over a secure
  channel.
- **Revoke** invalidates a token and ends its sessions immediately.
- **New token** replaces a user's current token in one step.
- **Delete** permanently removes a user with their tokens, sessions,
  pins and pending notifications. Their craft request history is kept.

If the server starts with no users and no bootstrap token, it exits with a
setup error rather than running without an administrator.

If you lose a token and no admin can sign in, issue a new one from the
server. This revokes the user's existing tokens and sessions and prints the
new token:

```bash
docker compose exec gtnh-craft-monitor python app.py new-token "Administrator"
```

### Item icons

The page shows item icons taken from a [NESQL](https://github.com/ShadowTheAge/nesql-exporter)
export. The icon lookup table (`server/reference/icons_lookup.json`) is
included in the repo and the image; the images are not.

To enable icons, copy NESQL's `image.zip` unmodified to
`server/data/images.zip` and restart the server. Images are read from the zip
on demand and never extracted. Without the zip, the page works normally but
shows no icons.

#### Regenerating the icon lookup

Needed after a modpack update or for a different pack. Requires a JDK
(`javac` and `java` on `PATH`).

1. In-game, run `/nesql` to export. This produces
   `.minecraft/nesql/<repo>/nesql-db.*` and `.minecraft/nesql/<repo>/image.zip`.
2. Run the generator, passing the database path without its extension:
   ```bash
   python3 tools/generate_icons_lookup.py --nesql-db "/path/to/nesql-repository/nesql-db"
   ```
   It downloads the HSQLDB driver if needed, exports the `Item` and `Fluid`
   tables to CSV with `tools/ExportItems.java` and `tools/ExportFluids.java`,
   and writes `server/reference/icons_lookup.json`.
3. Replace `server/data/images.zip` with the new `image.zip`.

The lookup contains three tables, tried in order:

- `by_key` — `modid:internalname:damage` → icon, for ordinary items.
- `fluids_by_key` — Forge fluid registry name → icon, for fluid pseudo-items
  (see [Fluid items](#fluid-items)).
- `by_label` — display name → icon. Least reliable fallback, because about 8%
  of labels collide across mods.

When several NESQL rows share a key (NBT variants of one item), the variant
without NBT is used, since the in-game scripts don't report NBT.

## In-game setup

### Requirements

- An OpenComputers computer with an **Internet Card**.
- An **Adapter** touching the ME Controller (any face), or an ME Interface if
  there is no controller.
- For the power monitor, an Adapter touching the GregTech multiblock (any
  casing block).
- `allowInternet` enabled in the server's `OpenComputers.cfg`, and your
  server's host not blocked by its whitelist. If this is off, the scripts
  can't POST, and nothing on the Lua side can fix it. The installer also
  needs `raw.githubusercontent.com` and `api.github.com`.

### Install

`gcm` installs the scripts from the latest release and updates them later.
Download it to `/tmp` once and run it from there; `gcm install` installs
itself to `/usr/bin`, so afterwards it runs as plain `gcm`:

```
wget -f https://raw.githubusercontent.com/Maselkov/gtnh-craft-monitor/main/oc/gcm.lua /tmp/gcm.lua
/tmp/gcm.lua install
```

`gcm install` asks which scripts to install, then asks for the server URL
and API key and writes them to `/home/config.lua`. It can also enable the
scripts so they start on every boot, and offers to reboot to start them.

To install specific scripts without being asked, name them:

```
gcm install craft power network
```

Run `gcm install` again later to add scripts. `/home/config.lua` is only
written if it doesn't exist yet.

The scripts are independent: they can run on one computer or on separate
ones.

### Update

```
gcm update     # install the latest release, then offer to reboot
gcm status     # installed version, installed scripts, latest release
```

`gcm update` replaces the scripts and libraries but never
`/home/config.lua`. Keep your settings there (see
[Script settings](#script-settings)). All files are downloaded before any
are replaced, so a failed download leaves the old version in place. The
reboot is needed because OpenOS keeps the old code loaded until then.

Both commands take `--ref=<tag or branch>` to use something other than the
latest release, for example `gcm update --ref=main` to try unreleased
changes. Releases older than `gcm` itself can't be installed with it.

Use a release that matches your server's version: the image tag and the
scripts come from the same release.

### Manual install

Without `gcm`, copy these files to the computer yourself (with a floppy and
`edit`, or `wget`):

| File | Destination | Needed for |
|---|---|---|
| `oc/config.lua` | `/home/config.lua` | All scripts |
| `oc/http.lua` | `/usr/lib/http.lua` | All scripts |
| `oc/json.lua` | `/usr/lib/json.lua` | Craft monitor, network browser |
| `oc/craft_monitor.lua` | `/etc/rc.d/craft_monitor.lua` | Crafts tab, remote crafting |
| `oc/power_monitor.lua` | `/etc/rc.d/power_monitor.lua` | Power tab |
| `oc/network_browser.lua` | `/etc/rc.d/network_browser.lua` | Network tab |
| `oc/item_catalog.txt` | `/home/item_catalog.txt` | Network browser |

Then set `SERVER_URL` (no trailing slash) and `API_KEY` (the server's
`API_KEY`) in `/home/config.lua`.

`http.lua` and `json.lua` go in `/usr/lib/` so `require()` finds them from
any script. `config.lua` is loaded by absolute path; to keep it somewhere
other than `/home/`, change `CONFIG_PATH` at the top of each script.

### Run the scripts

Each script is an OpenOS `rc` service:

```
rc craft_monitor enable     # start automatically on every boot
rc craft_monitor start      # start now
rc craft_monitor stop
rc craft_monitor status
```

`start` alone lasts only until the next reboot. The same commands work for
`power_monitor` and `network_browser`.

### Script settings

Change settings in `/home/config.lua`, not in the scripts, because
`gcm update` replaces the scripts. Add a table named after the script with
only the settings you want to change:

```lua
return {
  SERVER_URL = "https://monitor.example.com",
  API_KEY    = "...",

  craft_monitor = {
    SHOW_STATUS = true,
  },
  power_monitor = {
    POLL_SECONDS = 30,
  },
}
```

Anything not listed keeps the default from the tables below. Changes take
effect after a reboot.

The scripts don't draw to the screen by default (`SHOW_STATUS = false`). If
you turn on a status screen, enable it for only one script per screen: each
one clears the terminal on every cycle, so two would flicker.

**craft_monitor.lua**

| Setting | Default | Description |
|---|---|---|
| `POLL_SECONDS` | `5` | How often crafting CPUs are read and posted |
| `CRAFT_REQUEST_POLL_SECONDS` | `1.5` | How often pending craft requests are checked |
| `COMPONENT` | `nil` (auto) | Force `"me_controller"` or `"me_interface"` |
| `SHOW_STATUS` | `false` | Draw a compact status screen |
| `VERBOSE` | `false` | Print one line per CPU per cycle, for debugging |
| `DEBUG_DUMP` | `false` | Print the raw `getCpus()` data once and exit |
| `DEBUG_CPU_FILTER` | `nil` | Limit `DEBUG_DUMP` to one CPU by name |

`DEBUG_DUMP` is useful on first setup to check what the script sees:
set `craft_monitor = { DEBUG_DUMP = true }` in `config.lua`, reboot, read
the output, then remove it again.

**power_monitor.lua**

| Setting | Default | Description |
|---|---|---|
| `POLL_SECONDS` | `60` | How often stored EU is read and posted |
| `COMPONENT_ADDRESS` | `nil` | Pick a specific `gt_machine` if several are connected |
| `SHOW_STATUS` | `false` | Draw a status screen |

**network_browser.lua**

| Setting | Default | Description |
|---|---|---|
| `SCAN_INTERVAL_SECONDS` | `600` | Time between full network scans |
| `CATALOG_PATH` | `/home/item_catalog.txt` | Location of the item catalog |
| `BATCH_SIZE` | `300` | Item IDs queried per call |
| `RESULT_CHUNK_SIZE` | `100` | Items sent to the server per POST |
| `DELAY_BETWEEN_BATCHES_SECONDS` | `0.1` | Pause between batches |
| `SHOW_STATUS` | `false` | Draw a status screen |

A full scan takes 37 batches with the bundled catalog and runs every 10
minutes by default.

`item_catalog.txt` lists about 10,900 item IDs (`modid:internalname`, damage
values merged) from the same NESQL export used for icons. Regenerate it
alongside the icon lookup if you switch modpack versions.

## Using the web page

### Crafts tab

One card per crafting CPU, updated every 3 seconds. A busy card shows:

- **Output item** — needs an AE2 **Crafting Monitor** block in that CPU's
  multiblock. Without one, the card shows "Crafting job (no monitor tile)".
- **Progress** — items already produced versus items still needed for the
  job.

### Pins and notifications

Click the pin icon on a card to pin its job to the top of the page. When a
pinned job finishes, it shows as a completion to acknowledge and, if you've
clicked **Enable notifications**, triggers a desktop notification. Pins and
completions are saved per user on the server, so they follow you across
devices.

Browsers allow notifications only on a secure origin: `https://` or
`http://localhost`. Opened over plain HTTP from another device, the
**Enable notifications** button does nothing. Use a TLS reverse proxy (see
[Exposing it beyond your LAN](#exposing-it-beyond-your-lan)) or open the page
on the server machine itself.

### Power tab

Stored EU over time. The Hour, Day, Week, Month, and Lifetime
ranges are downsampled on the server to at most 800 points, so long ranges
stay fast. Readings are kept in `server/data/power.db`.

### Network tab

Every item and fluid in the ME network, with icon, name, and amount (fluids in
mB). It refreshes every minute, but its data changes only when a scan
finishes.

- Click an item to open its stock history chart. Each item has its own URL
  (`/network/item/<mod:internal:damage>`) that can be shared; link previews
  include the current amount and a chart.
- Pin items to keep them at the top of the list.
- The last scan is saved to `server/data/item_history.db` and restored when
  the server restarts. Until the next scan completes, the page marks the data
  as restored rather than live.

### Requesting and cancelling crafts

Requires an operator or admin account. Craftable items in the Network tab
have a small Blank Pattern icon in the corner.

- **Left-click** a craftable item that has no stock, or **middle-click** any
  craftable item, to open the request dialog. This matches AE2's terminal.
- Left-clicking an item that is in stock does nothing, since the page can't
  extract items.

After you submit, a pending card appears. Planning a large craft can take from
under a second to several minutes, and the request survives closing the tab.

- **Accepted** — the pending card is replaced by a pin on the CPU that AE2
  assigned, and the job is tracked like any other pinned craft.
- **Failed** — the card turns red with AE2's reason (usually
  `request failed (missing resources?)`) until you dismiss it.

To cancel a running job, click **×** on its card in the Crafts tab.

Fluids can be requested too.

## Limitations

- Crafting status is only as fresh as `POLL_SECONDS` (5 s by default) plus the
  page's 3 s refresh. Requests and cancellations wait for the next poll from
  the game.
- Progress is based on item counts, not on crafting time. A job with one slow
  expensive item and 63 cheap ones shows as nearly done once the 63 are made.
- With `allowInternet` disabled on the Minecraft server, nothing works. The
  only alternative is an in-game display.

## Technical notes

These notes explain design choices in the scripts, and were verified against
the GTNH OpenComputers source and live testing.

### Crafting CPUs

GTNH's AE2 integration has no `getCraftingCPUs()`. The method is
`getCpus()` (see `NetworkControl.scala` in `GTNewHorizons/OpenComputers`):

```
me.getCpus() -> array of { name, storage, coprocessors, busy, cpu }
```

Each `cpu` object provides `isActive()`, `isBusy()`, `activeItems()`,
`pendingItems()`, `storedItems()`, `finalOutput()`, and `cancel()`.
Progress is the summed `size` of `storedItems()` relative to
`pendingItems()`. `finalOutput()` fails unless the CPU has a Crafting
Monitor block.

### Craft request results

A request counts as finished when `isComputing()` returns `false`. `isDone()`
isn't used, because it only reports whether a crafting link was created. A
rejected request never creates one, so `isDone()` would stay `false` forever
in exactly the case where an error must be shown.

Fluid craftables use a different `getCraftables()` filter: by label instead
of by name and damage. The script handles this automatically.

### Scanning the network

Two obvious approaches fail on a large GTNH network:

- `getItemsInNetwork()` builds the entire result in one table, which ran a
  computer out of memory on a network with about 6,000 item types.
- `allItems()` is a Java iterator held open across thousands of calls. When
  the network changes during the scan, the iterator breaks. Tests returned as
  little as 22% of the real item count, sometimes without any error.

`network_browser.lua` uses `getItemsInNetworkById(idList)` instead. It is
stateless per call, filters by item ID before converting results, and
returned the correct count on every test. (The approach comes from
[oc-influxdb-exporter](https://gitlab.com/uncountablyinfinite1056/oc-influxdb-exporter).)
The candidate IDs are streamed from `item_catalog.txt` in batches rather than
loaded all at once, which roughly doubles free memory during a scan compared
with loading the full list.

Fluids are read with a single `getFluidsInNetwork()` call, since networks
hold far fewer fluid types than item types.

On the server, a scan is a `start` → `batch` × N → `finish` sequence. Batches
fill a buffer, and `finish` swaps it in as the live snapshot, so the page
never shows a half-finished scan.

### Fluid items

Fluid pseudo-items report `name` as a bare Forge fluid registry name with no
colon, e.g. `molten.mutatedlivingsolder`. They aren't in NESQL's `Item` table
because they're generated at runtime, so their icons come from the `Fluid`
table.

### Reading EU

`power_monitor.lua` uses `getEUStored()` and `getEUMaxStored()` on the
`gt_machine` component. Older builds overflowed above 2³² EU
(GTNewHorizons/GT-New-Horizons-Modpack#8619, fixed in
GTNewHorizons/OpenComputers#78). If the numeric call fails or reports more
stored than the capacity, the script falls back to parsing
`getSensorInformation()`.

## Development

Run the server tests:

```bash
cd server
pip install -r tests/requirements-test.txt
pytest
```

The tests use a temporary data directory and Flask's test client, so they
don't need a running server or Docker and never touch `server/data/`. They
cover the server's HTTP API and helper functions, not the Lua scripts.

Run the frontend tests (Node 24 or newer, no dependencies) from the repo root:

```bash
node --test 'server/tests/js/*.test.js'
```

Run the browser tests (Python with `server/requirements.txt`, Node 24+,
and Chrome or Chromium - set `CHROME=/path/to/binary` if it isn't found):

```bash
node server/tests/e2e/run.mjs
```

GitHub Actions runs all three test suites, a syntax check of every frontend
script, a Lua 5.3 syntax check of `oc/*.lua`, and a Docker build on every
push and pull request (`.github/workflows/ci.yml`).
Pushing a `v*` tag publishes the image to GHCR (`.github/workflows/publish.yml`).

## License

Licensed under the GNU General Public License v3.0 or later. See
[LICENSE](LICENSE).
