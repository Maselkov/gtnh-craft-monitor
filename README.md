# GTNH Craft Monitor

A web dashboard for a GregTech: New Horizons base. OpenComputers scripts
running in-game read your AE2 network and GregTech machines and report to a
small Flask server, which serves a live page you can open from any browser.
Basically, it's web AE2 for your base.

[demo.webm](https://github.com/user-attachments/assets/8aff745b-3819-45f4-a624-8a0846b361a2)

## Features

- **[Crafts](docs/using.md#crafts-tab)** — every AE2 crafting CPU with its
  current job, output item, progress, and a live grid of its ingredients or
  a live tree of its steps that points out the ones that are stuck. Pin a
  job to get a notification when it finishes. Recent crafts lists every job
  that ended.
- **[Power](docs/using.md#power-tab)** — stored EU of a GregTech multiblock
  (e.g. a Lapotronic Super Capacitor) charted over time, with its EU/s and
  the time until it's full or empty.
- **[Network](docs/using.md#network-tab)** — sortable list of every item,
  fluid and essentia in the ME network, with NEI-style search and per-item
  stock history charts that show how fast an item is running out.
- **[Remote crafting](docs/using.md#requesting-and-cancelling-crafts)** —
  request or cancel AE2 crafts from the browser, with a preview of the
  crafting plan.
- **[Stock rules](docs/using.md#stock-rules)** — get a notification when an
  item runs low, or have the server queue a craft to keep it stocked.
- **[Webhooks](docs/using.md#webhooks)** — finished pinned crafts and failed
  auto-crafts posted to a Discord channel or any other webhook.
- **[Accounts](docs/server.md#user-accounts)** — per-user tokens with
  viewer, operator, and admin roles, and a
  [private](docs/server.md#private-servers) setting so only signed-in
  users see anything.

## What you need

- A machine with Docker (amd64 or arm64) that the Minecraft server can
  reach over HTTP.
- In game, an OpenComputers computer (Tier 2 case or better) with a Lua
  BIOS EEPROM, a CPU and Graphics Card or an APU, at least 2× Tier 2
  memory (more is better), an **Internet Card**, and a hard disk with
  OpenOS installed from the OpenOS floppy. See
  [The computer](docs/in-game.md#the-computer) for details. It also needs
  Adapters connected to it (touching the block, or linked with an MFU):
  - **Crafts and Network tabs:** an Adapter on the ME Controller.
  - **Power tab:** an Adapter on any casing of the GregTech multiblock.
  - **Crafting plan and craft tree** (optional): an Adapter on a block
    holding an ME Interface Terminal part.

  Connect the Adapters with OpenComputers cable, not AE2's OpenComputers
  P2P tunnels: those make components go missing. One computer can run
  everything, or the scripts can be spread over several, each with the
  Adapters for its own scripts.
- An AE2 **Crafting Monitor** in each crafting CPU, so the page can show
  what it's making (see [Crafting CPUs](docs/in-game.md#crafting-cpus)).
- GTNH 2.9.0-beta-2 or later for crafting from the page.

The full list, including the OpenComputers config settings, is under
[Requirements](docs/in-game.md#requirements).

## Quick start

1. On the server, in an empty directory, download the Compose file and start
   it:
   ```bash
   curl -O https://raw.githubusercontent.com/Maselkov/gtnh-craft-monitor/main/deploy/docker-compose.yml
   docker compose up -d
   ```
2. Run `docker compose logs gtnh-craft-monitor` and open the setup link it
   printed, `http://<your-host>:8420/#setup=...`. Pick a name, and save the
   access token it shows (a password manager will offer to): that's how you
   sign in from now on, as the first admin. The link works once, and a
   restart prints a new one.
3. Pick your GTNH version under **Settings (⚙) → Game data**. Until then
   there are no item icons and the Network tab stays empty.
4. Open **Settings (⚙) → Status → Connect a computer** (each tab links
   there until its script reports). Check the address in the command is
   one the Minecraft server can reach (**Use a different address** changes
   it), then paste the command into that computer (each one, if you use
   several). It asks which scripts to install, checks that the computer
   can reach the server, and can start the scripts on every boot. If it can't reach it, see
   [Scripts can't reach the server](#scripts-cant-reach-the-server).

The page shows "waiting for data" until the scripts report. For settings,
HTTPS, backups and running without Docker, see the
[server guide](docs/server.md).

## Updating

```bash
docker compose pull
docker compose up -d
```

Then update the in-game scripts to the same release: each computer's
**Update** button under **Settings (⚙) → Status**, or `gcm update` in
game. Ticking **Update computers when the server updates** there does
this for you from then on. To stay on one release, set `GCM_TAG` in `.env` to its version,
e.g. `GCM_TAG=5.0.0`.

## Troubleshooting

### Scripts can't reach the server

The scripts' requests come from the **Minecraft server**, not from your PC,
so `SERVER_URL` must be an address the Minecraft server can reach. And by
default OpenComputers refuses to connect to local addresses at all. Its
`config/OpenComputers.cfg` has, under `internet`:

```
blacklist=[
  "127.0.0.0/8",
  "10.0.0.0/8",
  "192.168.0.0/16",
  "172.16.0.0/12"
]
```

If the monitor runs on the same machine as Minecraft, or on your LAN, remove
the range its address is in (or empty the list) and restart Minecraft. In
single player, the "Minecraft server" is your own game, and the same applies.
Also check that `enableHttp` and `enableHttpHeaders` are `true` there.

### The Network tab is empty

No GTNH version is picked yet: an admin picks one under **Settings (⚙) →
Game data** (see [Game data](docs/server.md#game-data)). The scanner fetches
the item list on its next scan.

### Something is late or stopped

Admins can see every feed from the game, and every in-game computer, under
**Settings (⚙) → Status** (see [Server status](docs/using.md#server-status)).
Then, in game:

- `rc craft_monitor status` (or `power_monitor`, `network_browser`) says
  whether a script is running.
- `network_browser.lua` logs errors and its memory use to
  `network_browser_debug.log` in the directory it runs from.
- To see what `craft_monitor.lua` reads, set
  `craft_monitor = { DEBUG_DUMP = true }` in `/home/config.lua` and reboot
  (see [Script settings](docs/in-game.md#craft_monitorlua)).
- Components that go missing, and a shell full of the scripts' crashes,
  usually mean the computer reaches its Adapters through AE2's OpenComputers
  P2P tunnels. Use OpenComputers cable or an MFU instead (see
  [Connecting the Adapter](docs/in-game.md#connecting-the-adapter)).

### Lost access token

Another admin can issue a new one under **Manage users**. If no admin can
sign in, issue one from the server; it revokes the user's existing tokens
and prints the new one:

```bash
docker compose exec gtnh-craft-monitor python app.py new-token "Your name"
```

## Documentation

- [Server setup](docs/server.md) — Docker, configuration, HTTPS, accounts,
  backups, game data
- [In-game setup](docs/in-game.md) — requirements, installing and updating
  the scripts, their settings
- [Using the web page](docs/using.md) — every tab, search syntax, crafting,
  stock rules, notifications, webhooks
- [How it works](docs/how-it-works.md) — how the pieces connect, and what
  the scripts read from AE2 and GregTech, and why
- [Icon export](tools/icon-export/README.md) — building game data for
  another pack
- [Contributing](CONTRIBUTING.md) — running it locally and the tests

## License

Licensed under the GNU General Public License v3.0 or later. See
[LICENSE](LICENSE).

### Credits

The Faithful 32x item icons in the published game data are rendered with the
[GTNH Faithful x32 textures](https://github.com/Ethryan/GTNH-Faithful-Textures)
by Ethryan and contributors, which build on the
[Faithful](https://faithfulpack.net/) team's Classic Faithful 32x textures
and Magnetanide's Stellar Fusion. The textures are theirs, not covered by
this project's license. Thanks to all of them. See the
[data repo's notice](https://github.com/Maselkov/gtnh-craft-monitor-data#notice)
for the rest of the published game data.

The network scan's approach comes from
[oc-influxdb-exporter](https://gitlab.com/uncountablyinfinite1056/oc-influxdb-exporter),
and the icon export's headless client launch follows
[gtnh-factory-flow](https://github.com/jackwrichards/gtnh-factory-flow) (MIT).
