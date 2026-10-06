// Browser tests: starts the real server on seeded data, drives headless
// Chrome through the Chrome DevTools Protocol, and clicks through every
// interactive part of the page - tabs, pins, modals, search, admin.
//
// No dependencies: Node's built-in WebSocket talks CDP directly.
//
//   node server/tests/e2e/run.mjs          (from the repo root)
//
// Needs Python with server/requirements.txt installed, Node 22+, and
// Chrome/Chromium (set CHROME=/path/to/binary if it isn't found).

import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';

const SERVER_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const API_KEY = 'e2e-api-key-0123456789abcdef0123456789abcdef';
const BOOTSTRAP_TOKEN = 'gcm_e2ebootstrap01_' + 'x'.repeat(32);
const TIMEOUT_MS = 12000;

const children = [];
const tempDirs = [];
process.on('exit', () => {
  for (const child of children) child.kill('SIGKILL');
  for (const dir of tempDirs) fs.rmSync(dir, { recursive: true, force: true });
});

function freePort() {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findChrome() {
  const candidates = [
    process.env.CHROME,
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  const found = candidates.find((c) => c && fs.existsSync(c));
  if (!found) throw new Error('No Chrome/Chromium found - set CHROME=/path/to/binary');
  return found;
}

// ---------------------------------------------------------------- server

async function startServer(extraEnv = {}) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'gcm-e2e-data-'));
  tempDirs.push(dataDir);
  const port = await freePort();
  const child = spawn(process.env.PYTHON || 'python3', ['app.py'], {
    cwd: SERVER_DIR,
    env: {
      ...process.env,
      DATA_DIR: dataDir,
      API_KEY,
      GCM_BOOTSTRAP_ADMIN_TOKEN: BOOTSTRAP_TOKEN,
      GCM_BOOTSTRAP_ADMIN_NAME: 'Administrator',
      SESSION_COOKIE_SECURE: '0',
      // The live crafting tree calls a step stuck after this long unmoved.
      CRAFT_STALL_SECONDS: '4',
      PORT: String(port),
      ...extraEnv,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  children.push(child);
  let log = '';
  child.stdout.on('data', (d) => { log += d; });
  child.stderr.on('data', (d) => { log += d; });
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(base + '/')).ok) return base;
    } catch (e) { /* not up yet */ }
    if (child.exitCode !== null) break;
    await sleep(100);
  }
  throw new Error('server did not start:\n' + log);
}

// ---------------------------------------------------------------- fake GitHub

// The Game data page lists gtnh-data-* releases from GitHub's API and
// downloads their assets. This serves one small but real bundle (a 16px
// icon for Iron Ingot, and a 32px one standing in for its Faithful
// render, and marked as drawn past the item box like a halo; Neutronium
// Ingot's is in the lookup but not the zips), made with Python so its zips and checksums are exactly what the
// server expects.
const E2E_DATA_VERSION = '2.9.0-e2e';
const MAKE_BUNDLE = `
import hashlib, json, struct, sys, zipfile, zlib
out = sys.argv[1]
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
def png(size):
    rows = b"".join(b"\\x00" + b"\\xcc\\x44\\x22\\xff" * size for _ in range(size))
    return (b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
for name, size in (("images.zip", 16), ("images-faithful32.zip", 32)):
    with zipfile.ZipFile(out + "/" + name, "w") as zf:
        zf.writestr("item/minecraft/iron_ingot~0.png", png(size))
open(out + "/icons_lookup.json", "w").write(json.dumps(
    {"by_key": {"minecraft:iron_ingot:0": "item/minecraft/iron_ingot~0.png",
                "gregtech:gt.metaitem.01:11028": "item/gregtech/gt.metaitem.01~11028.png"},
     "fluids_by_key": {}, "by_label": {}, "bleed": {"item/minecraft/iron_ingot~0.png": 12}}))
open(out + "/item_catalog.txt", "w").write("minecraft:iron_ingot\\n")
open(out + "/ore_dict.json", "w").write(json.dumps(
    {"gemDiamond": ["minecraft:diamond:0", "IC2:itemPartIndustrialDiamond:0"]}))
files = {}
for name in ("images.zip", "images-faithful32.zip", "icons_lookup.json", "item_catalog.txt", "ore_dict.json"):
    data = open(out + "/" + name, "rb").read()
    files[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
open(out + "/data.json", "w").write(json.dumps({"format": 1, "files": files, "generated_at": "2026-09-25T00:00:00Z"}))
`;
const BUNDLE_FILES = ['data.json', 'images.zip', 'images-faithful32.zip', 'icons_lookup.json', 'item_catalog.txt', 'ore_dict.json'];

async function startFakeGitHub() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gcm-e2e-gamedata-'));
  tempDirs.push(dir);
  const made = spawnSync(process.env.PYTHON || 'python3', ['-c', MAKE_BUNDLE, dir], { encoding: 'utf8' });
  if (made.status !== 0) throw new Error('making the game data bundle failed:\n' + made.stderr);
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/repos/')) {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify([{
        tag_name: `gtnh-data-${E2E_DATA_VERSION}`,
        published_at: '2026-09-25T00:00:00Z',
        assets: BUNDLE_FILES.map((name, index) => ({
          name,
          id: 1000 + index,
          size: fs.statSync(path.join(dir, name)).size,
          browser_download_url: `${base}/assets/${name}`,
        })),
      }]));
      return;
    }
    const name = req.url.replace('/assets/', '');
    if (!BUNDLE_FILES.includes(name)) {
      res.statusCode = 404;
      res.end();
      return;
    }
    res.end(fs.readFileSync(path.join(dir, name)));
  });
  await new Promise((resolve) => server.listen(port, '127.0.0.1', resolve));
  server.unref();
  return base;
}

async function api(base, method, urlPath, body, headers = {}) {
  const response = await fetch(base + urlPath, {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(`${method} ${urlPath} -> ${response.status} ${JSON.stringify(data)}`);
  return { response, data };
}

const game = (base, urlPath, body) => api(base, 'POST', urlPath, body, { 'X-API-Key': API_KEY });

// The server measures progress against the first report of a job, so
// seed() sends a starting report (atStart) first: 3/5 ingots and 6/10 ore
// left afterwards is 40%.
function postCrafts(base, w01Busy, atStart = false) {
  return game(base, '/api/crafts', {
    source: 'me_controller',
    jobs: [
      w01Busy
        ? {
            name: 'W01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
            final_output_internal: 'iron_ingot', final_output_damage: 0,
            active: [{ name: 'Iron Ingot', size: atStart ? 5 : 3 }],
            pending: [{ name: 'Iron Ore', size: atStart ? 10 : 6 }], stored: [],
          }
        : { name: 'W01', busy: false },
      { name: 'W02', busy: false },
    ],
  });
}

async function seed(base) {
  await postCrafts(base, true, true);
  await postCrafts(base, true);
  // Half an hour apart in all, so the Power tab has a trend to show.
  const now = Date.now() / 1000;
  for (const [ago, stored] of [[1800, 100000], [900, 200000], [0, 300000]]) {
    await game(base, '/api/power', { stored, capacity: 1000000, avg_eu_in_5s: 50, avg_eu_out_5s: 20, timestamp: now - ago });
  }
  await scanNetwork(base);
  await scanPatterns(base);
}

const SEEDED_ITEMS = [
  { mod: 'minecraft', internal: 'iron_ingot', damage: 0, name: 'Iron Ingot', size: 1234, isCraftable: true },
  { mod: 'gregtech', internal: 'gt.metaitem.01', damage: 11028, name: 'Neutronium Ingot', size: 5, isCraftable: false },
  { internal: 'water', kind: 'fluid', name: 'Water', size: 64000 },
];

async function scanNetwork(base) {
  const { data: scan } = await game(base, '/api/network/scan/start', {});
  // Copies: the server reads the items, and these are reused.
  await game(base, '/api/network/scan/batch', { scan_token: scan.scan_token, items: SEEDED_ITEMS.map(it => ({ ...it })) });
  await game(base, '/api/network/scan/finish', { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0 });
}

// ---------------------------------------------------------------- browser
// Iron Ingot from Iron Dust (in the later slot, so AE2 tries it first) or
// from Water, 20,000 a time; the dust from Neutronium Ingot, of which
// there are only 5; more Neutronium from Neutronium Dust, which there is
// none of - four levels, one more than the plan's first answer holds.
const pattern = (provider, slot, outputs, inputs) => ({
  provider: { name: provider, x: slot, y: 64, z: 0, dim: 0 }, slot, crafting: false, outputs, inputs,
});
const IRON_INGOT = { kind: 'item', mod: 'minecraft', internal: 'iron_ingot', damage: 0, name: 'Iron Ingot', size: 1 };
const IRON_DUST = { kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 2032, name: 'Iron Dust', size: 1 };
const SEEDED_PATTERNS = [
  pattern('Fluid Solidifier', 0, [IRON_INGOT], [{ kind: 'fluid', internal: 'water', name: 'Water', size: 20000 }]),
  pattern('Furnace', 1, [IRON_INGOT], [IRON_DUST]),
  pattern('Macerator', 2, [IRON_DUST],
    [{ kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 11028, name: 'Neutronium Ingot', size: 1 }]),
  pattern('Compressor', 3,
    [{ kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 11028, name: 'Neutronium Ingot', size: 1 }],
    [{ kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 2129, name: 'Neutronium Dust', size: 1 }]),
];

// A pattern's NBT holding only byte flags (substitute, beSubstitute),
// gzipped and hex-encoded, as network_browser.lua sends it.
function flagsTag(flags) {
  const parts = [Buffer.from([0x0a, 0, 0])];
  for (const [name, value] of Object.entries(flags)) {
    const len = Buffer.alloc(2);
    len.writeUInt16BE(name.length);
    parts.push(Buffer.from([0x01]), len, Buffer.from(name), Buffer.from([value]));
  }
  parts.push(Buffer.from([0]));
  return zlib.gzipSync(Buffer.concat(parts)).toString('hex');
}

async function scanPatterns(base) {
  const { data: scan } = await game(base, '/api/network/patterns/start', {});
  await game(base, '/api/network/patterns/batch', { scan_token: scan.scan_token, patterns: SEEDED_PATTERNS });
  await game(base, '/api/network/patterns/finish', { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0 });
}

class Cdp {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
      } else if (msg.method && this.handlers.has(msg.method)) {
        for (const fn of this.handlers.get(msg.method)) fn(msg.params);
      }
    };
    this.opened = new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = reject;
    });
  }
  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }
  on(method, fn) {
    if (!this.handlers.has(method)) this.handlers.set(method, []);
    this.handlers.get(method).push(fn);
  }
}

async function startBrowser() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'gcm-e2e-chrome-'));
  tempDirs.push(profile);
  const child = spawn(findChrome(), [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--no-first-run',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--window-size=1280,900', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  children.push(child);
  const wsUrl = await new Promise((resolve, reject) => {
    let err = '';
    child.stderr.on('data', (d) => {
      err += d;
      const m = err.match(/DevTools listening on (ws:\/\/\S+)/);
      if (m) resolve(m[1]);
    });
    child.on('exit', () => reject(new Error('chrome exited:\n' + err)));
  });
  const port = new URL(wsUrl).port;
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const cdp = new Cdp(target.webSocketDebuggerUrl);
  await cdp.opened;
  return cdp;
}

// ---------------------------------------------------------------- page helpers

function pageHelpers(cdp) {
  const errors = [];
  cdp.on('Runtime.exceptionThrown', (p) => {
    const d = p.exceptionDetails;
    errors.push(`uncaught: ${d.exception?.description || d.text}`);
  });
  cdp.on('Runtime.consoleAPICalled', (p) => {
    if (p.type === 'error') errors.push(`console.error: ${p.args.map((a) => a.value ?? a.description).join(' ')}`);
  });
  cdp.on('Log.entryAdded', ({ entry }) => {
    // Missing icons (no images.zip in the test data dir) are expected 404s.
    if (entry.level === 'error' && entry.source !== 'network') errors.push(`${entry.source}: ${entry.text}`);
  });

  async function evaluate(expression) {
    const result = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) {
      throw new Error(`${expression}\n  -> ${result.exceptionDetails.exception?.description || result.exceptionDetails.text}`);
    }
    return result.result.value;
  }

  async function waitFor(description, expression) {
    const deadline = Date.now() + TIMEOUT_MS;
    let last;
    while (Date.now() < deadline) {
      try {
        last = await evaluate(expression);
        if (last) return last;
      } catch (e) {
        last = e.message;
      }
      await sleep(100);
    }
    throw new Error(`timed out waiting for: ${description}\n  last value: ${JSON.stringify(last)}`);
  }

  return { errors, evaluate, waitFor };
}

// Selecting by visible text keeps these tests independent of how the
// markup wires its handlers, which is exactly what refactors change.
const PAGE_LIB = `
  window.$ = (sel, root = document) => root.querySelector(sel);
  window.$$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  window.byText = (sel, text, root = document) =>
    $$(sel, root).find((el) => el.textContent.trim() === text);
  window.visible = (el) => !!el && el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  window.card = (cpu) => $$('#root .card').find((c) => c.querySelector('.card-cpu')?.textContent === 'CPU ' + cpu);
  window.typeInto = (sel, text) => {
    const el = $(sel);
    el.focus();
    el.value = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };
  window.pressKey = (key, sel) => (sel ? $(sel) : document.body)
    .dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
  window.pressEnter = (sel) => pressKey('Enter', sel);
  // Opens an item's history the way a user would: search, click the cell.
  window.openItem = (name) => {
    typeInto('#networkSearch', name);
    const cells = $$('#networkList .network-cell');
    if (cells.length !== 1) throw new Error('expected one cell for ' + name + ', got ' + cells.length);
    cells[0].click();
  };
  window.confirm = () => true;
  true;
`;

// ---------------------------------------------------------------- tests

async function main() {
  const github = await startFakeGitHub();
  const base = await startServer({ GAMEDATA_API_URL: github, GAMEDATA_REPO: 'e2e/repo' });
  await seed(base);
  let adminToken;  // the bootstrap replacement, created through the UI below
  const cdp = await startBrowser();
  const { errors, evaluate, waitFor } = pageHelpers(cdp);
  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Page.enable');
  await cdp.send('Page.navigate', { url: base + '/' });
  await waitFor('page loaded', `document.readyState === 'complete'`);
  await evaluate(PAGE_LIB);

  const steps = [];
  const step = (name, fn) => steps.push({ name, fn });
  const click = (expr) => evaluate(`(${expr}).click(), true`);

  step('crafts tab renders the busy CPU', async () => {
    await waitFor('W01 card', `card('W01')?.querySelector('.craft-title').textContent.includes('Iron Ingot')`);
    await waitFor('W02 in idle group', `$('details.idle-group') && card('W02')`);
  });

  step('allowed inline styles apply; the progress bar is sized', async () => {
    // Every dialog starts hidden via style="display:none;" - which only
    // works if the CSP's style hashes match index.html.
    await waitFor('all dialogs hidden', `$$('.modal-overlay').length >= 5 && $$('.modal-overlay').every((m) => !visible(m))`);
    await waitFor('progress 40%', `card('W01').querySelector('.progress-fill').style.width === '40%'`);
  });

  step('Content-Security-Policy blocks injected script and style', async () => {
    const before = errors.length;
    await evaluate(`document.body.insertAdjacentHTML('beforeend',
      '<img id="xssImg" src="/icons?path=missing.png" onerror="window.__xss = 1">'
      + '<div id="xssStyle" style="color: rgb(255, 0, 0)">x</div>'), true`);
    await sleep(1000);
    await waitFor('inline handler did not run', `window.__xss === undefined`);
    await waitFor('inline style ignored', `getComputedStyle($('#xssStyle')).color !== 'rgb(255, 0, 0)'`);
    await evaluate(`$('#xssImg').remove(), $('#xssStyle').remove(), true`);
    const reported = errors.splice(before);
    if (!reported.some((e) => /Content Security Policy/i.test(e))) {
      throw new Error('expected a CSP violation report, got:\n  ' + reported.join('\n  '));
    }
  });

  step('sign in with the bootstrap token and rotate it', async () => {
    await evaluate(`typeInto('#accessTokenInput', ${JSON.stringify(BOOTSTRAP_TOKEN)})`);
    await click(`$('#authBtn')`);
    await waitFor('rotation dialog', `visible($('#bootstrapRotationModal'))`);
    await click(`$('#bootstrapRotationBtn')`);
    await waitFor('replacement token', `$('#bootstrapRotationToken').value.startsWith('gcm_')`);
    adminToken = await evaluate(`$('#bootstrapRotationToken').value`);
    await click(`$('#bootstrapRotationBtn')`);  // "I saved the replacement token"
    await waitFor('dialog closed, admin menu available', `!visible($('#bootstrapRotationModal')) && visible($('#settingsWrap'))`);
  });

  step('sign out, then sign in again with the Enter key', async () => {
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`byText('#settingsMenu button', 'Sign out')`);
    await waitFor('signed out', `visible($('#authBtn')) && !visible($('#settingsWrap'))`);
    await evaluate(`typeInto('#accessTokenInput', ${JSON.stringify(adminToken)})`);
    await evaluate(`pressEnter('#accessTokenInput')`);
    await waitFor('signed in', `visible($('#settingsWrap')) && $('#settingsUser').textContent.includes('Administrator')`);
  });

  step('notifications switch in the settings menu', async () => {
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await waitFor('off before permission', `$('#notifToggle').getAttribute('aria-checked') === 'false'`);
    await cdp.send('Browser.grantPermissions', { origin: base, permissions: ['notifications'] });
    await click(`$('#notifToggle')`);
    await waitFor('switched on', `$('#notifToggle').getAttribute('aria-checked') === 'true'`);
    await waitFor('menu stays open', `visible($('#settingsMenu'))`);
    await click(`$('#notifToggle')`);
    await waitFor('switched off despite permission', `$('#notifToggle').getAttribute('aria-checked') === 'false'`);
    await click(`$('#notifToggle')`);
    await waitFor('back on', `$('#notifToggle').getAttribute('aria-checked') === 'true'`);
    await waitFor('service worker registered', `navigator.serviceWorker.getRegistration('/').then(r => Boolean(r && r.active))`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('menu closed', `!visible($('#settingsMenu'))`);
  });

  step('a CPU\'s live crafting tree: rebuilt from patterns, updated in place, stuck steps', async () => {
    // J01 makes 10 Iron Ingots: the dust (crafting) from the Neutronium
    // it pulled - so the Furnace's pattern, not the Fluid Solidifier AE2
    // would try first.
    const ironIngot = { name: 'Iron Ingot', mod: 'minecraft', internal: 'iron_ingot', damage: 0 };
    const ironDust = { name: 'Iron Dust', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 2032 };
    const neutronium = { name: 'Neutronium Ingot', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 11028 };
    const report = (dustLeft) => game(base, '/api/crafts', { source: 'me_controller', jobs: [
      { name: 'W01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
        final_output_internal: 'iron_ingot', final_output_damage: 0,
        active: [{ name: 'Iron Ingot', size: 3 }], pending: [{ name: 'Iron Ore', size: 6 }], stored: [] },
      { name: 'W02', busy: false },
      { name: 'J01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
        final_output_internal: 'iron_ingot', final_output_damage: 0,
        pending: [{ ...ironIngot, size: 10 }], active: [{ ...ironDust, size: dustLeft }],
        stored: [{ ...neutronium, size: 10 }] },
    ] });
    await report(10);
    await waitFor('J01 card', `card('J01')?.querySelector('.ingredients-btn')`);
    await click(`card('J01').querySelector('.ingredients-btn')`);
    await click(`$('#ingredientsModal [data-view="tree"]')`);
    await waitFor('tree', `$$('#ingredientsTree li.job-node').length === 3 && !visible($('#ingredientsGrid'))`);
    await waitFor('rows', `(() => {
      const [ingot, dust, raw] = $$('#ingredientsTree li.job-node');
      const row = (li) => li.querySelector(':scope > .plan-row').textContent;
      return row(ingot).includes('0 / 10') && row(ingot).includes('Furnace') && ingot.classList.contains('job-waiting')
        && row(dust).includes('Macerator')
        && row(dust).includes('0 / 10') && dust.classList.contains('job-active')
        && raw.classList.contains('job-storage') && raw.textContent.includes('from storage');
    })()`);
    await evaluate(`window.dustText = $$('#ingredientsTree li.job-node')[1].querySelector('.job-step-text'), true`);
    // A new report: the numbers change in place, the row isn't redrawn.
    await report(4);
    await waitFor('updated in place', `window.dustText.textContent === '6 / 10' && window.dustText.isConnected
      && $$('#ingredientsTree .job-bar-fill')[1].style.width === '60%'`);
    // Shutting and opening a step redraws the tree: the bar is drawn where
    // it was, not replayed from 0.
    await sleep(5000);  // the update's own transition (4.5s) is over
    await click(`$$('#ingredientsTree [data-action="job-tree-toggle"]')[0]`);
    await click(`$$('#ingredientsTree [data-action="job-tree-toggle"]')[0]`);
    const ratio = await evaluate(`(() => {
      const fill = $$('#ingredientsTree .job-bar-fill')[1];
      return parseFloat(getComputedStyle(fill).width) / parseFloat(getComputedStyle(fill.parentElement).width);
    })()`);
    if (Math.abs(ratio - 0.6) > 0.02) throw new Error(`bar replayed from 0 on redraw: at ${ratio} of its track, not 0.6`);
    // Unmoved past CRAFT_STALL_SECONDS: stuck, with a chip to jump to it.
    await report(4);
    await waitFor('stuck', `$$('#ingredientsTree li.job-node')[1].classList.contains('job-stuck')
      && !$('#ingredientsStuck').hidden && $('#ingredientsStuck').textContent.includes('Iron Dust')`);
    const tint = await evaluate(`(() => {
      const bg = (li) => getComputedStyle(li.querySelector(':scope > .plan-row')).backgroundColor;
      const [root, stuck] = $$('#ingredientsTree li.job-node');
      return { stuck: bg(stuck), other: bg(root) };
    })()`);
    if (tint.stuck === tint.other) throw new Error(`a stuck step's row isn't tinted: ${JSON.stringify(tint)}`);
    await click(`$$('#ingredientsTree [data-action="job-tree-toggle"]')[0]`);
    await waitFor('shut, saying what is under it', `$$('#ingredientsTree li.job-node').length === 1
      && $('#ingredientsTree .job-step-note .job-count-stuck')?.textContent === '1 stuck'`);
    await click(`$('#ingredientsStuck .plan-chip')`);
    await waitFor('jumped to it', `$('#ingredientsTree li[data-pos="0"] > .plan-row')?.classList.contains('plan-jump')`);
    await click(`$('#ingredientsModal [data-view="grid"]')`);
    await waitFor('grid again', `visible($('#ingredientsGrid')) && $('#ingredientsTreeWrap').hidden`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed', `!visible($('#ingredientsModal'))`);
    await postCrafts(base, true);
  });

  step('ingredients modal; stays open across a refresh, Escape closes it', async () => {
    await waitFor('ingredients button', `card('W01').querySelector('.ingredients-btn')`);
    await click(`card('W01').querySelector('.ingredients-btn')`);
    await waitFor('open with a cell per item', `visible($('#ingredientsModal')) && $$('#ingredientsGrid .ingredient-cell').length === 2`);
    await waitFor('crafting cell first', `$('#ingredientsGrid .ingredient-cell').classList.contains('crafting')
      && $('#ingredientsGrid .ingredient-cell').textContent.includes('Crafting: 3')`);
    await sleep(3500);  // the crafts tab re-renders every 3s
    await waitFor('still open after re-render', `visible($('#ingredientsModal')) && $$('#ingredientsGrid .ingredient-cell').length === 2`);
    // The filter box keeps what it matches through the next refresh.
    await evaluate(`typeInto('#ingredientsFilter', 'ore'), true`);
    await waitFor('filtered to the ore', `$$('#ingredientsGrid .ingredient-cell').length === 1
      && $('#ingredientsGrid .ingredient-cell').dataset.name === 'Iron Ore'`);
    await sleep(3500);
    await waitFor('still filtered after re-render', `$$('#ingredientsGrid .ingredient-cell').length === 1`);
    await evaluate(`typeInto('#ingredientsFilter', 'gold'), true`);
    await waitFor('nothing matches', `!$('#ingredientsGrid .ingredient-cell')
      && $('#ingredientsGrid').textContent.includes('Nothing matches')`);
    // Escape in the box clears it; the next one closes the dialog.
    await evaluate(`pressKey('Escape', '#ingredientsFilter'), true`);
    await waitFor('cleared, still open', `visible($('#ingredientsModal')) && $('#ingredientsFilter').value === ''
      && $$('#ingredientsGrid .ingredient-cell').length === 2`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed', `!visible($('#ingredientsModal'))`);
  });

  step('expand idle CPUs; stays open across a refresh', async () => {
    await click(`$('details.idle-group summary')`);
    await waitFor('open', `$('details.idle-group').open`);
    await sleep(3500);
    await waitFor('still open after re-render', `$('details.idle-group').open`);
  });

  step('cancel dialog opens for the CPU and closes', async () => {
    await waitFor('cancel button', `card('W01').querySelector('.cancel-btn')`);
    await click(`card('W01').querySelector('.cancel-btn')`);
    await waitFor('dialog open', `visible($('#cancelConfirmModal')) && $('#cancelConfirmSub').textContent === 'CPU W01'`);
    await click(`byText('#cancelConfirmModal button', 'Keep going')`);
    await waitFor('closed by button', `!visible($('#cancelConfirmModal'))`);
    await click(`card('W01').querySelector('.cancel-btn')`);
    await waitFor('dialog open again', `visible($('#cancelConfirmModal'))`);
    await click(`$('#cancelConfirmModal')`);
    await waitFor('closed by backdrop', `!visible($('#cancelConfirmModal'))`);
  });

  step('pin a CPU, get its completion, acknowledge it', async () => {
    await click(`card('W01').querySelector('.pin-btn')`);
    await waitFor('pinned', `byText('.group-heading', 'Pinned (1)') && card('W01').querySelector('.pin-btn.pinned')`);
    await postCrafts(base, false);
    await waitFor('completion shown', `$('.completed-card')`);
    await click(`$('.completed-card .ack-btn')`);
    await waitFor('completion acknowledged', `!$('.completed-card')`);
  });

  step('acknowledge all completions', async () => {
    await postCrafts(base, true);
    await waitFor('busy again', `card('W01')?.querySelector('.pin-btn:not([disabled])')`);
    await click(`card('W01').querySelector('.pin-btn')`);
    await waitFor('pinned', `card('W01').querySelector('.pin-btn.pinned')`);
    await postCrafts(base, false);
    await waitFor('completion shown', `$('.ack-all-btn')`);
    await click(`$('.ack-all-btn')`);
    await waitFor('all acknowledged', `!$('.completed-card')`);
    await postCrafts(base, true);
  });

  step('recent crafts: lists ended jobs, picks up new ones, scrolls, opens the item', async () => {
    await click(`$('#craftHistory summary')`);
    await waitFor('both ended jobs', `$$('#craftHistoryList .history-row').length === 2`);
    await waitFor('item and CPU shown', `$('#craftHistoryList .history-row').textContent.includes('Iron Ingot')
      && $('#craftHistoryList .history-row').textContent.includes('CPU W01')`);
    await postCrafts(base, false);
    await waitFor('new end added while open', `$$('#craftHistoryList .history-row').length === 3`);
    await postCrafts(base, true);
    // More than a page ends while it's closed: reopened, it shows one
    // page, and scrolling down loads the rest.
    await click(`$('#craftHistory summary')`);
    for (let i = 0; i < 30; i++) {
      await postCrafts(base, false);
      await postCrafts(base, true);
    }
    await click(`$('#craftHistory summary')`);
    await waitFor('first page', `$$('#craftHistoryList .history-row').length === 25`);
    await evaluate(`window.scrollTo(0, document.documentElement.scrollHeight), true`);
    await waitFor('scrolled to the end', `$$('#craftHistoryList .history-row').length === 33
      && $('#craftHistoryLoading').hidden`);
    // Item history belongs to the Network tab, wherever it's opened from.
    await click(`$('#craftHistoryList .item-history-link')`);
    await waitFor('item history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'`);
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('item history closed', `!visible($('#itemHistoryModal'))`);
    await click(`$('#tabBtnCrafts')`);
    await waitFor('crafts tab', `visible($('#craftsTab'))`);
    await click(`$('#craftHistory summary')`);
    await waitFor('section closed', `!$('#craftHistory').open`);
  });

  step('confirm a cancel; the game reports it done', async () => {
    await waitFor('busy W01', `card('W01')?.querySelector('.cancel-btn:not([disabled])')`);
    await click(`card('W01').querySelector('.cancel-btn')`);
    await waitFor('dialog open', `visible($('#cancelConfirmModal'))`);
    await click(`$('#cancelConfirmSubmitBtn')`);
    await waitFor('pending on the card', `!visible($('#cancelConfirmModal')) && card('W01').querySelector('.cancel-btn[disabled]')`);
    const { data } = await api(base, 'GET', '/api/craft/cancel/pending', undefined, { 'X-API-Key': API_KEY });
    await api(base, 'POST', `/api/craft/cancel/${data.requests[0].id}/result`, { success: true }, { 'X-API-Key': API_KEY });
    await waitFor('reported', `$('#toastContainer').textContent.includes('Craft cancelled')`);
  });

  step('power tab and range buttons', async () => {
    await click(`$('#tabBtnPower')`);
    await waitFor('power tab', `visible($('#powerTab')) && location.pathname === '/power'`);
    await waitFor('reading shown', `$('#powerCurrent').textContent.includes('EU')`);
    // +200k EU over 30 minutes leaves 700k to go: 1h 45m.
    await waitFor('eta shown', `$('#powerEta')?.textContent === 'Full in 1h 45m \u00b7 +400.0k EU/h over the past hour'`);
    await click(`$('#powerTab [data-range="week"]')`);
    await waitFor('week active', `$('#powerTab [data-range="week"]').classList.contains('active')`);
  });

  step('a network_browser crash shows on the Network tab until a scan finishes', async () => {
    await game(base, '/api/network/crashed', { error: 'not enough memory', phase: 'Batch 30', free_memory: 2048 });
    await click(`$('#tabBtnCrafts')`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('crash shown', `$('#networkSourceLine').textContent.includes('network_browser.lua crashed')
      && $('#networkSourceLine').textContent.includes('2k memory free: not enough memory')
      && $('#networkSourceLine').classList.contains('stale-warning')`);
    await scanNetwork(base);
    await click(`$('#tabBtnCrafts')`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('cleared by the next scan', `!$('#networkSourceLine').textContent.includes('crashed')
      && !$('#networkSourceLine').classList.contains('stale-warning')`);
    await click(`$('#tabBtnCrafts')`);
  });

  step('network tab, search and sort', async () => {
    await click(`$('#tabBtnNetwork')`);
    await waitFor('3 cells', `location.pathname === '/network' && $$('#networkList .network-cell').length === 3`);
    await waitFor('search focused', `document.activeElement === $('#networkSearch')`);
    await evaluate(`typeInto('#networkSearch', '@gregtech')`);
    await waitFor('filtered to 1', `$$('#networkList .network-cell').length === 1`);
    await waitFor('highlight', `$('#networkSearchHighlight .search-hl-mod')?.textContent === '@gregtech'`);
    await click(`$('#networkTab [data-sort="name"]')`);
    await waitFor('sort active', `$('#networkTab [data-sort="name"]').classList.contains('active')`);
    await evaluate(`typeInto('#networkSearch', '')`);
    await waitFor('back to 3', `$$('#networkList .network-cell').length === 3`);
  });

  step('item history: open, range, pin, craft request with math', async () => {
    await evaluate(`openItem('Iron Ingot'), true`);
    await waitFor('history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'`);
    await waitFor('url', `location.pathname.startsWith('/network/item/minecraft:iron_ingot')`);
    await click(`$('#itemHistoryRangeButtons [data-range="week"]')`);
    await waitFor('week active', `$('#itemHistoryRangeButtons [data-range="week"]').classList.contains('active')`);
    await click(`$('#itemHistoryPinBtn')`);
    await waitFor('item pinned', `$('#itemHistoryPinBtn').classList.contains('pinned')`);
    // Requesting a craft closes the history popup on the way.
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('craft dialog', `visible($('#craftRequestModal')) && !visible($('#itemHistoryModal'))`);
    await click(`byText('#craftRequestModal button', 'Cancel')`);
    await waitFor('closed by button', `!visible($('#craftRequestModal'))`);
    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('craft dialog again', `visible($('#craftRequestModal'))`);
    await click(`$('#craftRequestModal')`);
    await waitFor('closed by backdrop', `!visible($('#craftRequestModal'))`);
    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('craft dialog', `visible($('#craftRequestModal')) && $('#craftRequestName').textContent.includes('Iron Ingot')`);
    await evaluate(`typeInto('#craftRequestAmount', '4+3*2')`);
    await waitFor('amount preview', `/\\b10\\b/.test($('#craftRequestAmountPreview').textContent)`);
    await click(`$('#craftRequestSubmitBtn')`);
    await waitFor('craft dialog closed', `!visible($('#craftRequestModal'))`);
  });

  step('the craft dialog shows the crafting plan: list, tree, alternatives', async () => {
    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('plan for 1', `visible($('#craftPlan')) && $('#craftPlanSummary').textContent.includes('Everything is in stock')`);
    // List view: every item the plan touches, Neutronium all from stock.
    await waitFor('list cells', `$$('#craftPlanBody .plan-cell').length === 3`);
    // Of Neutronium's 5, the 1 the plan takes, as the game shows it.
    await waitFor('available is what the plan takes', `$$('#craftPlanBody .plan-cell').some(c =>
      c.textContent.includes('Available: 1') && c.textContent.includes('Used: 20.0%'))`);
    // A stock rule shows only once the plan itself crosses it: 5 Neutronium
    // in stock, an alert below 3 - fine for 1, broken by 3.
    const neutronium = { label: 'Neutronium Ingot', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 11028 };
    await evaluate(`fetch('/api/stock/alert', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(${JSON.stringify({ ...neutronium, below: 3 })}) }).then(r => r.ok)`);
    await evaluate(`typeInto('#craftRequestAmount', '2')`);
    await waitFor('2 leaves 3: no rule broken', `$('#craftPlanSummary').textContent.includes('Everything')
      && $('#craftPlanBody .plan-cell') && $('#craftPlanRules').hidden`);
    await evaluate(`typeInto('#craftRequestAmount', '3')`);
    await waitFor('rule dropdown', `!$('#craftPlanRules').hidden && $('#craftPlanRulesSummary').textContent === '1 stock rule'
      && !$('#craftPlanSummary').textContent.includes('below your alert')`);
    await click(`$('#craftPlanRulesSummary')`);
    await waitFor('dropdown open', `$('#craftPlanRules').open && visible($('#craftPlanRulesList'))
      && $('#craftPlanRulesList').textContent.includes('Neutronium Ingot')
      && $('#craftPlanRulesList').textContent.includes('5 → 2 left, below your alert (3)')`);
    await click(`$('#craftPlanSummary')`);
    await waitFor('closed by a click elsewhere', `!$('#craftPlanRules').open`);
    await evaluate(`fetch('/api/stock/alert/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(${JSON.stringify(neutronium)}) }).then(r => r.ok)`);
    await evaluate(`typeInto('#craftRequestAmount', '10')`);
    // As AE2 plans it: the furnace makes the 5 its Neutronium covers, the
    // solidifier the 3 its water covers, and the furnace, first, takes the
    // rest - 2 Neutronium Dust short.
    await waitFor('2 Neutronium Dust short', `$('#craftPlanSummary .plan-missing-strip')?.textContent.includes('Neutronium Dust')
      && $('#craftPlanSummary .plan-chip b').textContent === '2'`);
    await waitFor('missing cell first', `$('#craftPlanBody .plan-cell').classList.contains('missing')`);
    // Neutronium: 5 from stock, 2 more crafted. Crafted cells say how many,
    // not how much of the stock is used (all of it, always), and aren't
    // tinted, as in the game.
    await waitFor('crafted cells without use', `$$('#craftPlanBody .plan-cell').filter(c => c.textContent.includes('Crafting'))
      .every(c => !c.querySelector('.plan-used') && !c.textContent.includes('Available'))
      && $$('#craftPlanBody .plan-cell').some(c => c.textContent.includes('Crafting: 2'))
      && !$('#craftPlanBody .plan-cell.crafting')`);
    // A haloed icon (drawn 2.5x its box, see .icon-bleed) in the rightmost
    // cell mustn't make the list scroll sideways. No game data is
    // installed yet, so one is put there by hand.
    await evaluate(`(() => {
      const cell = $$('#craftPlanBody .plan-cell').sort((a, b) =>
        b.getBoundingClientRect().right - a.getBoundingClientRect().right)[0];
      const icon = document.createElement('div');
      icon.id = 'haloTest';
      icon.className = 'ingredient-cell-icon icon-bleed';
      cell.appendChild(icon);
      return true;
    })()`);
    // A horizontal scrollbar would take height inside the box's 1px borders.
    await waitFor('no sideways scrollbar', `$('#craftPlanBody').offsetHeight - $('#craftPlanBody').clientHeight === 2`);
    await evaluate(`$('#haloTest').remove(), true`);

    // The tree: the ingot split over three parts, each a row of its own.
    const rows = `$$('#craftPlanBody li.plan-node').map(li => li.querySelector(':scope > .plan-row').textContent.replace(/\\s+/g, ' '))`;
    const toggle = (text, n = 0) => `$$('#craftPlanBody li.plan-node').filter(li =>
      li.querySelector(':scope > .plan-row').textContent.includes(${JSON.stringify(text)}))[${n}]
      .querySelector(':scope > .plan-row [data-action="plan-toggle"]')`;
    await click(`$('#craftPlan [data-view="tree"]')`);
    await waitFor('split tree', `$('#craftPlanBody .plan-tree') && $$('#craftPlanBody .plan-node').length === 7
      && ${rows}[0].includes('craft 10 from 3 patterns')
      && ${rows}[1].includes('via Furnace') && ${rows}[1].includes('makes 5')
      && ${rows}[3].includes('via Fluid Solidifier') && ${rows}[3].includes('makes 3')
      && ${rows}[5].includes('via Furnace') && ${rows}[5].includes('makes 2')
      && !$('#craftPlanBody select.plan-alt')`);
    // Down the last part to what's short.
    await click(toggle('Iron Dust', 1));
    await waitFor('dust open', `${rows}.some(r => r.includes('Neutronium Ingot') && r.includes('craft 2'))`);
    await click(toggle('Neutronium Ingot', 0));
    await waitFor('dust short', `${rows}.some(r => r.includes('Neutronium Dust') && r.includes('missing 2'))`);
    // The filter keeps only the way down to what matches, and that
    // step's own steps below it; Enter in it doesn't send the request.
    await evaluate(`typeInto('#craftPlanFilter', 'neutronium dust'), true`);
    await evaluate(`pressEnter('#craftPlanFilter'), true`);
    await waitFor('filtered tree', `${rows}.length === 5 && ${rows}[4].includes('Neutronium Dust')
      && $$('#craftPlanBody .filter-match').length === 1 && visible($('#craftRequestModal'))`);
    await click(`$('#craftPlan [data-view="list"]')`);
    await waitFor('filtered list', `$$('#craftPlanBody .plan-cell').length === 1`);
    await click(`$('#craftPlan [data-view="tree"]')`);
    await evaluate(`typeInto('#craftPlanFilter', ''), true`);
    await waitFor('unfiltered', `${rows}.some(r => r.includes('Neutronium Dust') && r.includes('missing 2'))
      && !$('#craftPlanBody .filter-match')`);
    // Collapsing survives a new amount.
    await click(toggle('via Furnace', 0));
    await waitFor('collapsed', `$$('#craftPlanBody .plan-node').length === 8`);
    await evaluate(`typeInto('#craftRequestAmount', '12')`);
    await waitFor('re-planned, still collapsed', `${rows}[0].includes('craft 12 from 3 patterns')
      && ${rows}.some(r => r.includes('via Furnace') && r.includes('makes 4'))
      && $$('#craftPlanBody .plan-node').length === 8`);
    // 3 the furnace alone covers; the other pattern for the ingot, from
    // water, of which there's enough for 3.
    await evaluate(`typeInto('#craftRequestAmount', '3')`);
    await waitFor('one pattern', `$('#craftPlanBody select.plan-alt') && ${rows}[0].includes('Furnace')`);
    await evaluate(`(() => { const sel = $('#craftPlanBody select.plan-alt');
      sel.value = sel.options[1].value; sel.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
    await waitFor('from water', `$('#craftPlanBody .plan-row').textContent.includes('Fluid Solidifier')
      && $$('#craftPlanBody .plan-node')[1].textContent.includes('Water')
      && $('#craftPlanSummary').textContent.includes('Everything is in stock')`);
    await click(`$('#craftPlanHide')`);
    await waitFor('water hidden, it is all in stock', `$$('#craftPlanBody .plan-node').length === 1`);
    await click(`$('#craftPlanHide')`);
    await click(`$('#craftPlan [data-view="list"]')`);
    await click(`byText('#craftRequestModal button', 'Cancel')`);
    await waitFor('closed, plan cleared', `!visible($('#craftRequestModal')) && $('#craftPlan').hidden`);
  });

  step('finding what is missing: hide all available, and jumping from the chips', async () => {
    // Neutronium Dust is short in two places: under the dust, and three
    // levels down under the rod, past the levels the tree first sends.
    const NEUTRONIUM_DUST = { kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 2129, name: 'Neutronium Dust', size: 1 };
    const IRON_ROD = { kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 23032, name: 'Iron Rod', size: 1 };
    const IRON_PLATE = { kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 17032, name: 'Iron Plate', size: 1 };
    const { data: pscan } = await game(base, '/api/network/patterns/start', {});
    await game(base, '/api/network/patterns/batch', { scan_token: pscan.scan_token, patterns: [
      pattern('Assembler', 0, [IRON_INGOT], [IRON_DUST, IRON_ROD]),
      pattern('Macerator', 1, [IRON_DUST], [NEUTRONIUM_DUST]),
      pattern('Lathe', 2, [IRON_ROD], [IRON_PLATE]),
      pattern('Bender', 3, [IRON_PLATE], [NEUTRONIUM_DUST]),
    ] });
    await game(base, '/api/network/patterns/finish', { scan_token: pscan.scan_token, chunks_sent: 1, total_errors: 0 });

    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('plan', `visible($('#craftPlan')) && $('#craftPlanSummary .plan-chip')?.textContent.includes('Neutronium Dust')`);
    await click(`$('#craftPlan [data-view="tree"]')`);
    // Shut, the plate's step says something is short under it.
    await waitFor('short below', `$('#craftPlanBody li[data-pos="1.0"]')?.textContent.includes('1 short below')`);

    // Only the way to what's missing, opened all the way down.
    await click(`$('#craftPlanHide')`);
    await waitFor('missing paths only', `$$('#craftPlanBody .plan-node').length === 6
      && $$('#craftPlanBody li[data-pos="0.0"], #craftPlanBody li[data-pos="1.0.0"]').length === 2
      && !$('#craftPlanBody [data-action="plan-toggle"][aria-expanded="false"]')`);
    await click(`$('#craftPlanHide')`);

    // From the list view, a chip goes to the tree and the first place.
    await click(`$('#craftPlan [data-view="list"]')`);
    await click(`$('#craftPlanSummary .plan-chip')`);
    await waitFor('first place', `$('#craftPlanBody .plan-tree')
      && $('#craftPlanBody li[data-pos="0.0"] > .plan-row').classList.contains('plan-jump')
      && $('#craftPlanSummary .plan-jump-count').textContent === '1/2'`);
    await click(`$('#craftPlanSummary .plan-chip')`);
    await waitFor('second place, opened down to it', `$('#craftPlanBody li[data-pos="1.0.0"] > .plan-row')?.classList.contains('plan-jump')
      && $('#craftPlanSummary .plan-jump-count').textContent === '2/2'`);
    await click(`$('#craftPlanSummary .plan-chip')`);
    await waitFor('round again', `$('#craftPlanSummary .plan-jump-count').textContent === '1/2'`);

    await click(`$('#craftPlan [data-view="list"]')`);
    await click(`byText('#craftRequestModal button', 'Cancel')`);
    await waitFor('dialog closed', `!visible($('#craftRequestModal'))`);
    await scanPatterns(base);
  });

  step('essentia: its own URL, a stand-in icon, and stock in the plan', async () => {
    // Thaumium from an Iron Ingot and 64 Ordo, of which there are 500.
    const ordo = { kind: 'essentia', internal: 'ordo', name: 'Ordo' };
    const thaumium = { kind: 'item', mod: 'Thaumcraft', internal: 'ItemResource', damage: 2, name: 'Thaumium Ingot' };
    const { data: scan } = await game(base, '/api/network/scan/start', {});
    await game(base, '/api/network/scan/batch', { scan_token: scan.scan_token, items: [
      ...SEEDED_ITEMS.map(it => ({ ...it })), { ...ordo, size: 500 }, { ...thaumium, size: 0, isCraftable: true }] });
    await game(base, '/api/network/scan/finish', { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0 });
    const { data: pscan } = await game(base, '/api/network/patterns/start', {});
    await game(base, '/api/network/patterns/batch', { scan_token: pscan.scan_token, patterns: [...SEEDED_PATTERNS,
      pattern('Infusion Altar', 4, [{ ...thaumium, size: 1 }], [{ ...IRON_INGOT }, { ...ordo, size: 64 }])] });
    await game(base, '/api/network/patterns/finish', { scan_token: pscan.scan_token, chunks_sent: 1, total_errors: 0 });

    await cdp.send('Page.navigate', { url: base + '/network/item/@ordo' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('essentia history', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Ordo'
      && $('#itemHistoryCurrent').textContent === 'Currently stored: 500'`);
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('closed', `!visible($('#itemHistoryModal'))`);
    await evaluate(`typeInto('#networkSearch', 'ordo')`);
    await waitFor('badge', `$$('#networkList .network-cell').length === 1
      && $('#networkList .network-cell .essentia-badge')?.textContent === 'O'`);

    await evaluate(`openItem('Thaumium Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('plan for 1', `visible($('#craftPlan')) && $('#craftPlanSummary').textContent.includes('Everything is in stock')
      && $$('#craftPlanBody .plan-cell .essentia-badge').length === 1`);
    await evaluate(`typeInto('#craftRequestAmount', '10')`);
    await waitFor('140 Ordo short', `$('#craftPlanSummary .plan-chip')?.textContent.includes('Ordo')
      && $('#craftPlanSummary .plan-chip b').textContent === '140'`);
    await click(`byText('#craftRequestModal button', 'Cancel')`);
    await waitFor('dialog closed', `!visible($('#craftRequestModal'))`);
    await scanNetwork(base);
    await scanPatterns(base);
    await cdp.send('Page.navigate', { url: base + '/network' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('back to 3', `$$('#networkList .network-cell').length === 3`);
  });

  step('item history closes via its button and via the backdrop', async () => {
    await evaluate(`openItem('Iron Ingot'), true`);
    await waitFor('history open', `visible($('#itemHistoryModal'))`);
    await click(`$$('#itemHistoryModal button').find(b => b.textContent.trim() === '×' || b.textContent.trim() === 'Close')`);
    await waitFor('closed by button', `!visible($('#itemHistoryModal')) && location.pathname === '/network'`);
    await evaluate(`openItem('Iron Ingot'), true`);
    await waitFor('history open again', `visible($('#itemHistoryModal'))`);
    await click(`$('#itemHistoryModal')`);
    await waitFor('closed by backdrop', `!visible($('#itemHistoryModal')) && location.pathname === '/network'`);
  });

  step('keyboard: Escape closes dialogs and menus, Enter submits a craft', async () => {
    await evaluate(`openItem('Iron Ingot'), true`);
    await waitFor('history open', `visible($('#itemHistoryModal'))`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('history closed', `!visible($('#itemHistoryModal'))`);
    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('craft dialog', `visible($('#craftRequestModal'))`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('craft dialog closed', `!visible($('#craftRequestModal'))`);
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('menu closed', `!visible($('#settingsMenu'))`);
    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('craft dialog', `visible($('#craftRequestModal'))`);
    await evaluate(`typeInto('#craftRequestAmount', '2k')`);
    await evaluate(`pressKey('Enter'), true`);
    await waitFor('submitted by Enter', `!visible($('#craftRequestModal'))`);
    await evaluate(`typeInto('#networkSearch', ''), true`);
  });

  step('failed craft requests can be dismissed', async () => {
    await waitFor('two requests pending', `$$('#craftRequestsSection .craft-request-card').length === 2`);
    const { data } = await api(base, 'GET', '/api/craft/requests/pending', undefined, { 'X-API-Key': API_KEY });
    for (const r of data.requests) {
      await api(base, 'POST', `/api/craft/requests/${r.id}/result`, { status: 'failed', reason: 'e2e says no' }, { 'X-API-Key': API_KEY });
    }
    await waitFor('failures shown', `$$('#craftRequestsSection .craft-request-card.failed').length === 2`);
    await waitFor('2k was parsed', `$('#craftRequestsSection').textContent.includes('×2000')`);
    await click(`$('#craftRequestsSection .craft-request-dismiss')`);
    await waitFor('one dismissed', `$$('#craftRequestsSection .craft-request-card').length === 1`);
    await click(`$('#craftRequestsSection .craft-request-dismiss')`);
    await waitFor('both dismissed', `!$('#craftRequestsSection .craft-request-card')`);
  });

  step('stock rules: set from the item popup, a scan requests the refill, remove', async () => {
    await evaluate(`openItem('Iron Ingot'), true`);
    await waitFor('panel shown', `visible($('#stockAlertRow')) && visible($('#stockTargetRow'))`);
    await evaluate(`typeInto('#stockAlertBelow', '2k')`);
    await click(`byText('#stockAlertRow button', 'Save')`);
    await waitFor('alert saved', `visible($('#stockAlertDelete')) && $('#stockAlertBelow').value === '2k'`);
    await evaluate(`typeInto('#stockKeep', '1.5k'), typeInto('#stockRefill', '3k')`);
    await click(`byText('#stockTargetRow button', 'Save')`);
    await waitFor('target saved', `visible($('#stockTargetDelete')) && $('#stockTargetNote').textContent.includes('No auto-craft yet')`);
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('overview lists it as low', `visible($('#stockRules'))
      && $('#stockRulesSummary').textContent === 'Stock rules (1) · 1 low'`);
    await waitFor('cell marked', `$('#networkList .network-cell.stock-low .network-cell-stock-badge')`);

    // Two idle CPUs, one kept free: the next scan (Iron Ingot still at
    // 1234) asks for the refill up to 3k.
    await postCrafts(base, false);
    await scanNetwork(base);
    const { data: pending } = await api(base, 'GET', '/api/craft/requests/pending', undefined, { 'X-API-Key': API_KEY });
    if (pending.requests.length !== 1 || pending.requests[0].amount !== 1766 || pending.requests[0].source !== 'auto') {
      throw new Error('expected one auto request for 1766, got ' + JSON.stringify(pending.requests));
    }
    await api(base, 'POST', `/api/craft/requests/${pending.requests[0].id}/result`,
      { status: 'failed', reason: 'e2e: missing resources' }, { 'X-API-Key': API_KEY });
    // Not one of the user's own request cards; shown on the rule instead.
    await click(`$('#tabBtnCrafts')`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('failure on the rule', `$('#stockRulesList .stock-chip.failed')?.title.includes('e2e: missing resources')`);
    await waitFor('no request card', `!$('#craftRequestsSection .craft-request-card')`);

    await click(`$('#stockRulesList .stock-row')`);
    await waitFor('popup from the list', `visible($('#itemHistoryModal')) && $('#stockTargetNote').textContent.includes('failed: e2e: missing resources')`);
    await click(`$('#stockTargetDelete')`);
    await waitFor('target removed', `!visible($('#stockTargetDelete'))`);
    await click(`$('#stockAlertDelete')`);
    await waitFor('alert removed', `!visible($('#stockAlertDelete'))`);
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('overview hidden', `!visible($('#stockRules'))`);
    await evaluate(`typeInto('#networkSearch', ''), true`);  // openItem() searched for it
  });

  step('admin: create user, history, new token, revoke, delete', async () => {
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#adminBtn')`);
    await waitFor('admin dialog', `visible($('#adminUsersModal')) && $('#adminUsersList').textContent.includes('Administrator')`);
    await evaluate(`typeInto('#adminUserName', 'E2E Tester')`);
    await click(`$('#adminCreateUserBtn')`);
    const row = `$$('#adminUsersList .admin-user-row').find(r => r.querySelector('.admin-user-name')?.textContent === 'E2E Tester')`;
    await waitFor('user created', row);
    await waitFor('token shown', `visible($('#adminTokenResult')) && $('#adminTokenValue').value.startsWith('gcm_')`);
    const firstToken = await evaluate(`$('#adminTokenValue').value`);
    // Clipboard access may be refused headless; either outcome proves the click ran.
    await click(`byText('#adminTokenResult button', 'Copy token')`);
    await waitFor('copied or selected', `$('#toastContainer').textContent.includes('Access token copied.') || document.activeElement === $('#adminTokenValue')`);
    await click(`byText('button', 'History', ${row})`);
    await waitFor('history dialog', `visible($('#userHistoryModal')) && $('#userHistoryTitle').textContent === 'E2E Tester history'`);
    await click(`$('#userHistoryModal .history-close-btn')`);
    await waitFor('closed by button', `!visible($('#userHistoryModal'))`);
    await click(`byText('button', 'History', ${row})`);
    await waitFor('history dialog again', `visible($('#userHistoryModal'))`);
    await click(`$('#userHistoryModal')`);
    await waitFor('closed by backdrop', `!visible($('#userHistoryModal'))`);
    await click(`byText('button', 'New token', ${row})`);
    await waitFor('new token', `$('#adminTokenValue').value !== ${JSON.stringify(firstToken)}`);
    await waitFor('one token row', `${row}.querySelectorAll('.admin-revoke-btn').length === 2`);
    await click(`${row}.querySelector('.admin-token-row .admin-revoke-btn')`);
    await waitFor('token revoked', `${row}?.textContent.includes('No active tokens')`);
    await click(`byText('button', 'Delete', ${row})`);
    await waitFor('user deleted', `!(${row})`);
    await click(`$('#adminUsersModal .history-close-btn')`);
    await waitFor('closed by button', `!visible($('#adminUsersModal'))`);
    await click(`$('#settingsBtn')`);
    await click(`$('#adminBtn')`);
    await waitFor('admin dialog again', `visible($('#adminUsersModal'))`);
    await click(`$('#adminUsersModal')`);
    await waitFor('closed by backdrop', `!visible($('#adminUsersModal'))`);
  });

  step('admin: switch game data version and textures; the new icons show', async () => {
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#gamedataBtn')`);
    await waitFor('game data dialog', `visible($('#gamedataModal')) && $('#gamedataCurrent').textContent.includes('no game data')`);
    await waitFor('version listed', `$$('#gamedataVersion option').some((o) => o.value === '${E2E_DATA_VERSION}')`);
    await evaluate(`$('#gamedataVersion').value = '${E2E_DATA_VERSION}', true`);
    await click(`$('#gamedataInstallBtn')`);
    await waitFor('installed', `$('#gamedataCurrent').textContent === 'In use: GTNH ${E2E_DATA_VERSION}, Default textures'`);
    await waitFor('listed as in use', `$('#gamedataVersion').selectedOptions[0].textContent.includes('(in use)')`);
    await click(`$('#gamedataModal')`);
    await waitFor('closed by backdrop', `!visible($('#gamedataModal'))`);
    await click(`$('#settingsBtn')`);
    await click(`$('#gamedataBtn')`);
    await waitFor('open again', `visible($('#gamedataModal'))`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed by Escape', `!visible($('#gamedataModal'))`);
    // Switching tabs re-fetches the item list; its icons now carry the
    // new version and load from the downloaded bundle.
    await click(`$('#tabBtnCrafts')`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('icon from the bundle', `$$('#networkList img.network-cell-icon').some((i) => i.src.includes('${E2E_DATA_VERSION}') && i.complete && i.naturalWidth === 16)`);
    // A halo icon is scaled up past its cell (.icon-bleed).
    await waitFor('halo icon enlarged', `$$('#networkList img.network-cell-icon.icon-bleed').some((i) => i.src.includes('bleed12') && getComputedStyle(i).transform !== 'none')`);
    // Neutronium Ingot (5 of them) resolves to an image the zip lacks: the
    // failed <img> removes itself rather than showing a broken glyph.
    await waitFor('neutronium icon resolved', `fetch('/api/network').then((r) => r.json()).then((d) => JSON.stringify(d).includes('gt.metaitem.01~11028.png'))`);
    await waitFor('broken icon removed', `$$('#networkList .network-cell').some((c) => c.querySelector('.network-cell-qty').textContent === '5' && !c.querySelector('img'))`);

    // Same version, Faithful textures: only their zip is fetched, and the
    // dialog credits the pack.
    await click(`$('#settingsBtn')`);
    await click(`$('#gamedataBtn')`);
    await waitFor('textures listed', `$$('#gamedataTextures option').some((o) => o.value === 'faithful32')`);
    await evaluate(`$('#gamedataTextures').value = 'faithful32', $('#gamedataTextures').dispatchEvent(new Event('change')), true`);
    await waitFor('credit in dialog', `visible($('#gamedataCredit')) && $('#gamedataCredit a').href.includes('Ethryan/GTNH-Faithful-Textures')`);
    await click(`$('#gamedataInstallBtn')`);
    await waitFor('faithful installed', `$('#gamedataCurrent').textContent === 'In use: GTNH ${E2E_DATA_VERSION}, Faithful 32x textures'`);
    await evaluate(`pressKey('Escape'), true`);
    await click(`$('#tabBtnCrafts')`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('faithful icon', `$$('#networkList img.network-cell-icon').some((i) => i.src.includes('~faithful32') && i.complete && i.naturalWidth === 32)`);
  });

  step('ore-dictionary substitutes: a Substitute-ticked pattern takes Industrial Diamonds as diamonds', async () => {
    // The bundle just installed says Diamond and Industrial Diamond are both
    // gemDiamond. Block of Diamond from 9 diamonds, Substitute ticked;
    // 4 diamonds and 20 industrial ones in stock, and an implosion
    // compressor making more industrial ones, marked "can be substituted".
    const diamond = { kind: 'item', mod: 'minecraft', internal: 'diamond', damage: 0, name: 'Diamond' };
    const industrial = { kind: 'item', mod: 'IC2', internal: 'itemPartIndustrialDiamond', damage: 0, name: 'Industrial Diamond' };
    const block = { kind: 'item', mod: 'minecraft', internal: 'diamond_block', damage: 0, name: 'Block of Diamond' };
    const dust = { kind: 'item', mod: 'gregtech', internal: 'gt.metaitem.01', damage: 2500, name: 'Diamond Dust' };
    const { data: scan } = await game(base, '/api/network/scan/start', {});
    await game(base, '/api/network/scan/batch', { scan_token: scan.scan_token, items: [
      ...SEEDED_ITEMS.map(it => ({ ...it })), { ...diamond, size: 4 }, { ...industrial, size: 20 },
      { ...dust, size: 40 }, { ...block, size: 0, isCraftable: true }] });
    await game(base, '/api/network/scan/finish', { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0 });
    const { data: pscan } = await game(base, '/api/network/patterns/start', {});
    await game(base, '/api/network/patterns/batch', { scan_token: pscan.scan_token, patterns: [
      { ...pattern('Molecular Assembler', 0, [{ ...block, size: 1 }], [{ ...diamond, size: 9 }]),
        crafting: true, tag: flagsTag({ substitute: 1 }) },
      { ...pattern('Implosion Compressor', 1, [{ ...industrial, size: 3 }], [{ ...dust, size: 4 }]),
        tag: flagsTag({ beSubstitute: 1 }) },
    ] });
    await game(base, '/api/network/patterns/finish', { scan_token: pscan.scan_token, chunks_sent: 1, total_errors: 0 });

    await cdp.send('Page.navigate', { url: base + '/network' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('scanned', `$$('#networkList .network-cell').length === 7`);
    await evaluate(`openItem('Block of Diamond'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('plan', `visible($('#craftPlan')) && $('#craftPlanSummary').textContent.includes('Everything is in stock')`);
    await click(`$('#craftPlan [data-view="tree"]')`);
    const rows = `$$('#craftPlanBody li.plan-node').map(li => li.querySelector(':scope > .plan-row').textContent.replace(/\\s+/g, ' '))`;
    // 2 blocks: 18 diamonds, 4 of them diamonds, 14 industrial ones.
    await evaluate(`typeInto('#craftRequestAmount', '2')`);
    await waitFor('substitutes from stock', `${rows}.some(r => r.includes('need 18 · 4 in stock · 14 as substitutes')
      && r.includes('instead: 14 Industrial Diamond from stock'))`);
    // 3 blocks: 27 - 24 in stock, 3 more industrial ones from the compressor.
    await evaluate(`typeInto('#craftRequestAmount', '3')`);
    await waitFor('substitute made', `${rows}.some(r => r.includes('Industrial Diamond via Implosion Compressor') && r.includes('makes 3'))
      && $('#craftPlanSummary').textContent.includes('Everything is in stock')`);
    await click(`$('#craftPlan [data-view="list"]')`);
    await click(`byText('#craftRequestModal button', 'Cancel')`);
    await waitFor('dialog closed', `!visible($('#craftRequestModal'))`);
    await scanNetwork(base);
    await scanPatterns(base);
    await cdp.send('Page.navigate', { url: base + '/network' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('back to 3', `$$('#networkList .network-cell').length === 3`);
  });

  step('sign out', async () => {
    await click(`$('#settingsBtn')`);
    await click(`byText('#settingsMenu button', 'Sign out')`);
    await waitFor('signed out', `visible($('#authBtn')) && !visible($('#settingsWrap'))`);
    // Pinning and cancelling both need a user, so the cards drop them.
    await click(`$('#tabBtnCrafts')`);
    await waitFor('no pin or cancel buttons', `$$('#root .card').length > 0 && !$('#root .pin-btn') && !$('#root .cancel-btn')`);
    await click(`$('#tabBtnNetwork')`);
  });

  step('back/forward between tabs', async () => {
    await click(`$('#tabBtnCrafts')`);
    await waitFor('crafts', `visible($('#craftsTab')) && location.pathname === '/crafts'`);
    await evaluate(`history.back(), true`);
    await waitFor('back to network', `visible($('#networkTab'))`);
  });

  step('a deep link to an item opens its history on load', async () => {
    await cdp.send('Page.navigate', { url: base + '/network/item/minecraft:iron_ingot:0' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('network tab', `visible($('#networkTab'))`);
    await waitFor('history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'`);
    // Still signed out: a craftable item offers neither a pin nor a request.
    await waitFor('no pin or request', `!visible($('#itemHistoryPinBtn')) && !visible($('#itemHistoryCraftBtn'))`);

    // NBT variants of one item id each get their own URL.
    const { data: scan } = await game(base, '/api/network/scan/start', {});
    const seed = { mod: 'cropsnh', internal: 'genericSeed', damage: 0, hasTag: true };
    await game(base, '/api/network/scan/batch', {
      scan_token: scan.scan_token,
      items: [
        { ...seed, name: 'Sugar Beet Seeds', size: 27 },
        { ...seed, name: 'Wheat Seeds', size: 3100 },
      ],
    });
    await game(base, '/api/network/scan/finish', { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0 });
    const variant = 'L' + createHash('sha256').update('Wheat Seeds').digest('hex').slice(0, 11);
    await cdp.send('Page.navigate', { url: base + '/network/item/cropsnh:genericSeed:0~' + variant });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('variant history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Wheat Seeds' && $('#itemHistoryCurrent').textContent.includes('3,100')`);

    await cdp.send('Page.navigate', { url: base + '/power' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('power tab', `visible($('#powerTab')) && $('#tabBtnPower').classList.contains('active')`);
  });

  let failed = 0;
  // Errors are checked from where the previous step's check left off, so
  // anything logged while the page first loads counts against step one.
  let checked = 0;
  for (const { name, fn } of steps) {
    try {
      await fn();
      if (errors.length > checked) throw new Error('page errors:\n  ' + errors.slice(checked).join('\n  '));
      checked = errors.length;
      console.log(`✔ ${name}`);
    } catch (e) {
      failed++;
      console.log(`✖ ${name}\n  ${e.message.split('\n').join('\n  ')}`);
      break;  // later steps build on earlier ones
    }
  }
  console.log(failed ? `\nFAILED` : `\n${steps.length} steps passed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
