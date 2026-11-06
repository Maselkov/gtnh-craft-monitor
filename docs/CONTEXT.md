## What this is

A homelab dashboard for GTNH (GregTech New Horizons) that shows:
- Live AE2 crafting-CPU status (what's crafting, progress, ingredients),
  with server-side multi-user pin/notify-when-done tracking for individual
  crafts, and craft request/cancellation submission from the browser
- An ME network item/fluid browser, with historical quantity charts per
  item, advanced NEI-style search (`@mod`, `term1|term2`, `-exclude`,
  `"exact phrase"`, `#tooltip`, `$oredict`, `=formula`), item
  descriptions, and pinning
- A GregTech machine's stored energy over time (built for a Lapotronic
  Super Capacitor, works for any GT multiblock), including a live net
  in/out trend indicator read straight from the battery's own tracking
- Item/fluid icons rendered from the actual modpack by a headless
  export (`tools/icon-export/`)

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
gcm/auth.py         API key, signed-in user, roles, setup code, route decorators
gcm/security.py     trusted-proxy wrapper, cross-origin hook, headers
gcm/state.py        in-memory live state (crafts, network scan, CPU
                    transition tracking) + reset() - plain data, no SQL
gcm/commands.py     craft/cancel request queues + their history rows
gcm/tracking.py     CPU job ends -> craft events + completions
gcm/inventory.py    network scan protocol, snapshot reload, item history
gcm/icons.py        icon lookup + images.zip access (versioned paths)
gcm/gamedata_index.py  a bundle's lookups as one read-only SQLite file
gcm/item_info.py    item descriptions, rarity, fluid details, mod names
                    (item_info.json, via the bundle index) by the lookup's keys
gcm/gamedata.py     per-GTNH-version game data bundles: list, download,
                    select, and which one icons.py uses
gcm/gamedata_check.py  whether the game data looks older than the game,
                    for the admins' notice
gcm/charts.py       OpenGraph chart PNGs, with cache + rate limit
gcm/chart_draw.py   draws them with matplotlib, in a child process
gcm/memory.py       hands freed memory back to the OS after each scan
gcm/routes/*.py     one Flask Blueprint per area: crafts, craft_requests,
                    network, power, users, gamedata, pages
```

Routes handle HTTP only; the logic behind them lives in the service
modules above (`tracking`, `commands`, `inventory`), and no route module
imports another. `state.py` holds data and nothing else, so it doesn't
import `store`.

Four rules keep this layout honest:
- **Importing any `gcm` module has no side effects.** Schema creation,
  migrations, restart cleanup, the first-run setup code and the network snapshot
  reload all run inside `create_app()`. The test suite relies on this -
  it points `create_app()` at a temp directory instead of juggling env
  vars before import.
- **SQL lives only in `db.py` and `gcm/store/`.** Routes validate the
  request and shape the response; store functions open their own
  connection, return plain tuples/dicts, and make each change that must
  be atomic a single function. `tests/test_store_boundary.py` fails on
  a query or connection anywhere else. The one exception is
  `gcm/gamedata_index.py`, whose file belongs to a game data bundle, not
  to the app.
- **Settings live in `config.py`, and other modules read them as
  `config.NAME` at call time**, never `from gcm.config import NAME` and
  never `os.environ` themselves - otherwise `configure()` (and tests'
  monkeypatching) would silently not reach them.
- **Security hooks never list routes by name.** The cross-origin check
  covers every non-GET request that carries a live session. A new
  route is protected without being listed; `tests/test_security_endpoints.py`
  requests every registered route to prove it. Only `NO_STORE_ENDPOINTS`
  is still by name, and that test checks each name exists.

**Every route declares its auth policy with a decorator from
`gcm/auth.py`** - `api_key_required` (the in-game scripts),
`login_required`, `operator_required`, `admin_required` (these set
`g.user`), `viewer_required` (the base's own data: anyone, or only
signed-in users once an admin makes the server private - `g.user` may be
None), `public`, or `custom_check` (checked in the view, with a comment
saying why). `tests/test_route_auth.py` fails if any route has none, and
pins the exact lists of public and viewer routes, so making something
public is always a visible, reviewed change. On a private server the
page's OpenGraph tags carry no data, viewer routes' responses are
`Cache-Control: private`, and the page (`auth.js` `onceDataVisible()`)
fetches nothing - not even its first tab - until someone signs in. A view that reads
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
Restoring from the page (`backup.restore()`) runs the same API the other
way, from the uploaded copy into the live file (`db.restore_copy()`),
so nothing on disk is swapped under open connections; then
`gcm.reload_data()` re-reads what startup reads into memory, and pending
craft/cancel requests are dropped since their history rows are gone.

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

**CPUs nobody named.** AE2 reports an unnamed crafting CPU's name as
an empty string (the page showed "CPU ?" for all of them), so every
unnamed CPU was the same one: `tracking` skips a job with no name,
the Ingredients button's `data-cpu=""` made `openIngredientsModal` a
no-op (`openCpu` was falsy, so nothing was ever drawn), and the tree,
pinning and cancelling all looked the CPU up by that name. AE2 also
lets two CPUs share a name, with the same result. Both sides now give
each such CPU a made-up name: its storage, after the shared name if
there is one, numbered among the CPUs with that name and size ("68k
#1", "Main 64k #2"; more coprocessors first, then the list's order),
marked `auto_named` (the page puts a "?" after it). Every CPU sharing
a name gets one, not just the second and later, so none keeps the
plain name merely by being listed first; when the name stops being
shared, the one left gets it back. A name some CPU is really called is
skipped. The script does it
(`get_cpus()`, so a cancel finds the CPU under the name it was
reported as) and so does the server (`name_cpus()` in
`routes/crafts.py`, for scripts still sending it empty); keep the two
alike, down to the label (`size_label()` / `_size_label()`: whole KB,
or MB/GB for an exact number of them) and to reading sizes as numbers
within +-2^53 only (NaN would break Lua's `table.sort`, a float past 2^63
Lua's `%d`, and an integer too big for a float Python's `math.isfinite`).
`test_oc_scripts.py` runs both on the same CPUs to keep them alike.

Why this way. The position in `getCpus()` won't do: the source says
that order is arbitrary. `getCpus()` is a copy of AE2's
`CraftingGridCache.craftingCPUClusters`, a `HashSet` of
`CraftingCPUCluster`s, which don't define `hashCode`, so it's in
identity-hash order and changes with a restart or a rebuilt CPU; and an
inactive or destroyed CPU is left out, moving everything after it. A
rank by size across all the CPUs (tried first) has the same weakness in
another form: any bigger CPU added, removed or going inactive shifts
every smaller one, and ranking only the unnamed ones also shifts them
when one is named. Counting within a size, a CPU of another size can't
take a CPU's name or shift its number. `storage` and `coprocessors` are
fixed when the crafting blocks are placed (`availableStorage`,
`accelerator`; a running job uses the separate `usedStorage`), so names
don't change when a CPU goes busy or idle - deliberately not sorted by
busy, which would move a running job's name whenever another same-size
CPU finishes. What's left: same-size CPUs fall back to the list's
order, so they can swap whenever AE2 rebuilds the set (a restart, or any
CPU added or removed, since a `HashSet` that resizes redistributes its
elements), and a job running on one then is recorded as ended and
started again. Naming a CPU (Shift + right-click with Wire Cutters)
avoids it.

**Why a scan was turned away.** The counts alone said that a scan was
incomplete but not why, and the why only reached `network_browser`'s
debug log in game - which a busy network rotates within the hour (each
item scan writes several lines per batch against a 200 KB cap), so by
the time anyone looked, it was gone. `scan/finish` and `patterns/finish`
now also carry `last_error`, the message of the last error the scan
counted. The server caps it at 300 characters, as the agents' errors
(`agents.py`), and puts it in the reason it logs, returns and shows on
the Status card ("incomplete (121/127 chunks, 6 errors), last: batch
POST failed: ..."). A refused pattern scan is kept in
`state.patterns["last_rejected"]` the way the network's is, so the
Patterns line can say it too. Older scripts send no `last_error`; their
reason reads as before.

**Scan size limits.** A scan whose buffer would pass `MAX_SCAN_ITEMS`
(100k; every NBT variant counts) is ended on the spot: buffer cleared,
token dropped, `last_rejected` set so the Status dialog says why. The
batch gets the same `stale_scan_token` refusal, since that's the one
code scripts already in the field stop on, and the abandoned token is
remembered so its scan/finish, if one still comes, doesn't overwrite the
reason. Ended, not truncated: a partial snapshot would record everything
past the cut as gone. Pattern scans have `MAX_SCAN_PATTERNS` (25k) the
same way. Both bound what a runaway or hostile script (anyone with the
API key) can make the server hold in memory or write to history; a
whole GTNH catalog is ~11k ids, and an item takes ~600 bytes buffered.

**Bodies that aren't quite JSON.** Two kinds made Python's parser
refuse a scan batch, so a 400 - the same batches on every scan, so a
player's scans were refused as incomplete forever, and Flask logs nothing
for a 400:

- Raw control characters in strings: `json.lua` used to escape only
  `\`, `"`, `\n`, `\r` and `\t`. It now escapes every control character,
  but scripts already in the field don't.
- Text that isn't UTF-8: one player's game sent every `§` (the
  formatting-code sign, `§a16k§r ME Storage Cell`) as the single byte
  0xA7, as cp1252/Latin-1 write it, rather than UTF-8's C2 A7. Presumably
  Java's default charset on Windows, somewhere between OC and the HTTP
  request - `json.lua` never sees it, so only the server can fix it.

The server (`gcm/lenient_json.py`, the app's JSON provider) reads a body
that fails to parse a second time, with any byte that isn't part of
valid UTF-8 taken as cp1252 and control characters allowed in strings.
Neither can alter a body's structure, only a string's text. A first
version replaced such bytes with U+FFFD; cp1252 gives the same names
other players' games send. A lenient read is logged once per path until
a restart (it's expected from such a game on every scan), with the text
around the problem; a body that still won't parse is logged every time.

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

**A component call can fail without throwing.** A player's scan crashed
on `#result` after `getItemsInNetworkById()` returned without an error
but without a list either - presumably `nil` and a reason, which is how
OC component calls fail softly. A `pcall` only catches the throwing
kind, so `network_browser.lua` checks the answer is a table too, and
counts anything else as a failed batch with the reason logged.

**`os.time()` in OpenComputers is NOT a Unix timestamp.** It's
reimplemented to return in-game seconds since world creation, on the
game's own clock (pauses when the game pauses, runs faster than real
time normally). Using it for anything compared against a server's real
wall-clock time will silently corrupt that comparison. Both Lua scripts
deliberately send no timestamp at all - the server stamps arrival time
with its own clock instead, which is already correct for this use case
(POST latency is negligible).
`/api/power` still accepts a `timestamp` (tests and backfills use it),
but only a number from the last day: one reading from before that check,
dated 2017, had stretched the Lifetime chart back nine years, and a power
migration deletes anything dated before 2026 or more than a day ahead.

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

**NBT variants of one item id are separate items.** Every CropsNH seed is
`cropsnh:genericSeed:0` and every Forestry bee of a type shares one id too;
the crop or species lives only in NBT. While items were keyed by
`mod|internal|damage|kind` alone, all variants collapsed into one key: the
history chart alternated between different seeds' counts, every seed type
opened the same page, and the snapshot kept only the last one. OC reports
`hasTag` on every stack, and the NBT itself as `tag` (gzipped binary) only
with `allowItemStackNBTTags=true` in `OpenComputers.cfg`.
`network_browser.lua` forwards both (the tag hex-encoded, skipped past
2 KiB). `inventory.assign_variant()` gives each stack with NBT a `variant`:
a hash of the canonically serialized NBT (`gcm/nbt.py`), or of the label
when no tag came. The key and URL gain it (`...|kind|variant`,
`/network/item/mod:internal:damage~variant`); items without NBT keep their
old key. `variant_name` is a readable difference (seed stats, analyzed
bees) or a short id, shown where several variants share a label. Stacks
still sharing a key (label fallback) are summed. The first scan after the
change deleted the mixed history under the old plain keys
(`pending_nbt_cleanup`).

Craft requests for a variant pick the pattern by NBT too. GregTech tools
share id, damage and label per tool type (every material's "Huge
Turbine"), so only the NBT tells their patterns apart. The server keeps
each variant's raw tag from the last scan (`state.network["tags"]`, in
memory only) and sends it with the request; `craft_monitor.lua` passes it
back as the `tag` of `getCraftable({name, damage, tag}, "item")`
(OpenComputers 1.12.47+, GTNH 2.9.0-beta-2+). OC decodes it the way it
encoded it, and AE2 matches the stack exactly (`findPrecise`), so key
order doesn't matter. Craftable-only stacks are found too: the lookup
goes through every inventory handler, the crafting grid's list of
craftables included. A variant without a tag (none scanned since a
restart) fails rather than craft the wrong one. This replaced filtering
`getCraftables()` by name, damage and label and comparing each pattern's
NBT, with a server round trip for key-order differences.

**GT5's item-icon "meta items" are rendered procedurally, not shipped as
static files.** There's no way to get correct icons for most GTNH items
(ingots, dusts, plates, circuits) by copying texture files out of mod
jars - GT composites them at runtime from material+shape layers. Icons
have to be rendered by a real game client; `tools/icon-export/` does that
headlessly (see "Icon pipeline" below). It replaced a manual
NESQL-Exporter export (`/nesql` in-game, HSQLDB dump, JDBC-to-CSV tools),
which is where the image path scheme and the lookup's tie-breaking rules
came from.

**NEI's item list is not "every item".** GTNH hides thousands of items
players do hold (GT ores in other stone types, tiny/impure dusts, crushed
ores), and GregTech doesn't put many of its items in NEI at all, hidden or
not (centrifuged ores, many tool heads). The export therefore ignores NEI's
hidden filter and adds everything in the ore dictionary, vanilla
crafting/smelting and GT's recipe maps. Without that, `by_key` lost ~15k
keys against the old NESQL export (which also collected recipe items).

**Rendering icons at the main menu (no world) needs a world's GL setup
faked.** Each of these produced plausible-looking but wrong icons and took
a full run to find:
- The lightmap texture is only computed in a world. Lit item renderers
  sample it, so icons came out as dark silhouettes until it's filled
  white.
- Items drawn by a TileEntitySpecialRenderer (chests, ender chests,
  skulls, Iron Chests, Cooking for Blockheads counters, ...) bind their
  texture through `TileEntityRendererDispatcher.field_147553_e`, which is
  only set when a world renders (`cacheActiveRenderInfo`); `bindTexture`
  silently skips a null one. They came out wearing the block atlas (noise)
  until the export sets it to the game's texture manager.
- Renderers blend as if over the opaque inventory background. Drawn onto
  a transparent framebuffer, a translucent layer's alpha got multiplied by
  itself and its colour darkened towards black: GT's cosmic halo (14%
  opaque grey in game) came out 2% opaque and near black, and GT's ore
  overlays, blended over the stone, left see-through dark holes where the
  ore specks should be (~18k icons, mostly ores and layered GT items).
  `IconRenderer.capture` now draws any icon with a partly transparent pixel
  a second time over opaque white; with the first pass as "over black",
  opacity = 1 - (white - black) and colour = black / opacity. Fully
  opaque/clear icons skip the second pass; it costs ~20 s a full export.
- Halos are drawn past the item's 16x16 box on purpose, over neighbouring
  slots: GT's cosmic halo spans -10 to 27, Avaritia's and Universal
  Singularities' reach 4-10 units out. Rendering exactly the box cut them
  to a hard-edged square. The FBO now covers `IconRenderer.BLEED` (12)
  units past each side, and most icons are cropped back to the box
  (byte-identical to before). Plenty else lands outside it: NEI's overlay
  text (Draconic's "100%", IC2's debug item), text item renderers draw
  themselves (TConstruct projectiles' ammo counts) and 3D models
  overhanging by a pixel or two - about 1,000 icons in a first attempt
  that kept anything spilling. So an icon keeps the canvas only if its
  partly transparent pixels reach `MIN_BLEED` (3) units out (glows are
  translucent; text and models are opaque), measured on a second render
  without NEI's overlay (`safeItemRenderContext` by reflection, drawing
  just `renderItemAndEffectIntoGUI`), done only for icons whose first
  render spills. Outside the box the icon then takes that overlay-free
  render, inside it the usual one. Such an icon keeps the whole canvas,
  2.5x the box with it in the middle. Those paths go in the
  lookup's `bleed` table (path -> 12). The server marks their resolved
  paths (`<prefix>~bleed12/item/...`, `icons.resolve_icon`), so every API
  field and stored craft-history path carries it without a schema change;
  `icons.current_path()` re-resolves stored paths on the way out
  (completions). The page gives those images `.icon-bleed`
  (`iconClass()` in `util.js`): `transform: scale(2.5)` keeps the layout
  box, so the item fills the usual space and the halo spills over what's
  around it, above neighbouring cells' backgrounds and below their icons,
  ignoring clicks. `export.py` keeps a Faithful render only if it's the
  default render's size (the bleed table describes the default one), and
  keeps an animation still if its frames differ in size.
- Fluid icons are drawn by the exporter itself (a textured quad), not
  NEI, so they inherited whatever the previous item left enabled. With
  the item lighting on they came out ~25% darker, or not, depending on
  what preceded them. That also made every fluid fail the animation
  stability check, so none animated. `renderFluid` now turns lighting off
  and sets normal alpha blending, and the colours match the raw textures.
- Some renderers need nothing from the player but its existence. GTNHLib's
  cosmic shader (`UniversiumShader`, used by Eternal Singularity, Avaritia's
  infinity gear and GT's Infinity material) reads `thePlayer.ticksExisted`
  and threw at the menu, so none of those had icons. A stack whose render
  throws is retried once with a stand-in player (allocated without a
  constructor, every field default) in `mc.thePlayer`, after removing it
  from NEI's error set, which otherwise skips it. That recovers ~200
  stacks. The stand-in is only set during that one retry, so nothing else
  ever sees it.
- Avaritia's infinity items leave their GLSL shader bound; every later icon
  rendered as a flat single colour (and 5x slower) until `glUseProgram(0)`
  per icon.
- NEI catches renderer exceptions and draws a **fire block** instead, so a
  "fire" icon means that item failed. ~4k stacks (mostly ExtraUtilities NBT
  variants; ~265 lookup keys: the bow, Thaumcraft devices, some Botania and
  Chisel blocks) have renderers that read the player or world and can't be
  rendered without one; they're reported as failed rather than written.
- Angelica (GTNH's renderer) emulates the GL attribute stack. Renderers
  that throw leak entries on it that can't be popped from outside, until
  it overflows and ~7.5k unrelated items fail. The export disables
  Angelica; nothing requires it.
A real world avoids all of this, but creating one runs every mod's
worldgen at server start (Galacticraft generates its planets then, and a
Bartworks ruin crashed on the first attempt), which is a worse failure
mode than a few hundred missing icons.

**The old reference `images.zip` was made with texture packs.** The NESQL
export came from an instance running GTNH-Faithful 32x and others, so
pixel-diffing new icons against it is meaningless for anything a pack
retextures (stone, machine casings, GT fluids). Compare against the
default textures, or eyeball.

**Forge's Maven rejects Python's default User-Agent with a 403.** The
export's downloader sets its own.

**A `%2F` inside a URL *path segment* gets mangled by reverse proxies.**
This is why icons are served as `/icons?path=<encoded>` (query string)
rather than `/icons/<path>` - a path like `item/gregtech/foo.webp` needs
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

**The LSC also reports the wireless EU network**, found by disassembling
`MTELapotronicSuperCapacitor.getInfoData()` (the same in GT 5.09.50 through
5.09.54, packs 2.7.3 to 2.9.0-RC-1). It always adds a
`...lapotronic_super_capacitor.wireless_eu` line, regardless of the wireless mode
switch (that only controls whether this LSC trades with the network), followed by
a second line with the same key in standard form. The value is
`WirelessNetworkManager.getUserEU(owner)`, which goes through
`SpaceProjectManager.getLeader`, so it's the whole team's network. Its argument is
built as `EnumChatFormatting.RED + NumberFormat.format(BigInteger)`, so it
starts with `§c` and uses the server locale's grouping; `power_monitor.lua` keeps
only the digits of the first match. It's posted as its own machine with
capacity 0, but only once it has been above 0 in that run, so bases without
wireless don't get an empty chart; after that it keeps posting at 0, so the
health dot doesn't go stale. Checked against a real 2.9.0 dump: the full
number first, then `§c6.50x10^16`.

**With the wireless network reporting, it's the Power tab's default and
badge.** An LSC in wireless mode keeps its wireless threshold (60T per UHV
capacitor, ×100 per tier above) and rebalances every 6,000 ticks: it uploads
everything above the threshold and downloads up to it. Its fill sits under
1% for good, which tripped the "under 10% full" badge all the time. So
`store.power.pick()` defaults to a machine with no capacity that has reported
in the last 5 minutes, and the tab button shows that machine's EU instead
of a fill. It goes by capacity 0, not by name, because `WIRELESS_NAME` is
configurable. Its badge warns only when the past hour's rate empties it
within a day. Since `/power` with no machine now means "the default", the
unnamed machine's button links to `?machine=`.

**An LSC in wireless mode is measured against its wireless threshold.**
From a real 2.9.0 dump: the mode is in the key with no value
(`...lapotronic_super_capacitor.wireless_mode.enabled`), and each wireless
tier has a line `...capacitors\§5§nUEV§r\1` (tier name in colour codes,
then the count). The dump's fields are split by two backslashes, which the
splitter takes as one. `power_monitor.lua` posts `wireless_mode` and the
raw `capacitors` counts; the server works out the threshold
(`store.power.wireless_threshold`: 6e13 per UHV, ×100 a tier, per the wiki)
so a correction reaches hosted instances on deploy. It's stored per reading
(`wireless_threshold`, NULL otherwise), so the chart can draw it and leave
a gap before wireless mode. That dump checked out: 6.21P stored against
1 UEV's 6.00P. The hourly "Empty in" fit is hidden for such an LSC, as the
network refills it every 5 minutes.

The LSC's `wireless_mode` is stored too (1/0, NULL when not posted). With the
wireless network as the default, the badge still warns about another machine
under 10% full, but only when it's posted `wireless_mode: false`. An LSC on a
script from before this posts nothing, and may be in wireless mode, where its
capacity says nothing; batteries never post it, and as before only warn as
the default.

`/api/power` refuses a non-finite `stored`/`capacity` and drops non-finite
trend fields and capacitor counts: one Infinity stored would make every later
reply invalid JSON (Flask writes it as a bare `Infinity`).

**The LSC's `avg_eu_in`/`avg_eu_out` are EU/t, not EU/s.** Its own
"time to full" only works out that way: (9.22E capacity − 6.21P stored) ÷
(109.95G − 59.12G 5-second averages) ÷ 20 ÷ 86,400 = 104.9 days, against its
105.15. The page's arrow multiplies them by 20; it used to label them EU/s
as they came.

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

**The MCP endpoint is hand-written, stateless and dual-era.** `/mcp`
(`gcm/mcp/`) doesn't use the MCP Python SDK: the SDK is ASGI and this
server is WSGI, and an endpoint that answers every request with one JSON
object, keeps no sessions and only offers tools is a few hundred lines of
JSON-RPC. It serves both protocol eras on one URL. A 2026-07-28 request
carries its version in `params._meta` and the `MCP-Protocol-Version`
header, which must match, as must `Mcp-Method`/`Mcp-Name`. An `initialize`
request picks a 2025-03-26..2025-11-25 version, and later legacy requests
name it in the header; as nothing is kept between requests, each is served
by its header alone (2025-03-26 without one). Tools never take an item
name: `search_items` returns item keys and the other tools take those, so
a model can't act on the wrong one of several items with the same name.
Tool output is compacted for a model's context (`tools._compact_numbers`,
no icons, capped lists, ISO times). Auth is `auth.bearer_required`: the
user's personal access token in an `Authorization: Bearer` header,
never a session cookie, so a browser can't be tricked into calling it.

**The server is its own OAuth server, for claude.ai.** claude.ai's
connectors only sign in with OAuth, so `/mcp` (gcm/oauth.py,
routes/oauth.py, store/oauth.py) answers 401 with a pointer to its
protected resource metadata (RFC 9728), which names this server as the
authorization server (RFC 8414). Clients register themselves (RFC 7591,
public clients only; Claude registers on every new connection, so unused
registrations are dropped after a day, and capped per hour, per address
and in all). A Client ID Metadata Document isn't supported: Claude falls
back to registering when the metadata doesn't advertise one. The consent
page signs in with the user's existing access token, so there's no new
credential, and only takes POSTs from its own page (Origin,
Sec-Fetch-Site), against login CSRF; it's plain HTML with its own CSP,
whose `form-action` allows the app's callback origin, as Chrome applies
`form-action` to the redirect after a POST. PKCE is S256 only. Loopback
redirect URIs match on any port (RFC 8252), as Claude Code picks a free
port each time. Access tokens last an hour and refresh tokens 30 days;
refresh tokens rotate, and one used again more than 30 s after its first
use revokes its grant (it was copied); sooner is a retry or two requests
refreshing at once, and gets tokens. A grant remembers the login token
the user consented with, so revoking that token, **New token** or
deleting the user ends it too. `PUBLIC_URL`, when set, is the issuer and
resource URL: behind a proxy the request's own host may not be what
clients use.

**The MCP write tools have no amount cap; the plan is the guard.** A fixed
cap can't be right for every item (100k mB of a fluid is little, 100k of an
endgame part is a lot). `request_craft` runs the same planner as the craft
dialog and refuses to queue when ingredients are missing unless called
again with `force`; it returns the item's name, amount, unit and stock so
the model can't misread what it sent. The tools that change the game carry
`destructiveHint` (clients ask the user first) and their descriptions, and
the server's instructions, tell the model to confirm. Each runs the page
route's own code and role check (`commands.request_cancel`,
`stock.set_target`...). `current_crafts` gives each busy CPU's job as a
`job_id` and `cancel_craft` passes it back as the cancel's expected output,
so a CPU that has moved on to another job isn't cancelled. Viewers aren't
offered operators' tools in `tools/list`.

**The MCP instructions are built per connection.** `gcm/mcp/
instructions.py` joins three parts: `instructions.md` (fixed guidance,
kept under 900 words by a test, since it's in every conversation's
context), a snapshot of the base when the assistant connects (the user
and role, GTNH version, CPUs, the last scan, patterns, power, stock
rules), and the admins' house rules (setting `mcp_house_rules`). The
snapshot says it's a snapshot, so the model checks the tools for
anything current. Because the text depends on the user, `server/discover`
is `cacheScope: private` with a 5-minute TTL. Each snapshot line is read
on its own and a failure only drops that line. House rules are framed as
unable to widen permissions or skip confirmations; the tools enforce
roles anyway. The core text answers what models got wrong in use:
suggesting keep-in-stock targets for items a Level Maintainer or passive
production keeps up (it has them check `get_item`'s `crafts_last_week`
and the history first), reading a plan's totals as "crafts instead of
using storage" (plan rows now carry a `plan` sentence, and the requested
item is reported apart), and fussing over which CPU AE2 used (AE2 picks;
the tools can't). Every tool call is logged (`store.assistant`,
30 days) for admins to see in the MCP server dialog.

**The MCP write tools guard against retries and a silent game.**
`request_craft` doesn't queue while the base is already getting the item
(a pending request for the same item key, the item's keep-in-stock
refill, or a busy CPU whose output is that item) unless called again
with `allow_duplicate`: models retry after slow answers, and AE2 would
run both. Both write tools refuse while `craft_monitor.lua` is past the
page's stale threshold (`state.stale_after`), since a request it never
picks up expires after two minutes anyway. Who started a job is kept on
its tracking entry (`requested_by`, set by `start_requested_job`) and
read through `tracking.started_by()`, not put on the jobs `/api/crafts`
serves, which can be public. Requests and cancels made through /mcp
are `source = 'assistant'` in their history tables (everything that
filters by source compares with 'auto', so they count as a person's);
the admin History marks them.

**The MCP game-knowledge tools read the game data, not the network.**
`find_recipes` reads the selected version's recipe index (`recipes.index`,
built on first use as for the pattern check); `game_item` reads
`item_info.describe` for any item key, not just the network's. Both take a
name as well as a key: `RecipeIndex.find_names` (a `LIKE` over the
index's `name` table, every item and fluid a recipe names) plus the
network's names, and when that isn't one item they answer with the
candidates' keys instead of guessing. `base_patterns` reads the planner's
pattern index (`planner.index().candidates`), in AE2's order.
`pattern_check` is `recipes.result()` for `recipes.default_versions()`,
the check the page's notice asks for, so it answers "preparing" while
recipes download or index.

**MCP prompts are tool recipes, not knowledge.** `gcm/mcp/prompts.py`
returns a single user message per prompt that names the tools to call,
in order, and how to lay out the answer: the base report, a slow craft,
a restock check that checks for existing automation before suggesting
anything, and the update check (operators, as its tool is). A test
checks that every tool a prompt names exists. `prompts/get` checks
`Mcp-Name` like `tools/call` in modern requests. `search_items` falls
back to approximate matching only when a plain query (no NEI prefixes)
finds nothing: words with spaces, hyphens and dots removed, then each
query word close (difflib ratio 0.8) to a word of the name. The results
are flagged `approximate`.

### Assistant behaviour checks

Run these by hand, with Claude Code or claude.ai connected to `dev.py`,
after changing the instructions or the tools. Compare what the model did
with the MCP server dialog's call log.

1. **"Craft 40 FusionTech Mk-V"** (or any craftable item): plans first,
   reads the plan right (what comes from storage, what's crafted), states
   the item and amount and asks before `request_craft`, never brings up
   CPUs.
2. **"Keep Radon Plasma stocked"**: checks `crafts_last_week` and
   `item_history` before proposing a target, and asks how it's made if it
   looks automated.
3. **"Why is the job on M00 slow?"**: `craft_job`, names the held-up steps
   and the machines with their coordinates.
4. **"What's running out?"**: `inventory_changes` (falling), with hours
   to empty, and no targets proposed without the check in 2.
5. **"Cancel the job on W03"**: passes the `job_id`, confirms first.
6. **Stale data** (stop the fake game): says how old the data is.
7. **A viewer asks for a craft**: says it needs an operator, no attempt.
8. **A house rule** ("never touch W03"): followed.
9. **An ambiguous name** ("steel plate"): searches, picks by context or
   asks, never makes up a key.
10. **Buckets** ("make 5 buckets of X"): asks for 5000 mB.

**Progress % counts steps, not time.** The server computes it
(`gcm/progress.py`, called from `tracking.process_jobs`), not
craft_monitor.lua. A step is one item or fluid type in the job's
pending + active lists. Its progress is how far its remaining amount has
fallen from the largest amount seen for it this job. The job's progress
is the average over steps, each counted equally, and it never moves
backwards. It used to be `stored / (stored + pending)` over raw stack
sizes, which had three problems. Fluids counted in mB swamped
everything. 4096 cheap items outweighed one slow one. And `stored` also
holds the raw ingredients pulled at the start, which shrink as they're
consumed, so the bar could go backwards. Remaining-over-peak has no
units, so fluids need no conversion (there isn't one anyway - not every
fluid is ingot-based). Time weighting was deliberately left out. Recipe
durations aren't available, machines are shared between concurrent
jobs, and "active" includes items queued behind others at a machine. So
neither observed per-item rates nor past durations of the same craft
describe the job in front of you. The per-step baselines live in
`cpu_last_known`, in memory only, so after a server restart an in-flight
job's bar re-baselines from its current state. The steps left at the
last report also classify a job end: at most one left (the final
output's own step, which can't be seen reaching zero before the CPU
goes idle) is `finished`, anything more is `incomplete`. That covers
cancellation and interruption without claiming more certainty than the
data supports.

**The Crafts tab's statistics are counted by SQLite.** craft_stats.py
used to read every job in the range into Python and count there; a base
with 160,000 jobs took 50-70 MB per request for "all time", 14 MB for a
week, and held the GIL while it counted, so six viewers' requests took
54 s. store.crafts.ended_stats() groups and sums in SQL and fetches only
the top items' durations, for their medians: the same answer, no
memory to speak of, ~7x faster under concurrent requests, ~0.05 s
slower alone (five scans, no index on occurred_at).

**"No Crafting Monitor" is only said once it's certain.** A CPU with a
monitor can report a job with no `finalOutput()` for its first poll or
two. The cause isn't confirmed; most likely AE2 updates the monitor tile
after the job starts. So `tracking.process_jobs` sets `monitor_missing`
on a job only when its CPU has never reported an output since the server
started (`state.cpu_has_monitor`), and the job has gone
`MONITOR_GRACE_SECONDS` without one. A job already running when the
server starts skips the wait, so a CPU without a monitor is named at
once after a restart. Until then the card says "Starting job…". dev.py
fakes both cases: every job's first report has no output, and a02 never
has one.

**network_browser.lua says whether it has an ME Interface Terminal.**
Without patterns, the server couldn't tell "no terminal" from "terminal
connected, first pattern scan not yet run", so the page could only say
"no pattern scan yet". `scan/finish` now carries `interface_terminal`
(left out with pattern scans off, so off isn't taken for missing; older
scripts leave it out too). `patterns.terminal_missing()` (no patterns read
and the last scan said `false`) words the plan and tree notes, the
Network tab's admin note, and Status. It's in memory only: after a
restart it's unknown until the next network scan.

**Held-up steps read pushes and returns apart** (`gcm/bottlenecks.py`).
In AE2's `CraftingCPUCluster`, pending is `tasks` (runs not yet pushed)
and active is `waitingFor` (outputs pushed and not back), so a push moves
an amount from one to the other, and only a fall in their sum is a
machine giving something back. Progress's `moved_at` only sees that sum;
tracking's per-step `flow` keeps pending and active apart, with when the
step was last pushed (`pushed_at`), since when what's out has waited
(`out_since`), and when a machine last gave it something (`gave_at`).
This reading comes from AE2's source and hasn't been checked on a live
network. If it's wrong, blocked and busy are what break.
Choices worth knowing:
- Blocked needs the inputs in `storedItems()` (`ready_since`, set on full
  reports) as well as an unmoving pending. Without them it's waiting, and
  the hold-up is further down the tree. Stalled waits
  `CRAFT_STALL_SECONDS`, longer than the others' `CRAFT_BLOCK_GRACE_SECONDS`,
  as it's the one a long recipe looks like.
- A step's machine comes from the job's own pattern, chosen as the live
  tree chooses it (`job_tree.used_by_job`), without building the tree,
  because accounting runs after every report for every busy CPU. Several
  candidate providers split the time between them.
- Two interfaces on one machine: OC reports where an interface is, not
  what it faces. Same name and Manhattan distance 2 (both touching one
  block) counts as one machine, but only for "busy". The totals stay per
  interface, which is the thing a player can find and fix. The check is
  pairwise, not transitive, so a row of identical machines doesn't merge.
- Time is counted only past a state's threshold (`held_for`): a report
  that first finds a step blocked adds what's past the grace, not the
  whole gap since the last report. Each report adds at most
  `MAX_STEP_SECONDS`, so a gap in the reports isn't counted as waiting.
- `provider_stalls` is per hour and unpruned, like `craft_events`: one
  row per provider that held something up that hour.

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

`#term` and `$term` are NEI's tooltip and ore-dictionary searches,
checked against GTNH NEI's source (SearchTokenParser and its filters,
NEIClientConfig): `#` matches the tooltip lines below the name, not the
name; `$` any of the item's ore names. Their search modes default to
ALWAYS, so a plain term matches the name, the tooltip or an ore name
(any of the ALWAYS providers), and so do ours; AE2's terminal searches
tooltips by default too. Mod names default to PREFIX, needing `@`,
and NEI's `@` matches the mod's name, so ours matches the id or the
name. `=` is GregTech's own NEI
prefix (`NEIGTConfig`, `ChemicalFormulaFilter`): the formula with
formatting codes dropped, GregTech's glyphs replaced and sub- and
superscript digits as plain ones, so `=sio2` finds SiO₂. The colours
are NEI's and GregTech's own for those prefixes (yellow, aqua, green),
darkened in the light theme to be readable on white, as are `@`'s pink
and the quote marks' orange.

**The light theme has its own colours, not the dark theme's.** The page
was tuned on dark, and the light theme first only swapped the
background and text: the accent and status colours stayed the pale
shades made for a dark background (2-3:1 on white, so links, amber
notes and red buttons washed out), borders were one translucent grey
that all but vanishes over near-white, and the grid's counts got a
black glow behind dark text. Now `app.css`'s light blocks set each
colour used as text to a deeper shade of the same hue, at 4.5:1 or
more on white (an e2e step checks it), borders to solid greys
(`--border-faint` to `--border-stronger`, the same five strengths). The
grid's counts are drawn like the game's in both themes, white with a
sharp black outline, which reads on any icon, black fluids included. Apart from the counts, the dark values are
exactly what was there before. The charts read the same colours (`chartColors()`), since a
canvas can't use `var()`, and rebuild when the theme changes. The
item tooltip stays dark in both themes, like the game's.

**GregTech's formulas use its own font glyphs.** Superscript isotope
numbers, a subscript 0, the classical elements and more are private-use
characters (`\uE000`-`\uE020`, GregTech's `CustomGlyphs`) drawn from
GTNH's font, which a browser lacks. The data keeps them, and
`description.js` maps them: to the Unicode stand-in GregTech registers
for each where it has one, else (elements, magnet, brimstone...) to the
nearest Unicode symbol. A glyph GregTech adds later shows as a box
until the table is updated.

**Some names carry formatting codes too.** AE2's storage parts are
named `§a16k§r ME Storage Component` in its lang file, and the label
comes through as that. It's stored as is, since the label is part of
lookups (icons `by_key_label`, label-derived variants, craft history
keys), and it's stripped only where shown: the page's lists and
dialogs show names in the page's own colours (`nameText()`), the
game-style tooltip box colours them as the game does (`nameHtml()`),
and search, notifications, webhooks and link previews drop the codes
(`gcm/mctext.py` on the server).

**Item info is served apart from the item list, and fetched lazily.**
Every GregTech material has a formula line and multiblocks have twenty,
so on a big network descriptions are megabytes, which `/api/network`
would carry on every poll. `/api/network/info` shares its ETag, and the
page only asks once a tooltip shows or a search has a `#`, `$` or `@`
term, then again with each item list after that. The item dialog gets
its item's info with its history instead, as it can be opened for an
item that isn't in the network.

**The exporter reads tooltips with no player and fakes Shift.** There's
no world at the menu, so lines that need the player are missing.
Shift: `KeyTransformer` points every `Keyboard.isKeyDown()` call at
`ExportKeys`, which reports Shift held during a second read of each
tooltip; LWJGL itself can't be transformed (the launcher excludes it),
so the call sites are what change. Only the Shift text that differs is
kept.

**Craft quantity accepts metric shorthand and basic math (`10k`,
`4+3*2`), desktop-only.** A hand-written recursive-descent evaluator,
not `eval()`/`new Function()` (a real code-injection vector to avoid
even here) and not a math library (massive overkill for four-function
arithmetic). Desktop-only because switching the input to `type="text"`
(needed to allow non-numeric characters) loses the native numeric
keypad a `type="number"` input gives on mobile, which is better UX
there - detected via `pointer: coarse`, matching how "is this really a
touch-primary device" is decided elsewhere in this project too.

**Notifications go two ways, deduplicated by tag.** A finished pinned
craft is pushed by the server (gcm/push.py, Web Push with a VAPID key
in `data/vapid_private.pem`) to every browser the user turned the
settings-menu switch on in, and static/sw.js shows it - that's what
reaches a closed tab or a suspended phone. An open page also notices
the completion on its own 3s poll and shows the same notification.
Both use the tag `craft-completion-<id>`, so a browser getting both
replaces one with the other instead of showing two. Pushes are sent
from one background thread, never inline in the game's POST, and only
to an allowlist of push-service hosts (the server POSTs to whatever
endpoint a browser registers). The worker is served at /sw.js, not
/static/sw.js, so its scope covers the page - iOS requires that.

**Discord previews are component embeds, with OG as the fallback.**
`/crafts`, `/power` and `/network/item/...` carry a
`<script id="discord:component-embed" type="application/json">`
(gcm/embeds.py, added to the OG tags by `pages._build_og_tags`).
Discord renders it as a Components V2 card, and any other crawler, or
Discord rejecting the payload, gets the OG tags. Discord shows nothing
for a payload over 3,000 bytes (counted after `\u003c` escapes), so
`script_tag` leaves out an oversized one, and the crafts embed lists
fewer CPUs, down to none, until it fits. Names come from the game, so
they're escaped as markdown (no links, headings or emoji). A JSON data
block is never run, so the CSP doesn't apply to it. Discord takes the
*last* `theme-color` tag as a plain preview's accent and ignores its
`media`, so the dark one comes last. Unfurled `components` embeds are
output only: webhooks still send a classic embed.

**Two deployments, one script, two keys.** A release tag deploys
production (publish.yml); each push to main deploys testing
(testing.yml), both through deploy.yml and the same forced command,
`deploy/gcm-deploy.sh`. The instance comes from the forced command in
`authorized_keys`, not from what CI sends, and each instance accepts only
its own kind of tag (`x.y.z` or `sha-<commit>`). So a leaked testing key
can't put an unreleased build on production, and CI can't pick the
directory. ci.yml has no push trigger of its own on main: testing.yml
runs it before deploying, which would otherwise run the suite twice.
Testing's image says `main-<commit>` as its version, which isn't a
`vX.Y.Z`, so it never auto-updates computers.

**The pattern check compares versions, not a pattern with a recipe.**
Players write processing patterns partial on purpose: an EBF pattern
without its gas, a DTPF one without its catalyst, the circuit in the input
bus, the hot ingot left to a vacuum freezer. Checking each pattern against
one version's recipes would flag all of those. So gcm/recipes.py matches
loosely: every pattern output among the recipe's outputs, every pattern
input among its inputs (ore-dictionary equivalents count, and a recipe's
stacks of one item are summed first, since GregTech lists 165 as
64+64+37), and every amount the same multiple of the recipe's. Or through
one recipe in `FINISHING_MAPS` (only the vacuum freezer: any one-in,
one-out recipe would let a macerator make chance matches). It then
asks whether what matched in the old version still matches in the new
one, in the same map. Any slack in matching applies to both versions alike, so
it cancels out. Run on one synthetic pattern per recipe of 20,000 from
2.9.0-RC-1, RC-1 against itself flags nothing, and RC-1 against 2.9.0
flags 18, all real changes (EBF heat and tier, assembler inputs).
"Changed" compares what a match relies on without stating: the recipe's
inputs and outputs the pattern leaves out, the voltage tier and special
value. A new byproduct is a change too: 2.9.0's bisphenol A recipe makes
diluted hydrochloric acid where it made water, which fills an LCR whose
pattern lists only the bisphenol. Enriched fertilizer, which went from the
LCR (fertilizer items) to the mixers (dusts) in 2.9.0, comes out
**broken**, not moved: its inputs changed too. "Moved" is the same loose
match in another map. A pattern's map is the one its provider's name
names (NEI's catalysts for the map, Recipes.java), else the one its
provider's other patterns matched alone, else its own single match. Each
version's recipes are a separate SQLite file (gcm/recipe_index.py,
~60 MB for 175k recipes), built in a subprocess like the game data index,
because the check reads only the few recipes the patterns' outputs lead
to. Building one takes ~200 MB, more than a hosted server's whole memory
limit, so as with the game data index, one beside a bundle's recipes
(`recipes.sqlite`, `app.py index-gamedata` builds it) is used as it is.
Nor does a check keep what it reads: it used to cache every recipe making
each output it looked up, for both versions, and a real base's 4,700
patterns reached 100,000 parsed recipes (one dust alone is made by 2,240)
- 150 MB, peaking at ~240 MB, for a check no faster than asking SQLite
each time. That ran after every accepted pattern scan.
A check that can't run is a notice for admins, not no notice: that would
read as nothing broken. It indexes what recipes take as well as what they make, so a broken
pattern whose output nothing makes any more can say whether anything
still uses it. Only an Unpackager recipe left (TAKE_APART_MAPS) means
removed: that's how GTNH 2.9.0 retired GT++'s Transmission Components,
so stock can be taken apart.

**Stale game data is found from item ids, confirmed against newer
catalogs** (`gcm/gamedata_check.py`). OpenComputers can't read the
modpack version, and the item scan only asks for the selected catalog's
ids, so items a newer pack added are missing with no error: 2.8.4's
catalog lacks 1,126 ids that 2.9.0's has (and has 770 that 2.9.0
dropped). Patterns and crafting jobs name items without going through
the catalog, so an id they report that the catalog lacks is the
evidence. Only whole ids (`mod:name`, the catalog's own lines) are
compared: variants differ by NBT and damage values for reasons that
aren't the version. An id counts only once a newer version's catalog
(an installed bundle's, or the release's `item_catalog.txt`, ~325 KB,
cached in `DATA_DIR/catalogs/` by the release's `data.json` asset id) is
found to have it. That leaves out NEI-hidden items (the catalog is NEI's
list), modified packs' and junk, so one confirmed id is enough. The
suggestion is the version with the most of them, the oldest of those,
and a final release before its own pre-releases: versions are ordered
by `gamedata.version_key()`, since publish order isn't version order
(2.8.4's data was published after 2.9.0's). Pre-releases can't be told
apart this way: 2.9.0-RC-1 to 2.9.0 added four ids (three LittleTiles
items and a StructureLib debug item) and RC-2 to 2.9.0 seven variants,
none likely in a network. So that case goes by name: selected data for a
pre-release whose final release is out. Not detectable: game data newer
than the game (a catalog id the game lacks reads as out of stock), and
fluids and essentia, which catalogs don't list.

**Admin notices are dismissed on the server, for every admin.** The
pattern check, scripts and game data notices and the Interface Terminal
note are each about the server, not a browser: "I've handled this
upgrade" holds whoever signs in next, on whatever device. So the
dismissed value (a version pair, a release) is a row in app.db's
settings (gcm/notices.py), which backups already cover, rather than in
localStorage, where a new device or another admin brought back a notice
dealt with a month ago. Until they're loaded, noteClosed() says closed,
so a dismissed notice doesn't flash up. Each browser moves what it kept
in localStorage before to the server once. Viewer-only conveniences (the
theme, a collapsed section) stay in localStorage.

**Operators see the last upgrade's pattern check, not any two versions.**
They request crafts and fix patterns in game, so after an upgrade the
check is theirs as much as an admin's: its notice, its dismissal
(gcm/notices.py's ROLES) and /api/recipe-check. That endpoint only
serves previous -> selected, the pair the notice already works out and
caches; picking another version downloads its recipes and builds a
~60 MB index, which stays an admin's call (/api/admin/recipe-check).
Viewers, who on a public server are anyone, don't see a base's patterns.

**Memory is handed back after every scan (gcm/memory.py).** A hosted
base has 160 MB, and with a big base's scans the process kept growing
past it, into swap, without anything leaking. A scan's lists, dicts and
longer strings come from glibc's malloc, which on free() only returns
the end of its main heap. So the process stayed the size of its biggest
moment, the old and new pattern lists and the indexes built from them
alive at once, and the heaps of waitress's 8 threads made it worse.
Both scan/finish routes call malloc_trim(0) once they're done, accepted
or rejected, which returns every free page. Measured with a replayed
4,700-pattern base, half its scans rejected: 147 -> 106 MB with 8
threads, 116 -> 92 MB in one. jemalloc would mean copying a library
into an image with no RUN steps. In the same vein:
- recipes.py keeps only the current pattern scan's check results (~7 MB
  each for that base), not the last four scans'.
- The image sets MALLOC_ARENA_MAX=2: glibc's per-thread heaps each keep
  their own free memory. ~5 MB on top of the trim; the lock the threads
  share hardly matters under the GIL. Fewer waitress threads saved the
  same and would queue the OC computers' minute bursts.
- Charts are drawn in a child process (gcm/chart_draw.py, `python -I`,
  the chart as JSON on stdin, the PNG on stdout). Imported in the
  server, matplotlib and numpy were ~40 MB for good after the first
  shared link; a child is ~0.3 s a chart, cached and rate-limited, and
  ~55 MB only while it draws. Web Push stays in the server: its ~16 MB
  would cost a process per completion.
- Scans intern their stacks' mod, internal, kind and name
  (inventory.intern_names()): each JSON batch parses its own copy, and
  a scan and the one it replaces are alive at once. ~4 MB of a 4,700-
  pattern list's 10.5, ~2 MB of 15,000 items' 8. On the image's Python
  (3.14) an interned string is freed once nothing uses it, so names of
  items gone from the network don't pile up. __slots__ or tuples would
  save ~7 MB more, for rewriting everything that reads the snapshots as
  dicts.

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

## Icon pipeline

Game data - item icons, the icon lookup and the scanner's item catalog -
belongs to a GTNH *pack* version, not an app version, so it's distributed
separately from app releases.

`tools/icon-export/run.sh <GTNH MultiMC zip> [--install]` builds one bundle
(see README):

1. `Dockerfile` builds the `gcmiconexport` Forge mod (`mod/`, GTNH
   buildscript, needs JDK 25 for Gradle) and a Temurin 21 + Xvfb + Mesa
   runtime image.
2. `export.py` unpacks the pack, resolves the pack's own MultiMC patch
   files (`patches/*.json` list every library with its URL) into a Java
   command line, and starts the client offline under Xvfb - no launcher,
   no account. The launch approach follows gtnh-factory-flow's dataset
   pipeline.
3. At the main menu the mod boots NEI's configs (`NEIClientConfig.loadWorld`
   only needs a directory, not a world), builds the item list (NEI's rules
   minus its hidden filter, plus ore dictionary and recipes), renders each
   stack through NEI's `drawItem` into an FBO (NESQL's projection and
   lighting), and writes `images.zip`, `icons_lookup.json`,
   `item_catalog.txt` and `export-report.json`, then exits. About 1.5
   minutes for ~220k icons; the animation stages add about 14 more for
   2.9.0's ~9,000 animated icons. Each frame renders batches for
   `frameMillis` (250 ms), since the main menu holds the game to 30 fps
   and one batch a frame spent most of the export waiting. Batches stay
   128 icons: where a batch starts shows in a few icons (GalaxySpace's
   rockets came out animated, BuildCraft's engines in another pose, when
   batches were cut by time instead).
4. Animated icons (see "Animated icons" below): after the still pass the
   mod finds which icons animate and renders those tick by tick, writing
   each one's distinct frames to `animations.zip` and their order and
   timing to `animations.json`. `export.py` (`build_animations`) finds each
   sequence's loop and builds an animated WebP of it, which takes the
   still icon's place. `finish_images` then writes every icon as lossless
   WebP, renamed `.webp` in `images.zip` and `icons_lookup.json` alike
   (see "Icon image format" below).
5. With `--faithful <zip>`, `export.py` enables that resource pack in
   `options.txt` (the pack ships none) and launches the game again into a
   scratch directory. It then writes `images-faithful32.zip` with every
   path the first run's lookup references, taking the default render
   where the Faithful run didn't render one, plus a `CREDITS.txt`. One
   lookup and catalog serve both texture sets because paths depend only on
   the item. Doing a second launch instead of reloading resources in
   place keeps the renderer's state fresh; the second pass takes as long
   as the first (about 6.5 minutes with animations).
6. `export.py` adds `data.json`: format version, GTNH version, counts, the
   texture sets (`textures.faithful32` carries the pack version, credit
   and URL), and each file's size and SHA-256.

**Animated icons.** ~2.7k atlas textures in the pack animate via `.mcmeta`
(GT materials and fluids above all, lava, Thaumcraft, Botania...). The
game ticks them in real time even at the main menu, so before this every
export caught each one at a random frame and `images.zip` churned between
identical runs. `AnimationClock` sets every animated sprite to a chosen
tick, uploading the frame and setting its counters as `updateAnimation()`
would. Sprite subclasses (Botania's `InterpolatedIcon`, which blends
between frames, ExtraUtils' lava) are set one tick short, frame uploaded,
and stepped onto the tick by their own `updateAnimation()`; the compass
and clock are left alone. It also sets GT's own
client animation counter (`GTClient.mAnimationTick`, with `renderTickTime`
zeroed), read through `getAnimationRenderTicks()` by GT's spinning
transcendent metal and its colour-cycling materials; without that they
came out at whatever angle or colour the menu had reached, differed
between runs, and never animated. The driver seeks before every batch,
since the game ticks in between. Stages:
- still pass at tick 0, so still icons are deterministic;
- DETECTING: everything again with each sprite on its first different
  frame; icons whose pixels changed are candidates;
- VERIFYING: candidates again at tick 0; those that don't match their
  still render change on every draw (the compass spins without a world)
  and stay still;
- CAPTURING: the rest at every tick up to `maxAnimationTicks` (400 = 20 s,
  covering 99% of texture cycles, up to 384 ticks; longer ones are cut and
  loop). From tick 160, every 40 ticks, icons whose frames have repeated
  in full at least twice stop (`ExportDriver.repeats`), so only long
  cycles run on: most loop within 160.
- RECHECKING: the captured icons at tick 0 again, minutes after the still
  pass. Renderers that follow the system clock rather than animation
  ticks can move slowly enough to pass
  VERIFYING; captured tick by tick (each tick is ~1.5 s of real time in a
  full run) they'd play back far too fast, so these stay still too.

**The system clock.** Some renderers animate by the wall clock, which the
mod can't set, so the jar is also a coremod (`core.ExportCorePlugin`,
registered only with `-Dgcm.iconexport=true`). Its `ClockTransformer`
redirects clock calls (`System.currentTimeMillis()`/`nanoTime()`,
`Minecraft.getSystemTime()`, LWJGL `Sys.getTime()`) to `ExportClock`, which
passes through to the real clock until the export sets a tick, then gives
tick x 50 ms. Classes touched:
- GTNHLib's `UniversiumShader`: the cosmic starfield on Eternal
  Singularity, Avaritia's infinity gear and GT's Infinity material. The
  shader's own texture atlas steps whenever the player's `ticksExisted`
  changes, so `AnimationClock` also sets its `SpriteAnimationMetadata`
  state and the stand-in player's tick count follows `ExportClock`.
- mod item renderers: classes that implement `IItemRenderer` themselves,
  and GT's `gregtech.common.render.items` (which mostly inherit it). That
  spins Galacticraft/GalaxySpace rockets and GT's wireframe tesseract,
  steadies GT's glitch effect, and more (the transformer logs each class
  and how many calls it redirected; ~30 in GTNH 2.9).
- vanilla `RenderItem`: the enchantment glint is frozen at one position
  rather than animated; glinting items are many and a glint changes the
  whole icon every frame, which would add hundreds of MB.
Nothing else in Minecraft is touched, so the game loop's own timing is
unaffected. A class that moves or renames its clock call just keeps the
real clock.

Interpolated textures: ~230 animated textures (212 GT, mostly GT++
materials like Chromatic Glass, plus Avaritia's infinity ingot, AE2's QE
singularity, a few EnderIO/Et Futurum/bartworks blocks) set
`"interpolate": true` in their `.mcmeta`, and in game each frame fades into
the next every tick. 1.7.10's `AnimationMetadataSection` doesn't read the
flag, so `AnimationClock` re-reads each sprite's `.mcmeta` through the
resource manager (leniently: some have unquoted keys) and, for those,
uploads Minecraft 1.8's blend on every seek: the frame's colour channels
mixed towards the next frame's by ticks-into-frame / frame length, alpha
from the current frame, every mipmap level. Before, they jumped from colour
to colour once a frame (every 20 ticks for Chromatic Glass).

Other clocks: Botania's `ClientTickHandler.ticksInGame`/`partialTicks`/
`total` are set like GT's counter. And renderers that jitter with
`java.util.Random` on every draw (Avaritia and Universal Singularities
halos, GT's Infinity and glitch effects) get their `Random` fields
(instance and static, found by type on the item's `IItemRenderer` class and
its superclasses) re-seeded from the animation tick before every render
(`IconRenderer.reseedRenderer`): the same tick always draws the same
jitter, so they pass VERIFYING and are captured like any other animation.
It never repeats, so the whole capture loops; at 20 fps it's close enough
to the per-frame shimmer in game. Avaritia's halo itself is a static quad;
what moves is its "pulse", a 60%-opacity copy of the icon scaled by
`nextGaussian() * 0.15 + 0.95` on every draw.

Re-seeding fields only reaches a renderer's own `Random`s. GT registers
one wrapper (`MetaGeneratedItemRenderer`) that hands each draw to a
per-material renderer (`GlitchEffectRenderer`, `InfinityRenderer`) the
wrapper doesn't hold, so Eternity, Magmatter, Infinity, Six-Phased Copper
and Exo-Halkonite items (~190 icons) never matched themselves. So
`ClockTransformer` also rewrites `java.util.Random` calls (`nextGaussian`,
`nextDouble`, ...) and `Math.random()` in the classes it already
transforms into `ExportClock.random*`, which draw from one sequence
restarted before every draw with the tick's seed (`beginDraw()`, from
`IconRenderer.resetState`): the n-th random number of a draw at tick t is
always the same, whichever Random the renderer holds. GT's glitch effect
also keeps state between draws (new colour offsets in the first 0.4 s of
every 2 s, held after), which now comes from those same draws.

The seed is the tick run through SplitMix64's finalizer
(`ExportClock.seed()`), not the tick itself: `java.util.Random` gives nearly
the same first numbers for seeds a few low bits apart, so seeding with
`constant ^ tick` made neighbouring ticks' jitter correlate at 0.7. GT's
Infinity pulse on UEV components (a 60%-opacity copy scaled by
`0.95 + 0.15 x gaussian`, drawn under the item, so only visible when above
1x) showed on 1% of ticks instead of ~36%, and with Faithful's static UEV
textures the icons weren't detected as animated at all.

Two other things made icons "random". Avaritia turns on GUI item lighting
and draws its pulse quad without a normal, taking the current one, which
was whatever the previous item left: its still came out up to a third
darker depending on its neighbour in the batch. `resetState` now sets the
normal to face the viewer. And the report lists the icons still classed
random (`random`, up to 500), for finding the next one: ~35 remain
(amazingtrophies' trophies, Galacticraft rockets, Forestry bees, a few
Thaumcraft/BuildCraft blocks), drawing from state outside the item
renderers.

Only icons the lookup references go through these stages. The mod renders
every stack (~215k), but NBT variants that share a key are only shown
when `by_key_label` (key plus label, e.g. each crop's seeds) points at them,
and `export.py` (`finish_images`) drops them from `images.zip` too, which
leaves ~117k icons. That roughly offsets what the animations add.
`build_animations` takes the shortest period the sequence repeats at least
twice. Failing that, `near_loop` looks for a frame that's nearly frame 0
again after moving well away (mean channel difference under 1), taking the
closest one: the transcendent ingot turns 3.5 degrees a tick, so it's only
exactly back after 720 ticks but within half a degree after 103. Else the
whole capture loops. An animation over 1 MB is rebuilt at half the frame rate
(each frame shown twice as long, so the speed stays), repeatedly, and kept
still if it doesn't fit even at 4 frames: big 3D renders that change
completely every tick (Tectech's Forge of the Gods) came out at several MB.
It's written with Pillow (frames after the first store only what changed).
The image pins Pillow 11.3 from pip: jammy's python3-pil (9.0) compared
APNG frames without alpha and merged the ones that differed only in it, so
the Raw Tesseract (a black wireframe turning on a transparent background)
came out still. `build_webp` fails the export if what it wrote doesn't
last as long as the frames it was given, or doesn't move. Frame 0 is the
still icon, which is what anything that can't animate shows.
`/icons?still=1` returns frame 0 (`icons.read_still_image`); the frontend
asks for it when the browser prefers reduced motion (`iconUrl()` in
`util.js`).

**Icon image format.** Bundles carry lossless WebP, animated WebP for
animations; bundles built before that carried PNG and APNG (and NESQL's
zips are PNG). Measured on 2.9.0-beta-3's 117k icons, WebP is pixel for pixel the
same in 67% of the stills' PNG bytes and 48% of the APNGs', which takes
`images.zip` from 254 MB to 160 MB. AVIF lost: lossless it's twice PNG's
size, and lossy it only wins once it softens the pixel art. Lossy WebP
smears colours (it halves their resolution), so it's no option either.
Pillow's `method=4` gets nearly all of `method=6`'s saving at ~1 ms a
still and ~20 ms an animation (6 took 0.6 s an animation). The mod still
writes PNG (Java has no WebP writer), and `finish_images` converts in a
process pool, ~40 s for a whole bundle on 16 cores. Still WebPs come out a little
bigger deflated, so they're stored in the zip as they are; animated ones
deflate by ~15%, so they're deflated. libwebp crops each animation frame
to what changed, the first one too, and sets the file's alpha flag only
if a cropped frame has transparency: an icon with a transparent margin
that never changes (OpenComputers' cards) lost it, and decoders that go by
the flag (Pillow) showed the margin opaque black. `build_webp` sets the
flag itself whenever a frame has transparency.

A WebP's name says WebP: the bundle's paths end `.webp`, and `data.json`
says `"image_format": "webp"` (without it, a bundle is PNG). Two kinds of
path outlive a bundle, and both have to work with either. Craft history
stores the path an icon had when it was recorded; `icons.current_path()`
re-prefixes those for the live bundle already, and swaps a `.png` or
`.webp` suffix for the live bundle's, so the API only ever hands out paths
that name a real entry. The page's own fixed icons (the favicon's ME
Interface, the Blank Pattern badge) had hard-coded `.png` paths; they ask
by item instead, `/icons?key=modid:internalname:damage`, served with a
day's cache rather than the immutable week, as the same URL gets the next
bundle's image. The favicon, apple-touch icon and manifest add
`notification=1`, a 192px PNG, since they declare `image/png`. `/icons`
names its content type from the bytes. A server from before WebP that
installs a WebP bundle mostly works (browsers don't trust an image's
content type anyway), but its favicon and craft history icons 404 and
reduced motion still animates.

**Run-to-run differences.** Two exports of the same pack with the same
code aren't identical. Measured on 2.9.0 (two runs at once): 91 of ~132k
images differ, only 9 of them animated (6 loop lengths, 3 with a few
frames off). Most of the rest is state the pack picks once per launch,
which the export can't set:
- GT++ alloys (Octiron, Laurenium...) tinted or not: a GregTech tie-break
  over an identity-hashed map (`MaterialUtils.getMostCommonTextureSet`);
  players see it too.
- BuildCraft gates: each gate is listed plain and once per expansion, the
  expansions from a hash set, all under one label. by_key_label takes the
  last one listed, so a different expansion each launch (22 entries).
- Galacticraft's Tier 2 rocket, Forestry bees, Thaumcraft's Mana Bean and
  crystals, NEI's icons for the Entangled Bose-Einstein Condensates, a
  cropsnh leaf and a few others.

Tried and dropped: breaking lookup ties by NBT (no NBT, then the
shortest) instead of list order fixes the gates but moves ~4,000 icons
to a worse variant (AE2 facades of another block, black Railcraft tank
walls, Draconic gear at 0%); the old order is stable for nearly
everything else. `-XX:hashCode=3` (sequential identity hashes) doesn't
steady the gates or the bees: startup hashes from many threads.

So to judge an exporter change, run the old code twice and compare with
`tools/icon-export/ci/compare_exports.py OLD NEW --base2 OLD2`: it decodes
each icon whose bytes differ, says how (still pixels, became animated or
still, animation timing, frame pixels, only the encoding) and per mod,
and leaves out what the two old runs already disagree on. For a quick
check, the same with `GCM_ANIMATION_TICKS=0` and
`-Dgcm.iconexport.only=<regex>` takes about 80 s a run.

**Distribution.** `.github/workflows/gtnh-data.yml` runs daily:
`ci/detect.py` lists GTNH's stable/beta/RC tags (GT-New-Horizons-Modpack
releases, nightlies skipped), drops those that already have a
`gtnh-data-<version>` release in the data repo, and finds each one's client zip on
downloads.gtnewhorizons.com (the folder and `Java_17-2x` suffix vary, so it
tries candidates). Each is exported twice, with default textures and with
the latest GTNH Faithful x32 release (Ethryan/GTNH-Faithful-Textures; it
doesn't need to match the GTNH version). It is then checked by
`ci/validate.py`: count floors, <5% flat single-colour icons (the
signature of the broken-GL failures above), and at least 20% of sampled
Faithful icons differing from the default ones (so a pack that silently
didn't load fails). The result is published as a release with the files
plus `data.json`, with the Faithful credit and an ownership notice in the
notes. A new Faithful release doesn't rebuild published versions; run the
workflow with `force` for that.

Releases go to a separate public repo, `Maselkov/gtnh-craft-monitor-data`
(`DATA_REPO` in the workflow, `GAMEDATA_REPO` on the server), not this one.
The icons are renders of Minecraft, mod and Faithful textures, which aren't
ours; keeping them apart means a removal request only touches data, never
the code or the app's releases. The data repo's README carries the notice.
`GITHUB_TOKEN` can't write to another repo, so publishing uses the
`GAMEDATA_TOKEN` secret (a fine-grained token with Contents: read and
write on the data repo); the build fails at its first step without it.
`detect.py` falls back to this repo when `DATA_REPO` is unset.

**Server side** (`gcm/gamedata.py`): the Game data admin page lists those
releases (GitHub API, cached 10 min), downloads the chosen one into
`DATA_DIR/gamedata/<version>/` with checksums checked against `data.json`,
records it in `gamedata/selected.json` (`{version, textures, previous}`),
and makes it live without a restart (`icons.load()`, then
`inventory.refresh_icons()` for the live snapshot). Only the chosen texture
set's zip is downloaded. Adding another set to an installed version fetches
`data.json` and that zip alone, unless `data.json` changed (a rebuilt
release), in which case the whole bundle is fetched again. The selected and
previously selected versions are kept, with whatever texture sets they
have. `GAMEDATA_SHARED_DIR` is a second place to read bundles from, for
one host running a server per base: a version is read from the server's
own directory if that copy is complete, else from the shared one. Installs
and pruning only touch the server's own directory, so the shared one can
be read-only, and a shared bundle never counts as outdated (whoever put
it there replaces it). The pack is credited in the data repo's README, each release's
notes, `CREDITS.txt` in the zip, `data.json` and the Game data dialog. With
no bundle there are no icons.

**Rebuilt releases.** CI's forced rebuild replaces a release under the same
tag. Each install records the release's `data.json` asset id in
`<version>/release.json`; a published release whose id differs is an
update (`update_available()`, shown on the Game data page), and picking
the version again downloads the whole bundle rather than switching to the
copy on disk. Bundles from `run.sh --install` record no id and are never
flagged.

Resolved icon paths are prefixed with the version, texture set and build
(`2.9.0-beta-3~a1b2c3d4/item/...`, `2.9.0-beta-3~faithful32~a1b2c3d4/...`;
the build is the start of `data.json`'s SHA-256, `build_id()`) because
`/icons` responses are cached as immutable: without it, browsers would keep
the previous image for any path two builds share, including a rebuild of
the same version. `read_image()` strips any prefix, so paths stored before
a switch (`craft_events.item_icon`) still resolve. The prefix is also part
of `/api/network`'s ETag, and `catalog_version` is `<version>~<build>`, so
the scanner refetches a rebuilt catalog too.

The OC scanner gets the catalog from the server: `scan/start` returns
`catalog_version`, and when it differs from
`/home/item_catalog.server.txt.version` `network_browser.lua` streams
`/api/network/catalog` to disk (`http.download_to_file`, which checks the
HTTP status - OC's request iterator doesn't) as
`/home/item_catalog.server.txt`, which scans then prefer over
`CATALOG_PATH` (`/home/item_catalog.txt`). `gcm` no longer installs a
catalog, since a bundled one goes stale with every GTNH release; that path
is only a fallback for a hand-placed list (or one an older `gcm`
installed), and a scan with neither fails, asking for a version to be
picked. The server's copy is a separate file so a hand-placed list is
never overwritten.

Image paths inside the zip keep NESQL's scheme
(`item/<mod>/<name>~<damage>[~<nbt>].png`, `fluid/<mod>/<name>.png`), only
ending `.webp` since bundles are WebP, so old and new zips can be diffed.
`images.zip` is ~160 MB per version (~250 MB as PNG), so
it's never in git; the server reads icons out of it on demand, never
unpacking it to disk.

**The bundle index.** Held in memory, a GTNH 2.9 bundle cost every
server ~200 MB: `zipfile`'s directory of the 131k-icon zip ~90 MB, the
icon lookup ~64 MB, the item info ~24 MB, the ore dictionary ~20 MB. So
`gcm/gamedata_index.py` puts all four in one SQLite file, `index.sqlite`
(~40 MB on disk), that's read in place: lookups are single-row queries
(11k items' icons resolve in ~30 ms; their descriptions, for
`/api/network/info`, in ~125 ms - item text is keyed by the item key
first, so one query finds it in every section and table), and only the
few hundred mod names are kept in memory. An icon is read straight from its offset in the
zip with `os.pread` and `zlib` (~20 µs), its CRC checked. The file is
memory-mapped, so its pages are the OS's file cache, shared by every
server reading the same file (`GAMEDATA_SHARED_DIR`). `activate()`
builds it on first use - next to the bundle, or in the server's own
directory for one it can't write to - in a separate `python -I`
process, so the ~200 MB building takes goes back to the OS. It's
rebuilt when `data.json` or a texture zip's size changes, or its
`FORMAT` is bumped (2 added the item info); `app.py index-gamedata`
builds one into a shared bundle.

With that, and pywebpush/cryptography (Web Push) imported on first use
and matplotlib/numpy (charts) only in a child process (~70 MB, which
most servers never need), a server with GTNH 2.9 data, a full scan and
icons served holds ~54 MB instead of ~287 MB (peak ~80 MB instead of
~313 MB).

For debugging, `-Dgcm.iconexport.only=<regex>` and
`-Dgcm.iconexport.limit=<n>` (passed via `JAVA_TOOL_OPTIONS` to the
container) render a subset in about a minute.

## Known limitations

- Job-end detection can't see a CPU that finishes and immediately
  starts another job making the same item between two polls, or any
  job switch on a CPU without an AE2 Crafting Monitor (no final output
  is reported at all). A pin there carries over to the next job.
- Fluid icons only cover fluids registered by the time the main menu
  loads - a handful registered on world load (Galacticraft's fallback
  oil/fuel, Witchery brews) come up blank.
- ~265 item keys have no icon because their renderer needs a player or a
  world (see "Rendering icons at the main menu" above).
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
- Power sources: `power_monitor.lua` targets one `gt_machine` (errors
  out if it finds more than one and `CONFIG.COMPONENT_ADDRESS` isn't
  set), or every machine listed in `MACHINES`, each posted with its
  name and charted separately (`power_readings.machine`, '' for the
  unnamed one).


## gcm_agent: health and remote updates

`oc/gcm_agent.lua` is an rc service in gcm's shared files; `gcm install`
and the first `gcm update` that brings it enable it (once:
`agent_set_up` in /etc/gcm.cfg). Every minute it POSTs a heartbeat to
`/api/agent/heartbeat` (version, components and services from
/etc/gcm.cfg, each service's `is_running()` via `rc.loaded`, memory,
uptime, POLL_SECONDS); the reply may carry one admin-queued command (gcm/agents.py,
in memory, handed over once, dropped if not picked up within 3 polls).

Every script sends its interval with what it already posts
(craft_monitor and power_monitor `poll_seconds`, network_browser
`scan_interval_seconds` on scan/start, gcm_agent `poll_seconds`), and
each server limit that assumes one is `state.stale_after()`: the fixed
default, or three of the reported interval if longer. Older scripts
send none and get the defaults.

Commands are deliberately only `update` with ref `release` or `main`,
and `reboot`. The agent runs `/usr/bin/gcm.lua update --no-reboot
[--ref=main]` through loadfile() (so gcm's error message comes back as
its second return value), reports the result, and reboots if the
version changed. A result about to reboot says `rebooting = true`; the
server keeps the command `awaiting_boot` until a heartbeat with a lower
uptime than the one it was handed over at, then stamps `back_at`, and fails it after 3 polls
without one. A successful command leaves the card 10 minutes after it's
over; a failed one stays for the day. gcm downloads from its own hard-coded repository, so a
compromised server can trigger a real release, main, or a reboot, and
nothing else. Separate from craft_monitor on purpose: an update must
still work when the script it would fix has crashed.

Connecting a computer: the Status dialog's "Connect a computer" builds
one line, `wget ... gcm.lua && /tmp/gcm.lua install <scripts>
--server=<url> --api-key=<key>` (OpenOS's shell chains with `&&`; checked
in game), from `/api/admin/connect` (the API key and `PUBLIC_URL`; the
page's own origin without it). The scripts are the ones switched on above it,
a row of toggle buttons (`aria-pressed`) with each one's needed block as
its tooltip, to keep the section short (`CONNECT_SCRIPTS` in agents.js,
which a JS test holds to oc/manifest.lua's components; the choice kept in
localStorage); naming them
skips gcm's question for each. That gcm.lua comes from main, not a
release, so installer changes reach new computers on merge. Install asks
nothing else: it always enables the scripts and gcm_agent in rc and
reboots 3 seconds later, except when the server check failed or a value
was left blank (`write_config` returns whether the config is usable),
because the reboot would clear the explanation off the screen. The key is masked on screen but copied in full - the
clipboard API needs HTTPS or localhost, so it falls back to
`execCommand('copy')` for a plain-HTTP LAN page. Before writing
config.lua, `gcm install` POSTs `/api/agent/ping` with the key and says
what went wrong (401: wrong key; no connection: the address must work
from the Minecraft server, and a local one is in OC's default internet
blacklist). It saves the settings either way. On an existing config.lua
the options overwrite just those two string values in place (the first
`SERVER_URL =`/`API_KEY =` not in a comment), keeping per-script
overrides, so pasting the command again fixes a moved server or a new
key; the missing one is read from the file for the check. The address field is
hidden behind "Use a different address": most pages are opened at the
address the game reaches too. Each tab's never-reported empty state
(crafts: `ever_reported` false, which app.db's settings remember from
the first report so a restart shows the stale banner instead; power: no
`latest`; network: no items and no scan running) carries a button that opens the dialog scrolled there; it and
its non-admin "ask an admin" line are both in the markup, picked by
`body.is-admin` so a sign-in needs no re-render.

Auto-restart is the agent's own job, so it keeps working while the
server is down: the heartbeat reply only carries the switch
(`auto_restart`, app.db `settings`). A script's `stop()` sets the global
`stopped_on_purpose`, readable through `rc.loaded[name]`, which is how a
crash (thread dead, flag false) is told apart from `rc <name> stop`.
Only services in /etc/rc.cfg's `enabled`, not in the first 2 minutes of
uptime (rc is still starting them), at most 3 an hour each, counted on
`computer.uptime()` and forgotten on reboot.

Why a script stopped: craft_monitor and power_monitor run their loop
through xpcall and keep what it died of in the global `last_error`
({message, uptime}; start() leaves it, so it outlives the restart),
then re-raise so OpenOS still logs it to /tmp/event.log. Without this
the thread dies silently and only /tmp/event.log knows why.
network_browser catches its own crashes and carries on, so it doesn't.
The heartbeat sends it as {message, ago} (the server has no use for the
computer's clock), with `stopped_on_purpose`. A reboot clears the
globals, so the server carries a service's last error over from the
previous heartbeat for up to a day (agents._keep_errors()); it's lost
only if the server restarted in between too.

Auto-update uses the plain `update:release` command, which every agent
since the first understands; an exact `--ref=<tag>` would be refused by
exactly the old agents that need updating. So it fires only while the
server's own release (`GCM_VERSION`, a build arg in publish.yml) is also
the latest tag, and never moves a computer past its server. The
heartbeat reads the latest tag from the cache and refreshes it in a
background thread rather than waiting on GitHub. One try per (computer,
release) per server process.

The page's "update your scripts" notice compares each computer with
`GCM_SCRIPTS_VERSION`, not with the latest tag, so a release that only
changed the server says nothing. The image is built from `server/` with
no `oc/` or git, so publish.yml works it out:
1. the last commit at or before the tag that touched `oc/`, leaving out
   `oc/tests` and the `*_dump.lua` debug tools, which aren't installed
2. the oldest `vX.Y.Z` tag that contains that commit and is merged into
   this tag

`agents.scripts_behind()` leaves out offline computers, ones with a
command under way, and ones auto-update is about to update (target set,
not tried yet). A failed auto attempt shows again.

`is_running()` (each service, from its worker thread's status) exists
because `running` stays true when a loop dies of an error; `start()`
uses it too, so `rc <name> start` works again after such a crash.
`rc.loaded` is from OpenOS's lib/rc.lua source, not yet confirmed in game.
