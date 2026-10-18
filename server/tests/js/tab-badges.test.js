import test from 'node:test';
import assert from 'node:assert/strict';
import { craftsBadge, powerBadge, stockBadge } from '../../static/js/tab-badges.js';

test('craftsBadge counts busy CPUs and turns green for finished crafts', () => {
  assert.equal(craftsBadge([], []), null);
  assert.equal(craftsBadge([{ busy: false }], []), null);
  assert.deepEqual(craftsBadge([{ busy: true }, { busy: true }, { busy: false }], []),
    { count: 2, tone: '', label: '2 crafting' });
  assert.deepEqual(craftsBadge([{ busy: true }], [{ id: 'a' }]),
    { count: 1, tone: 'done', label: '1 crafting, 1 finished' });
  // Only finished ones: a dot, no count.
  assert.deepEqual(craftsBadge([], [{ id: 'a' }, { id: 'b' }]),
    { count: null, tone: 'done', label: '2 finished' });
});

test('stockBadge shows only when something is low', () => {
  assert.equal(stockBadge(0), null);
  assert.deepEqual(stockBadge(3), { count: 3, tone: 'alert', label: '3 low' });
});

test('powerBadge warns when power is running out or nearly gone', () => {
  const half = { stored: 50, capacity: 100 };
  assert.equal(powerBadge(null, null), null);
  assert.equal(powerBadge(half, null), null);
  assert.equal(powerBadge(half, { seconds_to_empty: 7200 }), null);
  assert.equal(powerBadge(half, { seconds_to_empty: 1800 }).label, 'running out within the hour');
  assert.equal(powerBadge({ stored: 5, capacity: 100 }, { seconds_to_full: 60, seconds_to_empty: null }).label,
    'under 10% full');
  // An empty buffer of no size isn't "low".
  assert.equal(powerBadge({ stored: 0, capacity: 0 }, null), null);
});
