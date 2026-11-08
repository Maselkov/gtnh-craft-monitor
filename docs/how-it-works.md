# How it works

These notes explain design choices in the scripts and the server, and were
verified against the GTNH OpenComputers source and live testing.

- [How it fits together](#how-it-fits-together)
- [Crafting CPUs](#crafting-cpus)
- [Craft request results](#craft-request-results)
- [Scanning the network](#scanning-the-network)
- [Reading patterns](#reading-patterns)
- [Game recipe data](#game-recipe-data)
- [The crafting plan](#the-crafting-plan)
- [The craft tree](#the-craft-tree)
- [Held-up steps](#held-up-steps)
- [Fluid items](#fluid-items)
- [Reading EU](#reading-eu)

## How it fits together

```
 Minecraft (OpenComputers)                       Server (Docker)
┌───────────────────────────┐    HTTP POST    ┌──────────────────────┐
│ craft_monitor.lua         │ ──────────────▶ │ server/ (Flask)      │ ◀── browser
│ power_monitor.lua         │ ◀────────────── │ SQLite in data/      │
│ network_browser.lua       │  polled requests└──────────────────────┘
└───────────────────────────┘
```

AI assistants reach the same data through `/mcp`, an MCP endpoint
([AI assistants](using.md#ai-assistants)). Its tools call the code the
page's routes do, with the signed-in user's role.

The game always initiates the connection: scripts POST their readings and
poll the server for pending craft requests and cancellations. The server
never connects into the game.

## Game recipe data

Game data bundles include recipes exported from the selected GTNH client pack.
Format 2 adds supported crafting-table and furnace recipes to the GregTech maps;
format 1 has GregTech only. Format 3 adds native Assembly Line recipes/research,
GT display maps, NEI display recipes and an audit of registered handlers. Format 4
adds exact display-stack references, resolved GUI textures, slot geometry, aspect
names and static research metadata. The server
reads source formats 1–4 into a read-only SQLite index (format 5), normally built beside shared game data to avoid rebuilding it
on each hosted instance. The builder reads two passes of the compressed file,
keeping intern tables and one recipe in memory instead of the full JSON. The
[exporter guide](../tools/icon-export/README.md#bundle-format) describes the schema.

Crafting recipes retain grid holes and every ingredient alternative. The input
index lists a recipe under each alternative; concrete item queries also find
wildcard-damage ingredients. Queries deduplicate recipe IDs before counting and
paging, with a stable order: crafting, furnace, then GregTech by absolute EU/t
and duration (including Assembly Line), with approximate NEI displays last and recipe
ID for ties. Each index exposes its source fingerprint
so consumers can keep pages within the same bundle generation.

The [recipe viewer](using.md#recipes-and-uses) and assistant read a bounded sample of each slot's alternatives plus the full
count. NBT variants and custom matching/returned-container behavior are not
fully represented, and dynamic handlers can be absent. Coverage and approximation
flags accompany results. A mandatory slot with no alternatives excludes the
recipe rather than becoming an empty grid cell. The processing-pattern check
accepts only native GT processing recipes in matching, fallback recipes and uses/removal
classification, so adding these maps does not change what the check promises.

Category navigation uses accessible icon tabs over the existing map filter.
The first available machine icon represents a category; unknown icons fall back
to initials. Overflow controls and selected-tab reveal use the tab row's actual
width. Manual keyboard activation keeps focus in the row while asynchronous
recipe requests run. Category metadata stays available during filtering, while
old recipe rows are cleared and requests remain generation-guarded.

The viewer's read-only `/api/recipes/item` and `/api/recipes` routes follow the
base's public/private viewer policy. They return the selected version, recipe
fingerprint and icon-data version. Page requests pin those values; a change
returns a conflict and the viewer reloads rather than appending mixed pages.
Missing selection, missing recipes and index-build failures have distinct errors.
Browser history stores each selection's key, direction, category and originating
tab/dialogs; loaded pages and scroll stay in a bounded in-page cache. Back,
Forward and breadcrumb jumps restore that selection, while Close returns to
its origin. Request generations and abort signals discard late responses after
navigation. Alternative icons cycle only while visible and motion is allowed;
focus or hover freezes the slot's icon, label and clicked identity together.
Recipe entries include cached, page-local item descriptions from the same bundle
lookup used by Network. The views share tooltip formatting, including Shift text,
rarity and mod name. The shared tooltip tracks its owning view, so background
Network refreshes cannot replace a recipe tooltip. Display variants resolve their
concrete tooltip identity separately from recipe navigation.
Display NBT controls the icon and label, while navigation keeps the recipe's
canonical key (including wildcard damage). Positioned previews render exported
slots and texture crops; auxiliary NEI stacks do not enter the consumed-input
index. Assembly Line research costs remain separate from production costs.
Infusion completion status is never inferred from static research metadata.

## Crafting CPUs

GTNH's AE2 integration has no `getCraftingCPUs()`. The method is
`getCpus()` (see `NetworkControl.scala` in `GTNewHorizons/OpenComputers`):

```
me.getCpus() -> array of { name, storage, coprocessors, busy, cpu }
```

Each `cpu` object provides `isActive()`, `isBusy()`, `activeItems()`,
`pendingItems()`, `storedItems()`, `finalOutput()`, and `cancel()`.
`finalOutput()` fails unless the CPU has a Crafting Monitor block.

`craft_monitor.lua` only reports these lists; the server works out progress
(`server/gcm/progress.py`). Every item or fluid type in `pendingItems()` and
`activeItems()` is a step, measured as its remaining amount against the
largest amount seen for it during the job, and the job's progress is the
average over steps. That has no units, so fluids in mB don't swamp items,
and thousands of cheap items don't outweigh one slow one. `storedItems()` is
left out because it also holds the raw ingredients pulled at the start, which
shrink as they're used and would make the bar go backwards.

## Craft request results

A request counts as finished when `isComputing()` returns `false`. `isDone()`
isn't used, because it only reports whether a crafting link was created. A
rejected request never creates one, so `isDone()` would stay `false` forever
in exactly the case where an error must be shown.

Fluids are requested by their Forge name, through the same `getCraftable()`.

Requesting an item that differs from others only in NBT crafts that
variant: `getCraftable()` finds the pattern making exactly that stack, NBT
included. The NBT comes from the last network scan, so a variant that hasn't
been scanned since the server started fails rather than crafting the wrong
item.

## Scanning the network

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

Each call returns every stored stack under the IDs it asks for, and the
script holds all of them until they're sent. The catalog is sorted, so
GregTech's biggest IDs sit next to each other: `gt.metaitem.01` (about 6,800
variants), `gt.blockmachines`, `gt.blockores2` to `7` and so on. With 100 IDs
per call they all landed in one, and that call ran a computer with two Tier
3.5 memory sticks out of memory. So the server puts a blank line in the
catalog it sends wherever a call would ask for more than 500 variants, counted
from the game data, and the script ends its batch there. An ID that big gets
a call of its own. So does any ID that had 200 or more stacks in the last
scan, which `scan/start` names: bees, crop seeds and enchanted books differ
only in NBT, so the game data counts each as one variant. One ID can't be
split further, so its stacks are the floor of what a scan needs. The script
turns each call's stacks into JSON before it sends any of them, so it
doesn't hold the bigger raw tables while it waits on the server. The debug
log records how many stacks each call returned.

Fluids are read with a single `getFluidsInNetwork()` call, since networks
hold far fewer fluid types than item types. Essentia likewise, with one
`getEssentiaInNetwork()` call: Thaumic Energistics adds it to the same
`me_controller`/`me_interface` component, and it names each aspect by its
tag (`ordo`), as the Interface Terminal does in patterns, so the crafting
plan finds essentia in stock. Without Thaumic Energistics there's no such
method and the scan skips it.

If an error escapes the scan loop (most likely the computer running out of
memory, which the three scripts share), `network_browser.lua` writes it, with
a stack traceback and free memory, to `/home/network_browser_debug.log`. It
also reports the error to the server, which ends the dead scan and shows the
error on the Network tab until a scan finishes.
Then it starts over after `CRASH_RESTART_SECONDS`. Each line of that log
records free memory (`mem=`), so a slow leak shows as a falling number.
Without this, the error was printed to a screen that other scripts keep
clearing, `rc network_browser status` still said running, and the page just
showed a scan that never finished.

On the server, a scan is a `start` → `batch` × N → `finish` sequence. Batches
fill a buffer, and `finish` swaps it in as the live snapshot, so the page
never shows a half-finished scan.

Between full scans, `network_browser.lua` checks the items that have a
[stock rule](using.md#stock-rules). It asks the server for them (`GET
/api/network/watch`), looks them up with the same `getItemsInNetworkById()`
call on just their IDs, and posts what it found (`POST /api/network/levels`).
The server replaces only those items in the snapshot, records the changes
in their history, and checks the rules. The call takes IDs without damage
values, so a GregTech material brings back every stack sharing its ID
(hundreds, for `gt.metaitem.01`), about a full-scan batch on its own. So
the script asks for one ID per call (`WATCH_BATCH_SIZE`) and keeps only the
watched stacks. Each call's size and duration go to the script's debug log,
`/home/network_browser_debug.log`.

## Reading patterns

GTNH 2.9's OpenComputers can read an ME Interface Terminal
(`me_interface_terminal`). Its `getInterfaces()` lists everything the
terminal would show: interfaces, molecular assemblers and GregTech pattern
hatches, each with its name, location and patterns. The list is a copy taken
when it's called, so the network changing during the walk doesn't disturb it,
unlike `allItems()`. Each pattern comes with its inputs and outputs.

After a full item scan, at most every `PATTERN_SCAN_INTERVAL_SECONDS`,
`network_browser.lua` walks that list and posts the patterns in chunks, with
the same `start` → `batch` × N → `finish` protocol as the item scan
(`/api/network/patterns/...`). Running after the item scan rather than
alongside it keeps the two from holding memory at the same time. Each
interface arrives with all its patterns at once, so the biggest one sets
how much memory the scan needs: a Large Molecular Assembler's few hundred
patterns can take a few MB. The script turns them into JSON one at a time,
letting go of each as it goes, so they aren't held twice. A failed pattern
scan is only logged, and is tried again after the next item scan.

Each item scan's `scan/finish` also says whether there's a terminal
(`interface_terminal`: `true` or `false`; left out when pattern scans are
turned off). Until patterns have been read, the server uses it to say
"connect a terminal" rather than "wait for the first pattern scan".

What it reports, found with `oc/pattern_dump.lua` on a real network:

- Processing patterns have real sizes.
- Crafting-table patterns report every size as 0. AE2's converter reads its
  own `Cnt` field, and crafting patterns store a vanilla `Count`. The script
  sends every pattern's NBT, and the server reads those counts from it the
  way AE2 does (`Count`, else `Cnt`), along with the `substitute` and
  `beSubstitute` settings the plan needs (OpenComputers reports neither).
  Without `allowItemStackNBTTags` there's no NBT: inputs are still counted
  (one per grid slot), but outputs have no count, the pattern is marked
  inexact, and no pattern takes substitutes.
- Fluids come either as fluid entries or, in older patterns, as ae2fc
  `fluid_drop` items whose NBT names the fluid. The server turns both into
  fluids. Thaumic essentia comes as an amount with no item ID.

The server keeps the last complete scan, saved across restarts, and serves it
to signed-in users at `GET /api/network/patterns` (it includes each machine's
coordinates). Items are keyed like the Network tab's, NBT variants included.

## The crafting plan

The craft dialog's plan is worked out from these patterns and the last
network scan (`GET /api/network/plan`, `gcm/planner.py`), the way GTNH's AE2
plans:

- The requested item is always crafted in full.
- Everything below it comes first from what other steps made too much of (a
  batch's extra output, or a pattern's other outputs), then from stock. Both
  are only counted once, even when several steps need them.
- Steps run in whole batches.
- Where several patterns make an item, they're tried in AE2's order: by
  slot, the last first. Each makes as many whole batches as its inputs can
  be had for, and the next makes the rest, so one item can come from two
  patterns; the tree shows each part as a "via" row. Anything none of them
  can cover is planned with the first, and what that lacks is missing.
  Picking a pattern in the tree uses that one alone.
- A crafting pattern with Substitute ticked takes, for each input, the item
  itself first, then anything sharing an ore-dictionary name with it: from
  stock, then crafted with the item's own patterns, then with patterns
  marked "can be substituted" that make an alternative. The tree lists the
  alternatives taken from stock under the step ("instead: …"), and a part
  making one names it ("Industrial Diamond via …"). This needs game data
  with the ore dictionary.

AE2 may still differ. It also orders patterns by the interface's priority,
which OpenComputers doesn't report, and it checks each substitute against
the recipe itself, where the plan takes anything sharing an ore-dictionary
name.

## The craft tree

AE2 doesn't tell OpenComputers a job's own plan, so a running job's tree on
the Crafts tab is rebuilt from the network's patterns: an item the CPU
reports crafting or waiting to craft is a crafted step, anything else came
from storage, and where several patterns make an item, the one whose inputs
the job holds is used. An item used in two places shares one count, as the
CPU reports it.

## Held-up steps

The CPU's lists say a bit more than how much is left. Going by AE2's
`CraftingCPUCluster`, `pendingItems()` is the output of pattern runs not yet
sent to a machine, and `activeItems()` is output sent out and not back yet.
Sending a run moves its amount from pending to active, so the two together
only fall when a machine gives something back. The server
(`server/gcm/bottlenecks.py`) keeps, for each step, when it was last sent
and when something last came back, and calls it held up:

- **blocked**: runs are pending, `storedItems()` has one run's inputs, and
  nothing has been sent for `CRAFT_BLOCK_GRACE_SECONDS`. AE2 sends a run as
  soon as it has the inputs, so the machine side is refusing it.
- **busy**: output is out, none has come back for that long, and the same
  machine has given another step something back in that time.
- **stalled**: output is out, none back for `CRAFT_STALL_SECONDS`, and
  nothing else on the machine is moving.

The machine is the provider of the step's pattern, chosen as the tree
chooses it. Providers are told apart by the coordinates and name the
Interface Terminal reports. Those are the interface's own coordinates, so
for "busy", interfaces with the same name one block apart diagonally or two
apart in a line (both touching one block) count as one machine. After each
status report, the time since the last one goes to each held-up step's
provider. When nothing in the whole job has moved for the grace period, it
also goes to "jobs waited" for every provider holding the job's items. The
totals are kept per hour in `data/app.db`.

OpenComputers can't see inside a machine, so a stalled step may only have a
long recipe; that's why it waits longer than the others.

## Fluid items

Fluid pseudo-items report `name` as a bare Forge fluid registry name with no
colon, e.g. `molten.mutatedlivingsolder`. They aren't registered items, so
their icons come from the exported fluids (`fluids_by_key`), not the items.

## Reading EU

`power_monitor.lua` uses `getEUStored()` and `getEUMaxStored()` on the
`gt_machine` component. Older builds overflowed above 2³² EU
(GTNewHorizons/GT-New-Horizons-Modpack#8619, fixed in
GTNewHorizons/OpenComputers#78). If the numeric call fails or reports more
stored than the capacity, the script falls back to parsing
`getSensorInformation()`.

Each reading carries the name the machine has in `MACHINES` (none without)
and the `gt_machine` component's address. The server keeps a table of
machines (`power_machines` in `power.db`) that maps these to the name the
readings are stored under:

- A name from `MACHINES` is used as it is, or as an admin renamed it.
- The first unnamed address keeps the unnamed history from before addresses
  were sent. Each further unnamed address is numbered by kind ("LSC 2", or
  "Storage 2" without an LSC's wireless mode), and the first one becomes
  "LSC 1".
- A script from before addresses were sent posts unnamed readings without
  one; they go to the first unnamed machine, as before.

A rename rewrites the stored name in the readings, so links, health and the
MCP tool all follow it.

A Lapotronic Supercapacitor's `getSensorInformation()` also has a
`wireless_eu` line: the EU in its owner's wireless network (their team's,
when they're in one), whether or not the LSC's own wireless mode is on.
Once that's above 0, the script posts it as one more machine with a
capacity of 0, which the page reads as "no capacity". The value can be
past 2⁶³, so it's sent as a float and loses its last digits.

The LSC's own reading also carries its wireless mode (in the line's key:
`wireless_mode.enabled`) and its count of each wireless capacitor tier
(`capacitors` lines, UHV to UMV). From these the server works out the LSC's
wireless threshold: 6×10¹³ EU per UHV capacitor, ×100 a tier. That's the
EU the LSC keeps; every 5 minutes it uploads what's above it to the network
and downloads up to it. The script posts the raw counts and leaves the
arithmetic to the server, so a change to it doesn't need a script update.
The LSC's averaged EU in/out are per tick; the page shows them per second.


Item recipe shortcuts are registered beside tooltip bindings, with each view
resolving the current item from its live data rather than a saved tooltip name.
The shared R/U handler selects the hovered item, then keyboard focus, within the
topmost dialog. Network, CPU ingredients, craft-plan lists, recipe stacks and
craft item links share it. Typing, modifier shortcuts, repeats, unknown identities
and essentia are excluded. Recipe navigation can sit above a crafting plan;
Escape closes the recipe view first and preserves the plan underneath.
