## What this is

A homelab dashboard for GTNH (GregTech New Horizons) that shows:
- Live AE2 crafting-CPU status (what's crafting, progress, ingredients),
  with server-side multi-user pin/notify-when-done tracking for individual
  crafts, and craft request/cancellation submission from the browser
- An ME network item/fluid browser, with historical quantity charts per
  item, advanced NEI-style search (`@mod`, `term1|term2`, `-exclude`,
  `"exact phrase"`), and pinning
- A GregTech machine's stored energy over time (built for a Lapotronic
  Super Capacitor, works for any GT multiblock), including a live net
  in/out trend indicator read straight from the battery's own tracking
- Item/fluid icons pulled from a one-time NESQL export of the actual
  modpack

Two OpenComputers Lua scripts POST to a Flask server (a third submits
craft-status data too); the server stores state (in-memory for live
crafting/network status, SQLite for power history, item history, network
snapshot persistence, and craft completion tracking) and serves a
static HTML/JS frontend.

## Architecture

```
craft_monitor.lua   --POST--> /api/crafts  --\
power_monitor.lua   --POST--> /api/power   ---> Flask (server/gcm/)   --> browser
network_browser.lua --POST--> /api/network --/
                                               SQLite: app.db, power.db,
                                               item_history.db
```

All three Lua scripts are independent programs with their own polling
loops - they don't know about each other. Each is deployed as its own
OpenOS `rc` service (`/etc/rc.d/<name>.lua`, managed with `rc <name>
start/stop/enable/...`) rather than launched together from one script -
`start_monitors.lua` (which used OpenOS's `thread` library to run them
concurrently, since plain sequential execution doesn't work here - each
script's `event.pull` would block the other, OpenOS being single-
threaded by default without it) is retired.

**File organization, OC side**: three shared files, two loading
mechanisms, split by what kind of thing each one actually is.

`oc/config.lua` is just `SERVER_URL`/`API_KEY` - real, user-edited
deployment data, nothing else. `dofile()`'d by absolute path from
`/home/config.lua` - whether OpenOS's `require()` directory-relative
search (finding a file next to the CALLING script) works reliably for a
script loaded through `rc`'s own sandboxed loader was never confirmed,
and `dofile()` with an absolute path sidesteps that question entirely.

`oc/json.lua` (JSON encode/decode - OC has no JSON library built in,
confirmed against its own official API list) and `oc/http.lua` (the
HTTP request-with-timeout helper, including the `computer.uptime()`-
based stall detection and the OC thread-leak bug #3580 avoidance) are
handled differently: both are real libraries, deployed to `/usr/lib/`
and loaded with `require("json")`/`require("http")`. That's reliable
for a different reason than the directory-relative case above -
confirmed directly from OpenComputers' own `package.lua` source, OpenOS
sets `package.path = "/lib/?.lua;/usr/lib/?.lua;/home/lib/?.lua;./?.lua;..."`,
and `/usr/lib/?.lua` is a fixed, ABSOLUTE entry there, not resolved
relative to wherever the calling script lives - genuinely not the same
uncertain mechanism `config.lua` avoids. Tested directly (not just
reasoned about): a script in one directory calling `require("json")`
and `require("http")` correctly found both files in a completely
different one, via this exact `package.path` string, with `config.lua`
loaded the dofile() way in the same test. What this does NOT confirm:
whether `rc`'s sandbox preserves the standard `package`/`require`
machinery completely unmodified as part of managing a service - the
path being absolute rules out the specific concern that motivated
`config.lua` using `dofile()`, but isn't the same as having confirmed
`require()` itself behaves identically once `rc` is involved.

`http.lua` didn't always exist as its own file - it used to be bundled
into `shared_config.lua` alongside `SERVER_URL`/`API_KEY`, which was
the wrong shape: 72 of that file's 99 lines were `request_with_timeout`
and its helpers, entirely independent of the two config values sitting
next to them (the function takes `url`/`headers` as PARAMETERS, never
referencing config internally at all). Split out once that mismatch
was actually looked at, rather than left as "config" holding mostly
non-config code. `json.lua` and `http.lua` need slightly different
scripts: `craft_monitor.lua` and `network_browser.lua` both need
`json.lua` (each parses a JSON response body back at some point);
`power_monitor.lua` doesn't, since its payload is always a flat object
of plain numbers it builds directly with `string.format`. All three
scripts need `http.lua`. Both libraries used to be duplicated (with
real, if minor, drift on json.lua's part - confirmed by diffing the
copies directly before consolidating) across whichever scripts needed
them; consolidating means a fix like the json_encode-crash pcall fix
(see "Network scan integrity" below) - which had to be applied
separately to multiple call sites across multiple files - only needs
to happen once from here on.

**File organization, server side**: `server/app.py` is only the
entrypoint (`python app.py` to serve, `python app.py new-token <name>`
for admin recovery). The server itself is the `server/gcm/` package:

```
gcm/__init__.py     create_app() - the ONLY place startup work happens
gcm/config.py       every env setting + data paths (read as config.X at call time)
gcm/db.py           SQLite connections, transaction(), schemas + migrations
gcm/store/*.py      ALL other SQL, one module per domain: power, items,
                    crafts, requests, users
gcm/auth.py         API key, signed-in user, roles, bootstrap, route decorators
gcm/security.py     trusted-proxy wrapper, bootstrap/cross-origin hooks, headers
gcm/state.py        in-memory live state (crafts, network scan, CPU
                    transition tracking) + reset() - plain data, no SQL
gcm/commands.py     craft/cancel request queues + their history rows
gcm/tracking.py     CPU job ends -> craft events + completions
gcm/inventory.py    network scan protocol, snapshot reload, item history
gcm/icons.py        icon lookup + images.zip access
gcm/charts.py       matplotlib PNGs for OpenGraph, with cache + rate limit
gcm/routes/*.py     one Flask Blueprint per area: crafts, craft_requests,
                    network, power, users, pages
```

Routes handle HTTP only; the logic behind them lives in the service
modules above (`tracking`, `commands`, `inventory`), and no route module
imports another. `state.py` holds data and nothing else, so it doesn't
import `store`.

Four rules keep this layout honest:
- **Importing any `gcm` module has no side effects.** Schema creation,
  migrations, restart cleanup, admin bootstrap and the network snapshot
  reload all run inside `create_app()`. The test suite relies on this -
  it points `create_app()` at a temp directory instead of juggling env
  vars before import.
- **SQL lives only in `db.py` and `gcm/store/`.** Routes validate the
  request and shape the response; store functions open their own
  connection, return plain tuples/dicts, and make each change that must
  be atomic a single function. `tests/test_store_boundary.py` fails on
  a query or connection anywhere else.
- **Settings live in `config.py`, and other modules read them as
  `config.NAME` at call time**, never `from gcm.config import NAME` and
  never `os.environ` themselves - otherwise `configure()` (and tests'
  monkeypatching) would silently not reach them. The one exception is
  the bootstrap admin token/name, which `auth.bootstrap_admin()` reads
  from the environment when it runs, since nothing else uses them.
- **Security hooks never list routes by name.** The bootstrap-rotation
  block covers every route whose auth decorator is `login_required`,
  `operator_required` or `admin_required`, and the cross-origin check
  covers every non-GET request that carries a live session. A new
  route is protected without being listed; `tests/test_security_endpoints.py`
  requests every registered route to prove it. Only `NO_STORE_ENDPOINTS`
  is still by name, and that test checks each name exists.

**Every route declares its auth policy with a decorator from
`gcm/auth.py`** - `api_key_required` (the in-game scripts),
`login_required`, `operator_required`, `admin_required` (these set
`g.user`), `public`, or `custom_check` (checked in the view, with a
comment saying why). `tests/test_route_auth.py` fails if any route has
none, and pins the exact list of public routes, so making something
public is always a visible, reviewed change. A view that reads
`g.user` without its decorator crashes rather than serving anonymously.

Craft requests and cancellations share one `CommandQueue` class
(`commands.py`) - they used to be two hand-copied implementations of the
same pickup-timeout/result-timeout/retention lifecycle, differing only
in their numbers and messages. Two guarantees matter to the game side:
- **Each command is handed out at most once.** `/pending` returns only
  records nobody has picked up yet. Running a craft twice costs double
  resources and a second cancel can hit the next job on that CPU;
  a lost `/pending` response only costs a request that times out.
- **Ids keep counting across restarts.** `create_app()` starts each
  queue above the highest `request_id` in its history table.
  craft_monitor.lua reports results by id and may still hold one from
  before a restart; with a reused id, that late result landed on an
  unrelated new request.

- **A command is queued only once its history row exists.** `add()`
  takes a `persist` callback and runs it before the record becomes
  visible to `/pending`. If the SQLite write fails, the browser gets an
  error and the game never sees the request, so a user who retries
  can't end up with two crafts. The result endpoints also write the
  history row first: if that fails, the record is still pending and
  its expiry closes the row.
- **A cancel names the job, not just the CPU.** A CPU runs one job
  after another, and the job the user saw may end before the game picks
  the cancel up. The request carries `expected_output` (mod, internal,
  damage from the latest status report). The server answers 409 if the
  browser's copy already differs, and craft_monitor.lua compares it with
  the CPU's live `finalOutput()` before calling `cancel()`. A CPU without
  a Crafting Monitor reports no output, so for it only the name is
  checked. Two back-to-back jobs making the same item still can't be
  told apart.

Every change to a queued record goes through a `CommandQueue` method
(`add`, `claim_pending`, `resolve`, `dismiss`, `select`); routes only
ever see copies. The game's result wins over an expiry: a result that
arrives after the server gave up waiting still updates both the
in-memory record and its history row, since it's what actually
happened in-game. An expiry, in turn, only closes a history row that
nothing has resolved yet (`only_if_open` in `store/requests.py`). A
pending request can't be dismissed - the game may still report on it.

**File organization, frontend**: `server/index.html` is a small shell;
styles are `server/static/app.css` and the code is ES modules split per
area under `server/static/js/` (`crafts.js`, `network.js`, `power.js`,
...). `main.js` is the only `<script>` in the page; every other module
is reached through its imports, and each file's `import` lines say
exactly what it uses from where. Chart.js stays a CDN global.

Rules that keep this working:
- **Only `main.js` runs anything.** Every other module just declares
  functions and state - importing one has no side effects, which is
  also what lets the node tests import them directly. Listeners are
  registered inside each area's `setup...()` function, and `main.js`
  calls all of them before anything renders. The modules import each
  other in cycles (auth <-> crafts, tabs <-> network/history, ...),
  which is only safe BECAUSE nothing reads an import at top level.
- **Another module's `let` is read-only to you.** Imports are live but
  can't be assigned; state other modules need to reset goes through a
  small function in its owning module (`clearPins()`,
  `setPendingItemFromUrl()`).
- **No inline event handlers** - not in `index.html`, not in markup the
  JS builds (`tests/test_frontend_assets.py` enforces this). Clickable
  elements carry `data-action="name"` plus `data-*` arguments, and each
  setup function registers its actions with `delegateActions()`
  (`util.js`). Action names are unique page-wide - `delegateActions()`
  throws on a duplicate, since everything bubbles to `document` and a
  duplicate would fire twice. Non-click events (search and amount
  inputs, Enter to sign in, backdrop clicks via `onBackdropClick()`)
  are plain listeners in the same setup functions. Values like item or
  CPU names are only ever HTML-escaped into attributes, never spliced
  into JS source.

Content-Security-Policy: the page is served with a strict CSP
(`security.content_security_policy()`, sent by `pages.index()`), built
at startup from `index.html` itself so it can't drift from the page:
- `script-src 'self'` plus the two exact Chart.js file URLs - not all
  of `cdn.jsdelivr.net`, which hosts every npm package and would let an
  injected tag load anything. No `'unsafe-inline'`, so an injected
  `<script>` or `on*=` attribute simply doesn't run.
- Inline styles only by hash of the exact `style="..."` values in
  `index.html` (`'unsafe-hashes'`; today `display:none;` and two
  margins). Anything the JS builds must not carry `style="..."` -
  set `el.style.*` instead, which CSP doesn't restrict (the progress
  bar width does this). `tests/test_csp.py` enforces both.
- If you add a new external script, or a new inline `style=` value in
  `index.html`, the policy picks it up automatically on restart; a
  `style=` in JS-built markup is blocked, and the browser test fails
  on the resulting CSP violation.

Caching: `index.html` loads `main.js` and `app.css` with
`?v=<content hash>` (`pages.load_index_html()`), but modules import each
other by plain path, so `/static` is served with `Cache-Control:
no-cache` - browsers revalidate each file (a 304 when unchanged) instead
of running stale code after a deploy.

History, for context: the frontend started life as a multi-thousand-
line Python string (`INDEX_HTML`) embedded directly in `app.py`, which
meant every JS change needed a regex extraction of the `<script>` block
just to syntax-check it (and a JS regex inside a Python string literal
caused a `SyntaxWarning` on every test run). It moved to a plain
`index.html` first, then to `static/` once it was large enough that one
3,700-line file was its own friction.

The three SQLite files are split by kind of data, and that split was
kept deliberately (discussed twice, both times NOT merged):
- `app.db` - everything relational: users, tokens, sessions, CPU and
  item pins, completions, craft events, request/cancel audit. Until
  schema step 3 it was named `craft_history.db`; startup renames an
  old one (`db.adopt_legacy_app_db()`, which refuses to run if both
  names exist).
- `power.db` and `item_history.db` - bulk time series.

Merging them would gain nothing (no query joins across files) and cost
something: SQLite has one writer per file, so a network scan rewriting
`network_snapshot` would hold up sign-ins and pins. Splitting the
relational tables further would cost more: `user_completions` alone
points at both `users` and `craft_events`, and foreign keys can't cross
files. Revisit only if a genuine feature needs a cross-file join.

All three run in WAL mode (`db._use_wal()` at startup; each connection
sets `synchronous = NORMAL`), so readers never wait on a writer. The
catch is that a live `.db` file alone isn't a backup - recent commits
sit in its `-wal` file - so `python app.py backup <dir>`
(`db.backup_all()`) copies all three with SQLite's online backup API.

**Schema changes are numbered migration steps** (`db.py`,
`APP_MIGRATIONS` and friends). Each file's `PRAGMA user_version`
records how many steps it has had; startup runs the rest, each in one
transaction with its version bump. To change a schema, append a step -
never edit one that has shipped. Step 1 is the baseline from before
versioning and has to stay idempotent: every older install reports
version 0 whatever state its tables are in (a real one still had the
retired `craft_keys` table and none of the request-history tables). A
file at a higher version than the server knows stops startup rather
than being run by older code. Foreign keys are enforced on app.db
(`PRAGMA foreign_keys`, set per connection in `db.app_db()`). Every
table that exists only for its user (`user_pins`, `user_completions`,
`user_item_pins`, `access_tokens`, `sessions`) has `user_id ...
ON DELETE CASCADE`, so deleting a user is one `DELETE FROM users`.
The request/cancel audit tables have no key on purpose - their rows
outlive the user. SQLite can't add a key to an existing table, so step
3 rebuilds those five tables; `_USER_OWNED_SCHEMAS` in `db.py` is their
full current schema.

## Network scan integrity (why scan/finish looks the way it does)

This ended up being the single most-iterated-on piece of server logic
in the whole project, across multiple real bugs found in production -
worth understanding as a whole before touching `finish_scan()`,
`add_batch()`, or `start_scan()` in `gcm/inventory.py`.

**The original mystery**: network_browser.lua's scans would occasionally
just stop dead, with zero explanation, for a long time. Multiple wrong
theories were chased first (server TPS lag stalling `computer.uptime()`,
thread-based timeouts that turned out to trigger OC's own thread-leak
bug instead). The ACTUAL root cause, eventually confirmed: `json_encode()`
was called completely unprotected (no `pcall`) when building each
result sub-chunk's POST body. On the rare sub-chunk too large even after
chunking (some GT "mega" meta-items expand one candidate id into far
more actual items than expected), `table.concat` inside the encoder
threw "not enough memory for buffer allocation" - and since this all
runs inside a DETACHED thread (`thread.create(fn):detach()`), an
uncaught error there just kills that thread silently. Under the old
`start_monitors.lua` launch model there was nothing to catch or report
that at all. Converting to `rc` (which happens to log uncaught service
exceptions to `/tmp/event.log`) is what actually surfaced it - `rc`
didn't cause the bug, it was just the first thing that could ever tell
anyone about it. Fixed by wrapping every `json_encode()` call site in
`pcall`, turning a fatal silent thread death into a caught, logged,
recoverable per-chunk error that lets the scan continue.

**That fix alone wasn't the end of it.** A restart-interrupted (or
otherwise partial) scan could still get its incomplete results
committed via `scan/finish` - every item present in the previous
snapshot but missing from the incomplete new one would get recorded as
"vanished" (a false size=0 history point), even though it never really
left the network. Went through two real design iterations to close
this:

1. First attempt: reject `scan/finish` if the new item count was under
   some retention-fraction threshold (50%) of the previous snapshot.
   Deliberately REMOVED later - no threshold could be simultaneously
   loose enough to accept a genuine, large, intentional network change
   (voiding half a network in one go) and tight enough to reliably
   catch a partial scan, and worse, it could get permanently stuck
   rejecting a legitimate smaller network forever (a correctly-complete
   smaller scan just kept getting compared against the same stale
   larger snapshot it could never update past).

2. Current approach, no threshold to tune: `scan/start` mints a fresh
   random token (`current_scan_token` in `state.network`), required
   back on every `scan/batch`/`scan/finish` call. A mismatch means the
   data doesn't belong to the scan currently considered active - either
   the server restarted mid-scan (a fresh process has no memory of any
   previously-issued token) or a newer scan already started on the SAME
   process (scan/start always mints a new token, invalidating whatever
   the previous scan was using) - either way, rejected outright rather
   than committed. On top of that, `scan/finish` also requires
   `chunks_received == chunks_sent` (chunks_received counted
   independently server-side, never trusted from what Lua claims) AND
   `total_errors == 0` (Lua's own end-to-end error tally, covering
   failures a chunk-count check alone can't see at all - a
   `getItemsInNetworkById()` call failing outright never produces a
   chunk to send in the first place). A scan is either provably
   complete or it's discarded; a genuine mass removal with everything
   actually succeeding is correctly accepted, not guessed at.

**A real OC-specific gotcha this surfaced**: the rejection signal has
to live in the response BODY, not the HTTP status code, even though a
non-2xx status would be the more conventional REST signal. Confirmed
directly: `internet.request()`'s response handle has no reliable way to
read a status code from Lua in the first place (a real, still-open OC
GitHub issue - #3398 - found that iterating the handle to read the
body THROWS on a non-2xx status, and `response()` itself returns nil in
that same situation), and separately, `shared_config.lua`'s (now
`http.lua`'s) own `request_with_timeout` never even tries to inspect
the status - it only distinguishes "the read itself succeeded" from
"it didn't". So every rejection here returns a normal 200 with
`{"ok": false, ...}` or `{"ok": true, "rejected": true, ...}` in the
body, and Lua has to `json_decode` every response and check it
explicitly rather than relying on transport-level success.

`network_browser.lua`'s `run_scan()` tracks `chunksSent`/`totalErrors`
end to end and aborts the REST of a scan immediately (skipping
remaining sub-chunks and fluids, never calling scan/finish at all) the
moment the server signals a stale token mid-scan - no point continuing
to send data nobody's listening for.

Separately: `network_snapshot` (in `item_history.db`) is a full mirror
of the live network state, rewritten wholesale on every real scan (not
change-only, unlike `item_history`) - loaded back into memory once at
server startup (`inventory.load_snapshot()`) so a server restart doesn't
leave the Network tab empty until the next scan completes. Marked
`is_reconstructed: true` until the first real post-restart scan
completes, so the frontend can show "data from before the restart"
rather than presenting it as fresh. A real bug caught here during
testing: icon resolution only ever happened at LIVE scan ingestion time
(`inventory.add_batch()`), a path reconstructed items never go through -
`load_snapshot()` has to call `icons.attach_item_icons()` itself,
confirmed by testing (every icon was silently missing after a restart
until this was added).

## Gotchas discovered the hard way (read before assuming an API works)

**AE2's OC API is not what the method name suggests.** There is no
`getCraftingCPUs()` - the real method (confirmed from
`GTNewHorizons/OpenComputers` source, `NetworkControl.scala`) is
`me.getCpus()`, returning `{name, storage, coprocessors, busy, cpu}` per
CPU, where `cpu` is a proxy object with `activeItems()`, `pendingItems()`,
`storedItems()`, `isBusy()`, `finalOutput()`, `cancel()`. If something
here doesn't seem to work, check the actual OC/GT source before assuming
the API - GTNH's own docs and community info have been wrong or
incomplete more than once in this project.

**There's no pre-submission craft preview/plan API at all.** `.request()`
on a `Craftable` is fire-and-forget, returning only a `CraftingStatus`
handle with `isComputing()`/`hasFailed()`/`isCanceled()`/`isDone()` -
`hasFailed()` carries no reason (confirmed from source: nothing populates
one), and there's no `getCraftingPlan()`/preview method, so a "what's
missing before I even submit" UI isn't achievable through this API
surface at all, not just unbuilt.

**`os.time()` in OpenComputers is NOT a Unix timestamp.** It's
reimplemented to return in-game seconds since world creation, on the
game's own clock (pauses when the game pauses, runs faster than real
time normally). Using it for anything compared against a server's real
wall-clock time will silently corrupt that comparison. Both Lua scripts
deliberately send no timestamp at all - the server stamps arrival time
with its own clock instead, which is already correct for this use case
(POST latency is negligible).

**AE2 item `name` fields are inconsistently formatted.** Solid items are
usually `"modid:internalname"` (e.g. `"gregtech:gt.metaitem.03"`). But GT's
Fluid Discretizer pseudo-items (molten materials shown as items) have NO
colon at all - `name` is just the bare Forge fluid registry name (e.g.
`"molten.mutatedlivingsolder"`), and critically `hasTag=false` on these -
the fluid identity is NOT NBT-encoded (that was an early wrong guess,
disproven empirically). The presence/absence of a colon in `name` is what
`gcm/icons.py`'s `resolve_icon()` uses to decide whether to look up the
`by_key` (item) or `fluids_by_key` (fluid) lookup table - and the same
colon-presence heuristic is reused elsewhere for the same item/fluid
distinction (clean item URL paths, `/network/item/mod:internal:damage`
vs bare `/network/item/internal` for a fluid).

**GT5's item-icon "meta items" are rendered procedurally, not shipped as
static files.** There's no way to get correct icons for most GTNH items
(ingots, dusts, plates, circuits) by copying texture files out of mod
jars - GT composites them at runtime from material+shape layers. The only
practical solution was running `NESQL-Exporter` (a mod that renders every
item once in a real game client and exports the result) and building a
lookup table from that. See "Icon pipeline" below if this ever needs
redoing.

**`nesql-exporter`'s original release (D-Cysteine) doesn't build against
current GTNH.** Used `ShadowTheAge/nesql-exporter` fork instead - chosen
over the official `GTNewHorizons/nesql-exporter` org fork because
ShadowTheAge's GT5-Unofficial version pin (`5.09.54.20`) was much closer
to what's actually shipping than the org fork's (`5.09.52.342`). Neither
fork publishes prebuilt jars - has to be built from source
(`./gradlew build`). Building it hit two further issues, both actual bugs
in the fork's dependency pins, not anything environment-specific:
- The pinned RetroFuturaGradle plugin version (`1.3.35`) had been purged
  from GTNH's Maven repo. Bumped to `1.4.9` (the latest `1.4.x` - avoided
  jumping to `2.0.x`, which requires Gradle 9 + Java 25, a much bigger
  ask for an unrelated fix).
- A transitive dependency chain (`GTNH-Intergalactic` → `Galaxy-Space-GTNH`
  → `Galacticraft` → `ironchest:6.0.86`) pointed at an `ironchest` version
  that no longer exists anywhere. Fixed with an `exclude(group=...,
  module="ironchest")` on the `GTNH-Intergalactic` `compileOnly`
  dependency, since it's not needed at runtime for this tool anyway.

**A `%2F` inside a URL *path segment* gets mangled by reverse proxies.**
This is why icons are served as `/icons?path=<encoded>` (query string)
rather than `/icons/<path>` - a path like `item/gregtech/foo.png` needs
its slashes encoded, and many reverse proxies refuse to treat an encoded
slash in a path segment as a real separator (security measure against
path-traversal tricks). Query-string values don't have this problem.
(Item detail pages went the other way - clean paths, `/network/item/
mod:internal:damage` - deliberately, since that identifier never needs
an embedded slash; the two situations aren't actually the same one.)

**Desktop `Notification()` permission requires a secure context** -
`https://` or `http://localhost` specifically. Plain `http://` on a LAN
hostname/IP (what a bare `docker compose` setup gives you) means the
permission prompt silently never appears, no error. This project's
current deployment is behind a reverse proxy with real TLS for exactly
this reason.

**GregTech multiblock energy is read via a `gt_machine` OC component**
(`getEUStored()` / `getEUMaxStored()`, found via a real GTNH bug report -
`GTNewHorizons/GT-New-Horizons-Modpack#8619`, fixed upstream via
`GTNewHorizons/OpenComputers#78`). `power_monitor.lua` still defensively
falls back to parsing `getSensorInformation()`'s text if the direct call
fails or looks implausible, in case of an older OC build. Confirmed
directly from a real captured dump (a Lapotronic Super Capacitor) that
`getSensorInformation()` ALSO carries average EU in/out over several
windows (5s/5m/1h) and a time-to-empty estimate, as
`kekztech.infodata.lapotronic_super_capacitor.avg_eu_in.sec\0\5`-style
backslash-delimited lines - `power_monitor.lua` parses these by
substring match on the suffix (`avg_eu_in.sec` etc.), not the full key,
so it stays correct without assuming every storage structure type uses
that exact `kekztech...` prefix. Only the 5s window is currently
surfaced in the UI (5m/1h are sent and stored server-side regardless,
in case they're wanted later).

**A downsampled chart series is not safe to reuse for a "live" readout.**
The power tab's live stored/capacity/percentage line used to be derived
from `rows[-1]` of the range-filtered, DOWNSAMPLED chart points - which
meant switching the chart's range button (Day vs Week) visibly changed
the "current" percentage, since a downsampled bucket's last row is an
AVERAGE, not a true single reading, and different ranges downsample into
differently-sized buckets. Confirmed as a real reported bug, then
confirmed the fix's own regression test would have missed it on the
first attempt too (too few seeded rows to actually trigger downsampling
at all, so the buggy code path never even ran) before rewriting it with
enough density to genuinely exercise the bug. Fixed with a dedicated,
always-unfiltered "true latest row" query, entirely separate from
whatever the chart's current range happens to be.

**A CSS overlay's text must match the real input's font-weight exactly,
not just its font/size/padding.** The search-box syntax highlighting
(colored `@mod`/`-exclude`/`"quotes"` spans over an otherwise-invisible
real `<input>`) initially bolded the highlighted spans for emphasis -
which caused the cursor to visibly drift out of alignment the longer a
query got, since bold glyphs render wider than the real input's
(invisible, still normal-weight) text underneath, and the two layers'
character widths silently diverge as more of the query is highlighted.
Fixed by dropping font-weight from the highlight classes entirely -
color alone differentiates the highlighted terms now.

## Design decisions worth knowing (not bugs, but non-obvious choices)

**Progress % is an approximation, not AE2's real job timer.** It's
computed from `storedItems()` vs `pendingItems()` size ratios - weighted
by item count, so a job needing one expensive item and 63 cheap ones
looks "mostly done" once the 63 land, even if the expensive one is still
crafting. This is also what the server uses to classify a finished
craft-event as `finished` (progress ≥99%) vs `incomplete` (anything
else, covering cancellation/interruption without claiming more certainty
than the data supports).

**Craft completion tracking is entirely server-side now**, not
client/localStorage-based (an earlier version was - see git history /
the long chat this came from if curious about that iteration). Two
reasons it moved server-side: (1) a closed browser tab can't observe
anything, so a completion that happens while every tab is closed was
invisible to the old design; (2) "was busy, now isn't" looked identical
for a finished vs. a cancelled craft with no way to distinguish them
client-side. `craft_events` (permanent, unpruned - also intended as raw
material for future historical/analytics views) logs every CPU
job end unconditionally, regardless of whether anyone has it pinned.
`user_pins` / `user_completions` are per-user.

**Notification dedup is baseline-on-load, not a persisted "seen" list.**
An earlier version stored notified-completion ids in `sessionStorage`,
deliberately reasoning that a genuinely fresh session (as opposed to a
mere page refresh) re-notifying for something still pending was
acceptable - that reasoning was overridden later as a real reported
annoyance: closing and reopening the tab could re-fire a notification
already seen. Current design: the FIRST completions fetch after a page
loads just records what's already pending as a baseline, without
notifying for any of it; only a completion arriving AFTER that baseline
is established ever triggers a real notification - a refresh and an
actually-closed-then-reopened tab are now treated identically,
deliberately. Separately, a cross-tab race (two tabs of the page open
at once, both independently deciding "this is new to me") is closed
with a `localStorage`-based claim (shared across tabs, unlike
`sessionStorage`) - the first tab to see a new completion claims it
there before actually firing the Notification, so a second tab sees it
already claimed and skips.

**User identity comes from the session cookie**, set by signing in
with a personal access token (see `gcm/auth.py` and the README's "User
accounts"). It replaced an earlier self-issued UUID sent as
`X-User-Id`, which was identity without authentication. `user_id` is
an opaque string everywhere (`usr_<hex>` today); nothing should assume
its format. Deleting a user removes their tokens, sessions, CPU pins,
item pins and pending completions; their craft request/cancel history
stays as the audit trail.

**A pin can only ever exist for a currently-busy CPU**, and is always
auto-removed the instant that CPU's job ends (finished or not). A job
ends when its CPU goes busy->idle, or when a busy CPU reports a
different final output than it did last poll - a CPU that finishes and
starts its next job between two polls is never seen idle. The same
reasoning applies when the game accepts a browser craft request: it
only starts one on a CPU it saw idle, so a job the server still tracks
there from before the request was made is closed first
(`tracking.start_requested_job()`). This is
deliberate: "which CPU" is incidental (AE2 gives no other handle on a
job), "which item" is what the user actually cares about, and a CPU
immediately starting a new job after finishing shouldn't silently
continue being tracked under the old pin. Pinning an idle CPU is
rejected server-side (`POST /api/pins` checks the CPU's live `busy` state
against `state.crafts["jobs"]`), not just disabled in the UI - the UI disabling
the button is a courtesy, not the enforcement.

**`user_completions` rows are deleted on acknowledge**, not flagged with
an `acknowledged` column - row presence itself means "pending
acknowledgment". They're also pruned by age (30 days) regardless of
acknowledgment status, opportunistically (on insert and on
`GET /api/completions`), rather than via a background scheduler - avoids
adding a thread/cron for what's a very low write-volume table.

**Item search syntax intentionally matches NEI's own, confirmed against
real screenshots/the wiki, not guessed extensions.** `@mod` (pink),
`-exclude` (blue, only the dash itself colored, not the whole term),
`term1|term2` (OR), `"exact phrase"` (orange quote marks, literal
substring including spaces, not split into separate AND'd words) all
mirror what NEI actually does. `@mod` matches against the raw modid
(`gregtech`, `thaumcraft`), not a curated display-name table this
project doesn't have, so it degrades gracefully rather than exactly
matching NEI for mods where the id diverges more from the marketed
name. The syntax highlighting overlay and the actual matcher share one
tokenizer (`tokenizeSearchText`) rather than two separate parsers, so
they can't disagree about what a `-` or a `"` means.

**Craft quantity accepts metric shorthand and basic math (`10k`,
`4+3*2`), desktop-only.** A hand-written recursive-descent evaluator,
not `eval()`/`new Function()` (a real code-injection vector to avoid
even here) and not a math library (massive overkill for four-function
arithmetic). Desktop-only because switching the input to `type="text"`
(needed to allow non-numeric characters) loses the native numeric
keypad a `type="number"` input gives on mobile, which is better UX
there - detected via `pointer: coarse`, matching how "is this really a
touch-primary device" is decided elsewhere in this project too.

## Test suite (server/tests/)

201 pytest tests across 17 files, covering every endpoint - crafts, CPU
pins, craft requests, cancellation, network scanning (including the
scan-integrity mechanism above), item history, network item pins,
power readings (including the DB migration), auth/admin, OpenGraph
tags - plus the pure helpers, the security endpoint lists and the
frontend asset wiring. Plus 15 node:test tests (`server/tests/js/`)
for the frontend's pure functions: the NEI-style search tokenizer and
matcher, the craft amount evaluator and `formatQty`. They load
by importing the real `static/js/` modules directly.
And a browser test (`server/tests/e2e/run.mjs`, no dependencies - it
drives headless Chrome over the DevTools protocol with Node's built-in
WebSocket): it starts the real server on seeded data and clicks through
every interactive part of the page - tabs, pins and acknowledgements,
ingredients/idle toggles surviving the 3s re-render, the cancel and
craft-request dialogs (including the amount maths), item history,
network search/sort, admin user management, sign in/out, back/forward -
and fails on any uncaught error or console error. Elements are found by
id or visible text rather than by how their handlers are wired, so it
keeps meaning the same thing across frontend refactors. Run with:
```
cd server && pip install -r tests/requirements-test.txt && pytest
node --test 'server/tests/js/*.test.js'   # from the repo root
node server/tests/e2e/run.mjs             # from the repo root; needs Chrome/Chromium
```

Fully isolated from real data: `conftest.py` calls `gcm.create_app()`
once per session with a fresh temp directory as `data_dir`, an autouse
fixture calls `gcm.reset_runtime_state()` and empties every SQLite table
(discovered from `sqlite_master`) before each test, and the `flask_app`
fixture's test client talks to the real routes in-process - no live
server or Docker container needs to be running. New module-level state
needs adding to `state.reset()`; new tables need nothing.

Two genuinely worth knowing about, not just "tests exist": a regression
test for the scan-integrity mechanism initially PASSED even with the
fix deliberately reverted, because it seeded too few rows to actually
trigger the bug condition - caught by the discipline of reverting a fix
and confirming its own test fails before trusting that it passes, not
by luck. And the power-chart-downsampling bug above was caught the same
way. Both are a useful reminder for whoever extends this suite: a test
that merely runs without erroring is not the same as a test that would
actually catch the regression it claims to guard against - worth
verifying the second thing specifically for anything non-trivial.

Not covered: the `oc/*.lua` scripts (no Lua test harness exists), and
browser notifications (headless Chrome denies the permission).

## Icon pipeline (if it ever needs redoing)

1. Run `NESQL-Exporter` in-game (`/nesql`) → produces an HSQLDB database
   plus `image.zip` (all rendered icons) under `.minecraft/nesql/<repo>/`.
2. `ExportItems.java` / `ExportFluids.java` (standalone JDBC dump tools,
   not part of the running app - one-off use) connect read-only and
   `SELECT * FROM Item` / `SELECT * FROM Fluid` to CSV, deliberately not
   picking specific columns (avoids guessing Hibernate's physical column
   naming - just reads the real header row back).
3. Those CSVs were turned into `server/reference/icons_lookup.json`
   (`by_key`, `fluids_by_key`, `by_label`) - this step was done manually
   during the original build, not by a script currently in the repo.
4. `image.zip` itself is NOT in the repo (too large, and specific to one
   person's modpack export) - it has to be dropped into
   `server/data/images.zip` manually. The server reads icons out of it
   on demand via `zipfile`, never unpacking it to disk.

## Known limitations

- Job-end detection can't see a CPU that finishes and immediately
  starts another job making the same item between two polls, or any
  job switch on a CPU without an AE2 Crafting Monitor (no final output
  is reported at all). A pin there carries over to the next job.
- Fluid icon matching only covers items/fluids that actually appear in
  NESQL's static export - anything created dynamically with no NEI-visible
  registration (rare, but the Fluid Discretizer pattern is an example of
  the general risk) could still come up blank.
- `craft_events` has no cleanup at all by design (see above) - fine for a
  long time given low write volume, but worth knowing it's unbounded.
- No pre-submission craft preview (ingredients/missing items before
  confirming) - not just unbuilt, genuinely not achievable through AE2's
  OC API surface at all (see "Gotchas" above).
- The three SQLite files remain separate - deliberate, discussed, and
  deferred, not an oversight (see "Architecture" above).
- The `oc/*.lua` scripts have no automated test coverage at all (the
  test suites cover the server and frontend only) - anything Lua-side is
  still verified via manual `luac -p` syntax checks and live-server/
  in-game smoke tests during development.
- Single power-source assumption: `power_monitor.lua` targets one
  `gt_machine` (errors out if it finds more than one and
  `CONFIG.COMPONENT_ADDRESS` isn't set) - fine for one LSC, would need
  extending for multiple tracked machines.
