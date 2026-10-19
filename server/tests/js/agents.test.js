import test from 'node:test';
import assert from 'node:assert/strict';
import { renderComputer } from '../../static/js/agents.js';

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
