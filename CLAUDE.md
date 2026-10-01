# GTNH Craft Monitor

OpenComputers Lua scripts in `oc/` post to a Flask server in `server/`,
which serves one page (`server/index.html` + `server/static/`, plain ES
modules, no build step).

## Seeing a change

`cd server && python dev.py` runs the real app with a fake game feeding it
(crafts, network, power, history, icons) on http://127.0.0.1:8421/ and
prints an admin token. Use it to look at any frontend change before
calling it done. Run it in the background, and stop it with a plain
`kill`; that deletes its temp data directory.

## Before pushing

CI runs all of these; run all of them, not just pytest:

```bash
cd server && python -m pytest -q                        # server + dev.py
node --test 'server/tests/js/*.test.js'                 # from the repo root
node server/tests/e2e/run.mjs                           # browser, needs Chromium
for f in server/static/js/*.js; do node --check "$f"; done
lua5.3 oc/tests/run.lua                                 # from the repo root
```

## Testing the OC scripts

`oc/tests/harness/` runs the real `oc/*.lua` scripts on a fake OpenOS
computer with a virtual clock, a fake ME network (`fake_ae2.lua`) and GT
machine (`fake_gt.lua`). `oc/tests/*_test.lua` test script logic against
scripted HTTP; `server/tests/test_oc_scripts.py` runs the scripts against
the real Flask app, so a payload change on either side shows up there.
The fakes only know the AE2/GT API as the notes in `craft_monitor.lua`
describe it, so they can't catch the real API differing from those notes.
Any Lua 5.3+ runs them (CI uses 5.3, as OpenComputers does).

The e2e suite clicks through the real page, so UI changes usually need
matching edits in `server/tests/e2e/run.mjs`.

## Frontend conventions

- The Content-Security-Policy blocks inline scripts, `on*=` handlers and
  `style="..."` in generated markup: set styles through the DOM
  (`el.style.width = ...`), and wire clicks with `data-action` attributes
  and `delegateActions()` (`static/js/util.js`). Action names are unique
  page-wide.
- The crafts tab re-renders `#root` from scratch every 3s; UI state that
  must survive (open sections, scroll) lives outside it or is restored.
- New `static/js` modules are only imported, never run on import;
  `main.js` calls their setup functions.
