# In-game setup

The OpenComputers side: what the scripts need, installing and updating
them, and their settings. The [Quick start](../README.md#quick-start)
covers the usual install from the web page.

- [Requirements](#requirements)
- [Install](#install)
- [Update](#update)
- [Manual install](#manual-install)
- [Run the scripts](#run-the-scripts)
- [Script settings](#script-settings)

## Requirements

### The computer

Each computer that runs the scripts needs:

| Part | Requirement |
|---|---|
| Computer Case | Tier 2 or better, so it takes Tier 2 memory |
| EEPROM | **Lua BIOS** |
| CPU | Any tier, or an **APU** in its place (an APU has graphics built in, so no Graphics Card is needed) |
| Graphics Card | Any tier; not needed with an APU |
| Memory | At least **2× Tier 2**; more or higher tiers are better, see below |
| Internet Card | Required: it's how the scripts reach the server |
| Hard Disk Drive | Any tier, with OpenOS installed on it |
| Screen and Keyboard | To install and watch the scripts |

To install OpenOS, put the **OpenOS floppy disk** in a Disk Drive next to
the computer (a Tier 3 case has one built in), boot from it and run
`install`, choosing the hard disk. The floppy is read-only, so the scripts
can't be installed onto it.

Memory is shared by every script on the computer. `network_browser.lua`
uses the most, and grows with the size of your ME network: on a large
network give it Tier 3 or 3.5 memory, or a computer of its own. If two
sticks still aren't enough on a big base, use a **Server** in a **Server
Rack** instead of a case: a Tier 2 server holds 3 memory sticks and a
Tier 3 server 4, where a case holds 2. It takes the same parts otherwise.
The most a scan holds at once is usually every stored stack of one big
GregTech ID, such as `gt.metaitem.01` (see
[Scanning the network](how-it-works.md#scanning-the-network)).
`craft_monitor.lua` grows with the crafts running: each report carries
every busy CPU's lists, so several big crafts at once take room too.
`network_browser_debug.log` records free memory (`mem=`) at each step and
how many stacks each call returned, so the lowest numbers there show how
close it gets and which call it was.

### Adapters

Adapters for what each script reads:

| Script | Needs an Adapter on | For |
|---|---|---|
| `craft_monitor.lua` | the ME Controller (any face), or an ME Interface if there is no controller | Crafts tab, crafting from the page |
| `network_browser.lua` | the ME Controller, as above | Network tab, stock rules |
| `network_browser.lua` (optional) | a block holding an **ME Interface Terminal** part | the crafting plan and the craft tree (see [ME Interface Terminal](#me-interface-terminal)) |
| `power_monitor.lua` | any casing block of the GregTech multiblock, one per machine with [`MACHINES`](#power_monitorlua) | Power tab |

The scripts are independent: they can run on one computer or on separate
ones. They share a computer's memory, so on a large network a separate
computer for `network_browser.lua` leaves it more room. Each computer
needs an Internet Card and the Adapters for the scripts it runs, so if
`craft_monitor` and `network_browser` run on different computers, both
need one on the ME Controller. One Adapter can serve several rows if it
touches several of the blocks, and an MFU in an Adapter stands in for
touching a block (see [Connecting the Adapter](#connecting-the-adapter)).
Connect the Adapters with OpenComputers cable, not AE2's OpenComputers P2P
tunnels.

The rest:

- HTTP allowed in the Minecraft server's `OpenComputers.cfg` (`enableHttp`
  and `enableHttpHeaders`, on by default), and your monitor's address not
  blocked there. Local addresses are blocked by default; see
  [Scripts can't reach the server](../README.md#scripts-cant-reach-the-server).
  If the scripts can't POST, nothing on the Lua side can fix it. The
  installer also needs `raw.githubusercontent.com` and `api.github.com`.
- For craft requests, GTNH 2.9.0-beta-2 or later: they use OpenComputers'
  `getCraftable()`, added in 1.12.47.
- `allowItemStackNBTTags=true` (under `integration.vanilla`) in
  `OpenComputers.cfg`, which is GTNH's default. Items that share an id but
  differ in NBT (seeds with different stats, bees of one species) are then
  listed separately, as AE2 does. Without it they're only told apart by
  name, GregTech tools (turbines, drills...) all show one icon whatever
  their material, and patterns lose their crafting-table output counts and
  substitution settings.

### Crafting CPUs

Put an AE2 **Crafting Monitor** in each crafting CPU's multiblock. It's
the only way the scripts can ask a CPU what it's making. A CPU without
one still shows its progress and ingredients, but:

- its card says "Unknown output" instead of the item, and has no icon
  or link to the item's history
- its job has no **Tree** view
- a job that starts right after another ends isn't noticed as new, so a
  pin on the first carries over to the second
- cancelling from the page can't check it's still the job you saw
- when several crafts start at once, a request from the page can't be
  matched to its CPU, so it isn't pinned for you

A job that has only just started can say "Starting job…" for a few
seconds even with a monitor. "Unknown output" means the CPU really has
no monitor.

### ME Interface Terminal

Optional, but it's what the page reads your patterns through. With an
Adapter touching a block that holds an ME Interface Terminal part (often
the cable the terminal sits on), connected to the computer running
`network_browser.lua`, you get:

- the **crafting plan** in the craft dialog: what a craft takes and
  what's missing, before you request it
- the **Tree** view of a running job on the Crafts tab, step by step
- stuck steps called out, with the machine they're waiting on

Patterns are first read after the next network scan, then every
[`PATTERN_SCAN_INTERVAL_SECONDS`](#network_browserlua) (an hour by default).

Without one, the network scan tells the server so. The craft dialog and
the Tree view then say a terminal is needed, the Network tab shows admins
a note about it until one of them closes it, and **Status** lists Patterns as
"no ME Interface Terminal connected (optional)".

### Connecting the Adapter

An Adapter doesn't have to touch the ME Controller. Put an **MFU** in the
Adapter's upgrade slot instead. Link it by sneak-right-clicking a face of the
block, and the Adapter then works as if it touched that face, wirelessly. That
way no controller face is used up. The MFU only reaches **16 blocks**, so keep
the Adapter within that range of the block. It works the same way for the
power monitor's Adapter and a GregTech multiblock.

Run OpenComputers cable between the computer and its Adapters. Don't carry
the connection through AE2's **OpenComputers P2P tunnels**: they're buggy.
Components go missing from the computer, and the shell fills up with the
scripts' crashes.

## Install

`gcm` installs the scripts from the latest release and updates them later.
Download it to `/tmp` once and run it from there; `gcm install` installs
itself to `/usr/bin`, so afterwards it runs as plain `gcm`:

```
wget -f https://raw.githubusercontent.com/Maselkov/gtnh-craft-monitor/main/oc/gcm.lua /tmp/gcm.lua
/tmp/gcm.lua install --server=<url> --api-key=<key>
```

**Settings (⚙) → Status → Connect a computer** on the web page shows
both as one command, joined with `&&`, with this server's address and API
key filled in, ready to copy. Paste it into the computer with the middle
mouse button or Insert.
The address is the page's own unless `PUBLIC_URL` is set; if the
Minecraft server reaches the monitor some other way, press **Use a
different address** there and type it in.

`gcm install` asks which scripts to install, and warns when a chosen
script's block isn't connected: an Adapter touching an ME controller or
interface for craft and network, or a GregTech machine for power. For
network it also notes when there's no optional ME Interface Terminal. The
script is installed anyway, so the Adapter can go in later. Then it checks that the
computer reaches the server with the key, saying what's wrong if it
doesn't, and writes both to `/home/config.lua`. Without `--server` and
`--api-key` it asks for them. It can also enable the scripts so they start
on every boot, and offers to reboot to start them.

To install specific scripts without being asked, name them:

```
gcm install craft power network
```

Run `gcm install` again later to add scripts. If `/home/config.lua`
already exists, `--server` and `--api-key` replace the address and key in
it and leave the rest alone, so to move a computer to a new address or key,
paste the Connect a computer command again. Without them the file isn't
touched.

## Update

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
changes. Releases older than `gcm` itself can't be installed with it. Keep
the scripts on the same release as the server (see
[Updating](../README.md#updating)).

### From the web page

`gcm` also installs and enables `gcm_agent`, a small service that reports
the computer to the web page every minute. Admins see each computer under
**Settings (⚙) → Status**: its version and whether a newer release
is out, which scripts are running, free memory and uptime. Buttons there
update it (to the latest release, or to `main`) or reboot it. The agent
runs `gcm update`, says how it went, and reboots. The card says it's
rebooting until the computer reports in again (usually within a few
seconds), then shows "back up" for 10 minutes. If the computer doesn't
report in within 3 minutes, the card says so.

Two checkboxes there apply to every computer:

- **Restart scripts that stop** (on by default): when a script enabled with
  `rc <name> enable` stops on its own, usually after a crash, the agent
  starts it again within a minute. It waits 2 minutes after boot so rc can
  start the scripts first, and gives up on a script after 3 restarts in an
  hour; the card shows how many times each one was restarted. A script you
  stop with `rc <name> stop` stays stopped; `rc <name> disable` keeps it
  off after reboots too. The agent remembers the setting while the server
  is unreachable.
- **Update computers when the server updates** (off by default): a
  computer on an older release than the server is updated to the server's
  release and rebooted, once per release. It only does this while the
  server's release is also the latest one, and only with a server that
  knows its release (the published Docker image does).

When a release changes the in-game scripts, admins don't have to open
Status to find out. A notice at the top of every tab says how many
computers run scripts older than the server needs, with a button that
opens Status at the computers and a link to that release's notes. Those
computers' cards say "update recommended". A release that only changed
the server doesn't bring the notice up. Neither does a computer that's
being updated, or one that **Update computers when the server updates**
is about to update; if that update fails, the notice shows. Closing the
notice hides it, for every admin, until the next release that changes
the scripts. It only
knows about computers running `gcm_agent`, and needs the published Docker
image (see `GCM_SCRIPTS_VERSION` in [server.md](server.md)).

The server can only ask for those three things. `gcm` downloads from its
own fixed GitHub repository, so nothing on the server can make a computer
run other code. A computer installed before the agent existed shows up
after one `gcm update` in game. `rc gcm_agent disable` turns it off.

## Manual install

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

Then set `SERVER_URL` (no trailing slash) and `API_KEY` (the server's API
key; see [Docker](server.md#docker)) in `/home/config.lua`.

`http.lua` and `json.lua` go in `/usr/lib/` so `require()` finds them from
any script. `config.lua` is loaded by absolute path; to keep it somewhere
other than `/home/`, change `CONFIG_PATH` at the top of each script.

## Run the scripts

Each script is an OpenOS `rc` service:

```
rc craft_monitor enable     # start automatically on every boot
rc craft_monitor start      # start now
rc craft_monitor stop
rc craft_monitor status
```

`start` alone lasts only until the next reboot. The same commands work for
`power_monitor` and `network_browser`.

## Script settings

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

The scripts don't draw to the screen by default (`SHOW_STATUS = false`), and
they print only when something's wrong (a missing component or card), so
after boot the screen shows just the shell prompt. `rc <name> status` says
whether a script is running. If
you turn on a status screen, enable it for only one script per screen: each
one clears the terminal on every cycle, so two would flicker.

### craft_monitor.lua

| Setting | Default | Description |
|---|---|---|
| `POLL_SECONDS` | `5` | How often crafting CPUs are read and posted |
| `WATCH_SECONDS` | `1` | Between those, how often busy CPUs are checked, so a job's end is noticed sooner; `0` turns it off |
| `CRAFT_REQUEST_POLL_SECONDS` | `1.5` | How often pending craft requests are checked |
| `WATCH_REQUEST_OUTCOMES` | `true` | After a requested craft starts, report whether it finished or was cancelled |
| `COMPONENT` | `nil` (auto) | Force `"me_controller"` or `"me_interface"` |
| `HTTP_TIMEOUT_SECONDS` | `20` | Give up on a request to the server that stalls this long |
| `SHOW_STATUS` | `false` | Draw a compact status screen |
| `VERBOSE` | `false` | Print one line per CPU per cycle, for debugging |
| `DEBUG_DUMP` | `false` | Print the raw `getCpus()` data once and exit |
| `DEBUG_CPU_FILTER` | `nil` | Limit `DEBUG_DUMP` to one CPU by name |

`DEBUG_DUMP` is useful on first setup to check what the script sees:
set `craft_monitor = { DEBUG_DUMP = true }` in `config.lua`, reboot, read
the output, then remove it again.

### power_monitor.lua

| Setting | Default | Description |
|---|---|---|
| `POLL_SECONDS` | `60` | How often stored EU is read and posted |
| `COMPONENT_ADDRESS` | `nil` | Pick a specific `gt_machine` if several are connected |
| `MACHINES` | `nil` | Report several machines instead, each with its own chart: see below |
| `WIRELESS_NAME` | `"Wireless"` | Name of the wireless EU network's chart; `false` leaves it out. See below |
| `HTTP_TIMEOUT_SECONDS` | `20` | Give up on a request to the server that stalls this long |
| `SHOW_STATUS` | `false` | Draw a status screen |

To chart several machines (say an LSC and a bank of battery buffers), list
them with a name each:

```lua
power_monitor = {
  MACHINES = {
    { address = "1f3a..." },                      -- no name: keeps the existing chart
    { address = "9c02...", name = "Batteries" },
  },
},
```

Each is read and posted every poll. A machine without a name keeps the
history recorded before `MACHINES` was set, so leave the one you were
already charting unnamed. The addresses are the ones `component.list("gt_machine")`
prints, or that the script lists when it finds several.

An LSC also reports your wireless EU network (your team's, if you're in
one). Once the network holds any EU, it gets its own chart, named
`WIRELESS_NAME`, with no Adapter or setting needed. That holds whether the
LSC's own wireless mode is on or off. A base that doesn't use wireless
never sees it.

### network_browser.lua

| Setting | Default | Description |
|---|---|---|
| `SCAN_INTERVAL_SECONDS` | `600` | Time between full network scans |
| `WATCH_INTERVAL_SECONDS` | `60` | Between full scans, how often to check just the items with a [stock rule](using.md#stock-rules); `0` turns it off |
| `PATTERN_SCAN_INTERVAL_SECONDS` | `3600` | How often to [read every pattern](how-it-works.md#reading-patterns), if an ME Interface Terminal is connected; `0` turns it off |
| `PATTERN_CHUNK_SIZE` | `20` | Patterns sent to the server per POST |
| `CRASH_RESTART_SECONDS` | `30` | After an error stops scanning, how long to wait before starting over |
| `CATALOG_PATH` | `/home/item_catalog.txt` | Item catalog to use when the server has no game data |
| `BATCH_SIZE` | `100` | Most item IDs queried per call; the server's catalog ends calls sooner around [big IDs](how-it-works.md#scanning-the-network). Each call's result is held in memory until it's sent, so lower uses less |
| `WATCH_BATCH_SIZE` | `1` | Item IDs queried per call when checking stock rules |
| `RESULT_CHUNK_SIZE` | `100` | Items sent to the server per POST |
| `DELAY_BETWEEN_BATCHES_SECONDS` | `0.1` | Pause between batches |
| `MAX_CONSECUTIVE_ERRORS` | `5` | Batches in a row that may fail before the scan is abandoned |
| `HTTP_TIMEOUT_SECONDS` | `20` | Give up on a request to the server that stalls this long |
| `SHOW_STATUS` | `false` | Draw a status screen |

A full scan takes about 165 batches with the GTNH catalog, which can take 10
minutes or more on a big network, and runs every 10 minutes by default.

`item_catalog.txt` lists about 11,000 item IDs (`modid:internalname`, damage
values merged): everything in NEI's item list, including items NEI hides,
plus anything in the ore dictionary or a recipe. It comes from the server:
once a GTNH version is picked on the [Game data](server.md#game-data) page,
the scanner downloads that version's catalog at the start of its next scan
into `/home/item_catalog.server.txt` and uses that from then on, until the
version changes again (the version is noted in
`/home/item_catalog.server.txt.version`). If the download fails it keeps
scanning with the catalog it has. A server without game data sends no
catalog; the scanner then uses `CATALOG_PATH` if you've put one there, and
otherwise reports that no version is picked.

If an error stops a scan, the script logs it to `network_browser_debug.log`
in the directory it runs from; see
[Scanning the network](how-it-works.md#scanning-the-network).

### gcm_agent.lua

In a `gcm_agent` table:

| Setting | Default | Description |
|---|---|---|
| `POLL_SECONDS` | `60` | How often it reports, and so how soon a button on the page takes effect |
| `RESTART_GRACE_SECONDS` | `120` | How long after boot it waits before restarting a stopped script |
| `MAX_RESTARTS` | `3` | Most restarts of one script in an hour before it gives up |
