# How it works

These notes explain design choices in the scripts and the server, and were
verified against the GTNH OpenComputers source and live testing.

- [Crafting CPUs](#crafting-cpus)
- [Craft request results](#craft-request-results)
- [Scanning the network](#scanning-the-network)
- [Reading patterns](#reading-patterns)
- [The crafting plan](#the-crafting-plan)
- [The craft tree](#the-craft-tree)
- [Fluid items](#fluid-items)
- [Reading EU](#reading-eu)

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

Fluids are read with a single `getFluidsInNetwork()` call, since networks
hold far fewer fluid types than item types. Essentia likewise, with one
`getEssentiaInNetwork()` call: Thaumic Energistics adds it to the same
`me_controller`/`me_interface` component, and it names each aspect by its
tag (`ordo`), as the Interface Terminal does in patterns, so the crafting
plan finds essentia in stock. Without Thaumic Energistics there's no such
method and the scan skips it.

If an error escapes the scan loop (most likely the computer running out of
memory, which the three scripts share), `network_browser.lua` writes it, with
a stack traceback and free memory, to `network_browser_debug.log` in the
directory it runs from. It also reports the error to the server, which ends
the dead scan and shows the error on the Network tab until a scan finishes.
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
(hundreds, for `gt.metaitem.01`), roughly one full-scan batch, and the script
keeps only the watched ones. Each call's size and duration go to the
script's debug log, `network_browser_debug.log`, in the directory it runs from.

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
alongside it keeps the two from holding memory at the same time. A failed
pattern scan is only logged, and is tried again after the next item scan.

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
