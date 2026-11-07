# Using the web page

- [Crafts tab](#crafts-tab)
- [Pins and notifications](#pins-and-notifications)
- [Webhooks](#webhooks)
- [Server status](#server-status)
- [Pattern check](#pattern-check)
- [Power tab](#power-tab)
- [Network tab](#network-tab)
- [Requesting and cancelling crafts](#requesting-and-cancelling-crafts)
- [Multiblock kits](#multiblock-kits)
- [Stock rules](#stock-rules)
- [Limitations](#limitations)

## Crafts tab

One card per crafting CPU, updated within a second of each report from the
game. A CPU's card is titled with its name. One that nobody has named in AE2
is shown by its storage, such as `68k #1`, and CPUs that share a name get
their size added, such as `Main 64k #1`, each with a **?** that says so (see
[CPU names](in-game.md#cpu-names)). A busy card shows:

- **Output item** — needs an AE2 **Crafting Monitor** block in that CPU's
  multiblock. Without one, the card shows "Unknown output" and a link to
  [Crafting Monitor](in-game.md#crafting-monitor), which lists what else a CPU
  without one loses. For a job's first few seconds the card can say
  "Starting job…" until the CPU reports what it's making.
- **Progress** — each item or fluid type the job still has to make counts
  as one step, and the bar is the average of how far along each step is.
  It never moves backwards. It counts steps done, not time left.
- **Run time** — how long the job has been running ("≥" for one that was
  already running when the server started). After two minutes and 10% of
  progress, a rough time left follows: what's left at the pace so far.
  Since steps can take very different times, take it as a guess.
- **Ingredients (N)** — opens a grid of everything the job holds, is crafting
  and is waiting on, laid out like AE2's crafting status screen. It stays
  live while open. Click an item to open its stock history over the grid;
  the browser's Back button closes the top dialog. With an
  [ME Interface Terminal](in-game.md#me-interface-terminal) connected,
  switch it to **Tree** to see the job step by step:
  each step shows how much of it is made, steps being crafted are green
  as in the grid, and finished branches fold away. A step held up at its
  machine turns amber as stuck, and says why and where the machine is:
  - **can't be sent to its machine**: the CPU has the step's inputs, but
    nothing has gone out for `CRAFT_BLOCK_GRACE_SECONDS` (1 minute by
    default). The machine won't take it: its inventory is full, the
    interface is in blocking mode, or a pattern hatch is full.
  - **machine busy with ...**: what was sent there hasn't come back for
    that long, while the same machine finished another step in that time,
    of this job or another CPU's. Two interfaces with the same name that
    touch one block count as one machine.
  - **nothing back from its machine**: what was sent hasn't come back for
    `CRAFT_STALL_SECONDS` (10 minutes by default), and nothing else there
    is moving. The machine may be stopped (power, maintenance, its output
    full, the output not reaching the network), or the recipe is just
    long: the game doesn't say which.

  A folded branch says what's under it ("1 stuck · 2 active"). Stuck steps
  are also listed above the tree; click one to jump to it.

  The tree needs the network's patterns (see
  [The craft tree](how-it-works.md#the-craft-tree) for how it's rebuilt).

Below the cards, **Recent crafts** lists every job that ended on any CPU,
newest first and grouped by day. Each row shows the CPU, how long the job ran,
and an Incomplete badge if it stopped early. Click an item's name to open its
stock history without leaving the Crafts tab. Jobs that were already running when the server started show no
duration, since their start wasn't seen. The list is kept permanently in
`data/app.db`. The filter box above it takes the same syntax as the
[Network tab's search](#search), and **Incomplete** and **Auto** show only
those jobs; both look through the whole list, not just the rows loaded so
far.

**Statistics**, below it, adds up the jobs that ended in the last day, week,
month or ever: how many, how many stopped early or were auto-crafts, the most
crafted items with how long one usually takes, and how much of the range each
CPU spent busy. A job counts once it ends, and its time only from when the
server saw it start, so jobs already running at a restart add no busy time.
**Held up at** lists the machines running jobs' steps were held up at, as
in the live tree, with their coordinates. They're ranked by how long whole
jobs waited on them, with nothing else in the job moving, and then by how
long steps were held up there, split by why. A step that could have come from
several interfaces gives each of them a share. Unlike the rest, this counts
jobs still running.

Pasted into Discord, a link to the Crafts tab previews the busy CPUs, each
with what it's crafting and that item's icon, and an **Open** button.

### Linking one craft

A craft's details (its **Ingredients** dialog) have their own link,
`/crafts?cpu=…&job=…`: it's in the address bar while they're open, and the
link button beside the close button copies it, for the installed app, which
has no address bar. If the CPU moves on to another craft while they're
open, the link follows. Opened while the craft runs, the link opens its
details over its card, which is outlined. The link keeps working after the
craft ends: the
page then says how it went (finished, or stopped at what progress), when,
and how long it took, with the item's name opening its stock history, over
the CPUs as they are now. In Discord, it previews the same, with the item's
icon; a running craft's preview counts down to when it's due, once the
server has an estimate. Discord keeps a link's preview for a while, so an
old preview can still say the craft is running; the times in it stay
current, and **Open** shows how it is now.

A link only says "how it went wasn't recorded" when the server was down
as the craft ended. A craft that was already running when the server
restarted is still the link's, though the page can't say how long it ran.

## Theme

The page follows your system's light or dark setting. To pick one for
this browser instead, use **Theme** in the settings (⚙) menu: sun for
light, moon for dark, the half circle to follow the system again. It's
there signed out too.

## Pins and notifications

Click the pin icon on a card to pin its job to the top of the page. When a
pinned job finishes, it shows as a completion to acknowledge and, if you've
turned notifications on, triggers a notification. Pins and
completions are saved per user on the server, so they follow you across
devices.

The browser tab shows the same things in the background. Its title counts
what wants a look: finished crafts not yet acknowledged, items under their
stock rules, and a power alert (crafts still running don't count). For
example, **(2) Crafts · GTNH Monitor**. The rest of the title names the open
tab, or the item or craft whose dialog is open. A craft's details also show
its progress there (**45% Iron Ingot**), then **Finished** once it ends, so
a tab left on one craft says how it's going. The tab's icon gets a dot:
red for low stock or power, green for finished crafts. iOS and Safari don't
show the dot, but the title count works there too.

Turn notifications on with the **Notifications** switch in the settings
(⚙) menu. Each browser you turn it on in gets them, delivered through
the browser's push service (Web Push), so they arrive even with the page
closed. Signing out stops them for that browser.

Browsers allow notifications only on a secure origin: `https://` or
`http://localhost`. Opened over plain HTTP from another device, the switch is
disabled with a "Needs HTTPS" note. Use a TLS reverse proxy (see
[Exposing it beyond your LAN](server.md#exposing-it-beyond-your-lan)) or
open the page on the server machine itself.

On phones: Android Chrome works as-is. On iPhone and iPad (iOS 16.4+),
notifications only work after **Share → Add to Home Screen**, opening the page
from that icon, and turning the switch on there.

For push, the server needs outbound HTTPS to the push services
(`fcm.googleapis.com` for Chrome and Android, `*.push.services.mozilla.com`
for Firefox, `*.push.apple.com` for Safari and iOS, `*.notify.windows.com` for
Edge on Windows). Without it, notifications still show while the page is open.
The server signs pushes with a key it generates on first start,
`data/vapid_private.pem`. Keep it with your [backups](server.md#backups):
if it's lost, every browser has to turn the switch off and on again.

## Webhooks

To also post notifications to a Discord channel (or Slack, or anything that
takes a JSON POST), an admin adds one or more webhook URLs under
**Settings (⚙) → Webhooks**, one per line, and picks the events. A Discord
webhook (**Channel settings → Integrations → Webhooks**) gets an embed with
the item's icon; any other URL gets a JSON body with `event`, `title`,
`body`, `icon` and `url`, plus a `text` line that Slack-style incoming webhooks
show as is.

Unlike push notifications, webhook posts are for the whole base, so the
dialog picks what's sent:

| Event | Sent when |
|---|---|
| `crafts` | a pinned job finishes or stops (anyone's pin; one post per job) |
| `autocraft` | a keep-in-stock target's auto-craft fails |
| `stock` | a low-stock alert fires. Off by default, since alerts are each user's own; on, it posts once per item, without saying whose alert it was. |

A post about a craft links to [that craft](#linking-one-craft), and the
others to the item's page. Icons and links need `PUBLIC_URL`, the address the
page is reached at from outside; without it, posts are text only. **Send test** in
the dialog saves it and posts a test message to every URL.

On the server, `WEBHOOK_URLS` (and `WEBHOOK_EVENTS`, with the event names
above) set them in the environment instead: the dialog then only shows how
many there are and can't change them. `WEBHOOK_HOSTS` limits the URLs set on
the page to a list of hostnames; see [Configuration](server.md#configuration).

## Server status

Admins get a **Status** dialog (**Settings (⚙) → Status**). The
in-game computers are listed under it (see
[From the web page](in-game.md#from-the-web-page)), and above them a
**Server** section shows:

- each feed from the game (crafting CPUs, the ME network scan, patterns,
  each power machine), with how long ago it last reported. A green dot
  means it's current; amber means it's late (crafts after
  `STALE_AFTER_SECONDS`, power after 5 minutes, the network after an hour
  or a scan running over 30 minutes, or after three of the script's own
  intervals if it's set to report less often), crashed, or had its last
  scan turned away as incomplete. A turned-away scan says how many of its
  chunks arrived, how many errors the scanner hit, and the last of those
  errors (older scripts don't send it), so a scan that keeps being turned
  away can usually be explained from here, without the in-game debug log.
  A grey dot on Patterns means there's no ME Interface Terminal, which is
  optional.
- the size of each database and the free space where they're kept.
- when stock history was last thinned, and how many rows that removed.
- how many browsers get push notifications, and how the last webhook post
  went.
- how long the server has been up, its release, and the game data version.
  When a newer release is out, it says so, with a link to what's new; a
  dot on **Settings (⚙)** and a line under **Status** in its menu point
  here. Updating the server is done where it runs (see the
  [README](../README.md#updating)). Anyone signed in sees the release in
  the settings menu.

Until a tab's script first reports, the Crafts, Power and Network tabs
say which script they're waiting for. Admins get a **Connect a computer**
button there that opens this dialog at
[Connect a computer](in-game.md#from-the-web-page).

## Pattern check

GTNH doesn't publish its recipe changes, so after an update a processing
pattern made for the old recipe can quietly stall a machine. The pattern
check compares two GTNH versions' GregTech recipes and lists the patterns
a change affects. It needs your patterns, so an
[ME Interface Terminal](in-game.md#me-interface-terminal).

- **Before updating** (admins): **Settings (⚙) → Game data**, pick the
  new version in the list, then **Check patterns** under *Pattern check*.
  Only that version's recipes are downloaded (about 2 MB); the icons in
  use don't change.
- **After updating:** if you forget to switch the game data, admins get a
  notice once your patterns or crafting jobs use items only a newer
  version has (see
  [When the game updates](server.md#when-the-game-updates)). Once an
  admin switches the game data to the new version, operators and admins
  get a notice at the top of the page if the update affects any pattern.
  **Show patterns** opens the list, and **Settings (⚙) → Pattern check**
  shows it again later. The notice's &times; hides it for that update,
  for every operator and admin.

Operators see only the last update's check (the game data in use against
the version before it): checking another version downloads its recipes,
which is an admin's call. Viewers don't see the check.

Each pattern lands in one of these:

- **Broken:** nothing makes the pattern's output from its inputs any more,
  or not in those amounts. The check shows the old recipe and what makes the
  output now. When no GregTech recipe makes it at all, it says so: an item
  nothing makes or uses any more was removed (GTNH often leaves an
  Unpackager recipe to take your stock of it apart); one that's still used
  may be made outside GregTech now, at a crafting table or in another
  mod's machine.
- **Moved to another machine:** the same recipe, now in another machine
  (its pattern needs to move).
- **Changed outside the pattern:** the pattern still matches, but
  something it leaves out changed: a gas or fluid supplied separately, the
  programmed circuit, a catalyst, the voltage tier, the coil heat, or a
  new byproduct that will fill the machine's output.
- **Not checked:** it matched no GregTech recipe in the older version
  either: another mod's machine (Thaumcraft, Botania and the like), or a
  setup the check can't follow.

Patterns don't have to list everything. An EBF pattern without its gas or
circuit matches, and so does one scaled up (16 dust to 16 ingots). An EBF
pattern can also output the cooled ingot and leave the hot one to a
vacuum freezer. The pattern's machine comes from the name the ME Interface
Terminal shows for its interface or hatch, when that's a GregTech
machine's name (or starts with one), or else from the other patterns on the
same interface. When neither tells, the check takes any machine that made
the recipe before, and says so. Crafting-table patterns aren't checked:
AE2 already marks those invalid itself.

## Power tab

Stored EU over time. The Hour, Day, Week, Month, and Lifetime
ranges are downsampled on the server to at most 800 points, so long ranges
stay fast. Readings are kept in `data/power.db`.

Above the chart, an arrow shows the machine's net EU/s over the last 5
seconds, from the LSC's own averages (hover it for input and output separately), and a line gives the
rate over the past hour with the time until the machine is full or empty
at that rate. The estimate needs at least 10 minutes of readings. The
arrow and the hour's rate are green while the machine fills and red while
it drains.

When the tab's red dot is on, the button of the machine it's about is red
too.

With several machines (`MACHINES` in
[power_monitor.lua's settings](in-game.md#power_monitorlua)), buttons above
the readout switch between them, and the page's URL
(`/power?machine=Batteries`) keeps the choice for links and link previews.
In Discord, a link to the tab previews the latest reading and the day's
chart.
The unnamed machine shows as **Main**, and the tab badge follows it
(the wireless network instead, while it reports). The buttons put the
wireless network first, then the machines by size, largest first (an LSC
in wireless mode by its threshold).

Unnamed machines on different computers each get a chart of their own.
When a second one reports, **Main** becomes **LSC 1** (keeping its
history) and the new one **LSC 2**; a storage machine that isn't an LSC is
numbered **Storage 1**, **Storage 2**. A machine keeps its number for good:
a bigger one later only changes the order of the buttons.

Admins can rename the machine shown with **Rename…** beside the buttons.
Its history keeps the new name, and so do its script's readings from then
on; nothing needs changing in the game. A link to its old name opens the
default machine instead. While there's an unnamed machine, no other can be
named **Main**, which it goes by (its link is `/power?machine=Main`).

With an LSC reporting and EU in your wireless network, there's also a
**Wireless** entry, first among the buttons. The network has no capacity, so
it shows only stored EU and its rate, with no fill; its button shows its EU
(`64.9P`) where the others show how full they are.

While the wireless network reports, it's what the tab opens on, and what
link previews show. The tab button then shows the network's EU (`64.9P`)
instead of a fill, and its red dot means the past hour's rate would empty
the network within a day.

An LSC in wireless mode keeps only its wireless threshold (60T EU per UHV
capacitor, ×100 a tier above) and trades the rest with the network every
5 minutes: it uploads what's above the threshold and downloads up to it.
So the Power tab measures it against its threshold, not its capacity: its
button and readout say how full that 5-minute buffer is, past 100% while
it holds a surplus waiting for the next upload, and its chart draws the
threshold where the capacity line would be. Instead of an "Empty in" line
it says what it sent to or drew from the network over the past hour, from
how far above or below its threshold it climbs between rebalances (it
needs about 10 minutes of readings first; server lag makes it read a little
high). When it's drawing, the line also gives the most it can
refill: one threshold every 5 minutes (20T EU/s for one UEV capacitor). A
draw near that needs more capacitors. If it drops under 10% of its threshold,
the tab's red dot comes on: it's draining faster than the network can
refill it, and it needs more or higher-tier capacitors. An LSC that's not
in wireless mode still turns the dot on when it's under 10% full, even
with the wireless network as the tab's default.

## Network tab

Every item and fluid in the ME network, with icon, name, and amount (fluids in
mB), and its essentia with Thaumic Energistics installed. It refreshes every
minute, but its data changes only when a scan finishes.

Kit-slot outputs are hidden from this list: both the initial Command
Block and the tagged "… kit" outputs generated as those slots are reused.
The scan data is still available to the planner and item history.

- Click an item to open its stock history chart. Each item has its own URL
  (`/network/item/<mod:internal:damage>`, `/network/item/@<aspect>` for
  essentia) that can be shared, and the link button in its dialog copies
  it; link previews
  include the item's icon, the current amount and a chart. Discord reuses
  its preview of a link for about half an hour; to get a current one, post
  the link with a query string added (`?v=2`). Discord's
  [Embed Debugger](https://discord.com/developers/embeds) shows how it
  reads a link. Under the amount, the popup shows
  how fast it changed over the chosen range and, if it's falling, when it
  runs out at that rate.
- Pin items to keep them at the top of the list.
- Sort by **Change** to see what's being used up: items are ordered by how
  much of their stock went over the last **Day** or **Week**, emptied ones
  first and the ones that grew last, each labelled with the change
  ("−80%"). Comparing shares rather than amounts lets items and fluids
  sit side by side. The tooltip gives the amount at the start, the rate,
  and when it runs out at that rate. An item whose history starts inside
  the range is measured from its first recorded amount.
- The last scan is saved to `data/item_history.db` and restored when
  the server restarts. Until the next scan completes, the page marks the data
  as restored rather than live.
- Stock history keeps every change for 30 days. Older history keeps the last
  value of each hour, and after a year of each day, so charts over long
  ranges look the same while the file stops growing quickly (a base with
  ~8,000 items records about 20,000 changes a day). A background job does
  this daily; `HISTORY_HOURLY_AFTER_DAYS` and `HISTORY_DAILY_AFTER_DAYS` set
  the ages, and `0` keeps everything.

Hovering an item shows its description, with the name in its rarity's
colour and its mod's name below; hold **Shift** to see what the game shows
with Shift held, such as a multiblock's structure. The item's dialog shows
the same, with a **Shift** button, and its ore-dictionary names: click one
to search for it. Descriptions are the items' in-game tooltips below their
name (a GregTech machine's type and info lines, a material's formula, a
fluid's temperature), from the game data, starting with the item's or
fluid's GregTech formula where the tooltip doesn't have it.

### Search

The search box takes NEI's syntax, matched against item names and ignoring
case. Press `/` on any tab to jump to it.

| Query | Matches |
|---|---|
| `molten neutronium` | items with both words, in any order, each in the name, description or an ore-dictionary name |
| `copper\|tin` | items with either |
| `"air shard"` | items with that exact phrase |
| `@gregtech` | items from mods whose id or name contains `gregtech` (`@industrialcraft` and `@ic2` both work) |
| `#dtpf` | only the description, not the name, as NEI's `#` does |
| `$ingotiron` | only the ore-dictionary names |
| `=sio2` | items whose chemical formula contains `sio2`, sub- and superscripts read as digits (GregTech's `=`) |
| `-dust` | items without `dust`; `-` also works before `@mod`, `#`, `$`, `=`, `a\|b` and `"phrase"` |

Plain words match descriptions and ore-dictionary names as well as names, as
NEI's default search modes and AE2's terminal do: `boundaries` finds the
Dimensionally Transcendent Plasma Forge by its tooltip. `#`, `$` and `=` take
`a|b` and `"a phrase"` too. Game data built before descriptions existed has
none of them, so `#`, `$` and `=` match nothing, and `@` matches mod ids
only.

### Keyboard

The grid works from the keyboard too: **↓** from the search box (or **Tab**)
goes into it, the arrow keys, **Home**/**End** and **Page Up**/**Page Down**
move, and the focused item shows its tooltip. **Enter** opens the item, **C**
opens the craft dialog (as middle-click does), **P** pins it, and **Escape**
(or **↑** from the top row) goes back to the search box. Closing a dialog
with Escape returns to the item it was opened from.

Elsewhere, item names on the Crafts tab and the cells in a CPU's ingredients
are reached with **Tab** and open with **Enter**. **Tab** stays inside an open
dialog, and closing one puts focus back where it was.

## Requesting and cancelling crafts

Requires an operator or admin account. Craftable items and fluids in the
Network tab have a small Blank Pattern icon in the corner.

- **Left-click** a craftable item that has no stock, or **middle-click** any
  craftable item, to open the request dialog. This matches AE2's terminal.
- Left-clicking an item that is in stock does nothing, since the page can't
  extract items.

The amount can be written as `10k`, `1.5M` or `2G`, or as arithmetic such as
`4*64+10` or `(10k-2k)/2`; the dialog shows the result before you submit. The
stock rule fields below take the same forms.

With an [ME Interface Terminal](in-game.md#me-interface-terminal)
connected, the dialog also shows the **crafting plan** for the amount you've
typed, before you submit, built from your patterns (see
[Reading patterns](how-it-works.md#reading-patterns)). Without one, it says
a terminal is needed and links to how to connect one. Like
AE2's own Request Craft screen, it has two views:

- **List**: every item the plan touches, missing ones first. As in the game,
  an item taken from stock shows how much the plan takes ("Available") and
  what share of the stock that is ("Used", red for all of it, then orange,
  green and blue). An item that's crafted shows how many.
- **Tree**: the recipes step by step, as a collapsible outline. Each step
  says how much it needs, how much comes from stock, how much is crafted and
  on which machine. A closed step with something short under it says how
  many steps are short there. **Hide all available** shows only the way to
  what's missing, already opened. Where several patterns make the same
  item, pick another one to see that plan instead. Endgame plans can run
  past 100,000 steps; they're planned in full, so the list and the missing
  items cover all of it, and the tree loads deeper steps as you open them.

Above both views is everything the plan is short of. Click an item there to
jump to where it's short in the tree; click it again for the next place. If
the plan would take an item from at or above your low-stock alert or a
keep-in-stock target to below it, a **stock rules** button beside the views
lists those items. An item that was already below its rule isn't listed,
since the plan didn't cause it.

The plan is the server's estimate from your patterns and the last network
scan, worked out the way GTNH's AE2 plans (see
[The crafting plan](how-it-works.md#the-crafting-plan)). AE2 may still
differ, and the Request button works whatever the plan says.

Items that differ only in NBT (GregTech turbines of each material, seeds,
bees) are separate entries in the grid, and requesting one crafts that
variant. A variant that hasn't been scanned since the server started fails
rather than crafting the wrong item.

After you submit, a pending card appears. Planning a large craft can take from
under a second to several minutes, and the request survives closing the tab.

- **Accepted** — the pending card is replaced by a pin on the CPU that AE2
  assigned, and the job is tracked like any other pinned craft.
- **Failed** — the card turns red with AE2's reason (usually
  `request failed (missing resources?)`) until you dismiss it.

To cancel a running job, click **×** on its card in the Crafts tab.

## Multiblock kits

A kit lists every block a GregTech multiblock is built from: its
controller first, then its casings, coils, glass, pipes and so on, with how
many the network has. Open one with
**Multiblock kit** in a controller's item dialog, or **Multiblocks** in the
Network tab to pick a multiblock by name. When the controller is craftable,
its item dialog shows equally sized **Multiblock kit** and **Request Craft**
buttons: choose the kit for the structure's blocks, or Request Craft for
the controller itself. The dropdown lists controllers
stored in the network or available to craft, and refreshes each time you
open the kit dialog from **Multiblocks**. When opened from an item's
dialog, the kit is fixed to that multiblock and the picker is hidden;
its name and icon appear in the header. **How many** stays editable in
the header for either entry point, wrapping below the name on narrow
screens. It needs game data built since
kits were added; the button isn't there without it.

After a request is accepted, the page remembers your chosen coils, glass,
structural sizes and separate-craft setting **for your account and that
multiblock**, including when you sign in from another browser. **How many**
always starts at **1**. Each material's Request amount is calculated again
from the current stock, so a previous shortage isn't requested twice.

Multiblocks with tiered parts have a picker for each, named after the
hologram projector's channel for it: **Coil** and **Glass** list the blocks
they take directly in the material row, beside its counts. The block icon
stays in the same column as the other materials, with the channel's name
below the selector. **Length**, **Height** and the like stay above the
preview. A block setting without a corresponding material row appears
there too. **How many**,
beside the multiblock's name, multiplies the kit. The themed menus show
each block's icon and mark the current selection with a check. Type in
the multiblock picker to search; use the arrow keys and Enter to select,
or Escape to close a menu without changing your choice.

- **Need** is the most the multiblock takes. Hatches, buses, mufflers and
  maintenance hatches aren't in the kit, as you pick your own; every spot
  that takes either a hatch or a casing counts as a casing.
- **In network** is from the last scan, orange where it's short.
- **Request** (operators and admins) starts at what's missing of each
  block the network has a pattern for. Type an amount, or drag the slider
  under the block, from none to all the kit needs. Lower it for the hatches
  you'll put in casing spots, and for walls you share with another
  multiblock. A block without a pattern says so and can't be requested.

Choose **Review & request** to move to the crafting plan before starting
the craft. **Back to kit** returns to your parts and quantities without
losing your choices. The review keeps **How many** fixed; go back to change
it. The final **Request** button starts crafting.

If no pattern scan is available, the button stays **Request** and sends
the craft directly. A note explains why no preview is available, including
how to connect an ME Interface Terminal when one is missing. The terminal
is needed for the preview, not for submitting a craft request.

**Request** asks for the kit as **one job** where the base has a
[kit interface](in-game.md#kit-interface): AE2 plans every block together,
so what they share (the plates two casings both need) is made once, runs
it on one CPU, and puts the blocks in the network when they're all done.
The job shows on the Crafts tab as making "*multiblock* kit". AE2 uses
what's in storage first, so the kit asks for what's in stock of each block
(up to what it needs) plus the Request amount, and crafts the Request
amount.

Tick **Request each block on its own** to queue each block as its own
craft instead, as if you'd requested them one by one: each lands in the
network as soon as it's done. Without a kit interface, that's how a kit is
always requested, and the box says so. The page remembers the choice.

If no kit slots are detected, **Set up a kit interface** links directly to
the illustrated [setup guide](in-game.md#kit-interface), including the
placeholder processing pattern and return-network wiring. You can still
request the materials separately while setting it up.

The review (signed in; **Review plan** for viewers) shows the [crafting plan](#requesting-and-cancelling-crafts)
for everything Request would ask for, all together, before you request it:
the same List and Tree views as a single craft, with one root for the kit
and each block under it, and one Missing list for the lot. Going back to
change a Request amount and reviewing again updates the plan. Unlike AE2, which plans each craft on
its own as it starts, the plan counts the blocks against the same stock,
one after another: two blocks that need the same plates aren't both
counted as having them, which is what happens when all the crafts start at
once and one of them comes up short.

**3D view** shows the multiblock as you've configured it, block by block,
with the game's textures: change a coil or the length and it follows. Drag
to turn it, and scroll or pinch to zoom. The slider below it hides the
layers above the one you pick, to see inside. Blocks are drawn with the
geometry and textures the game draws them with, the controller's front
face included, and glass, coils and casings join up with their neighbours
as they do in the game. Animated textures are still. It's open
unless you close it with the **3D view** button; the page remembers that.

The counts come from building each multiblock in the game when the game data
is made, the way NEI's multiblock preview does (see
[Bundle format](../tools/icon-export/README.md#bundle-format)). A
multiblock whose structure isn't built with StructureLib has no kit.

## Stock rules

Set them in an item's popup on the Network tab. Every rule is checked after
each full network scan, and after a quicker check of just the rules' items
that `network_browser.lua` makes every `WATCH_INTERVAL_SECONDS` (1 minute by
default) in between.

- **Alert me below N** — any signed-in user. You get one notification when the
  item drops below N, and another only after it has been back at or above N.
  Alerts are your own; other users don't see them.
- **Keep at least N, refill to M** — operators and admins, craftable items
  only (so not essentia, which OpenComputers can't ask AE2 to make). When
  the item drops below N, the server requests a craft of
  (M − current) and AE2 plans it like any other request. There's one target
  per item for the whole base.

A target holds off while a craft for it is already under way: its own request
waiting on the game, a CPU busy making the item, or a job that finished making
it during the scan. It only starts a craft while more than
`AUTOCRAFT_KEEP_IDLE_CPUS` crafting CPUs are idle, so some stay free for
requests made by hand. When CPUs are short, the emptiest items go first. If a
craft fails (usually missing ingredients), the target waits
`AUTOCRAFT_RETRY_SECONDS` before asking again, and anyone with an alert on
that item is notified.

Auto-crafts aren't pinned for anyone. They show with an **Auto** badge on their
CPU card and in Recent crafts, and as "Auto request" in an admin's view of the
user who last saved the target.

When any rules exist, a **Stock rules** list sits above the Network tab's
item grid. It shows each rule with the item's current amount and status
(ok, low, craft requested, crafting, waiting for a free CPU, craft failed).
Click a row to open that item. Grid cells with a rule carry a bell (alert) or
circular-arrows (target) badge, tinted red while low, and the item's chart
draws each threshold as a dashed line.

## AI assistants

The server is also an [MCP](https://modelcontextprotocol.io) server, so an AI
assistant can answer questions about the base ("what's been draining fastest
this week?", "why is the craft on M00 slow?", "what's missing to make 64 of
these?") and act on them ("craft 64 of those", "keep 4k of every circuit up
to IV"). Its address is your page's address with
`/mcp` on the end, e.g. `https://monitor.example.com/mcp`; operators and
admins can copy it from **Settings (⚙) → MCP server**.

The assistant acts as you: it signs in with your account, sees what you see,
and can only change what your [role](server.md#user-accounts) can.

**claude.ai, the Claude apps, or another app that connects by URL:** add a
custom connector with the `/mcp` address. The app sends you to this server's
**Connect** page: sign in with your access token if you aren't already,
check which app is asking, and click **Allow**. The server must be reachable
from the internet over HTTPS for claude.ai (see
[Exposing it beyond your LAN](server.md#exposing-it-beyond-your-lan)); one
on your LAN only works with apps running on your own computer.

**[Claude Code](https://claude.com/claude-code):** add the server, then run
`/mcp` in Claude Code and pick it to sign in the same way:

```bash
claude mcp add --transport http gtnh https://monitor.example.com/mcp
```

Or skip the sign-in page and give it your access token, as with any MCP
client that can send a header:

```bash
claude mcp add --transport http gtnh https://monitor.example.com/mcp \
  --header "Authorization: Bearer gcm_tok-..."
```

Anyone holding the token can use it as you, so keep it out of shared config
files, and use **New token** in Manage users if it leaks.

An app connected through the Connect page shows under its user in
**Manage users** ("App: Claude · connected Nov 4 · used 5 minutes ago"),
where an admin can **Disconnect** it. Revoking the token you signed in with,
or **New token**, disconnects it too.

What it can look at:

| Tool | What it answers |
|---|---|
| `search_items` | Items, fluids and essentia by name ([NEI-style search](#search)), with amounts |
| `get_item` | One item: amount, trend, tooltip, stock rules, the CPUs making it |
| `item_history` | An item's amount over the last hour to its whole history |
| `inventory_changes` | What's being used up or piling up over a day or a week |
| `current_crafts` | Each CPU's job and progress, and which are idle |
| `craft_job` | A running job's steps, held-up ones first, with the machines holding them up |
| `craft_history` | Jobs that ended |
| `craft_stats` | Totals, most crafted items, CPU use, slowest machines |
| `plan_craft` | The [crafting plan](#requesting-and-cancelling-crafts) for an amount of an item |
| `stock_rules` | Keep-in-stock targets and your alerts |
| `power` | Stored EU over time and its trend |
| `find_recipes` | How the game makes an item: GregTech machine recipes for your GTNH version, cheapest first |
| `game_item` | Any item's tooltip, including a multiblock's structure (its shift tooltip) |
| `multiblock_kit` | Every block a multiblock is built from, for the coils, glass or size asked for, with the network's stock |
| `base_patterns` | How your base makes an item: its patterns and the machines they're in |
| `pattern_check` | After a GTNH update, the patterns it broke (operators, as on the page) |

| `request_status` | Whether a craft request or cancel went through, and on which CPU |

What it can change:

| Tool | What it does | Who |
|---|---|---|
| `set_stock_alert`, `remove_stock_alert` | Your own [low-stock alerts](#stock-rules) | Anyone signed in |
| `set_stock_target`, `remove_stock_target` | The base's keep-in-stock targets | Operators, admins |
| `request_craft` | Asks AE2 for an amount of an item, like **Craft** on the page | Operators, admins |
| `cancel_craft` | Cancels a CPU's job, like **Cancel** on the page | Operators, admins |

`request_craft` works out the crafting plan first. If ingredients are
missing it doesn't start, and tells the assistant what's missing; it only
starts anyway when asked to again with `force`. It also doesn't start when
the base is already getting the item (a request waiting for the game, a CPU
making it, a keep-in-stock refill on its way), unless asked again with
`allow_duplicate`, so a retry can't make a second craft. There's no fixed
limit on the amount, since what's sensible depends on the item. Instead, the
tools tell the assistant to confirm the item and amount with you, and apps
such as Claude ask before running anything that changes the game.

`cancel_craft` only cancels the job the assistant saw: if the CPU has
started another one since, nothing happens. `current_crafts` says who asked
for each job the server started (a user, or a keep-in-stock target), so the
assistant can ask before cancelling someone else's. While `craft_monitor.lua`
isn't reporting, both refuse, since the game would never see them.

Requests and cancels show in your [history](server.md#user-accounts) like
ones made on the page, marked **Assistant**, and an accepted request pins
its CPU for you.

`search_items` uses the [search syntax](#search). When a plain query finds
nothing, it tries again allowing for words joined or split differently
("inconel625") and small typos ("tungstenstel"), and says the results are
approximate.

`find_recipes` and `game_item` read the game data of the GTNH version set on
the Game data page, so they need one set. They cover GregTech machine
recipes, not crafting-table ones.

Answers are only as fresh as the game's last report, the same as the page's.

### Ready-made prompts

The server also offers prompts: requests worded to get a thorough answer to
a common question, which you pick instead of typing. In Claude Code they're
slash commands (`/mcp__gtnh__base_report`, with your server's name in place
of `gtnh`). Other apps that support MCP prompts list them with the
connector.

| Prompt | What you get |
|---|---|
| `base_report` | Power, crafting, what's running low and what needs attention |
| `slow_craft` | Why a job is slow: the machine holding it up and where it is (give a CPU, or leave it out for the longest-running job) |
| `restock_check` | What's running low, how each item is kept up now, and what the assistant would do, without changing anything |
| `update_check` | After a GTNH update: the patterns it broke, by machine, with what to change (operators) |

### What the assistant is told

When an assistant connects, the server gives it instructions, which apps
such as Claude add to what the model is told:

- how to use the tools well: find items first, plan before crafting, read
  a plan's rows, confirm before changing anything
- what GTNH's units and tiers mean
- that AE2 picks the CPU for a craft, so there's nothing to choose
- that much of a base is kept stocked by things this server can't see
  (Level Maintainers, passive production), so it checks how an item is
  made before suggesting a keep-in-stock target
- a snapshot of the base as it connects: who's signed in and what their
  role can change, the GTNH version, how many CPUs are busy, when the
  network was last scanned, whether patterns and power are reporting

**House rules.** Admins can add their own, in **Settings (⚙) → AI
assistant**. Every assistant that connects afterwards gets them, so use
them for what's true of this base. For example:

- "Plasmas and the Infinity Catalyst are kept by Level Maintainers: never
  add keep-in-stock targets for them."
- "CPU M00 is for fusion crafts: don't cancel jobs on it."
- "Ask in Discord before requesting anything over 1M EU/t."

House rules can't give an assistant more than its user's role allows: the
tools check the role either way. The same dialog shows the latest tool
calls assistants made (who, which tool, with what), which helps when an
assistant does something odd.

## Limitations

- Crafting status is only as fresh as `POLL_SECONDS` (5 s by default) plus the
  page's 1 s refresh. Requests and cancellations wait for the next poll from
  the game.
- Progress counts steps, not crafting time, since recipe durations aren't
  available. A job with one slow step and 63 quick ones shows as nearly done
  once the 63 are made, and its time left is as far off.
- The game doesn't show OpenComputers inside a machine, so "nothing back
  from its machine" can't tell a stopped machine from a long recipe, and
  which interface holds a step's items is a guess when several have its
  pattern. Two interfaces on one machine are only taken as one when they
  share a name and touch a common block.
- On a CPU without a Crafting Monitor, a job that starts right after
  another ends looks like the same job, so a pin carries over to it. The
  same goes for two back-to-back jobs making the same item on any CPU.
- Stock rules react within `WATCH_INTERVAL_SECONDS` (1 minute by default).
  AE2 picks the CPU for each craft itself, so the idle-CPU limit is checked
  when a craft is requested, not enforced while it runs.
- The pattern check only knows GregTech's recipe maps (GT++, bartworks
  and the other GregTech addons included). It compares what a pattern
  matched before with what matches now, so a pattern that never matched a
  GregTech recipe isn't checked, and a new, cheaper way to make something
  your patterns make doesn't show.
  It also needs game data built with recipes: versions built before that
  can't be compared until they're rebuilt.
- With HTTP disabled in the Minecraft server's OpenComputers config, nothing works. The
  only alternative is an in-game display.
