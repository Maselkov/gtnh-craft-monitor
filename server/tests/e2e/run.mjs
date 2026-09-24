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

import { spawn } from 'node:child_process';
import fs from 'node:fs';
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

async function startServer() {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'gcm-e2e-data-'));
  tempDirs.push(dataDir);
  const lookup = path.join(SERVER_DIR, 'data', 'icons_lookup.json');
  if (fs.existsSync(lookup)) fs.copyFileSync(lookup, path.join(dataDir, 'icons_lookup.json'));
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

  // Rotate the bootstrap token the way the UI would, to get a normal admin token.
  const { response } = await api(base, 'POST', '/api/auth/login', { token: BOOTSTRAP_TOKEN });
  const cookie = response.headers.get('set-cookie').split(';')[0];
  const { data: rotated } = await api(base, 'POST', '/api/auth/rotate-bootstrap', {}, { Cookie: cookie });
  return rotated.token;
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
  window.pressEnter = (sel) => $(sel).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  window.confirm = () => true;
  true;
`;

// ---------------------------------------------------------------- tests

async function main() {
  const base = await startServer();
  const adminToken = await seed(base);
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

  step('icons that fail to load are removed', async () => {
    // The test data dir has no images.zip, so every resolved icon 404s.
    // (Icons inside collapsed <details> are lazy and never load at all.)
    await waitFor('an icon was resolved', `card('W01').querySelector('.craft-title .item-history-link').dataset.icon`);
    await waitFor('broken title icon removed', `!card('W01').querySelector('.craft-title img')`);
  });

  step('sign in with the Enter key', async () => {
    await evaluate(`typeInto('#accessTokenInput', ${JSON.stringify(adminToken)})`);
    await evaluate(`pressEnter('#accessTokenInput')`);
    await waitFor('signed in', `visible($('#settingsWrap')) && $('#settingsUser').textContent.includes('Administrator')`);
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
    await waitFor('dialog closed', `!visible($('#cancelConfirmModal'))`);
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
    const ironCell = `$$('#networkList .network-cell')[networkShownItems.findIndex(it => it.name === 'Iron Ingot')]`;
    await click(ironCell);
    await waitFor('history open', `visible($('#itemHistoryModal')) && $('#itemHistoryName').textContent === 'Iron Ingot'`);
    await waitFor('url', `location.pathname.startsWith('/network/item/minecraft:iron_ingot')`);
    await click(`$('#itemHistoryRangeButtons [data-range="week"]')`);
    await waitFor('week active', `$('#itemHistoryRangeButtons [data-range="week"]').classList.contains('active')`);
    await click(`$('#itemHistoryPinBtn')`);
    await waitFor('item pinned', `$('#itemHistoryPinBtn').classList.contains('pinned')`);
    await click(`$('#itemHistoryCraftBtn')`);
    await waitFor('craft dialog', `visible($('#craftRequestModal')) && $('#craftRequestName').textContent.includes('Iron Ingot')`);
    await evaluate(`typeInto('#craftRequestAmount', '4+3*2')`);
    await waitFor('amount preview', `/\\b10\\b/.test($('#craftRequestAmountPreview').textContent)`);
    await click(`$('#craftRequestSubmitBtn')`);
    await waitFor('craft dialog closed', `!visible($('#craftRequestModal'))`);
    // Requesting a craft closes the history popup on the way.
    await waitFor('history closed too', `!visible($('#itemHistoryModal'))`);
  });

  step('item history closes via its button and via the backdrop', async () => {
    const ironCell = `$$('#networkList .network-cell')[networkShownItems.findIndex(it => it.name === 'Iron Ingot')]`;
    await click(ironCell);
    await waitFor('history open', `visible($('#itemHistoryModal'))`);
    await click(`$$('#itemHistoryModal button').find(b => b.textContent.trim() === '×' || b.textContent.trim() === 'Close')`);
    await waitFor('closed by button', `!visible($('#itemHistoryModal')) && location.pathname === '/network'`);
    await click(ironCell);
    await waitFor('history open again', `visible($('#itemHistoryModal'))`);
    await click(`$('#itemHistoryModal')`);
    await waitFor('closed by backdrop', `!visible($('#itemHistoryModal')) && location.pathname === '/network'`);
  });

  step('failed craft request can be dismissed', async () => {
    await waitFor('request pending', `$('#craftRequestsSection .craft-request-card')`);
    await api(base, 'GET', '/api/craft/requests/pending', undefined, { 'X-API-Key': API_KEY });
    await api(base, 'POST', '/api/craft/requests/1/result', { status: 'failed', reason: 'e2e says no' }, { 'X-API-Key': API_KEY });
    await waitFor('failure shown', `$('#craftRequestsSection .craft-request-card.failed')?.textContent.includes('e2e says no')`);
    await click(`$('#craftRequestsSection .craft-request-dismiss')`);
    await waitFor('dismissed', `!$('#craftRequestsSection .craft-request-card')`);
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
    await click(`byText('button', 'History', ${row})`);
    await waitFor('history dialog', `visible($('#userHistoryModal')) && $('#userHistoryTitle').textContent === 'E2E Tester history'`);
    await click(`$$('#userHistoryModal button').at(-1)`);
    await waitFor('history closed', `!visible($('#userHistoryModal'))`);
    await click(`byText('button', 'New token', ${row})`);
    await waitFor('new token', `$('#adminTokenValue').value !== ${JSON.stringify(firstToken)}`);
    await waitFor('one token row', `${row}.querySelectorAll('.admin-revoke-btn').length === 2`);
    await click(`${row}.querySelector('.admin-token-row .admin-revoke-btn')`);
    await waitFor('token revoked', `${row}?.textContent.includes('No active tokens')`);
    await click(`byText('button', 'Delete', ${row})`);
    await waitFor('user deleted', `!(${row})`);
    await click(`$$('#adminUsersModal button').find(b => b.textContent.trim() === 'Close' || b.textContent.trim() === '×')`);
    await waitFor('admin dialog closed', `!visible($('#adminUsersModal'))`);
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

  let failed = 0;
  for (const { name, fn } of steps) {
    const before = errors.length;
    try {
      await fn();
      if (errors.length > before) throw new Error('page errors:\n  ' + errors.slice(before).join('\n  '));
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
