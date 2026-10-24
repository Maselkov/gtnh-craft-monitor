# Contributing

## Repository layout

| Path | Purpose |
|---|---|
| `server/` | Flask server (`app.py` entrypoint, `gcm/` package) and the web page (`index.html`, `static/`) |
| `oc/craft_monitor.lua` | Reports crafting CPUs; executes craft requests and cancellations |
| `oc/power_monitor.lua` | Reports a GregTech machine's stored EU |
| `oc/network_browser.lua` | Scans ME network contents |
| `oc/gcm.lua`, `oc/manifest.lua` | Installer and updater, and the list of files it installs |
| `oc/gcm_agent.lua` | Reports the computer to the Status dialog; runs its update and reboot buttons |
| `oc/config.lua` | Server URL, API key and per-script settings for all scripts |
| `oc/http.lua`, `oc/json.lua` | Libraries used by the scripts |
| `oc/sensor_info_dump.lua` | One-off diagnostic: prints a GT machine's full sensor info |
| `oc/pattern_dump.lua` | One-off diagnostic: sends what the ME Interface Terminal reports about the network's patterns to `/api/debug` |
| `tools/icon-export/` | Builds game data (item icons, icon lookup, item catalog) from a GTNH pack; CI runs it for each GTNH release. See its [README](tools/icon-export/README.md). |
| `deploy/` | The Compose file for the published image, and the release deploy script |
| `docs/` | The user guides, [how it works](docs/how-it-works.md), and [`CONTEXT.md`](docs/CONTEXT.md), longer design notes and the gotchas found along the way |

## Running it locally

Run a local server with a fake game feeding it (Python with
`server/requirements.txt` installed):

```bash
cd server
python dev.py --open
```

It prints an admin access token to sign in with, and serves
http://127.0.0.1:8421/ from a temporary data directory (deleted on exit;
`--data-dir PATH` keeps one between runs). There are fake crafting CPUs
whose jobs progress, finish and restart, network scans, power readings,
a week of history for the charts, and an icon for every fake item. Craft
and cancel requests from the page are answered the way the in-game
scripts would. `--host 0.0.0.0` makes it reachable from a phone on the
same network; `--no-icons` runs without game data. It never touches
`server/data/`.

## Tests and checks

CI runs all of these on every push and pull request; run all of them before
pushing:

```bash
cd server && python -m pytest -q                        # server + dev.py
node --test 'server/tests/js/*.test.js'                 # from the repo root
node server/tests/e2e/run.mjs                           # browser, needs Chromium
for f in server/static/js/*.js; do node --check "$f"; done
lua5.3 oc/tests/run.lua                                 # from the repo root
ruff check                                              # from the repo root
luacheck oc                                             # from the repo root
shellcheck $(git ls-files '*.sh')
zizmor .github/workflows                                # after editing a workflow
```

- **Server** (`pip install -r server/tests/requirements-test.txt`): Flask's
  test client on a temporary data directory, so no running server or Docker
  and never `server/data/`. `test_oc_scripts.py` also runs the real
  `oc/*.lua` scripts against the app, so a payload change on either side
  shows up there; it needs a Lua interpreter (`OC_LUA=/path/to/lua` if it
  isn't found) and is skipped without one.
- **Frontend** (Node 24 or newer, no dependencies): the page's pure
  functions, such as search, amounts and formatting.
- **Browser** (Python with `server/requirements.txt`, Node 24+, Chrome or
  Chromium; set `CHROME=/path/to/binary` if it isn't found): starts the real
  server on seeded data and clicks through the page. UI changes usually need
  matching edits in `server/tests/e2e/run.mjs`.
- **OC scripts** (any Lua 5.3+; OpenComputers uses 5.3): `oc/tests/harness/`
  runs the real scripts on a fake OpenOS computer with a virtual clock, a fake
  ME network and a fake GT machine, against scripted HTTP.
  `lua5.3 oc/tests/run.lua craft` runs only tests whose name contains
  `craft`, and `OC_HARNESS_ECHO=1` shows what the scripts print. The fakes
  follow the AE2 and GT APIs as described in
  [How it works](docs/how-it-works.md), so they can't catch the real API
  differing from those.
- **Lint**: ruff's and zizmor's versions are in
  `.github/requirements-lint.txt`; luacheck and shellcheck are CI's Ubuntu
  packages (`lua-check`, `shellcheck`). Rules are in `ruff.toml` and
  `.luacheckrc`.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) also checks
`oc/*.lua` with `luac5.3 -p`, checks that every file `oc/manifest.lua`
lists exists, tests the icon export tools and builds the Docker image.
Actions are pinned to commit SHAs with the version in a comment; Dependabot
bumps both.

## Releases

Pushing a `v*` tag publishes the image to GHCR
([`.github/workflows/publish.yml`](.github/workflows/publish.yml)). `gcm`
in game installs from the newest release tag, so the image and the scripts
of one release go together.

## Docs

The README is the front page: what it is, the quick start, updating and
troubleshooting. The full guides are in `docs/`: [server](docs/server.md),
[in-game](docs/in-game.md), [using the page](docs/using.md) and
[how it works](docs/how-it-works.md). A user-visible change updates the
guide that describes it.
