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

// Resolves to { base, setupCode }: the code from the setup link a server
// with no users prints at startup.
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
      // "Serving on" follows the setup link on the same stream, so once
      // it has been read, so has the link.
      if ((await fetch(base + '/')).ok && log.includes('Serving on')) {
        return { base, setupCode: (/\/#setup=(\S+)/.exec(log) || [])[1] };
      }
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
// Ingot's is in the lookup but not the zips, and it has a description
// and the rest of item_info.json),
// made with Python so its zips and checksums are exactly what the
// server expects. A second release, E2E_NEXT_VERSION, is the same bundle
// with other recipes (next/): the seeded Macerator pattern's recipe takes
// two Neutronium Ingots there, for the pattern check. Its item list has
// the GregTech items the seeded patterns use too, which the first one's
// lacks, so admins are told the game data looks older than the game.
const E2E_DATA_VERSION = '2.9.0-e2e';
const E2E_NEXT_VERSION = '2.9.1-e2e';
const MAKE_BUNDLE = `
import gzip, hashlib, json, os, struct, sys, zipfile, zlib
out = sys.argv[1]
def recipes(path, ingots):
    data = {"format": 1, "items": ["gregtech:gt.metaitem.01:11028", "gregtech:gt.metaitem.01:2032",
                                   "gregtech:gt.metaitem.01:2129", "minecraft:iron_ingot:0"],
            "item_names": ["Neutronium Ingot", "Iron Dust", "Neutronium Dust", "Iron Ingot"],
            "fluids": ["water"], "fluid_names": ["Water"],
            "maps": {"gt.recipe.macerator": {"machines": [{"name": "Macerator", "key": None}],
                                             "recipes": [{"i": [[0, ingots]], "o": [[1, 1]], "e": 30, "d": 100}]},
                     "gt.recipe.compressor": {"machines": [{"name": "Compressor", "key": None}],
                                              "recipes": [{"i": [[2, 1]], "o": [[0, 1]], "e": 30, "d": 100}]}}}
    with gzip.open(path, "wt") as f:
        json.dump(data, f)
recipes(out + "/recipes.json.gz", 1)
os.makedirs(out + "/next", exist_ok=True)
recipes(out + "/next/recipes.json.gz", 2)
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
open(out + "/next/item_catalog.txt", "w").write("gregtech:gt.metaitem.01\\nminecraft:iron_ingot\\n")
open(out + "/ore_dict.json", "w").write(json.dumps(
    {"gemDiamond": ["minecraft:diamond:0", "IC2:itemPartIndustrialDiamond:0"],
     "ingotNeutronium": ["gregtech:gt.metaitem.01:11028"]}))
nt = "gregtech:gt.metaitem.01:11028"
open(out + "/item_info.json", "w").write(json.dumps({
    "tooltips": {"by_key": {nt: "\\u00a7eNt\\n\\u00a77Machine Type: Plasma Forge, \\u00a7bDTPF"}},
    "shift_tooltips": {"by_key": {nt: "\\u00a77Structure: \\u00a7e33x24x33"}},
    "rarity": {"by_key": {nt: "d"}},
    "fluids": {"water": {"temperature": 295, "gaseous": False, "formula": "H\\u2082O"}},
    "mods": {"gregtech": "GregTech", "minecraft": "Minecraft"}}))
for bundle in (out, out + "/next"):
    files = {}
    for name in ("images.zip", "images-faithful32.zip", "icons_lookup.json", "item_catalog.txt", "ore_dict.json",
                 "item_info.json", "recipes.json.gz"):
        data = open((bundle if name in ("recipes.json.gz", "item_catalog.txt") else out) + "/" + name, "rb").read()
        files[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    open(bundle + "/data.json", "w").write(
        json.dumps({"format": 1, "files": files, "generated_at": "2026-09-25T00:00:00Z"}))
`;
const BUNDLE_FILES = [
  'data.json', 'images.zip', 'images-faithful32.zip', 'icons_lookup.json', 'item_catalog.txt', 'ore_dict.json',
  'item_info.json', 'recipes.json.gz',
];
// The files E2E_NEXT_VERSION has of its own; the rest are the same.
const NEXT_FILES = ['data.json', 'recipes.json.gz', 'item_catalog.txt'];

// What the server posted to its webhook (WEBHOOK_URLS points at the
// fake GitHub server's /webhook).
const webhookPosts = [];

async function startFakeGitHub() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gcm-e2e-gamedata-'));
  tempDirs.push(dir);
  const made = spawnSync(process.env.PYTHON || 'python3', ['-c', MAKE_BUNDLE, dir], { encoding: 'utf8' });
  if (made.status !== 0) throw new Error('making the game data bundle failed:\n' + made.stderr);
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const server = http.createServer((req, res) => {
    if (req.url === '/webhook' && req.method === 'POST') {
      let body = '';
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => {
        webhookPosts.push(JSON.parse(body));
        res.statusCode = 204;
        res.end();
      });
      return;
    }
    if (req.url.startsWith('/repos/e2e/scripts/tags')) {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify([{ name: 'v1.1.0' }, { name: 'v1.2.0' }]));
      return;
    }
    if (req.url.startsWith('/repos/')) {
      res.setHeader('Content-Type', 'application/json');
      const release = (version, publishedAt, assets, ids) => ({
        tag_name: `gtnh-data-${version}`,
        published_at: publishedAt,
        assets: BUNDLE_FILES.map((name, index) => {
          const own = assets === 'assets-next' && NEXT_FILES.includes(name);
          return {
            name,
            id: ids + index,
            size: fs.statSync(path.join(own ? path.join(dir, 'next') : dir, name)).size,
            browser_download_url: `${base}/${assets}/${name}`,
          };
        }),
      });
      // Published before E2E_DATA_VERSION, so that one stays first.
      res.end(JSON.stringify([
        release(E2E_DATA_VERSION, '2026-09-25T00:00:00Z', 'assets', 1000),
        release(E2E_NEXT_VERSION, '2026-09-20T00:00:00Z', 'assets-next', 2000),
      ]));
      return;
    }
    const [, assets, name] = req.url.split('/');
    if (!BUNDLE_FILES.includes(name) || !['assets', 'assets-next'].includes(assets)) {
      res.statusCode = 404;
      res.end();
      return;
    }
    const own = assets === 'assets-next' && NEXT_FILES.includes(name);
    res.end(fs.readFileSync(path.join(own ? path.join(dir, 'next') : dir, name)));
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
  const { base, setupCode } = await startServer({
    GAMEDATA_API_URL: github, GAMEDATA_REPO: 'e2e/repo', SCRIPTS_REPO: 'e2e/scripts',
    // As publish.yml would set it: computers older than this get a notice.
    GCM_SCRIPTS_VERSION: 'v1.1.0',
  });
  // The first admin, made the way the setup dialog makes it; the dialog
  // itself is tried on a server of its own, last.
  const setup = await fetch(base + '/api/auth/setup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: setupCode, display_name: 'Administrator' }),
  });
  if (!setup.ok) throw new Error(`setup: ${setup.status} ${await setup.text()}`);
  const adminToken = (await setup.json()).token;
  await seed(base);
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
  // Real mouse input, for what el.click() skips: the press and release
  // go through the browser's own hit testing and click targeting.
  const mouse = (type, [x, y]) => cdp.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
  const centerOf = (expr) => evaluate(`(() => { const r = (${expr}).getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()`);
  // A click on a modal's dimmed backdrop, in its top-left corner, clear
  // of the dialog box.
  const clickBackdrop = async () => {
    await mouse('mousePressed', [5, 5]);
    await mouse('mouseReleased', [5, 5]);
  };

  step('crafts tab renders the busy CPU', async () => {
    await waitFor('W01 card', `card('W01')?.querySelector('.craft-title').textContent.includes('Iron Ingot')`);
    await waitFor('W02 in idle group', `$('details.idle-group') && card('W02')`);
    await waitFor('tab badge counts the busy one', `$('#tabBtnCrafts .tab-badge')?.textContent === '1'`);
  });

  step('a CPU without a Crafting Monitor: "Starting job…" at first, then a hint', async () => {
    const crafts = (jobs) => game(base, '/api/crafts', { source: 'me_controller', jobs: [
      { name: 'W01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
        final_output_internal: 'iron_ingot', final_output_damage: 0,
        active: [{ name: 'Iron Ingot', size: 3 }], pending: [{ name: 'Iron Ore', size: 6 }], stored: [] },
      { name: 'W02', busy: false },
      ...jobs,
    ] });
    // M01 just started; M02 was already running when the server first saw
    // it, so it's past the wait for an output.
    await crafts([{ name: 'M01', busy: false }]);
    await crafts([{ name: 'M01', busy: true, pending: [{ name: 'Iron Ore', size: 6 }] },
                  { name: 'M02', busy: true, pending: [{ name: 'Iron Ore', size: 6 }] }]);
    await waitFor('M01 starting', `card('M01')?.querySelector('.craft-title').textContent === 'Starting job…'
      && !card('M01').querySelector('.monitor-hint')`);
    await waitFor('M02 hint', `card('M02')?.querySelector('.craft-title').textContent === 'Unknown output'
      && card('M02').querySelector('.monitor-hint a').href.endsWith('in-game.md#crafting-monitor')`);
    await crafts([]);
    await waitFor('gone', `!card('M01') && !card('M02')`);
  });

  step('a CPU nobody named is named after its size, with a (?) saying so', async () => {
    const crafts = (jobs) => game(base, '/api/crafts', { source: 'me_controller', jobs: [
      { name: 'W01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
        final_output_internal: 'iron_ingot', final_output_damage: 0,
        active: [{ name: 'Iron Ingot', size: 3 }], pending: [{ name: 'Iron Ore', size: 6 }], stored: [] },
      { name: 'W02', busy: false },
      ...jobs,
    ] });
    // AE2 reports a CPU nobody named with an empty name.
    await crafts([{ name: '', busy: false, storage: 1024 }, { name: '', busy: false, storage: 65536 }]);
    await waitFor('each named after its size, with the mark', `card('64k #1')?.querySelector('.cpu-auto')?.title.includes('its size was added')
      && card('1k #1')?.querySelector('.cpu-auto')`);
    // A link to the docs, as a phone has no hover for the title.
    await waitFor('the mark links to the docs', `card('64k #1').querySelector('.cpu-auto').href.endsWith('in-game.md#cpu-names')
      && card('64k #1').querySelector('.cpu-auto').getAttribute('aria-label').includes('Wire Cutters')`);
    // Beside the name, not in it, so a long name's ellipsis can't hide it.
    await waitFor('the mark is shown in full', `(() => {
      const mark = card('64k #1').querySelector('.card-top > .cpu-auto');
      const r = mark?.getBoundingClientRect();
      return r && r.width >= 13 && r.height >= 13;
    })()`);
    await waitFor('named CPUs have no mark', `card('W02') && !card('W02').querySelector('.cpu-auto')`);
    await crafts([]);
    await waitFor('gone', `!card('64k #1') && !card('1k #1')`);
  });

  step('crafts poll each second, and an unchanged report answers 304', async () => {
    const polls = `performance.getEntriesByType('resource').filter((e) => new URL(e.name).pathname === '/api/crafts').length`;
    const before = await evaluate(polls);
    await sleep(2500);
    const after = await evaluate(polls);
    if (after - before < 2) throw new Error(`only ${after - before} crafts polls in 2.5s`);
    const status = await evaluate(`(async () => {
      const first = await fetch('/api/crafts', { cache: 'no-store' });
      const again = await fetch('/api/crafts', { cache: 'no-store', headers: { 'If-None-Match': first.headers.get('ETag') } });
      return again.status;
    })()`);
    if (status !== 304) throw new Error(`conditional crafts fetch: ${status}`);
  });

  step('allowed inline styles apply; the progress bar is sized', async () => {
    // Every dialog starts hidden via style="display:none;" - which only
    // works if the CSP's style hashes match index.html.
    await waitFor('all dialogs hidden', `$$('.modal-overlay').length >= 5 && $$('.modal-overlay').every((m) => !visible(m))`);
    await waitFor('progress 40%', `card('W01').querySelector('.progress-fill').style.width === '40%'`);
    // Busy in the server's first report, so only a lower bound on its run time.
    await waitFor('run time', `/running ≥\\d/.test(card('W01').querySelector('.progress-label').textContent)`);
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

  step('sign in with the admin token', async () => {
    await evaluate(`typeInto('#accessTokenInput', ${JSON.stringify(adminToken)})`);
    await click(`$('#authBtn')`);
    await waitFor('admin menu available', `$('#settingsUser').textContent !== '' && !visible($('#authBtn'))`);
    // Run from source: no version, and nothing to update to.
    await waitFor('no version, no update dot', `$('#settingsVersion').textContent === '' && !visible($('#settingsUpdateDot'))`);
  });

  step('sign out, then sign in again with the Enter key', async () => {
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`byText('#settingsMenu button', 'Sign out')`);
    await waitFor('signed out', `visible($('#authBtn')) && $('#settingsUser').textContent === ''`);
    await evaluate(`typeInto('#accessTokenInput', ${JSON.stringify(adminToken)})`);
    // A synthetic Enter keydown doesn't submit a form; requestSubmit() is
    // what pressing Enter in the field does.
    await evaluate(`$('#signInForm').requestSubmit(), true`);
    await waitFor('signed in', `$('#settingsUser').textContent !== '' && $('#settingsUser').textContent.includes('Administrator')`);
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

  step('theme: light, dark or the system\'s, remembered across a reload', async () => {
    const bg = `getComputedStyle(document.body).backgroundColor`;
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await waitFor('auto by default', `!document.documentElement.dataset.theme
      && $('[data-theme-choice="auto"]').getAttribute('aria-checked') === 'true'`);
    await click(`$('[data-theme-choice="dark"]')`);
    await waitFor('dark', `document.documentElement.dataset.theme === 'dark' && ${bg} === 'rgb(15, 17, 21)'`);
    await waitFor('menu stays open', `visible($('#settingsMenu'))`);
    await click(`$('[data-theme-choice="light"]')`);
    await waitFor('light', `document.documentElement.dataset.theme === 'light' && ${bg} === 'rgb(244, 245, 247)'
      && $('[data-theme-choice="light"]').getAttribute('aria-checked') === 'true'
      && $('[data-theme-choice="dark"]').getAttribute('aria-checked') === 'false'`);
    // Light's own text colours read on a card (4.5:1, WCAG AA), and a
    // card's edge shows against the page - the dark theme's translucent
    // and pale colours didn't, over near-white.
    const contrasts = await evaluate(`(() => {
      const css = getComputedStyle(document.documentElement);
      const hex = (name) => css.getPropertyValue(name).trim();
      const lum = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
        .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
        .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
      const ratio = (a, b) => {
        const [hi, lo] = [lum(hex(a)), lum(hex(b))].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
      };
      const out = {};
      for (const name of ['--text', '--muted', '--accent', '--busy', '--idle', '--stale', '--essentia', '--rise',
        '--search-mod', '--search-quote']) out[name] = ratio(name, '--card');
      out.edge = ratio('--border-faint', '--bg');
      return out;
    })()`);
    for (const [name, ratio] of Object.entries(contrasts)) {
      if (ratio < (name === 'edge' ? 1.3 : 4.5)) throw new Error(`${name} at ${ratio.toFixed(2)}:1 in light`);
    }
    await cdp.send('Page.reload');
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('still light', `document.documentElement.dataset.theme === 'light'
      && $('[data-theme-choice="light"]').getAttribute('aria-checked') === 'true'`);
    await click(`$('#settingsBtn')`);
    await click(`$('[data-theme-choice="auto"]')`);
    await waitFor('back to auto', `!document.documentElement.dataset.theme
      && localStorage.getItem('gtnhCraftMonitor.theme') === null`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('menu closed', `!visible($('#settingsMenu'))`);
  });

  step('admin: set a webhook on the page; one that isn\'t a web address is refused', async () => {
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#webhooksBtn')`);
    await waitFor('webhooks dialog', `visible($('#webhooksModal')) && $('#webhookUrls').value === ''
      && $('#webhookEventCrafts').checked && !$('#webhookEventStock').checked`);
    await evaluate(`typeInto('#webhookUrls', 'file:///etc/passwd'), true`);
    await click(`$('#webhooksSaveBtn')`);
    await waitFor('refused', `visible($('#webhooksError')) && $('#webhooksError').textContent.includes('Not a web address')`);
    await evaluate(`typeInto('#webhookUrls', ${JSON.stringify(`${github}/webhook\n`)}), true`);
    await click(`$('#webhooksSaveBtn')`);
    await waitFor('saved', `$('#toastContainer').textContent.includes('Webhooks saved.') && !visible($('#webhooksError'))
      && $('#webhookUrls').value === ${JSON.stringify(`${github}/webhook`)}`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed', `!visible($('#webhooksModal'))`);
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
    // Nothing back past CRAFT_STALL_SECONDS: stalled, shown as stuck,
    // saying why and where the machine is, with a chip to jump to it.
    await report(4);
    await waitFor('stuck', `$$('#ingredientsTree li.job-node')[1].classList.contains('job-stuck')
      && !$('#ingredientsStuck').hidden && $('#ingredientsStuck').textContent.includes('Iron Dust')
      && $('#ingredientsStuck .plan-chip').title.startsWith('nothing back from its machine for')
      && $$('#ingredientsTree li.job-node')[1].querySelector(':scope > .plan-row .job-step-note').textContent
        .match(/^nothing back from its machine for \\d+s · -?\\d+, -?\\d+, -?\\d+/)`);
    const tint = await evaluate(`(() => {
      const bg = (li) => getComputedStyle(li.querySelector(':scope > .plan-row')).backgroundColor;
      const [root, stuck] = $$('#ingredientsTree li.job-node');
      return { stuck: bg(stuck), other: bg(root) };
    })()`);
    if (tint.stuck === tint.other) throw new Error(`a stuck step's row isn't tinted: ${JSON.stringify(tint)}`);
    await click(`$$('#ingredientsTree [data-action="job-tree-toggle"]')[0]`);
    await waitFor('shut, saying what is under it', `$$('#ingredientsTree li.job-node').length === 1
      && $('#ingredientsTree .job-step-note .job-count-stuck')?.textContent === '1 stuck'`);
    // The filter box works on the tree too: only the way to a match,
    // opened even where it was shut, the match marked.
    await evaluate(`typeInto('#ingredientsFilter', 'neutronium'), true`);
    await waitFor('filtered tree', `(() => {
      const rows = $$('#ingredientsTree li.job-node');
      return rows.length === 3 && rows[2].classList.contains('filter-match')
        && !rows[0].classList.contains('filter-match');
    })()`);
    await evaluate(`typeInto('#ingredientsFilter', 'gold'), true`);
    await waitFor('nothing matches', `$$('#ingredientsTree li.job-node').length === 1
      && $('#ingredientsTree').textContent.includes('Nothing in this job matches')`);
    // A stuck chip clears the filter to show its step.
    await click(`$('#ingredientsStuck .plan-chip')`);
    await waitFor('jumped to it', `$('#ingredientsTree li[data-pos="0"] > .plan-row')?.classList.contains('plan-jump')
      && $('#ingredientsFilter').value === ''`);
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
    await waitFor('the page under it does not scroll', `getComputedStyle(document.documentElement).overflowY === 'hidden'`);
    await waitFor('crafting cell first', `$('#ingredientsGrid .ingredient-cell').classList.contains('crafting')
      && $('#ingredientsGrid .ingredient-cell').textContent.includes('Crafting: 3')`);
    await evaluate(`window.ingBar = $('#ingredientsProgress .progress-fill'), true`);
    await sleep(3500);  // the crafts tab re-renders every 3s
    await waitFor('still open after re-render', `visible($('#ingredientsModal')) && $$('#ingredientsGrid .ingredient-cell').length === 2`);
    await waitFor('the same progress bar', `window.ingBar && $('#ingredientsProgress .progress-fill') === window.ingBar`);
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
    await waitFor('the page scrolls again', `getComputedStyle(document.documentElement).overflowY !== 'hidden'`);
  });

  step('an ingredient opens its item over the dialog; Back closes one at a time', async () => {
    // An ingredient with an item id, which is what makes it a link.
    await game(base, '/api/crafts', { source: 'me_controller', jobs: [
      { name: 'W01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
        final_output_internal: 'iron_ingot', final_output_damage: 0,
        active: [{ name: 'Iron Ingot', size: 3 }],
        pending: [{ name: 'Iron Ore', mod: 'minecraft', internal: 'iron_ore', damage: 0, size: 6 }], stored: [] },
      { name: 'W02', busy: false },
    ] });
    await waitFor('W01 card', `card('W01')?.querySelector('.ingredients-btn')`);
    await click(`card('W01').querySelector('.ingredients-btn')`);
    await waitFor('dialog open', `visible($('#ingredientsModal')) && $$('#ingredientsGrid .ingredient-cell').length === 2
      && $$('#ingredientsGrid .ingredient-cell').find(c => c.dataset.internal === 'iron_ore')`);
    await click(`$$('#ingredientsGrid .ingredient-cell').find(c => c.dataset.name === 'Iron Ore')`);
    await waitFor('item over the dialog, still on crafts', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ore'
      && visible($('#ingredientsModal')) && visible($('#craftsTab')) && $('#tabBtnCrafts').classList.contains('active')
      && location.pathname.startsWith('/network/item/')`);
    await evaluate(`history.back(), true`);
    await waitFor('item closed, dialog still open', `!visible($('#itemHistoryModal')) && visible($('#ingredientsModal'))
      && visible($('#craftsTab')) && !location.pathname.startsWith('/network')`);
    await evaluate(`history.back(), true`);
    await waitFor('dialog closed, still on crafts', `!visible($('#ingredientsModal')) && visible($('#craftsTab'))`);
    await evaluate(`history.forward(), true`);
    await waitFor('forward reopens the dialog', `visible($('#ingredientsModal')) && $$('#ingredientsGrid .ingredient-cell').length === 2`);
    await evaluate(`history.forward(), true`);
    await waitFor('and the item over it', `visible($('#itemHistoryModal')) && visible($('#ingredientsModal')) && visible($('#craftsTab'))`);
    // Closing by hand steps back too, so Back has nothing left to reopen.
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('item closed by its button', `!visible($('#itemHistoryModal')) && visible($('#ingredientsModal'))
      && !location.pathname.startsWith('/network')`);
    await click(`$('#ingredientsModal [data-action="close-ingredients"]')`);
    await waitFor('dialog closed by its button', `!visible($('#ingredientsModal')) && visible($('#craftsTab'))
      && (history.state === null || history.state.view !== 'ingredients')`);
    await postCrafts(base, true);
  });

  step('dialogs by keyboard: focus goes in, Tab stays in, focus comes back; item links open with Enter', async () => {
    // Real key presses, so focus moves the browser's own way.
    const CODES = { Enter: 13, Escape: 27, Tab: 9 };
    const key = async (k, shift) => {
      const ev = { key: k, code: k, windowsVirtualKeyCode: CODES[k], modifiers: shift ? 8 : 0 };
      // Enter's text is what makes a <button> click.
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', ...ev, ...(k === 'Enter' ? { text: '\r' } : {}) });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...ev });
    };
    await game(base, '/api/crafts', { source: 'me_controller', jobs: [
      { name: 'W01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
        final_output_internal: 'iron_ingot', final_output_damage: 0,
        active: [{ name: 'Iron Ingot', size: 3 }],
        pending: [{ name: 'Iron Ore', mod: 'minecraft', internal: 'iron_ore', damage: 0, size: 6 }], stored: [] },
      { name: 'W02', busy: false },
    ] });
    await waitFor('W01 card', `card('W01')?.querySelector('.ingredients-btn')`);
    const box = `$('#ingredientsModal .modal-box')`;
    await evaluate(`card('W01').querySelector('.ingredients-btn').focus(), true`);
    await key('Enter');
    await waitFor('open', `visible($('#ingredientsModal'))`);
    await waitFor('a modal dialog', `${box}.getAttribute('role') === 'dialog' && ${box}.getAttribute('aria-modal') === 'true'`);
    await waitFor('labelled by its title', `document.getElementById(${box}.getAttribute('aria-labelledby'))?.textContent.includes('Iron Ingot')`);
    await waitFor('focus in it', `$('#ingredientsModal').contains(document.activeElement)`);
    // Round the dialog more than once, both ways: never out of it.
    for (const shift of [false, true]) {
      for (let i = 0; i < 8; i++) {
        await key('Tab', shift);
        const inside = await evaluate(`$('#ingredientsModal').contains(document.activeElement) && document.activeElement !== ${box}`);
        if (!inside) throw new Error(`Tab ${shift ? 'backwards ' : ''}left the dialog, at ${i}: ${await evaluate('document.activeElement.outerHTML.slice(0, 120)')}`);
      }
    }
    // An ingredient is a button to the keyboard.
    await evaluate(`$$('#ingredientsGrid .ingredient-cell').find(c => c.dataset.internal === 'iron_ore').focus(), true`);
    await key('Enter');
    await waitFor('item open over it, focus in the item', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ore'
      && $('#itemHistoryModal').contains(document.activeElement)`);
    await key('Escape');
    await waitFor('item closed, focus back in the ingredients', `!visible($('#itemHistoryModal'))
      && $('#ingredientsModal').contains(document.activeElement)`);
    await key('Escape');
    await waitFor('closed, focus back on its button', `!visible($('#ingredientsModal'))
      && document.activeElement.matches('[data-action="open-ingredients"][data-cpu="W01"]')`);
    // So is a card's item name.
    await evaluate(`card('W01').querySelector('.item-history-link').focus(), true`);
    await key('Enter');
    await waitFor('its item open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'`);
    await key('Escape');
    await waitFor('closed, focus back on the name', `!visible($('#itemHistoryModal'))
      && document.activeElement.matches('.item-history-link[data-internal="iron_ingot"]')`);
    await postCrafts(base, true);
  });

  step('a redraw keeps icon <img>s, so animated ones carry on', async () => {
    // Kept isn't enough: an <img> moved through another document (a
    // template's content) restarts all the same. adoptedCallback counts
    // those trips.
    const kept = await evaluate(`(async () => {
      const { updateHtml } = await import('/static/js/util.js');
      customElements.define('adopt-count-img', class extends HTMLImageElement {
        adoptedCallback() { window.__adopted++; }
      }, { extends: 'img' });
      // In the page: a custom element is only upgraded once connected.
      const el = document.body.appendChild(document.createElement('div'));
      const img = (cls, src) => '<img is="adopt-count-img" class="' + cls + '" src="/icons?path=' + src + '">';
      const html = (cls) => img(cls, 'a.png') + img('', 'b.png') + img('', 'a.png') + '<span>x</span>';
      updateHtml(el, html('one'));
      const [a1, b, a2] = el.querySelectorAll('img');
      window.__adopted = 0;
      updateHtml(el, '<i>y</i>' + html('two'));
      const now = el.querySelectorAll('img');
      el.remove();
      return now[0] === a1 && now[1] === b && now[2] === a2 && a1.className === 'two'
        && el.firstChild.tagName === 'I' && el.textContent === 'yx' && window.__adopted === 0;
    })()`);
    if (!kept) throw new Error('icon <img>s were not kept across updateHtml');
  });

  step('a redraw updates in place: items, icons, focus, selection and clicks survive it', async () => {
    const result = await evaluate(`(async () => {
      const { updateHtml } = await import('/static/js/util.js');
      const el = document.body.appendChild(document.createElement('div'));
      const img = (src) => '<img is="adopt-count-img" src="/icons?path=' + src + '">';
      const item = (id, src, text) => '<div data-id="' + id + '">' + img(src) + '<span>' + text + '</span><button>b' + id + '</button></div>';
      updateHtml(el, item('a', 'x.png', 'one') + item('b', 'y.png', 'two'));
      const [a, b] = el.children;
      const [ax, by] = el.querySelectorAll('img');
      a.querySelector('span').style.width = '40%';
      // Any src set on an <img> already in the page restarts its animation.
      let srcChanges = 0;
      const watch = new MutationObserver((rs) => { srcChanges += rs.filter(r => r.target.isConnected && r.oldValue !== null).length; });
      watch.observe(el, { subtree: true, attributes: true, attributeFilter: ['src'], attributeOldValue: true });
      window.__adopted = 0;
      const fail = [];
      // Reordered: the same items move, with their icons.
      updateHtml(el, item('b', 'y.png', 'two') + item('a', 'x.png', 'one'));
      if (el.children[0] !== b || el.children[1] !== a) fail.push('reorder replaced the items');
      if (b.querySelector('img') !== by || a.querySelector('img') !== ax) fail.push('reorder replaced the icons');
      // The icons swap items: each moves, none is given the other's src.
      updateHtml(el, item('b', 'x.png', 'two') + item('a', 'y.png', 'one'));
      if (b.querySelector('img') !== ax || a.querySelector('img') !== by) fail.push('swapped icons were not moved');
      // Text changes in place; a style set through the DOM stays.
      const span = a.querySelector('span');
      updateHtml(el, item('b', 'x.png', 'two') + item('a', 'y.png', 'uno'));
      if (a.querySelector('span') !== span || span.textContent !== 'uno') fail.push('text was not updated in place');
      if (span.style.width !== '40%') fail.push('the style set through the DOM was dropped');
      // Focus, and a selection in text that didn't change, carry on.
      a.querySelector('button').focus();
      getSelection().selectAllChildren(b.querySelector('span'));
      updateHtml(el, item('b', 'x.png', 'two') + item('a', 'y.png', 'one'));
      if (document.activeElement !== a.querySelector('button')) fail.push('focus was lost');
      if (getSelection().toString() !== 'two') fail.push('the selection was lost');
      getSelection().removeAllRanges();
      // A new item's new icon is a new <img>; the others are untouched.
      updateHtml(el, item('c', 'z.png', 'three') + item('b', 'x.png', 'two') + item('a', 'y.png', 'one'));
      if (el.children[1] !== b || el.children[2] !== a || el.querySelectorAll('img').length !== 3) fail.push('a new item disturbed the others');
      await new Promise(r => setTimeout(r, 0));
      watch.disconnect();
      if (srcChanges) fail.push(srcChanges + ' src change(s) on kept <img>s');
      if (window.__adopted) fail.push(window.__adopted + ' <img>(s) moved through another document');
      window.redrawTarget = el;
      return fail.join('; ');
    })()`);
    if (result) throw new Error(result);
    // Lists in sections, as the Crafts tab draws them.
    const sections = await evaluate(`(async () => {
      const { updateHtml: update } = await import('/static/js/util.js');
      const el = document.body.appendChild(document.createElement('div'));
      const img = (src) => '<img is="adopt-count-img" src="/icons?path=' + src + '">';
      const card = (id, src) => '<div data-id="' + id + '">' + img(src) + '<button>' + id + '</button></div>';
      const section = (id, cards) => '<section data-id="' + id + '"><div class="grid">' + cards.join('') + '</div></section>';
      const byId = (id) => el.querySelector('[data-id="' + id + '"]');
      const fail = [];
      // Whatever was kept or moved, the result is exactly the new markup.
      const updateHtml = (target, html) => {
        update(target, html);
        const t = document.createElement('template');
        t.innerHTML = html;
        if (target.innerHTML !== t.innerHTML) fail.push('the result differs from the markup: ' + html.slice(0, 80));
      };
      // Kept nodes taken out of the page and put back (a move) lose focus.
      let moved = new Set();
      const watch = new MutationObserver((rs) => { for (const r of rs) for (const n of r.removedNodes) moved.add(n); });
      watch.observe(el, { subtree: true, childList: true });
      const settle = async () => { await new Promise(r => setTimeout(r, 0)); const m = moved; moved = new Set(); return m; };

      // c2 first: the old code gave a new card the first free <img>
      // showing its icon, in page order - c2's.
      updateHtml(el, section('active', [card('c2', 'x.png'), card('c1', 'x.png'), card('c3', 'y.png')]));
      const [c1, c2, c3] = ['c1', 'c2', 'c3'].map(byId);
      const [x2, x1, y3] = el.querySelectorAll('img');
      const active = byId('active');
      await settle();
      window.__adopted = 0;

      // c1 finishes: a Finished section appears above, with a card for the
      // same item. The Active section and the cards in it stay put, c2
      // keeps its own icon, and the finished card takes c1's.
      updateHtml(el, section('finished', [card('done1', 'x.png')]) + section('active', [card('c2', 'x.png'), card('c3', 'y.png')]));
      let m = await settle();
      if (byId('active') !== active || byId('c2') !== c2 || byId('c3') !== c3) fail.push('a section appearing replaced the one after it');
      if (m.has(active) || m.has(c2) || m.has(c3)) fail.push('a section appearing moved the one after it');
      if (c2.querySelector('img') !== x2) fail.push("the finished card took the icon of a card that stayed");
      if (byId('done1').querySelector('img') !== x1) fail.push("the finished card didn't take the finished CPU's icon");

      // The first card of a list goes: none after it moves.
      updateHtml(el, section('finished', [card('done1', 'x.png')]) + section('active', [card('c3', 'y.png')]));
      m = await settle();
      if (byId('c3') !== c3 || m.has(c3)) fail.push('removing the first card moved the next');

      // c3 is pinned with its button focused: it moves to a new Pinned
      // section, the same element, and keeps focus.
      c3.querySelector('button').focus();
      updateHtml(el, section('finished', [card('done1', 'x.png')]) + section('pinned', [card('c3', 'y.png')]));
      if (byId('c3') !== c3 || c3.querySelector('img') !== y3) fail.push('a card moving section was rebuilt');
      if (document.activeElement !== c3.querySelector('button')) fail.push('a card moving section lost focus');

      // A rotated list moves only the one node that changed place.
      const moves = [];
      const proto = Element.prototype;
      const real = { moveBefore: proto.moveBefore, insertBefore: Node.prototype.insertBefore };
      proto.moveBefore = function (node, ref) { if (el.contains(this)) moves.push(node); return real.moveBefore.call(this, node, ref); };
      Node.prototype.insertBefore = function (node, ref) { if (el.contains(this)) moves.push(node); return real.insertBefore.call(this, node, ref); };
      try {
        updateHtml(el, section('active', [card('r1', 'x.png'), card('r2', 'x.png'), card('r3', 'y.png')]));
        const r1 = byId('r1');
        moves.length = 0;
        updateHtml(el, section('active', [card('r2', 'x.png'), card('r3', 'y.png'), card('r1', 'x.png')]));
        if (moves.length !== 1 || moves[0] !== r1) fail.push('rotating a list moved ' + moves.length + ' nodes, not just the one');
      } finally {
        proto.moveBefore = real.moveBefore;
        Node.prototype.insertBefore = real.insertBefore;
      }
      // A card moving into another section as its old one goes: the card
      // is kept, and nothing of the old section stays behind.
      updateHtml(el, section('a', [card('m1', 'x.png'), card('m2', 'y.png')]) + section('b', [card('m3', 'z.png')]));
      const [m1, m3] = [byId('m1'), byId('m3')];
      updateHtml(el, section('b', [card('m1', 'x.png'), card('m3', 'z.png')]));
      if (byId('m1') !== m1 || byId('m3') !== m3 || byId('a') || byId('m2')) fail.push('a move between sections left or lost something');
      // The same key at two depths: each stays at its own.
      updateHtml(el, '<div data-id="k"><div data-id="k">in</div></div>');
      const [k1, k2] = el.querySelectorAll('[data-id="k"]');
      updateHtml(el, '<div data-id="k"><div data-id="k">in</div></div>');
      const [k1b, k2b] = el.querySelectorAll('[data-id="k"]');
      if (k1b !== k1 || k2b !== k2) fail.push('a key repeated at two depths was moved or rebuilt');
      // Repeated keys (CPUs without a name): each keeps its own card.
      updateHtml(el, section('active', [card('cpu:', 'x.png'), card('cpu:', 'y.png')]));
      const [n1, n2] = el.querySelectorAll('[data-id="cpu:"]');
      updateHtml(el, section('active', [card('cpu:', 'x.png'), card('cpu:', 'y.png')]));
      const [n1b, n2b] = el.querySelectorAll('[data-id="cpu:"]');
      if (n1b !== n1 || n2b !== n2) fail.push('cards sharing a key were rebuilt');

      watch.disconnect();
      if (window.__adopted) fail.push(window.__adopted + ' <img>(s) moved through another document');
      el.remove();
      return fail.join('; ');
    })()`);
    if (sections) throw new Error(sections);
    const actions = await evaluate(`(async () => {
      const { updateHtml } = await import('/static/js/util.js');
      const el = document.body.appendChild(document.createElement('div'));
      const fail = [];
      // A button going doesn't turn the next one into its action.
      updateHtml(el, '<button data-action="toggle-pin">Pin</button><button data-action="cancel-craft">Cancel</button>');
      const cancel = el.querySelector('[data-action="cancel-craft"]');
      cancel.focus();
      updateHtml(el, '<button data-action="cancel-craft">Cancel</button>');
      if (el.querySelector('button') !== cancel || document.activeElement !== cancel) fail.push('the cancel button became another');
      if (el.querySelector('[data-action="toggle-pin"]')) fail.push('the pin button stayed');
      // Nor one coming: the new one is new.
      updateHtml(el, '<button data-action="toggle-pin">Pin</button><button data-action="cancel-craft">Cancel</button>');
      if (el.querySelector('[data-action="cancel-craft"]') !== cancel || document.activeElement !== cancel) fail.push('a new button took over the cancel button');
      // An element appearing among whitespace-separated siblings (a card's
      // AUTO badge): the element after it, and its focused button, stay.
      updateHtml(el, '<div class="top">\\n  <span>CPU A</span>\\n  <div class="right"><button data-action="toggle-pin">Pin</button></div>\\n</div>');
      const right = el.querySelector('.right');
      const pin = right.querySelector('button');
      pin.focus();
      updateHtml(el, '<div class="top">\\n  <span>CPU A</span>\\n  <span class="badge">AUTO</span>\\n  <div class="right"><button data-action="toggle-pin">Pin</button></div>\\n</div>');
      if (el.querySelector('.right') !== right || right.querySelector('button') !== pin) fail.push('an element after a new one was rebuilt');
      if (document.activeElement !== pin) fail.push('focus after a new element was lost');
      // Keyed elements that swap nesting are both kept.
      updateHtml(el, '<div data-id="b"><div data-id="a">a</div></div>');
      const outer = el.querySelector('[data-id="b"]');
      const inner = el.querySelector('[data-id="a"]');
      updateHtml(el, '<div data-id="a"><div data-id="b">b</div></div>');
      if (el.querySelector('[data-id="a"]') !== inner || el.querySelector('[data-id="b"]') !== outer
        || outer.parentElement !== inner) fail.push('keyed elements swapping nesting were rebuilt');
      // A bar is keyed by its job: the same job's bar is kept and slides,
      // a new job on the CPU gets a new bar, which doesn't animate in.
      const barHtml = (job) => '<div class="progress-track"><div class="progress-fill" data-id="job:' + job + '"></div></div>';
      updateHtml(el, barHtml(1));
      const bar = el.querySelector('.progress-fill');
      bar.style.width = '10%';
      void bar.offsetWidth;
      updateHtml(el, barHtml(1));
      if (el.querySelector('.progress-fill') !== bar) fail.push("a job's bar was not kept");
      bar.style.width = '100%';
      if (!bar.getAnimations().length) fail.push("a job's bar did not slide on");
      updateHtml(el, barHtml(2));
      const next = el.querySelector('.progress-fill');
      next.style.width = '3%';
      if (next === bar || next.getAnimations().length) fail.push("a new job's bar slid from the old one");
      // A style set through the DOM stays with an element of the same class,
      // and is dropped by one matched by position that changes class.
      updateHtml(el, '<div class="a"></div>');
      el.firstChild.style.width = '40%';
      updateHtml(el, '<div class="b"></div>');
      if (el.firstChild.getAttribute('style')) fail.push('a style stayed on an element that changed class');
      el.remove();
      return fail.join('; ');
    })()`);
    if (actions) throw new Error(actions);
    // A click whose press and release straddle a redraw still lands, with
    // real mouse events.
    const box = await evaluate(`(async () => {
      const { updateHtml } = await import('/static/js/util.js');
      const el = window.redrawTarget;
      el.style.position = 'fixed'; el.style.left = '0'; el.style.top = '0'; el.style.zIndex = '9999';
      updateHtml(el, '<button data-id="go">Go 1</button>');
      window.clicks = 0;
      el.addEventListener('click', (e) => { if (e.target.closest('button')) window.clicks++; });
      const r = el.querySelector('button').getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    })()`);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await evaluate(`import('/static/js/util.js').then(m => { m.updateHtml(window.redrawTarget, '<button data-id="go">Go 2</button>'); return true; })`);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: box.x, y: box.y, button: 'left', clickCount: 1 });
    await waitFor('the click landed', `window.clicks === 1 && window.redrawTarget.textContent === 'Go 2'`);
    await evaluate(`window.redrawTarget.remove(), true`);
    // On the page: a card is the same element after the tab's redraw.
    // A report with other numbers, so the redraw shows: same card, new text.
    await evaluate(`window.w01 = card('W01'), window.w01btn = card('W01').querySelector('.ingredients-btn'),
      window.w01text = card('W01').textContent, true`);
    await postCrafts(base, true, true);
    await waitFor('redrawn in place', `card('W01').textContent !== window.w01text && card('W01') === window.w01
      && card('W01').querySelector('.ingredients-btn') === window.w01btn`);
    await evaluate(`window.w01text = card('W01').textContent, true`);
    await postCrafts(base, true);
    await waitFor('back to the usual numbers, still in place', `card('W01').textContent !== window.w01text && card('W01') === window.w01`);
  });

  step('expand idle CPUs; stays open across a refresh', async () => {
    // A redraw straight after the click, before its toggle event: still open.
    // The page's own crafts.js: modules carry the page's ?v= (pages.py).
    await evaluate(`(async () => {
      const v = new URL(document.querySelector('script[type="module"]').src).search;
      const crafts = await import('/static/js/crafts.js' + v);
      $('details.idle-group summary').click();
      crafts.render(crafts.lastData);
      return true;
    })()`);
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
    await clickBackdrop();
    await waitFor('closed by backdrop', `!visible($('#cancelConfirmModal'))`);
  });

  step('pin a CPU, get its completion, acknowledge it', async () => {
    await click(`card('W01').querySelector('.pin-btn')`);
    await waitFor('pinned', `byText('.group-heading', 'Pinned (1)') && card('W01').querySelector('.pin-btn.pinned')`);
    await postCrafts(base, false);
    await waitFor('completion shown', `$('.completed-card')`);
    await waitFor('tab badge: a green dot, nothing busy', `$('#tabBtnCrafts .tab-badge.dot.done')`);
    await click(`$('.completed-card .ack-btn')`);
    await waitFor('completion acknowledged', `!$('.completed-card') && !$('#tabBtnCrafts .tab-badge')`);
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
    // The filter and status buttons are applied by the server.
    await evaluate(`typeInto('#craftHistoryFilter', 'gold'), true`);
    await waitFor('nothing matches', `$('#craftHistoryList').textContent.includes('No crafts match.')`);
    await evaluate(`typeInto('#craftHistoryFilter', '@minecraft iron'), true`);
    await waitFor('matches a page again', `$$('#craftHistoryList .history-row').length === 25`);
    await click(`$('#craftHistoryStatus [data-status="auto"]')`);
    await waitFor('no auto-crafts', `$('#craftHistoryStatus [data-status="auto"]').classList.contains('active')
      && $('#craftHistoryList').textContent.includes('No crafts match.')`);
    await click(`$('#craftHistoryStatus [data-status=""]')`);
    await evaluate(`typeInto('#craftHistoryFilter', ''), true`);
    await waitFor('unfiltered', `$$('#craftHistoryList .history-row').length === 25
      && !$('#craftHistoryList').textContent.includes('No crafts match.')`);
    // An item opens over the Crafts tab, and closing it stays there.
    await click(`$('#craftHistoryList .item-history-link')`);
    await waitFor('item history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'
      && visible($('#craftsTab')) && location.pathname.startsWith('/network/item/')`);
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('item history closed', `!visible($('#itemHistoryModal')) && visible($('#craftsTab'))
      && !location.pathname.startsWith('/network')`);
    await click(`$('#craftHistory summary')`);
    await waitFor('section closed', `!$('#craftHistory').open`);
  });

  step('statistics: totals, most crafted, CPU busy time, ranges', async () => {
    await click(`$('#craftStats summary')`);
    await waitFor('stats shown', `$('#craftStatsBody .stats-summary')?.textContent.includes('crafts')
      && byText('#craftStatsBody .item-history-link', 'Iron Ingot')
      && $('#craftStatsBody').textContent.includes('CPU W01')`);
    // J01's stalled dust (the live tree's step above), at its Macerator.
    await waitFor('held up at', `(() => {
      const list = $$('#craftStatsBody .craft-history-list').find((l) => l.textContent.includes('Held up at'));
      return list && list.textContent.includes('Macerator') && list.textContent.includes('nothing back');
    })()`);
    await click(`$('#craftStatsRange [data-range="day"]')`);
    await waitFor('day range', `$('#craftStatsRange [data-range="day"]').classList.contains('active')
      && byText('#craftStatsBody .item-history-link', 'Iron Ingot')`);
    await click(`byText('#craftStatsBody .item-history-link', 'Iron Ingot')`);
    await waitFor('item history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'`);
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('item history closed', `!visible($('#itemHistoryModal'))`);
    await click(`$('#tabBtnCrafts')`);
    await waitFor('crafts tab', `visible($('#craftsTab'))`);
    await click(`$('#craftStats summary')`);
    await waitFor('section closed', `!$('#craftStats').open`);
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
    await waitFor('eta shown', `$('#powerEta')?.textContent === 'Full in 1h 45m \u00b7 +400.0k EU/h over the past hour'
      && $('#powerEta .power-trend-up')?.textContent === '+400.0k EU/h'`);
    await waitFor('one machine: no machine buttons', `$('#powerMachines').hidden`);
    await waitFor('fill on the tab button', `$('#tabBtnPower .tab-fill')?.textContent === '30%'`);
    await game(base, '/api/power', { stored: 250, capacity: 1000, machine: 'Batteries' });
    await click(`$('#powerTab [data-range="week"]')`);
    await waitFor('week active', `$('#powerTab [data-range="week"]').classList.contains('active')`);
    // The Crafts tab's statistics range buttons are left alone.
    await waitFor('stats range untouched', `$('#craftStatsRange [data-range="day"]').classList.contains('active')
      && !$('#craftStatsRange [data-range="week"]').classList.contains('active')`);
    await waitFor('machine buttons, each with its fill', `!$('#powerMachines').hidden
      && $$('#powerMachines .range-btn').map((b) => b.textContent).join() === 'Main 30%,Batteries 25%'`);
    // Both together, by size: the 1M EU one outweighs the 1k EU one.
    await waitFor('tab fill counts both', `$('#tabBtnPower .tab-fill')?.textContent === '30%'`);
    await click(`$('#powerMachines [data-machine="Batteries"]')`);
    await waitFor('batteries shown', `location.search === '?machine=Batteries'
      && $('#powerCurrent').textContent.includes('250 EU / 1.00k EU (25.0%)')
      && $('#powerMachines [data-machine="Batteries"]').classList.contains('active')`);
    await click(`$('#tabBtnCrafts')`);
    await click(`$('#tabBtnPower')`);
    await waitFor('kept when coming back', `location.search === '?machine=Batteries'
      && $('#powerCurrent').textContent.includes('250 EU')`);
    // The wireless network: no capacity, so no fill, "/ capacity" or line.
    await game(base, '/api/power', { stored: 2e18, capacity: 0, machine: 'Wireless' });
    await click(`$('#powerTab [data-range="day"]')`);
    await waitFor('wireless button first, with its EU', `$$('#powerMachines .range-btn').map((b) => b.textContent).join()
      === 'Wireless 2.00E,Main 30%,Batteries 25%'`);
    await click(`$('#powerMachines [data-machine="Wireless"]')`);
    await waitFor('wireless shown', `location.search === '?machine=Wireless'
      && $('#powerCurrent .big').textContent === '2.00E EU'
      && !$('#powerCurrent').textContent.includes('%')`);
    await waitFor('no capacity line', `Chart.getChart($('#powerChart')).data.datasets[1].hidden === true`);
    // An LSC in wireless mode is measured against its threshold (1 UEV: 6P), past 100% too.
    await game(base, '/api/power', {
      stored: 6.3e15, capacity: 9.2e18, machine: 'LSC', wireless_mode: true, capacitors: { UEV: 1 },
      avg_eu_in_5s: 100, avg_eu_out_5s: 40, avg_eu_in_1h: 100, avg_eu_out_1h: 150,
    });
    await click(`$('#powerTab [data-range="day"]')`);
    await waitFor('lsc button', `$('#powerMachines [data-machine="LSC"]')`);
    await click(`$('#powerMachines [data-machine="LSC"]')`);
    await waitFor('lsc by its threshold', `$('#powerMachines [data-machine="LSC"]').textContent === 'LSC 105%'
      && $('#powerCurrent').textContent.includes('6.30P EU / 6.00P EU wireless threshold (105.0%)')
      && Chart.getChart($('#powerChart')).data.datasets[1].label === 'Wireless threshold'
      && !Chart.getChart($('#powerChart')).data.datasets[1].hidden
      // One reading isn't enough to tell what it trades with the network.
      && $('#powerEta').textContent === 'Refills up to 20.00T EU/s'`);
    // Above its threshold the whole time, its axis still starts below the
    // threshold line, rather than on it, where the line would be hidden.
    await waitFor('threshold line inside the chart', `Chart.getChart($('#powerChart')).scales.y.min < 6e15`);
    // Its averages are EU/t: 60 EU/t is 1.2k EU/s.
    await waitFor('arrow in EU/s', `$('#powerCurrent .power-trend').textContent.includes('1.20k EU/s')`);
    await evaluate(`window.lscChartTop = $('#powerChartWrap').getBoundingClientRect().top`);
    await click(`$('#powerMachines [data-machine="Wireless"]')`);
    await waitFor('wireless again', `location.search === '?machine=Wireless' && $('#powerEta').textContent === ''`);
    // Its readout has no "Empty in" line yet, but the chart stays where it was.
    await waitFor('chart doesn\'t move', `$('#powerChartWrap').getBoundingClientRect().top === window.lscChartTop`);
    // An LSC in wireless mode stays near empty: the tab button shows the network.
    await waitFor('tab shows the wireless network', `$('#tabBtnPower .tab-fill')?.textContent === '2.00E'
      && !$('#tabBtnPower .tab-badge')`);
    await click(`$('#powerMachines [data-machine=""]')`);
    await waitFor('main again', `location.pathname === '/power' && location.search === '?machine='
      && $('#powerMachines [data-machine=""]').classList.contains('active') && $('#powerEta').textContent !== ''`);
    // An LSC drained under its threshold: the red dot, and its button in red.
    await game(base, '/api/power', {
      stored: 3e14, capacity: 9.2e18, machine: 'LSC', wireless_mode: true, capacitors: { UEV: 1 },
    });
    // With no machine picked, the wireless network is the default.
    await cdp.send('Page.navigate', { url: base + '/power' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('wireless by default', `$('#powerMachines [data-machine="Wireless"]')?.classList.contains('active')
      && $('#powerCurrent .big')?.textContent === '2.00E EU'`);
    await waitFor('drained lsc in red', `$('#powerMachines [data-machine="LSC"]').classList.contains('alert')
      && !$('#powerMachines [data-machine="Wireless"]').classList.contains('alert')
      && $('#tabBtnPower .tab-badge.alert')?.getAttribute('aria-label') === 'LSC under 10% of its wireless threshold'`);
    await game(base, '/api/power', {
      stored: 6.3e15, capacity: 9.2e18, machine: 'LSC', wireless_mode: true, capacitors: { UEV: 1 },
    });
    // The chart is in the theme's accent, and redrawn in the new one when the theme changes.
    const line = `Chart.getChart($('#powerChart')).data.datasets[0].borderColor`;
    const accent = `getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()`;
    await waitFor('chart in the accent', `${line} === ${accent}`);
    await click(`$('#settingsBtn')`);
    await click(`$('[data-theme-choice="dark"]')`);
    await waitFor('chart in dark\'s accent', `${line} === '#5fb3ff'`);
    await click(`$('[data-theme-choice="light"]')`);
    await waitFor('chart in light\'s accent', `${line} === '#2272c9'`);
    await click(`$('[data-theme-choice="auto"]')`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('menu closed', `!visible($('#settingsMenu'))`);
  });

  step('power: unnamed machines on two computers get a number each; an admin renames one', async () => {
    // The first address keeps the unnamed history; a second one numbers both.
    await game(base, '/api/power', { stored: 6e9, capacity: 1e10, address: 'lsc-a', wireless_mode: false });
    await game(base, '/api/power', { stored: 9e9, capacity: 2e10, address: 'lsc-b', wireless_mode: false });
    await click(`$('#tabBtnPower')`);
    await click(`$('#powerTab [data-range="week"]')`);
    // Wireless, then by size: the wireless-mode LSC by its 6P threshold, then 20G, 10G, 1k.
    await waitFor('numbered, by size', `$$('#powerMachines .range-btn').map((b) => b.dataset.machine).join()
      === 'Wireless,LSC,LSC 2,LSC 1,Batteries'`);
    await click(`$('#powerMachines [data-machine="LSC 2"]')`);
    await waitFor('lsc 2 shown', `location.search === '?machine=LSC%202' && visible($('#powerRenameBtn'))
      && $('#powerMachines [data-machine="LSC 2"]').classList.contains('active')
      && $('#powerCurrent .big').textContent === '9.00G EU'`);
    await click(`$('#powerRenameBtn')`);
    await waitFor('rename dialog', `visible($('#powerRenameModal')) && $('#powerRenameInput').value === 'LSC 2'`);
    // Unchanged, it just closes.
    await click(`$('#powerRenameSubmitBtn')`);
    await waitFor('unchanged closes', `!visible($('#powerRenameModal')) && location.search === '?machine=LSC%202'`);
    await click(`$('#powerRenameBtn')`);
    await waitFor('rename dialog again', `visible($('#powerRenameModal'))`);
    // A name in use is refused in the dialog.
    await evaluate(`typeInto('#powerRenameInput', 'LSC 1')`);
    await click(`$('#powerRenameSubmitBtn')`);
    await waitFor('name in use', `visible($('#powerRenameError'))
      && $('#powerRenameError').textContent === "There's already a machine named LSC 1."`);
    await evaluate(`typeInto('#powerRenameInput', 'Nether LSC')`);
    await click(`$('#powerRenameSubmitBtn')`);
    await waitFor('renamed', `!visible($('#powerRenameModal')) && location.search === '?machine=Nether%20LSC'
      && $('#powerMachines [data-machine="Nether LSC"]').classList.contains('active')
      && !$('#powerMachines [data-machine="LSC 2"]')
      && $('#powerCurrent .big').textContent === '9.00G EU'`);
    // Its script's next reading, from the same address, lands under the new name.
    await game(base, '/api/power', { stored: 8e9, capacity: 2e10, address: 'lsc-b', wireless_mode: false });
    await click(`$('#powerTab [data-range="day"]')`);
    await waitFor('later reading kept', `$('#powerCurrent .big').textContent === '8.00G EU'
      && !$('#powerMachines [data-machine="LSC 2"]')
      && Chart.getChart($('#powerChart')).data.datasets[0].data.length === 2`);
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

  step('network Change sort: most used up first, by how much', async () => {
    const rescan = async (sizes) => {
      const { data: scan } = await game(base, '/api/network/scan/start', {});
      await game(base, '/api/network/scan/batch', { scan_token: scan.scan_token,
        items: SEEDED_ITEMS.map(it => ({ ...it, size: sizes[it.name] ?? it.size })) });
      await game(base, '/api/network/scan/finish', { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0 });
    };
    await rescan({ 'Neutronium Ingot': 1, Water: 70000 });
    await click(`$('#networkTab [data-sort="change"]')`);
    await waitFor('range shown', `visible($('#networkChangeRange'))`);
    // The tab only refetches the items each minute; reopening it fetches the new scan.
    await click(`$('#tabBtnCrafts')`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('neutronium first, falling; water rising; iron unchanged last', `(() => {
      const cells = $$('#networkList .network-cell');
      return cells.length === 3
        && cells[0].querySelector('.network-cell-change.falling')?.textContent === '−80%'
        && cells[1].querySelector('.network-cell-change.rising')?.textContent === '+9%'
        && !cells[2].querySelector('.network-cell-change')
        && visible(cells[2].querySelector('.network-cell-qty'));
    })()`);
    await click(`$('#networkTab [data-range="week"]')`);
    await waitFor('week active', `$('#networkTab [data-range="week"]').classList.contains('active')
      && $('#networkList .network-cell-change.falling')`);
    await rescan({});
    await click(`$('#networkTab [data-sort="name"]')`);
    await waitFor('range hidden', `!visible($('#networkChangeRange')) && !$('#networkList .network-cell-change')`);
  });

  step('network grid by keyboard: arrows, Enter, Escape back, P pins, C crafts', async () => {
    // Real key presses, so focus moves and :focus-visible are the browser's own.
    const CODES = { ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39, Enter: 13, Escape: 27, p: 80, c: 67 };
    const key = async (k) => {
      const ev = { key: k, code: k.length === 1 ? 'Key' + k.toUpperCase() : k, windowsVirtualKeyCode: CODES[k] };
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', ...ev, ...(k.length === 1 ? { text: k } : {}) });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...ev });
    };
    // In parentheses: `${focused}.startsWith(...)` otherwise binds to the ''
    // alone, and any labelled element passes.
    const focused = `(document.activeElement.getAttribute('aria-label') || '')`;
    await evaluate(`$('#networkSearch').focus(), true`);
    await key('ArrowDown');
    await waitFor('first cell', `${focused}.startsWith('Iron Ingot')`);
    await key('ArrowRight');
    await waitFor('second cell, with its tooltip', `${focused}.startsWith('Neutronium Ingot')
      && visible($('.network-tooltip')) && $('.network-tooltip').textContent.includes('Neutronium Ingot')`);
    await key('Enter');
    await waitFor('history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Neutronium Ingot'`);
    // Between 1 and 5 of them: whole-number ticks, not 0.5, 1, 1.5...
    // rounded to repeated labels.
    await waitFor('whole-number y ticks', `(Chart.getChart($('#itemHistoryChart'))?.scales.y.ticks || [])
      .map((t) => t.value).every(Number.isInteger) && Chart.getChart($('#itemHistoryChart')).scales.y.ticks.length > 1`);
    await key('Escape');
    await waitFor('closed, focus back on the cell', `!visible($('#itemHistoryModal')) && ${focused}.startsWith('Neutronium Ingot')`);
    await key('p');
    await waitFor('pinned, floated to the top, still focused', `${focused}.startsWith('Neutronium Ingot')
      && document.activeElement.dataset.idx === '0' && document.activeElement.querySelector('.network-cell-pin-badge')`);
    await key('p');
    await waitFor('unpinned', `!$('#networkList .network-cell-pin-badge')`);
    // Twice in a row, without waiting: the second press undoes the first.
    await key('p');
    await key('p');
    await sleep(300);
    await waitFor('still unpinned, here and on the server', `(async () => !$('#networkList .network-cell-pin-badge')
      && (await (await fetch('/api/network/pins')).json()).pins.length === 0)()`);
    await key('ArrowLeft');
    await waitFor('iron', `${focused}.startsWith('Iron Ingot')`);
    await key('c');
    await waitFor('craft dialog, no stray c typed', `visible($('#craftRequestModal')) && $('#craftRequestName').textContent === 'Iron Ingot'
      && !$('#craftRequestAmount').value.includes('c') && document.activeElement === $('#craftRequestAmount')`);
    await key('5');
    await waitFor('typed over the 1', `$('#craftRequestAmount').value === '5'`);
    await key('Escape');
    await waitFor('closed, back on iron', `!visible($('#craftRequestModal')) && ${focused}.startsWith('Iron Ingot')`);
    await key('ArrowUp');
    await waitFor('top row: up to the search', `document.activeElement === $('#networkSearch')`);
    // Escape closes the dialog and focus is back on the cell at once; a key
    // pressed straight after (here, the move ↑ makes to the search) isn't
    // undone by a later refocus.
    await key('ArrowDown');
    await waitFor('iron again', `${focused}.startsWith('Iron Ingot')`);
    await key('c');
    await waitFor('craft dialog again', `visible($('#craftRequestModal'))`);
    const kept = await evaluate(`(async () => {
      document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
      await Promise.resolve();
      $('#networkSearch').focus();
      await new Promise(r => setTimeout(r, 100));
      return document.activeElement === $('#networkSearch');
    })()`);
    if (!kept) throw new Error('focus moved on right after Escape was taken back');
  });

  step('"/" jumps to the network search from another tab', async () => {
    await click(`$('#tabBtnCrafts')`);
    await waitFor('crafts tab', `visible($('#craftsTab'))`);
    await evaluate(`document.activeElement.blur(), pressKey('/'), true`);
    await waitFor('search focused on the network tab', `location.pathname === '/network'
      && document.activeElement === $('#networkSearch')`);
  });

  step('network list view shows names and is remembered', async () => {
    await waitFor('names hidden in the grid', `!visible($('#networkList .network-cell-name'))`);
    await click(`$('#networkTab [data-view="list"]')`);
    await waitFor('names shown', `$('#networkList').classList.contains('list')
      && $$('#networkList .network-cell-name').some(n => visible(n) && n.textContent === 'Iron Ingot')
      && $('#networkTab [data-sort="name"]').classList.contains('active')`);
    await cdp.send('Page.navigate', { url: base + '/network' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('still a list', `$$('#networkList .network-cell').length === 3
      && $('#networkList').classList.contains('list')
      && $('#networkTab [data-view="list"]').classList.contains('active')`);
    await click(`$$('#networkList .network-cell').find(c => c.textContent.includes('Iron Ingot'))`);
    await waitFor('row opens history', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'`);
    await clickBackdrop();
    await waitFor('closed', `!visible($('#itemHistoryModal'))`);
    await click(`$('#networkTab [data-view="grid"]')`);
    await waitFor('grid again', `!$('#networkList').classList.contains('list')`);
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
    await clickBackdrop();
    await waitFor('closed by backdrop', `!visible($('#craftRequestModal'))`);
    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('craft dialog', `visible($('#craftRequestModal')) && $('#craftRequestName').textContent.includes('Iron Ingot')`);
    // Drag-selecting the amount and letting go over the backdrop keeps
    // the dialog open.
    await evaluate(`typeInto('#craftRequestAmount', '123')`);
    await mouse('mousePressed', await centerOf(`$('#craftRequestAmount')`));
    await mouse('mouseMoved', [5, 5]);
    await mouse('mouseReleased', [5, 5]);
    await evaluate(`new Promise((r) => setTimeout(r, 200))`);
    await waitFor('still open after a drag out', `visible($('#craftRequestModal'))`);
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
    // The filter searches the whole plan, not just the levels sent: the
    // Neutronium Ingot the first furnace part takes from stock is three
    // levels down, unopened, and still found.
    await evaluate(`typeInto('#craftPlanFilter', 'neutronium ingot'), true`);
    await waitFor('found where not loaded', `$$('#craftPlanBody .filter-match').length === 2
      && ${rows}.some(r => r.includes('Neutronium Ingot') && r.includes('in stock'))`);
    await evaluate(`typeInto('#craftPlanFilter', 'gold'), true`);
    await waitFor('nothing in the plan', `$('#craftPlanBody').textContent.includes('Nothing in this plan matches')`);
    await evaluate(`typeInto('#craftPlanFilter', ''), true`);
    await waitFor('unfiltered again', `$$('#craftPlanBody .plan-node').length === 7`);
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
    await clickBackdrop();
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
    await waitFor('tab badge', `$('#tabBtnNetwork .tab-badge.alert')?.textContent === '1'`);
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
    await waitFor('overview hidden', `!visible($('#stockRules')) && !$('#tabBtnNetwork .tab-badge')`);
    await evaluate(`typeInto('#networkSearch', ''), true`);  // openItem() searched for it
  });

  step('admin: create user, history, new token, revoke, delete', async () => {
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#adminBtn')`);
    await waitFor('admin dialog', `visible($('#adminUsersModal')) && $('#adminUsersList').textContent.includes('Administrator')`);
    // Your own history: the requests and the cancel made earlier, each
    // with how it turned out (the game refused every request).
    await click(`byText('button', 'History', $$('#adminUsersList .admin-user-row').find(r => r.querySelector('.admin-user-you')))`);
    await waitFor('own history', `$('#userHistoryTitle').textContent === 'Administrator'
      && $('#userHistorySummary').textContent === 'Admin · 2 requests · 1 cancel · 2 failed'
      && $('#userHistoryList .status-badge.cancelled') && $('#userHistoryList .history-day')
      && !$('#userHistoryList .status-badge.auto') && $('#userHistoryAuto').textContent === 'Stock refills (1)'`);
    // A stock target's requests only on asking: the server made them.
    await click(`$('#userHistoryAuto')`);
    await waitFor('refills shown', `$('#userHistoryAuto').classList.contains('active') && $('#userHistoryList .status-badge.auto')
      && $('#userHistorySummary').textContent === 'Admin · 2 requests · 1 cancel · 2 failed'`);
    await click(`$('#userHistoryFilters [data-filter="cancel"]')`);
    await waitFor('cancels only', `$$('#userHistoryList .history-row').length > 0
      && $$('#userHistoryList .history-row').every(r => r.querySelector('.cpu-id').textContent.startsWith('Cancel on CPU'))`);
    await click(`$('#userHistoryFilters [data-filter="problem"]')`);
    await waitFor('failed only', `$$('#userHistoryList .history-row').every(r => r.querySelector('.status-badge.failed'))`);
    await click(`$('#userHistoryModal .history-close-btn')`);
    // Your own row says so, and can't be deleted (the server refuses it).
    const me = `$$('#adminUsersList .admin-user-row').find(r => r.querySelector('.admin-user-name')?.textContent === 'Administrator')`;
    await waitFor('own row', `${me}.querySelector('.admin-user-you') && !${me}.querySelector('[data-action="delete-user"]')`);
    // New users are operators unless picked otherwise; the picked role is explained.
    await waitFor('operator by default', `$('#adminUserRole').value === 'operator' && $('#adminRoleHelp').textContent.includes('requests and cancels crafts')`);
    await evaluate(`(() => { const s = $('#adminUserRole'); s.value = 'admin'; s.dispatchEvent(new Event('change')); })()`);
    await waitFor('admin explained', `$('#adminRoleHelp').textContent.includes('manages users')`);
    await evaluate(`(() => { const s = $('#adminUserRole'); s.value = 'operator'; s.dispatchEvent(new Event('change')); })()`);
    await evaluate(`typeInto('#adminUserName', 'E2E Tester')`);
    await click(`$('#adminCreateUserBtn')`);
    const row = `$$('#adminUsersList .admin-user-row').find(r => r.querySelector('.admin-user-name')?.textContent === 'E2E Tester')`;
    await waitFor('user created', row);
    await waitFor('an operator', `${row}.querySelector('.role-badge').textContent === 'operator' && ${row}.textContent.includes('never used')`);
    // The one-time token shows in the new user's row.
    await waitFor('token shown', `visible($('#adminTokenResult')) && ${row}.contains($('#adminTokenResult')) && $('#adminTokenValue').value.startsWith('gcm_')`);
    const firstToken = await evaluate(`$('#adminTokenValue').value`);
    // Clipboard access may be refused headless; either outcome proves the click ran.
    await click(`byText('#adminTokenResult button', 'Copy token')`);
    await waitFor('copied or selected', `$('#toastContainer').textContent.includes('Access token copied.') || document.activeElement === $('#adminTokenValue')`);
    await click(`byText('button', 'History', ${row})`);
    await waitFor('history dialog', `visible($('#userHistoryModal')) && $('#userHistoryTitle').textContent === 'E2E Tester'
      && $('#userHistorySummary').textContent === 'Operator · 0 requests · 0 cancels' && $('#userHistoryList').textContent.includes('No craft requests or cancels yet.')`);
    await click(`$('#userHistoryModal .history-close-btn')`);
    await waitFor('closed by button', `!visible($('#userHistoryModal'))`);
    await click(`byText('button', 'History', ${row})`);
    await waitFor('history dialog again', `visible($('#userHistoryModal'))`);
    await clickBackdrop();
    await waitFor('closed by backdrop', `!visible($('#userHistoryModal'))`);
    await click(`byText('button', 'New token', ${row})`);
    await waitFor('new token', `$('#adminTokenValue').value !== ${JSON.stringify(firstToken)}`);
    await waitFor('one token row', `${row}.querySelectorAll('.admin-token-revoke').length === 1`);
    await click(`${row}.querySelector('.admin-token-revoke')`);
    await waitFor('token revoked', `${row}?.textContent.includes('No active tokens')`);
    await click(`byText('button', 'Delete', ${row})`);
    await waitFor('user deleted', `!(${row})`);
    await click(`$('#adminUsersModal .history-close-btn')`);
    await waitFor('closed by button', `!visible($('#adminUsersModal'))`);
    await click(`$('#settingsBtn')`);
    await click(`$('#adminBtn')`);
    await waitFor('admin dialog again', `visible($('#adminUsersModal'))`);
    await clickBackdrop();
    await waitFor('closed by backdrop', `!visible($('#adminUsersModal'))`);
  });

  step('an AI assistant connects with OAuth: consent page, then listed and disconnected in Manage users', async () => {
    // A loopback callback, as Claude Code registers: this server's own
    // address, where the code shows up in the URL.
    const callback = base + '/e2e-callback';
    const { data: client } = await api(base, 'POST', '/oauth/register',
      { client_name: 'E2E Assistant', redirect_uris: [callback] });
    const verifier = 'e2e-verifier-'.padEnd(64, 'x');
    const params = new URLSearchParams({
      response_type: 'code', client_id: client.client_id, redirect_uri: callback, state: 'e2e',
      code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256',
    });
    await cdp.send('Page.navigate', { url: `${base}/oauth/authorize?${params}` });
    await waitFor('consent page', `document.querySelector('.oauth-title')?.textContent === 'Connect E2E Assistant?'
      && document.body.textContent.replace(/\\s+/g, ' ').includes('as Administrator (admin)')`);
    // The site's stylesheet loads under the page's own CSP.
    await waitFor('styled', `getComputedStyle(document.querySelector('.oauth-box')).borderRadius === '12px'`);
    await evaluate(`[...document.querySelectorAll('button')].find((b) => b.textContent === 'Allow').click(), true`);
    await waitFor('sent back with a code', `location.pathname === '/e2e-callback'
      && new URLSearchParams(location.search).get('state') === 'e2e'`);
    const code = await evaluate(`new URLSearchParams(location.search).get('code')`);
    const exchanged = await fetch(base + '/oauth/token', {
      method: 'POST',
      body: new URLSearchParams({ grant_type: 'authorization_code', code, client_id: client.client_id,
        redirect_uri: callback, code_verifier: verifier }),
    });
    const tokens = await exchanged.json();
    if (!exchanged.ok) throw new Error(`token: ${exchanged.status} ${JSON.stringify(tokens)}`);
    const mcpStatus = () => fetch(base + '/mcp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tokens.access_token}` },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'ping' }),
    }).then((r) => r.status);
    if (await mcpStatus() !== 200) throw new Error('the access token should work on /mcp');

    await cdp.send('Page.navigate', { url: base + '/' });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#adminBtn')`);
    const app = `$$('#adminUsersList .admin-token-row').find((r) => r.textContent.includes('E2E Assistant'))`;
    await waitFor('app listed', `${app}?.textContent.includes('App: E2E Assistant') && ${app}.textContent.includes('used')`);
    await click(`${app}.querySelector('[data-action="revoke-app"]')`);
    await waitFor('disconnected', `!(${app}) && $('#toastContainer').textContent.includes('E2E Assistant disconnected.')`);
    if (await mcpStatus() !== 401) throw new Error('a disconnected app\'s token should stop working');
    await click(`$('#adminUsersModal .history-close-btn')`);
    await waitFor('closed', `!visible($('#adminUsersModal'))`);
  });

  step('admin: MCP server dialog: the address, the guide, house rules, and the tool calls made', async () => {
    // One tool call to show up in the log.
    const called = await fetch(base + '/mcp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'current_crafts', arguments: {} } }),
    });
    if (!called.ok) throw new Error(`tools/call: ${called.status}`);
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#assistantBtn')`);
    await waitFor('dialog', `visible($('#assistantModal')) && $('#assistantUrl').value.endsWith('/mcp')
      && $('#assistantModal .modal-item-name').textContent === 'MCP server'
      && $('#assistantDocsLink').href.endsWith('/docs/using.md#ai-assistants') && visible($('#assistantAdmin'))`);
    await waitFor('the call listed', `$('#assistantCalls .assistant-call b')?.textContent === 'current_crafts'
      && $('#assistantCalls').textContent.includes('Administrator')`);
    await evaluate(`typeInto('#assistantRules', 'Plasmas are kept by Level Maintainers.')`);
    await waitFor('counted', `$('#assistantRulesCount').textContent.startsWith('38 /')`);
    await click(`byText('#assistantModal button', 'Save')`);
    await waitFor('saved', `$('#toastContainer').textContent.includes('House rules saved.')`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed by Escape', `!visible($('#assistantModal'))`);
    await click(`$('#settingsBtn')`);
    await click(`$('#assistantBtn')`);
    await waitFor('rules kept', `visible($('#assistantModal'))
      && $('#assistantRules').value === 'Plasmas are kept by Level Maintainers.'`);
    // What an assistant connecting now is told.
    const init = await fetch(base + '/mcp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18' } }),
    }).then((r) => r.json());
    if (!init.result.instructions.endsWith('Plasmas are kept by Level Maintainers.')) {
      throw new Error('house rules missing from the instructions');
    }
    await clickBackdrop();
    await waitFor('closed by backdrop', `!visible($('#assistantModal'))`);
    // A request an assistant makes shows as one in History.
    await postCrafts(base, false);
    const requested = await fetch(base + '/mcp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${adminToken}` },
      body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'request_craft', arguments: {
        item: 'minecraft|iron_ingot|0|item', amount: 1, force: true, allow_duplicate: true } } }),
    }).then((r) => r.json());
    if (requested.result.isError) throw new Error(`request_craft: ${requested.result.content[0].text}`);
    await click(`$('#settingsBtn')`);
    await click(`$('#adminBtn')`);
    await waitFor('admin dialog', `visible($('#adminUsersModal'))`);
    await click(`byText('button', 'History', $$('#adminUsersList .admin-user-row').find(r => r.querySelector('.admin-user-you')))`);
    await waitFor('the assistant\'s request', `$('#userHistoryList .status-badge.assistant')?.textContent === 'Assistant'`);
    await click(`$('#userHistoryModal .history-close-btn')`);
    await click(`$('#adminUsersModal .history-close-btn')`);
    await waitFor('closed', `!visible($('#adminUsersModal'))`);
  });

  step('admin: a notice when a computer\'s scripts are older than the server needs', async () => {
    // v1.0.0 is older than GCM_SCRIPTS_VERSION (v1.1.0). The next step's
    // computer, on v1.1.0, only has a newer release to update to: no notice.
    await game(base, '/api/agent/heartbeat', {
      address: 'ffffffff-0000-4000-8000-000000000002', version: 'v1.0.0', components: ['power'],
      services: [{ name: 'power_monitor', running: true }], free_memory: 1048576, total_memory: 4194304, uptime: 60,
    });
    await cdp.send('Page.reload', {});
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('notice', `visible($('#scriptsNotice'))
      && $('#scriptsNoticeText').textContent.startsWith('A computer runs in-game scripts older than v1.1.0')
      && $('#scriptsNoticeChanges').href.endsWith('/releases/tag/v1.1.0')`);
    await click(`$('[data-action="scripts-notice-update"]')`);
    await waitFor('Status, the computer says why', `visible($('#agentsModal'))
      && $('#agentsList').textContent.includes('scripts changed in v1.1.0: update recommended')`);
    await click(`$('[data-action="close-agents"]')`);
    await click(`$('[data-action="dismiss-scripts-notice"]')`);
    await waitFor('closed', `!visible($('#scriptsNotice'))`);
    // Kept on the server for every admin, not in this browser.
    await sleep(300);
    await evaluate(`localStorage.clear(), true`);
    await cdp.send('Page.reload', {});
    await waitFor('reloaded again', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('signed in', `$('#settingsUser').textContent !== ''`);
    await sleep(1000);
    await waitFor('still closed for v1.1.0', `!visible($('#scriptsNotice'))`);
  });

  step('admin: Status shows the server; lists computers; update one and see the result', async () => {
    const heartbeat = {
      address: 'abcdef12-0000-4000-8000-000000000001', version: 'v1.1.0', components: ['craft'],
      services: [{ name: 'craft_monitor', running: true, last_error: { message: 'not enough memory', ago: 60 } }],
      free_memory: 1048576, total_memory: 4194304, uptime: 3600,
    };
    await game(base, '/api/agent/heartbeat', heartbeat);
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#agentsBtn')`);
    await waitFor('server section', `visible($('#agentsModal'))
      && $$('#serverHealth .health-feed').some(f => f.textContent.includes('Crafting CPUs') && f.querySelector('.agent-dot.running'))
      && $$('#serverHealth .health-feed').some(f => f.textContent.includes('ME network') && f.textContent.includes('scan took'))
      && $('#serverHealth').textContent.includes('item_history.db')`);
    await waitFor('computer listed', `visible($('#agentsModal'))
      && $('#agentsList').textContent.includes('Computer abcdef12')
      && $('#agentsList').textContent.includes('update available: v1.2.0')
      && $('#agentsList').textContent.includes('craft_monitor')
      && $$('#agentsList .agent-command.failed').some(l => l.textContent === 'craft_monitor crashed 1m ago: not enough memory')`);
    // The checkboxes: auto-restart on by default and saved on the server;
    // auto-update needs a server that knows its release, which this one doesn't.
    await waitFor('settings loaded', `$('#autoRestartToggle').checked && !$('#autoRestartToggle').disabled
      && $('#autoUpdateToggle').disabled && $('#autoUpdateNote').textContent.includes('Needs a server')`);
    const agentsAutoRestart = async (want) => {
      for (let i = 0; i < 50; i++) {
        if ((await game(base, '/api/agent/heartbeat', heartbeat)).data.auto_restart === want) return;
        await new Promise(r => setTimeout(r, 100));
      }
      throw new Error(`heartbeats never said auto_restart: ${want}`);
    };
    await click(`$('#autoRestartToggle')`);
    await agentsAutoRestart(false);
    await click(`$('#autoRestartToggle')`);
    await agentsAutoRestart(true);
    await waitFor('auto-restart back on', `$('#autoRestartToggle').checked`);
    await click(`byText('#agentsList [data-action="agent-command"]', 'Update to v1.2.0')`);
    await waitFor('queued', `$('#agentsList').textContent.includes('waiting for the computer to pick it up')
      && $('#agentsList [data-action="agent-command"]').disabled`);
    const { data } = await game(base, '/api/agent/heartbeat', heartbeat);
    if (data.command?.action !== 'update' || data.command?.ref !== 'release') throw new Error(JSON.stringify(data));
    await game(base, `/api/agent/commands/${data.command.id}/result`,
      { ok: true, message: 'Updated v1.1.0 -> v1.2.0, rebooting.', version: 'v1.2.0', rebooting: true });
    await waitFor('rebooting', `$('#agentsList').textContent.includes('rebooting, waiting for it to come back')
      && !$('#agentsList').textContent.includes('update available')`);
    await game(base, '/api/agent/heartbeat', { ...heartbeat, version: 'v1.2.0', uptime: 3 });
    await waitFor('back up', `$('#agentsList').textContent.includes('Updated v1.1.0 -> v1.2.0, back up')
      && !$('#agentsList [data-action="agent-command"]').disabled`);

    // Connect a computer: every script picked, this page's address, the
    // key masked until shown, the address field hidden until asked for.
    const install = '/tmp/gcm.lua install craft power network --server=';
    await waitFor('connect command', `$('#connectServerUrl').value === location.origin
      && !visible($('#connectServerUrl'))
      && $('#connectCommand').textContent.startsWith('wget -f ')
      && $('#connectCommand').textContent.endsWith('/oc/gcm.lua /tmp/gcm.lua && ${install}' + location.origin + ' --api-key=' + '•'.repeat(12))
      && visible($('#connectLocalNote'))`);
    // Scripts switched off drop out of the command; with none, nothing to copy.
    await click(`$('#connectScripts [data-script="power"]')`);
    await waitFor('power unpicked', `$('#connectCommand').textContent.includes('/tmp/gcm.lua install craft network --server=')
      && $('#connectScripts [data-script="power"]').getAttribute('aria-pressed') === 'false'
      && !$('#connectScripts [data-script="power"]').classList.contains('active')`);
    await click(`$('#connectScripts [data-script="craft"]')`);
    await click(`$('#connectScripts [data-script="network"]')`);
    await waitFor('none picked', `$('#connectCommand').textContent.includes('/tmp/gcm.lua install --server=')
      && $('[data-action="copy-connect"]').disabled && visible($('#connectNoScripts'))`);
    for (const name of ['craft', 'power', 'network']) await click(`$('#connectScripts [data-script="${name}"]')`);
    await waitFor('all picked again', `$('#connectCommand').textContent.includes('${install}')
      && !$('[data-action="copy-connect"]').disabled && !visible($('#connectNoScripts'))`);
    await click(`$('#connectKeyBtn')`);
    await waitFor('key shown', `$('#connectCommand').textContent.endsWith('--api-key=${API_KEY}')`);
    await click(`$('#connectAddressBtn')`);
    await waitFor('address field', `visible($('#connectServerUrl')) && document.activeElement === $('#connectServerUrl')
      && !visible($('#connectAddressBtn'))`);
    await evaluate(`typeInto('#connectServerUrl', 'https://gtnh.example.com/'), true`);
    await waitFor('address followed', `$('#connectCommand').textContent.includes('${install}https://gtnh.example.com --api-key=')
      && !visible($('#connectLocalNote'))`);
    await click(`$('#connectKeyBtn')`);
    // Copies the key in full though it's masked again.
    await cdp.send('Browser.grantPermissions', { origin: base, permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] });
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true });
    await click(`$('[data-action="copy-connect"]')`);
    await waitFor('copied', `$('#toastContainer').textContent.includes('Copied. Paste it into the computer')`);
    const copied = await evaluate(`navigator.clipboard.readText()`);
    if (!copied.startsWith('wget -f ') || !copied.endsWith(` && ${install}https://gtnh.example.com --api-key=${API_KEY}`)) {
      throw new Error(`copied: ${copied}`);
    }
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed', `!visible($('#agentsModal'))`);
  });

  step('admin: test webhooks; a pinned craft ending was posted too', async () => {
    if (!webhookPosts.some((p) => p.event === 'crafts' && p.title === 'Craft finished' && p.body === 'Iron Ingot is done crafting.')) {
      throw new Error('no crafts webhook post: ' + JSON.stringify(webhookPosts));
    }
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#webhooksBtn')`);
    await waitFor('webhooks dialog', `visible($('#webhooksModal')) && $('#webhookUrls').value !== ''`);
    // Send test saves the dialog first: the stock box goes with it.
    await click(`$('#webhookEventStock')`);
    await click(`$('#webhookTestBtn')`);
    await waitFor('reported', `$('#toastContainer').textContent.includes('Test sent to 1 webhook.')`);
    const test = webhookPosts.find((p) => p.event === 'test');
    if (!test || !test.body.includes('autocraft, crafts, stock')) throw new Error('no test post: ' + JSON.stringify(test));
    await click(`$('#webhooksModal .history-close-btn')`);
    await waitFor('closed', `!visible($('#webhooksModal'))`);
  });

  let backupZip;
  step('admin: download a backup', async () => {
    const downloads = fs.mkdtempSync(path.join(os.tmpdir(), 'gcm-e2e-downloads-'));
    tempDirs.push(downloads);
    await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads });
    await click(`$('#settingsBtn')`);
    await waitFor('menu open', `visible($('#settingsMenu'))`);
    await click(`$('#backupBtn')`);
    await waitFor('backups dialog', `visible($('#backupsModal')) && $('#restoreBtn').disabled`);
    await click(`$('#backupDownloadBtn')`);
    await waitFor('started', `$('#toastContainer').textContent.includes('Downloading gcm-backup-')`);
    const deadline = Date.now() + 10000;
    let zip;
    while (!zip && Date.now() < deadline) {
      zip = fs.readdirSync(downloads).find((n) => /^gcm-backup-.*\.zip$/.test(n));
      if (!zip) await sleep(200);
    }
    if (!zip) throw new Error('no backup downloaded: ' + fs.readdirSync(downloads).join(', '));
    // A zip's local file header starts "PK\x03\x04".
    if (fs.readFileSync(path.join(downloads, zip)).subarray(0, 4).toString('hex') !== '504b0304') throw new Error('not a zip');
    backupZip = path.join(downloads, zip);
  });

  step('admin: restore a backup; a file that isn\'t one is refused', async () => {
    const pick = async (file) => {
      const { result } = await cdp.send('Runtime.evaluate', { expression: `$('#restoreFile')` });
      await cdp.send('DOM.setFileInputFiles', { objectId: result.objectId, files: [file] });
    };
    const junk = path.join(path.dirname(backupZip), 'not-a-backup.zip');
    fs.writeFileSync(junk, 'hello');
    await pick(junk);
    await waitFor('restore enabled', `!$('#restoreBtn').disabled`);
    await click(`$('#restoreBtn')`);
    await waitFor('asks again', `$('#restoreBtn').textContent === 'Click again to restore'`);
    await click(`$('#restoreBtn')`);
    await waitFor('refused', `visible($('#restoreError')) && $('#restoreError').textContent === "That isn't a zip file."`);

    await pick(backupZip);
    await waitFor('error cleared', `!visible($('#restoreError')) && $('#restoreBtn').textContent === 'Restore'
      && $('#restoreFileName').textContent === ${JSON.stringify(path.basename(backupZip))}`);
    await click(`$('#restoreBtn')`);
    await click(`$('#restoreBtn')`);
    await waitFor('restored', `$('#toastContainer').textContent.includes('Backup restored. Reloading')`);
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    // The session was in the backup, so still signed in as the admin.
    await waitFor('still signed in', `$('#settingsUser').textContent !== '' && $('#settingsUser').textContent === 'Signed in as Administrator'`);
    await waitFor('crafts back', `card('W01')`);
  });

  step('admin: switch game data version and textures; the new icons show', async () => {
    // With none picked, the Network tab says so, with a button for admins.
    await click(`$('#tabBtnNetwork')`);
    await waitFor('no game data note', `visible($('#networkGameDataNote')) && visible($('#networkGameDataBtn')) && !visible($('#networkGameDataAsk'))`);
    await click(`$('#networkGameDataBtn')`);
    await waitFor('game data dialog', `visible($('#gamedataModal')) && $('#gamedataCurrent').textContent.includes('no game data')`);
    await waitFor('version listed', `$$('#gamedataVersion option').some((o) => o.value === '${E2E_DATA_VERSION}')`);
    await evaluate(`$('#gamedataVersion').value = '${E2E_DATA_VERSION}', true`);
    await click(`$('#gamedataInstallBtn')`);
    await waitFor('installed', `$('#gamedataCurrent').textContent === 'In use: GTNH ${E2E_DATA_VERSION}, Default textures'`);
    await waitFor('listed as in use', `$('#gamedataVersion').selectedOptions[0].textContent.includes('(in use)')`);
    await clickBackdrop();
    await waitFor('closed by backdrop, the note gone', `!visible($('#gamedataModal')) && !visible($('#networkGameDataNote'))`);
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

    // The bundle's item info: # searches descriptions, and the tooltip
    // shows the description (its Shift text while Shift is held), the
    // name in its rarity's colour and the mod's name.
    await evaluate(`typeInto('#networkSearch', '#dtpf'), true`);
    await waitFor('found by its description', `$$('#networkList .network-cell').length === 1
      && $('#networkList .network-cell .network-cell-qty').textContent === '5'`);
    await evaluate(`$('#networkList .network-cell').dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: 50, clientY: 50 })), true`);
    await waitFor('description in the tooltip', `visible($('.network-tooltip'))
      && $('.network-tooltip .network-tooltip-description .mc-b')?.textContent === 'DTPF'
      && $('.network-tooltip .network-tooltip-name').classList.contains('mc-d')
      && $('.network-tooltip .network-tooltip-hint')?.textContent === '[shift]'
      && $('.network-tooltip .network-tooltip-mod').textContent === 'GregTech'`);
    await evaluate(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Shift' })), true`);
    await waitFor('Shift text', `$('.network-tooltip .network-tooltip-description').textContent === 'Structure: 33x24x33'`);
    await evaluate(`window.dispatchEvent(new KeyboardEvent('keyup', { key: 'Shift' })), true`);
    await waitFor('back without Shift', `$('.network-tooltip .network-tooltip-description').textContent.includes('DTPF')`);
    await evaluate(`$('#networkList .network-cell').dispatchEvent(new MouseEvent('mouseout', { bubbles: true })), true`);
    // The dialog: the description collapsed to its first line; opened,
    // it ends in the mod's name and has a Shift button. Then the ore
    // names, each a $ search.
    await click(`$('#networkList .network-cell')`);
    // checkVisibility(), not visible(): a closed <details> hides its
    // content without taking its boxes away.
    await waitFor('description collapsed', `visible($('#itemHistoryDetails')) && !$('#itemHistoryDetails').open
      && $('#itemHistorySummary').textContent === 'Nt' && $('#itemHistoryMore').textContent === '(+1 line)'
      && !$('#itemHistoryDescription').checkVisibility()`);
    await click(`$('#itemHistoryDetails summary')`);
    await waitFor('description in the dialog', `visible($('#itemHistoryDescription'))
      && $('#itemHistoryDescription').textContent === 'Nt\\nMachine Type: Plasma Forge, DTPF\\nGregTech'`);
    await click(`$('#itemHistoryShiftBtn')`);
    await waitFor('dialog Shift text', `$('#itemHistoryDescription').textContent === 'Structure: 33x24x33\\nGregTech'
      && $('#itemHistoryShiftBtn').getAttribute('aria-pressed') === 'true'`);
    await click(`byText('.history-ore-name', 'ingotNeutronium')`);
    await waitFor('searched by ore name', `!visible($('#itemHistoryModal')) && $('#networkSearch').value === '$ingotNeutronium'
      && $$('#networkList .network-cell').length === 1 && $('#networkSearchHighlight .search-hl-ore')`);
    // Opened stays opened, for the next item too.
    await click(`$('#networkList .network-cell')`);
    await waitFor('still open', `visible($('#itemHistoryDescription')) && $('#itemHistoryDetails').open`);
    await click(`$('#itemHistoryDetails summary')`);
    await waitFor('closed again', `!$('#itemHistoryDetails').open`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('dialog closed again', `!visible($('#itemHistoryModal'))`);
    // Plain words match descriptions too, as the game's terminal does.
    await evaluate(`typeInto('#networkSearch', 'plasma forge'), true`);
    await waitFor('found by plain words in its description', `$$('#networkList .network-cell').length === 1
      && $('#networkList .network-cell .network-cell-qty').textContent === '5'`);
    // = searches formulas with plain digits, as GregTech's NEI search does.
    await evaluate(`typeInto('#networkSearch', '=h2o'), true`);
    await waitFor('found by its formula', `$$('#networkList .network-cell').length === 1
      && $('#networkList .network-cell').getAttribute('aria-label').startsWith('Water')
      && $('#networkSearchHighlight .search-hl-formula')`);
    await evaluate(`typeInto('#networkSearch', ''), true`);

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

  step('admin: check the patterns against another version\'s recipes', async () => {
    await click(`$('#settingsBtn')`);
    await click(`$('#gamedataBtn')`);
    await waitFor('both versions listed', `$$('#gamedataVersion option').some((o) => o.value === '${E2E_NEXT_VERSION}')`);
    // The version in use, with none before it: nothing to compare.
    await waitFor('nothing to compare', `$('#recipeCheckBtn').disabled
      && $('#recipeCheckFor').textContent.startsWith('Pick a version other than the one in use')`);
    await evaluate(`$('#gamedataVersion').value = '${E2E_NEXT_VERSION}', $('#gamedataVersion').dispatchEvent(new Event('change')), true`);
    await waitFor('compares the two', `!$('#recipeCheckBtn').disabled
      && $('#recipeCheckFor').textContent === "Compares GTNH ${E2E_DATA_VERSION}'s recipes with ${E2E_NEXT_VERSION}'s."`);
    await click(`$('#recipeCheckBtn')`);
    // Only the other version's recipes are downloaded, in the background.
    await waitFor('checked', `visible($('#recipeCheckModal'))
      && $('#recipeCheckSummary').textContent.startsWith('From GTNH ${E2E_DATA_VERSION} to ${E2E_NEXT_VERSION}: 1 broken, 0 moved')`);
    await waitFor('the Macerator pattern broke', `(() => {
      const row = $('.recipe-check-row');
      return row && row.querySelector('.recipe-check-provider').textContent === 'Macerator'
        && $$('.recipe-label', row).map((l) => l.textContent).join('|') === 'Before (${E2E_DATA_VERSION})|Now (${E2E_NEXT_VERSION})'
        && $$('.recipe-block', row)[1].textContent.includes('2xNeutronium Ingot');
    })()`);
    await waitFor('the in-use version is unchanged', `$('#gamedataCurrent').textContent.startsWith('In use: GTNH ${E2E_DATA_VERSION}')`);
    // Escape closes the check first, then Game data.
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('check closed', `!visible($('#recipeCheckModal')) && visible($('#gamedataModal'))`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('game data closed', `!visible($('#gamedataModal'))`);
    // The settings menu's Pattern check (operators' too) is the last
    // upgrade's only, and the game data was never switched.
    await click(`$('#settingsBtn')`);
    await click(`$('#patternCheckBtn')`);
    await waitFor('no upgrade yet', `visible($('#recipeCheckModal'))
      && $('#recipeCheckError').textContent.startsWith("There's no upgrade to check yet")`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('check closed again', `!visible($('#recipeCheckModal'))`);
  });

  step('admin: a notice when the game data looks older than the game', async () => {
    // The seeded patterns' GregTech items: only E2E_NEXT_VERSION's item
    // list has them.
    await waitFor('notice', `visible($('#gamedataNotice'))
      && $('#gamedataNoticeText').textContent === "Your network has an item type the GTNH ${E2E_DATA_VERSION} game data doesn't know (Iron Dust), so the network scanner skips it. It's in ${E2E_NEXT_VERSION}."
      && $('#gamedataNoticeSwitch').textContent === 'Switch to ${E2E_NEXT_VERSION}\u2026'`);
    await click(`$('#gamedataNoticeSwitch')`);
    await waitFor('Game data, the version picked, why', `visible($('#gamedataModal'))
      && $('#gamedataVersion').value === '${E2E_NEXT_VERSION}'
      && $('#gamedataCurrent').textContent.startsWith('In use: GTNH ${E2E_DATA_VERSION}')
      && visible($('#gamedataUnknown'))
      && $('#gamedataUnknownSummary').textContent === "1 item type your network has isn't in this game data"
      && $('#gamedataUnknownList').textContent === 'Iron Dust gregtech:gt.metaitem.01, since ${E2E_NEXT_VERSION}'`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('game data closed', `!visible($('#gamedataModal'))`);
    await click(`$('[data-action="dismiss-gamedata-notice"]')`);
    await waitFor('closed', `!visible($('#gamedataNotice'))`);
    await cdp.send('Page.reload', {});
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('signed in', `$('#settingsUser').textContent !== ''`);
    await sleep(1000);
    await waitFor('still closed for that suggestion', `!visible($('#gamedataNotice'))`);
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
    await waitFor('signed out', `visible($('#authBtn')) && $('#settingsUser').textContent === ''`);
    // The settings menu stays, with just the Theme.
    await click(`$('#settingsBtn')`);
    await waitFor('theme-only menu', `visible($('#settingsMenu')) && visible($('.theme-row'))
      && !visible($('#settingsUser')) && !visible($('#notifToggle')) && !visible($('#signOutBtn'))
      && !visible($('#assistantBtn'))`);
    await evaluate(`pressKey('Escape'), true`);
    // Only an admin renames machines.
    await click(`$('#tabBtnPower')`);
    await waitFor('no rename button', `visible($('#powerTab')) && $('#powerCurrent .big') && !visible($('#powerRenameBtn'))`);
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

  step('a link to a craft: its details\' URL, copied from them, opens them while it runs, says how it went after', async () => {
    const load = async (url) => {
      await cdp.send('Page.navigate', { url });
      await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
      await evaluate(PAGE_LIB);
    };
    const stubClipboard = () => evaluate(`navigator.clipboard.writeText = async (text) => { window.copied = text; }, true`);
    await postCrafts(base, true);
    await load(base + '/crafts');
    await waitFor('W01 busy, with no link button on its card', `card('W01')?.querySelector('.ingredients-btn')
      && !$('#root [data-action="copy-craft-link"]')`);
    await click(`card('W01').querySelector('.ingredients-btn')`);
    await waitFor('its details at its link', `visible($('#ingredientsModal')) && visible($('#ingredientsLinkBtn'))
      && location.pathname === '/crafts' && new URLSearchParams(location.search).get('cpu') === 'W01'
      && Number(new URLSearchParams(location.search).get('job')) > 0`);
    await stubClipboard();
    await click(`$('#ingredientsLinkBtn')`);
    const link = await waitFor('copied', `window.copied`);
    if (link !== await evaluate('location.href')) throw new Error('copied ' + link);
    await waitFor('said so', `$$('.toast').some((t) => t.textContent === 'Link to this craft copied.')`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed, back at /crafts', `!visible($('#ingredientsModal')) && location.pathname + location.search === '/crafts'`);

    await load(link);
    await waitFor('the link opens its details', `visible($('#ingredientsModal')) && $('#ingredientsCpu').textContent.startsWith('CPU W01 ')
      && card('W01')?.classList.contains('linked')`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed, done with the link', `!visible($('#ingredientsModal')) && location.pathname + location.search === '/crafts'
      && !$('#root .card.linked') && $('#jobLinkNote').hidden`);

    await load(link);
    await waitFor('open again', `visible($('#ingredientsModal'))`);
    await postCrafts(base, false);
    await waitFor('how it went, behind the details', `!$('#jobLinkNote').hidden
      && $('#jobLinkNoteText').textContent.includes('Iron Ingot') && $('#jobLinkNoteText').textContent.includes('on CPU W01')`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('closed onto the note, at the link', `!visible($('#ingredientsModal')) && !$('#jobLinkNote').hidden
      && location.search.includes('job=')`);
    await postCrafts(base, true);
    await waitFor('the next job isn\'t the link\'s', `card('W01')?.querySelector('.progress-fill') && !$('#root .card.linked')`);
    await click(`$('#jobLinkNote .note-dismiss')`);
    await waitFor('dismissed', `$('#jobLinkNote').hidden && location.search === '' && location.pathname === '/crafts'`);

    // The item dialog copies its URL the same way.
    await click(`card('W01').querySelector('.item-history-link')`);
    await waitFor('item open', `visible($('#itemHistoryModal')) && location.pathname.startsWith('/network/item/')`);
    await stubClipboard();  // the page has been reloaded since
    await click(`$('#itemHistoryModal [data-action="copy-item-link"]')`);
    await waitFor('item link copied', `window.copied === location.href
      && $$('.toast').some((t) => t.textContent === 'Link to this item copied.')`);
    await click(`$('#itemHistoryModal .history-close-btn')`);
    await waitFor('item closed', `!visible($('#itemHistoryModal'))`);

    // On a CPU with no history: on W01, the first job the server saw was
    // already running then, so it's taken as an earlier link's job.
    await load(base + '/crafts?cpu=W09&job=1000');
    await waitFor('a link to nothing says so', `!$('#jobLinkNote').hidden
      && $('#jobLinkNoteText').textContent.includes("how it went wasn't recorded")`);
    await click(`$('#tabBtnPower')`);
    await waitFor('another tab leaves the link', `$('#jobLinkNote').hidden && location.pathname === '/power'`);
  });

  step('a private server: signed out shows only a notice and fetches nothing; signing in shows it all', async () => {
    await evaluate(`typeInto('#accessTokenInput', ${JSON.stringify(adminToken)})`);
    await click(`$('#authBtn')`);
    await waitFor('signed in', `$('#settingsUser').textContent !== ''`);
    await click(`$('#settingsBtn')`);
    await click(`$('#adminBtn')`);
    await waitFor('admin dialog, still public', `visible($('#adminUsersModal')) && !$('#privateToggle').checked`);
    await click(`$('#privateToggle')`);
    await waitFor('saved', `$('#toastContainer').textContent.includes('Only signed-in users can see this base now.')`);
    await click(`$('#adminUsersModal .history-close-btn')`);

    await click(`$('#settingsBtn')`);
    await click(`byText('#settingsMenu button', 'Sign out')`);
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('the notice instead of the tabs', `visible($('#privateNotice')) && !visible($('.tab-bar')) && !visible($('#powerTab')) && visible($('#authBtn'))`);
    await sleep(1500);
    const fetched = await evaluate(`performance.getEntriesByType('resource').map((e) => new URL(e.name).pathname)
      .filter((p) => ['/api/crafts', '/api/power', '/api/stock/rules'].includes(p)).length`);
    if (fetched) throw new Error(`fetched the base's data ${fetched} times while signed out`);
    if ((await fetch(base + '/api/crafts')).status !== 401) throw new Error('/api/crafts answered signed out');

    await evaluate(`typeInto('#accessTokenInput', ${JSON.stringify(adminToken)})`);
    await evaluate(`$('#signInForm').requestSubmit(), true`);
    await waitFor('the open tab, filled in', `!visible($('#privateNotice')) && visible($('#powerTab')) && $('#powerTab').textContent.includes('EU')`);
    await click(`$('#tabBtnCrafts')`);
    await waitFor('crafts too', `card('W01')`);

    await click(`$('#settingsBtn')`);
    await click(`$('#adminBtn')`);
    await waitFor('ticked', `visible($('#adminUsersModal')) && $('#privateToggle').checked`);
    await click(`$('#privateToggle')`);
    await waitFor('public again', `$('#toastContainer').textContent.includes('Anyone with the link can see this base now.')`);
    await click(`$('#adminUsersModal .history-close-btn')`);
    if ((await fetch(base + '/api/crafts')).status !== 200) throw new Error('/api/crafts still refused');
  });

  // Last, as its session cookie replaces the main server's (cookies
  // don't tell ports apart).
  step('first-time setup through the setup link on a server with no users', async () => {
    // A release, older than the fake GitHub's latest (v1.2.0).
    const fresh = await startServer({
      GAMEDATA_API_URL: github, GAMEDATA_REPO: 'e2e/repo', SCRIPTS_REPO: 'e2e/scripts', GCM_VERSION: 'v1.1.0',
    });
    if (!fresh.setupCode) throw new Error('no setup link in the server log');
    await cdp.send('Page.navigate', { url: `${fresh.base}/#setup=${fresh.setupCode}` });
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await waitFor('setup dialog, code filled in', `visible($('#setupModal')) && $('#setupCode').value === ${JSON.stringify(fresh.setupCode)}`);
    // Nothing has reported: signed out, the Crafts tab says to ask an admin.
    await waitFor('no script yet, ask an admin', `$('#root .connect-note')?.textContent.includes('craft_monitor.lua')
      && visible($('#root .connect-note .non-admin-only')) && !visible($('#root [data-action="connect-computer"]'))
      && !visible($('#staleBanner'))`);
    await waitFor('code taken out of the address bar', `location.hash === ''`);
    await evaluate(`typeInto('#setupName', 'Ann'), typeInto('#setupCode', 'wrong'), true`);
    await click(`$('#setupBtn')`);
    await waitFor('wrong code refused', `visible($('#setupError')) && $('#setupError').textContent === 'wrong setup code'`);
    await evaluate(`typeInto('#setupCode', ${JSON.stringify(fresh.setupCode)})`);
    await evaluate(`pressEnter('#setupCode'), true`);
    await waitFor('token shown', `visible($('#setupResult')) && $('#setupToken').value.startsWith('gcm_')`);
    await click(`$('#setupDoneBtn')`);
    await waitFor('signed in as the new admin', `!visible($('#setupModal')) && $('#settingsUser').textContent === 'Signed in as Ann' && $('#adminBtn').style.display === ''`);
    await waitFor('then on to picking the GTNH version', `visible($('#gamedataModal'))`);
    await click(`$('[data-action="close-gamedata"]')`);
    // A newer release is out: a dot on the settings button, and the menu
    // says which, beside this server's own.
    await waitFor('update dot', `visible($('#settingsUpdateDot'))`);
    await click(`$('#settingsBtn')`);
    await waitFor('menu: version, and the newer one at Status', `visible($('#settingsMenu'))
      && $('#settingsVersion').textContent === 'v1.1.0' && $('#settingsUser').textContent === 'Signed in as Ann'
      && visible($('#agentsBtnNote')) && $('#agentsBtnNote').textContent === 'v1.2.0 is out'`);
    await click(`$('#settingsBtn')`);
    await waitFor('menu closed', `!visible($('#settingsMenu'))`);
    // An admin gets the button, which opens Status at Connect a computer.
    await waitFor('connect button', `visible($('#root [data-action="connect-computer"]'))
      && !visible($('#root .connect-note .non-admin-only'))`);
    await click(`$('#root [data-action="connect-computer"]')`);
    await waitFor('Status at Connect a computer', `visible($('#agentsModal')) && (() => {
      const heading = $('#agentsModal .connect-heading').getBoundingClientRect();
      const body = $('#agentsModal .status-body').getBoundingClientRect();
      return heading.top >= body.top - 1 && heading.bottom <= body.bottom + 1;
    })()`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('Status closed', `!visible($('#agentsModal'))`);
    // Power and Network say the same, in place of an empty chart or grid.
    await click(`$('#tabBtnPower')`);
    await waitFor('power: no script yet', `visible($('#powerConnectNote [data-action="connect-computer"]'))
      && !visible($('#powerChartWrap')) && !visible($('#powerEmpty'))`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('network: no script yet', `visible($('#networkEmpty [data-action="connect-computer"]'))`);

    // A network scan that says there's no ME Interface Terminal: admins
    // get a note on the Network tab, and the craft dialog says why
    // there's no plan.
    const { data: scan } = await game(fresh.base, '/api/network/scan/start', {});
    await game(fresh.base, '/api/network/scan/batch', { scan_token: scan.scan_token, items: SEEDED_ITEMS.map(it => ({ ...it })) });
    await game(fresh.base, '/api/network/scan/finish',
      { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0, interface_terminal: false });
    // The tab refetches every minute, and on being opened.
    await click(`$('#tabBtnPower')`);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('terminal note', `visible($('#networkTerminalNote'))
      && $('#networkTerminalHow').href.endsWith('in-game.md#me-interface-terminal')`);
    await evaluate(`openItem('Iron Ingot'), true`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('plan note with a link', `visible($('#craftRequestModal'))
      && $('#craftPlanNote').textContent.includes('needs an ME Interface Terminal')
      && $('#craftPlanNote a').href.endsWith('in-game.md#me-interface-terminal')`);
    await click(`byText('#craftRequestModal button', 'Cancel')`);
    // Game data's pattern check says it before the button's pressed.
    await click(`$('#settingsBtn')`);
    await click(`$('#gamedataBtn')`);
    await waitFor('pattern check needs a terminal', `visible($('#gamedataModal'))
      && $('#recipeCheckFor').textContent.startsWith('The pattern check is built from your patterns, and needs an ME Interface Terminal')
      && visible($('#recipeCheckForHow')) && $('#recipeCheckForHow').href.endsWith('in-game.md#me-interface-terminal')
      && $('#recipeCheckBtn').disabled`);
    await evaluate(`pressKey('Escape'), true`);
    await waitFor('game data closed', `!visible($('#gamedataModal'))`);
    await click(`$('[data-action="dismiss-terminal-note"]')`);
    await waitFor('note closed', `!visible($('#networkTerminalNote'))`);
    await cdp.send('Page.reload', {});
    await waitFor('reloaded', `document.readyState === 'complete' && !window.byText`);
    await evaluate(PAGE_LIB);
    await click(`$('#tabBtnNetwork')`);
    await waitFor('items back', `$$('#networkList .network-cell').length === ${SEEDED_ITEMS.length}`);
    await sleep(500);
    await waitFor('still closed after a reload', `!visible($('#networkTerminalNote'))`);
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
