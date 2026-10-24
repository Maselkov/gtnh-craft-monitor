import test from 'node:test';
import assert from 'node:assert/strict';
import { connectCommand, isLocalAddress, renderComputer, renderHealth } from '../../static/js/agents.js';

const COMPUTER = {
  address: '1f3a9c20-5b1e-4d2a', version: 'v1.0.0', behind: true, offline: false,
  services: [{ name: 'craft_monitor', running: true }, { name: 'network<b>', running: false }],
  free_memory: 1048576, total_memory: 4194304, uptime: 3700, last_seen: Date.now() / 1000, command: null,
};

test('a computer: version, services, memory, buttons', () => {
  const html = renderComputer(COMPUTER, 'v1.2.0');
  assert.match(html, /Computer 1f3a9c20/);
  assert.match(html, /v1\.0\.0 &middot; update available: v1\.2\.0/);
  assert.match(html, /agent-dot running"><\/span>craft_monitor</);
  assert.match(html, /network&lt;b&gt; \(stopped\)/);
  assert.match(html, /data-fill="75"/);
  assert.match(html, /up 1h 1m/);
  assert.doesNotMatch(html, /disabled/);
});

test('buttons wait while a command is under way, and offline', () => {
  const busy = renderComputer({ ...COMPUTER, command: { action: 'reboot', ref: null, status: 'sent' } }, 'v1.2.0');
  assert.match(busy, /Reboot: running…/);
  assert.equal(busy.match(/ disabled/g).length, 3);
  const failed = renderComputer({ ...COMPUTER, command: { action: 'update', ref: 'main', status: 'done', ok: false, message: 'HTTP 404' } });
  assert.match(failed, /agent-command failed">Update to main: HTTP 404/);
  assert.equal(renderComputer({ ...COMPUTER, offline: true }).match(/ disabled/g).length, 3);
});

const HEALTH = {
  uptime_seconds: 90061, gamedata: '2.7.2',
  feeds: [
    { key: 'crafts', label: 'Crafting CPUs', script: 'craft_monitor.lua', age_seconds: 3, ok: true, cpus: 4 },
    { key: 'network', label: 'ME network', script: 'network_browser.lua', age_seconds: 4000, ok: false, items: 8123,
      scan_seconds: 95, scanning_for: null, levels_age_seconds: 30, crash: null, reconstructed: false,
      rejected: { at: 1, reason: 'incomplete (2/3 chunks, 0 errors)' } },
    { key: 'power:', label: 'Power: Main', script: 'power_monitor.lua', age_seconds: null, ok: false },
  ],
  storage: { files: [{ name: 'app.db', bytes: 2500000 }], free_bytes: 1.2e11, total_bytes: 5e11 },
  retention: { enabled: true, last_run: null },
  push_subscriptions: 1,
  webhooks: { configured: 2, last: { age_seconds: 120, sent: 1, failed: [{ host: 'bad.test', error: 'refused' }] } },
};

test('server health: each feed, storage, background jobs', () => {
  const html = renderHealth(HEALTH);
  assert.match(html, /agent-dot running"><\/span><span class="health-label">Crafting CPUs/);
  assert.match(html, /updated 3s ago &middot; 4 CPUs/);
  assert.match(html, /agent-dot warn"><\/span><span class="health-label">ME network/);
  assert.match(html, /8,123 entries &middot; scan took 1m 35s/);
  assert.match(html, /last scan turned away: incomplete \(2\/3 chunks, 0 errors\)/);
  assert.match(html, /nothing from power_monitor\.lua yet/);
  assert.match(html, /app\.db 2\.50MB/);
  assert.match(html, /hasn&#39;t run since the server started/);
  assert.match(html, /1 browser with push &middot; 2 webhooks, last post 2m 0s ago - failed: bad\.test \(refused\)/);
  assert.match(html, /Up 1d 1h &middot; game data 2\.7\.2/);
});

test('connect command: download, then install with the address (no trailing slash) and key', () => {
  assert.equal(connectCommand(' https://gtnh.example.com/ ', 'ab12'),
    'wget -f https://raw.githubusercontent.com/Maselkov/gtnh-craft-monitor/main/oc/gcm.lua /tmp/gcm.lua'
    + ' && /tmp/gcm.lua install --server=https://gtnh.example.com --api-key=ab12');
  assert.ok(connectCommand('http://h:8420', 'my key;x').endsWith(' --server=http://h:8420 --api-key="my key;x"'));
});

test('local addresses are the ones OpenComputers blocks by default', () => {
  for (const url of ['http://localhost:8420', 'http://127.0.0.1:8420', 'http://10.1.2.3', 'http://192.168.1.20:8420', 'http://172.20.0.5']) {
    assert.equal(isLocalAddress(url), true, url);
  }
  for (const url of ['https://gtnh.example.com', 'http://172.32.0.1', 'http://8.8.8.8', 'not a url']) {
    assert.equal(isLocalAddress(url), false, url);
  }
});
