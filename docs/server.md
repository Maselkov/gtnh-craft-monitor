# Server setup

Everything about running the monitor's server. To get one running quickly,
follow the [Quick start](../README.md#quick-start) first; this page covers
the rest.

- [Docker](#docker)
- [Building from a clone](#building-from-a-clone)
- [Without Docker](#without-docker)
- [Configuration](#configuration)
- [Exposing it beyond your LAN](#exposing-it-beyond-your-lan)
- [User accounts](#user-accounts)
- [Backups](#backups)
- [Game data](#game-data)

## Docker

Releases are published as an image, `ghcr.io/maselkov/gtnh-craft-monitor`,
for amd64 and arm64. The Compose file in
[`deploy/docker-compose.yml`](../deploy/docker-compose.yml) runs it; the
[Quick start](../README.md#quick-start) downloads it and starts it.

All persistent data (databases, game data, keys) lives in `data/`, beside
`docker-compose.yml`, which mounts it into the container. Replacing the
container keeps it. The rest of these docs call it `data/`.

The in-game scripts sign their reports with an API key. The first start
generates one into `data/api_key`. **Settings (⚙) → Status →
Connect a computer** puts it in the install command for you; to see it
otherwise, show it through the container, as the file belongs to the
container's root user:

```bash
docker compose exec gtnh-craft-monitor cat data/api_key
```

No `.env` is needed. To change settings, create one beside
`docker-compose.yml` ([`.env.example`](../.env.example) lists the common
settings; see [Configuration](#configuration)), then `docker compose up -d`
again.

The Compose file runs the newest release (`latest`); to stay on one, set
`GCM_TAG` in `.env` to its version, e.g. `GCM_TAG=5.0.0`.
`deploy/gcm-deploy.sh` sets it on each release deploy. Updating is covered
in the [README](../README.md#updating).

## Building from a clone

To run your own changes, or unreleased ones, build the image from a clone
instead. Its [`docker-compose.yml`](../docker-compose.yml) keeps the data in
`server/data/`.

```bash
git clone https://github.com/Maselkov/gtnh-craft-monitor.git
cd gtnh-craft-monitor
docker compose up -d --build
```

`main` can be ahead of the latest release, which is what `gcm` installs in
game; check out a release tag (`git checkout v5.0.0`) to keep the two
matched, or install the scripts with `gcm install --ref=main`.

## Without Docker

```bash
cd server
pip install -r requirements.txt
SESSION_COOKIE_SECURE=0 python app.py
```

With a `.env`, load it first: `set -a; . ../.env; set +a`.

`SESSION_COOKIE_SECURE=0` is needed to sign in over plain HTTP; see
[Exposing it beyond your LAN](#exposing-it-beyond-your-lan).

## Configuration

Environment variables (set in `.env` for Docker; all optional).

The ones you're most likely to set:

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | generated | Shared secret for the in-game scripts, 32+ characters. Unset, one is generated into `DATA_DIR/api_key` on first start. |
| `PUBLIC_URL` | empty | The page's address from outside, e.g. `https://gtnh.example.com`, for the Connect a computer command, and icons and links in webhook posts. |
| `GTNH_VERSION` | empty | Game data version to install at startup; see [Game data](#game-data). |
| `GTNH_TEXTURES` | empty | Icon textures for `GTNH_VERSION`: `default` or `faithful32`. Empty keeps what was picked on the page. |
| `WEBHOOK_URLS` | empty | Comma-separated webhook URLs for notifications; see [Webhooks](using.md#webhooks). |
| `WEBHOOK_EVENTS` | `crafts,autocraft` | Which events go to them: `crafts`, `autocraft`, `stock`. |
| `SESSION_COOKIE_SECURE` | `1` (`0` in Compose) | Mark the session cookie HTTPS-only. |
| `TRUSTED_PROXIES` | empty | Comma-separated IPs/CIDRs allowed to send `X-Forwarded-*` headers. |
| `PORT` | `8420` | Listening port. |
| `DATA_DIR` | `server/data` | Where the databases and game data are kept. |

Everything else:

| Variable | Default | Description |
|---|---|---|
| `SESSION_LIFETIME_SECONDS` | `604800` (7 days) | Browser session length. |
| `STALE_AFTER_SECONDS` | `30` | How old crafting data can get before the page marks it stale. |
| `VAPID_SUBJECT` | the page's URL | Contact given to push services with each notification: a `mailto:` or `https:` URL. |
| `AUTOCRAFT_KEEP_IDLE_CPUS` | `1` | Keep-in-stock crafts only start while more crafting CPUs than this are idle; see [Stock rules](using.md#stock-rules). |
| `AUTOCRAFT_RETRY_SECONDS` | `1800` | How long a keep-in-stock target waits after a failed craft before trying again. |
| `CRAFT_STALL_SECONDS` | `600` | How long a step of a running job can be crafting without moving before its live tree calls it stuck. |
| `HISTORY_HOURLY_AFTER_DAYS` | `30` | Stock history older than this keeps one value per item per hour. `0`: off. |
| `HISTORY_DAILY_AFTER_DAYS` | `365` | ...and older than this, one per day. `0`: off. |
| `MAX_SCAN_ITEMS` | `100000` | Most items (each NBT variant counts) one network scan may send; a bigger scan is refused, and the Status dialog says so. |
| `MAX_SCAN_PATTERNS` | `25000` | Most patterns one pattern scan may send. |
| `CHART_CACHE_TTL_SECONDS` | `60` | Cache time for chart PNGs. |
| `CHART_CACHE_MAX_ENTRIES` | `64` | Chart PNGs kept in the cache. |
| `CHART_RATE_LIMIT_PER_MINUTE` | `30` | Chart PNG requests allowed per client per minute. |
| `CHART_MAX_TRACKED_CLIENTS` | `4096` | Clients the chart rate limit keeps track of at once. |
| `GAMEDATA_REPO` | `Maselkov/gtnh-craft-monitor-data` | Repo whose `gtnh-data-*` releases the Game data page offers. |
| `GAMEDATA_SHARED_DIR` | empty | Bundles shared by several servers, read without downloading; see [Game data](#game-data). |
| `GAMEDATA_API_URL` | `https://api.github.com` | GitHub API base for that list (for mirrors). |
| `SCRIPTS_REPO` | `Maselkov/gtnh-craft-monitor` | Repo whose newest `vX.Y.Z` tag the Status dialog offers as an update. |
| `GCM_VERSION` | the image's release | This server's release (`vX.Y.Z`), shown in the Status dialog. The published image sets it; set it yourself when building from a release tag if you want **Update computers when the server updates**. |

## Exposing it beyond your LAN

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
another device (see [Pins and notifications](using.md#pins-and-notifications)).

## User accounts

Browser users sign in with a personal access token, which is exchanged for an
`HttpOnly` session cookie. The server stores only a SHA-256 hash of each
token. Pins, completions, and craft history belong to the user, not the token,
so a token can be replaced without losing data.

| Role | Can |
|---|---|
| Viewer | View everything; keep personal pins and completion history |
| Operator | Also request and cancel crafts |
| Admin | Also manage users and tokens |

The first admin is made with the setup link from the server's log (see the
[Quick start](../README.md#quick-start)). Opening the page without it shows
the same dialog; paste the code from the link there. Once signed in,
**Manage users** is available for creating accounts. Consider creating a
second admin as a backup.

On a server you can't open a browser to, create the first admin from the
command line instead. It prints the token:

```bash
docker compose exec gtnh-craft-monitor python app.py create-admin "Your name"
```

Managing users:

- Each generated token is shown once. Send it to its owner over a secure
  channel.
- **Revoke** invalidates a token and ends its sessions immediately.
- **New token** replaces a user's current token in one step.
- **Delete** permanently removes a user with their tokens, sessions,
  pins and pending notifications. Their craft request history is kept.

### Private servers

By default anyone who can open the page sees the crafts, the network and
the power; signing in only adds pins, requests and the admin tools. For a
server reachable from the internet, an admin can tick **Only signed-in
users can see this base** in **Manage users**. Then:

- Signed out, the page shows only a sign-in box, and the server answers
  every request for the crafts, network or power with 401. Give each
  person who should see it a token; a viewer can see everything, as
  before.
- Links to the server show no preview when pasted into Discord or
  elsewhere: the preview is fetched signed out, so it says only "GTNH
  Monitor".
- The in-game scripts report as before; they use the API key.

The same from the command line:

```bash
docker compose exec gtnh-craft-monitor python app.py visibility private   # or public
```

If you lose a token and no admin can sign in, issue a new one from the
server. This revokes the user's existing tokens and sessions and prints the
new token:

```bash
docker compose exec gtnh-craft-monitor python app.py new-token "Administrator"
```

## Backups

The databases are in `data/` (`app.db` for accounts, pins and craft
history; `power.db`; `item_history.db`). They run in SQLite's WAL mode, so
recent changes can sit in the `*.db-wal` files for a while: copying the `.db`
files from a running server can miss data. Instead, take a consistent copy
while the server keeps running, in either of two ways:

- **Settings (⚙) → Download backup** (admins) makes a zip of every
  database, `vapid_private.pem`, the key that signs notifications, and
  `api_key`, the generated API key, and downloads it. It's made beside the
  databases first, so it needs about their size in free space there; it's
  deleted once downloaded, or after 10 minutes.
- The built-in command writes the databases to a directory:

  ```bash
  docker compose exec gtnh-craft-monitor python app.py backup "/app/data/backup-$(date +%F)"
  ```

  It doesn't include `data/vapid_private.pem` or `data/api_key`. They
  never change, so copy them once alongside.

To restore, stop the container, replace the `.db` file in `data/`
(unzipped, if from a download), delete that file's `-wal` and `-shm` files if
present, and start it again. WAL mode needs `data/` on a local disk,
not a network share.

## Game data

Item icons and the network scanner's item list depend on the GTNH version
your players run. Until a version is picked, the server has no item icons
and the network scanner has no item list, so the Network tab stays empty.

An admin picks it once: **Settings (⚙) → Game data**, choose the version
and the icon textures, **Use these icons**. The server downloads that
version's game data (about 300 MB: the icons, the icon lookup and the item
catalog), switches to it without a restart, and the in-game scanner fetches
the new item catalog on its next scan. Versions and textures downloaded
before can be switched back to instantly. When a version is rebuilt (say,
with better icons), the page marks it **update available**; picking it
again downloads the new build.

The first time a version goes live (and once after updating to a server
that does this), the server spends a few seconds indexing its game data,
so it can read icons, item descriptions and the ore dictionary from disk instead of holding
them in memory. A server with a GTNH 2.9 bundle and a big network uses
about 55 MB of RAM.

To pin the version without the page, set `GTNH_VERSION` (e.g.
`2.9.0-beta-3`), and optionally `GTNH_TEXTURES=faithful32`; the server
installs it at startup if it isn't already.

Running several servers on one machine? Set `GAMEDATA_SHARED_DIR` on each to
one directory of bundles, laid out like `DATA_DIR/gamedata` (a
`<version>/` folder per version, e.g. copied from a server that downloaded
it). A version found there is used without downloading, and the servers
never write to or delete from it, so it can be mounted read-only. Picking
textures the shared copy lacks downloads the whole version into that
server's own `DATA_DIR/gamedata`. Shared versions are never marked **update
available**: replace them there when a version is rebuilt. Then index each
one there (`python app.py index-gamedata <bundle directory>`, a couple of
seconds), or every server builds its own copy of the index the first time
it uses that version.

Icons come in two texture sets: the pack's own (**Default**), and
**Faithful 32x**, rendered with the
[GTNH Faithful x32 resource pack](https://github.com/Ethryan/GTNH-Faithful-Textures)
by Ethryan and contributors. Faithful icons are rendered with the newest
release of that pack when the data is built, which may be newer than the
one made for your GTNH version.

Icons that animate in game (GT materials and fluids, lava, many magic
items) are animated on the page too. Browsers set to reduce motion get
still icons. Halos (GT's cosmic items, Avaritia's singularities and
infinity items) spill past their slot the way they do in game.

The list comes from the releases of a separate repo,
[gtnh-craft-monitor-data](https://github.com/Maselkov/gtnh-craft-monitor-data):
a workflow here ([`gtnh-data.yml`](../.github/workflows/gtnh-data.yml)) checks
GTNH's releases daily and publishes game data there for every new stable,
beta and RC release, tagged `gtnh-data-<version>`. The icons are renders of
Minecraft's and the mods' textures, which aren't this project's, so they're
kept apart from the code; requests to remove something go to that repo's
issues. The server needs to reach `api.github.com` and `github.com` to list
and download them.

For a pack that isn't covered (a nightly, a modified pack), or if you'd
rather not use prebuilt data, build it yourself with
[`tools/icon-export`](../tools/icon-export/README.md).
