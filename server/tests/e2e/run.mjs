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
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

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
files = {}
for name in ("images.zip", "images-faithful32.zip", "icons_lookup.json", "item_catalog.txt"):
    data = open(out + "/" + name, "rb").read()
    files[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
open(out + "/data.json", "w").write(json.dumps({"format": 1, "files": files, "generated_at": "2026-09-25T00:00:00Z"}))
`;
const BUNDLE_FILES = ['data.json', 'images.zip', 'images-faithful32.zip', 'icons_lookup.json', 'item_catalog.txt'];

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

function postCrafts(base, w01Busy) {
  return game(base, '/api/crafts', {
    source: 'me_controller',
    jobs: [
      w01Busy
        ? {
            name: 'W01', busy: true, final_output: 'Iron Ingot', final_output_mod: 'minecraft',
            final_output_internal: 'iron_ingot', final_output_damage: 0, progress_percent: 40,
            active: [{ name: 'Iron Ingot', size: 3 }], pending: [{ name: 'Iron Ore', size: 5 }], stored: [],
          }
        : { name: 'W01', busy: false, progress_percent: 100 },
      { name: 'W02', busy: false },
    ],
  });
}

async function seed(base) {
  await postCrafts(base, true);
  for (const stored of [100000, 200000, 300000]) {
    await game(base, '/api/power', { stored, capacity: 1000000, avg_eu_in_5s: 50, avg_eu_out_5s: 20 });
  }
  const { data: scan } = await game(base, '/api/network/scan/start', {});
  await game(base, '/api/network/scan/batch', {
    scan_token: scan.scan_token,
    items: [
      { mod: 'minecraft', internal: 'iron_ingot', damage: 0, name: 'Iron Ingot', size: 1234, isCraftable: true },
      { mod: 'gregtech', internal: 'gt.metaitem.01', damage: 11028, name: 'Neutronium Ingot', size: 5, isCraftable: false },
      { internal: 'water', kind: 'fluid', name: 'Water', size: 64000 },
    ],
  });
  await game(base, '/api/network/scan/finish', { scan_token: scan.scan_token, chunks_sent: 1, total_errors: 0 });
}

// ---------------------------------------------------------------- browser

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
  window.card = (cpu) => $$('#root .card').find((c) => c.querySelector('.cpu-id')?.textContent.includes('CPU ' + cpu + ' '));
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

  step('notifications button asks for permission', async () => {
    await cdp.send('Browser.grantPermissions', { origin: base, permissions: ['notifications'] });
    await waitFor('not yet enabled', `$('#notifBtn').textContent === 'Enable notifications'`);
    await click(`$('#notifBtn')`);
    await waitFor('enabled', `$('#notifBtn').textContent === 'Notifications on'`);
  });

  step('expand ingredients; stays open across a refresh', async () => {
    await waitFor('ingredients', `card('W01').querySelector('details.ingredients summary')`);
    await click(`card('W01').querySelector('details.ingredients summary')`);
    await waitFor('open', `card('W01').querySelector('details.ingredients').open`);
    await sleep(3500);  // the crafts tab re-renders every 3s
    await waitFor('still open after re-render', `card('W01').querySelector('details.ingredients').open`);
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
    await waitFor('pinned', `byText('.group-heading', '📌 Pinned (1)') && card('W01').querySelector('.pin-btn.pinned')`);
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
    await click(`$('#powerTab [data-range="week"]')`);
    await waitFor('week active', `$('#powerTab [data-range="week"]').classList.contains('active')`);
  });

  step('network tab, search and sort', async () => {
    await click(`$('#tabBtnNetwork')`);
    await waitFor('3 cells', `location.pathname === '/network' && $$('#networkList .network-cell').length === 3`);
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

  step('sign out', async () => {
    await click(`$('#settingsBtn')`);
    await click(`byText('#settingsMenu button', 'Sign out')`);
    await waitFor('signed out', `visible($('#authBtn')) && !visible($('#settingsWrap'))`);
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
