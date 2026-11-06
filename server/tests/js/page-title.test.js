import test from 'node:test';
import assert from 'node:assert/strict';
import { craftView, dotHref, faviconDot, pageTitle } from '../../static/js/page-title.js';
import { craftsBadge, powerBadge, stockBadge } from '../../static/js/tab-badges.js';

const finished = craftsBadge([{ busy: true }], [{ id: 'a' }, { id: 'b' }]);
const busy = craftsBadge([{ busy: true }], []);
const low = stockBadge(3);
const power = powerBadge({ stored: 5, capacity: 100 }, null);

test('pageTitle counts what wants attention, not busy crafts', () => {
  assert.equal(pageTitle('', {}), 'GTNH Monitor');
  assert.equal(pageTitle('Power', { crafts: busy, power: null }), 'Power · GTNH Monitor');
  assert.equal(pageTitle('Crafts', { crafts: finished }), '(2) Crafts · GTNH Monitor');
  assert.equal(pageTitle('Network', { crafts: finished, network: low, power }), '(6) Network · GTNH Monitor');
});

test('faviconDot: an alert beats finished crafts', () => {
  assert.equal(faviconDot({}), null);
  assert.equal(faviconDot({ crafts: busy }), null);
  assert.equal(faviconDot({ crafts: finished }), 'done');
  assert.equal(faviconDot({ crafts: finished, power }), 'alert');
  assert.equal(faviconDot({ network: low }), 'alert');
});

test('dotHref adds the dot to the icon URL', () => {
  assert.equal(dotHref('/static/icons/favicon.svg?v=abc', null), '/static/icons/favicon.svg?v=abc');
  assert.equal(dotHref('/static/icons/favicon.svg?v=abc', 'done'), '/static/icons/favicon.svg?v=abc&dot=done');
  assert.equal(dotHref('/x.png', 'alert'), '/x.png?dot=alert');
});

test('craftView: the craft dialog shows progress, then that it ended', () => {
  assert.equal(craftView('Iron Ingot', 45, false), '45% Iron Ingot');
  assert.equal(craftView('Iron Ingot', null, false), 'Iron Ingot');
  assert.equal(craftView('Iron Ingot', 45, true), 'Finished · Iron Ingot');
});
