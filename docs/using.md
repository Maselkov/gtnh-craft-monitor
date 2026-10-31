# Using the web page

- [Crafts tab](#crafts-tab)
- [Pins and notifications](#pins-and-notifications)
- [Webhooks](#webhooks)
- [Server status](#server-status)
- [Power tab](#power-tab)
- [Network tab](#network-tab)
- [Requesting and cancelling crafts](#requesting-and-cancelling-crafts)
- [Stock rules](#stock-rules)
- [Limitations](#limitations)

## Crafts tab

One card per crafting CPU, updated within a second of each report from the
game. A busy card shows:

- **Output item** — needs an AE2 **Crafting Monitor** block in that CPU's
  multiblock. Without one, the card shows "Unknown output" and a link to
  [Crafting CPUs](in-game.md#crafting-cpus), which lists what else a CPU
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
  live while open. With an
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
stock history. Jobs that were already running when the server started show no
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

Icons and the link to the item's page need `PUBLIC_URL`, the address the page
is reached at from outside; without it, posts are text only. **Send test** in
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
  or a scan running over 10 minutes), crashed, or had its last scan turned
  away as incomplete. A grey dot on Patterns means there's no ME Interface
  Terminal, which is optional.
- the size of each database and the free space where they're kept.
- when stock history was last thinned, and how many rows that removed.
- how many browsers get push notifications, and how the last webhook post
  went.
- how long the server has been up, its release, and the game data version.

Until a tab's script first reports, the Crafts, Power and Network tabs
say which script they're waiting for. Admins get a **Connect a computer**
button there that opens this dialog at
[Connect a computer](in-game.md#from-the-web-page).

## Power tab

Stored EU over time. The Hour, Day, Week, Month, and Lifetime
ranges are downsampled on the server to at most 800 points, so long ranges
stay fast. Readings are kept in `data/power.db`.

Above the chart, an arrow shows the machine's net EU/s over the last 5
seconds (hover it for input and output separately), and a line gives the
rate over the past hour with the time until the machine is full or empty
at that rate. The estimate needs at least 10 minutes of readings.

With several machines (`MACHINES` in
[power_monitor.lua's settings](in-game.md#power_monitorlua)), buttons above
the readout switch between them, and the page's URL
(`/power?machine=Batteries`) keeps the choice for links and link previews.
In Discord, a link to the tab previews the latest reading and the day's
chart.
The unnamed machine shows as **Main**, and the tab badge follows it.

With an LSC reporting and EU in your wireless network, there's also a
**Wireless** entry. The network has no capacity, so it shows only stored EU and
its rate, with no fill. It doesn't count toward the fill on the tab button.

## Network tab

Every item and fluid in the ME network, with icon, name, and amount (fluids in
mB), and its essentia with Thaumic Energistics installed. It refreshes every
minute, but its data changes only when a scan finishes.

- Click an item to open its stock history chart. Each item has its own URL
  (`/network/item/<mod:internal:damage>`, `/network/item/@<aspect>` for
  essentia) that can be shared; link previews
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
- With HTTP disabled in the Minecraft server's OpenComputers config, nothing works. The
  only alternative is an in-game display.
