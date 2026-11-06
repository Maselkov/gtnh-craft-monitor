import test from 'node:test';
import assert from 'node:assert/strict';
import { craftsBadge, powerBadge, stockBadge } from '../../static/js/tab-badges.js';

test('craftsBadge counts busy CPUs and turns green for finished crafts', () => {
  assert.equal(craftsBadge([], []), null);
  assert.equal(craftsBadge([{ busy: false }], []), null);
  assert.deepEqual(craftsBadge([{ busy: true }, { busy: true }, { busy: false }], []),
    { count: 2, tone: '', label: '2 crafting', attention: 0 });
  assert.deepEqual(craftsBadge([{ busy: true }], [{ id: 'a' }]),
    { count: 1, tone: 'done', label: '1 crafting, 1 finished', attention: 1 });
  // Only finished ones: a dot, no count.
  assert.deepEqual(craftsBadge([], [{ id: 'a' }, { id: 'b' }]),
    { count: null, tone: 'done', label: '2 finished', attention: 2 });
});

test('stockBadge shows only when something is low', () => {
  assert.equal(stockBadge(0), null);
  assert.deepEqual(stockBadge(3), { count: 3, tone: 'alert', label: '3 low', attention: 3 });
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

test('powerBadge warns about the wireless network only when it empties within a day', () => {
  const wireless = { stored: 6.5e16, capacity: 0 };
  assert.equal(powerBadge(wireless, null), null);
  assert.equal(powerBadge(wireless, { seconds_to_empty: 30 * 3600 }), null);
  assert.equal(powerBadge(wireless, { seconds_to_empty: 20 * 3600 }).label, 'running out within a day');
});

test('powerBadge measures an LSC in wireless mode by its threshold', () => {
  const now = 10000;
  const wireless = { stored: 6.5e16, capacity: 0 };
  const lsc = (stored, t = now) => ({ name: 'LSC', t, stored, capacity: 9.2e18, wireless_threshold: 6e15 });
  // Under 1% of its capacity, but right at its threshold: fine.
  assert.equal(powerBadge(lsc(6e15), null, [lsc(6e15)], now), null);
  assert.equal(powerBadge(lsc(6.3e15), null, [lsc(6.3e15)], now), null);
  // Drained between refills, whichever machine is the default.
  assert.equal(powerBadge(wireless, null, [lsc(3e14), { ...wireless, t: now }], now).label,
    'LSC under 10% of its wireless threshold');
  assert.equal(powerBadge(lsc(3e14), null, [lsc(3e14)], now).label, 'LSC under 10% of its wireless threshold');
  // An LSC that says it's not in wireless mode still warns when low, even
  // with the wireless network the default; one on an older script doesn't
  // say, so its capacity (meaningless in wireless mode) isn't judged.
  const normal = { name: 'LSC', t: now, stored: 5, capacity: 100, wireless_mode: false };
  assert.equal(powerBadge(wireless, null, [normal], now).label, 'LSC under 10% full');
  assert.equal(powerBadge(wireless, null, [{ ...normal, wireless_mode: null }], now), null);
  assert.equal(powerBadge(wireless, null, [{ ...normal, stored: 50 }], now), null);
  assert.equal(powerBadge(wireless, null, [{ ...normal, name: '', t: now - 600 }], now), null);
  assert.equal(powerBadge(wireless, null, [{ ...normal, name: '' }], now).label, 'Main under 10% full');
  // Draining between refills isn't running out.
  assert.equal(powerBadge(lsc(5e15), { seconds_to_empty: 600 }, [lsc(5e15)], now), null);
  // A reading that's gone stale doesn't count.
  assert.equal(powerBadge(wireless, null, [lsc(3e14, now - 600)], now), null);
});

test('powerBadge says which machine it is about, for its button', () => {
  const now = 10000;
  const wireless = { name: 'Wireless', stored: 6.5e16, capacity: 0 };
  assert.equal(powerBadge(wireless, { seconds_to_empty: 3600 }, [], now).machine, 'Wireless');
  const lsc = { name: 'LSC', t: now, stored: 3e14, capacity: 9.2e18, wireless_threshold: 6e15 };
  assert.equal(powerBadge(wireless, null, [lsc], now).machine, 'LSC');
  const main = { name: '', stored: 5, capacity: 100 };
  assert.equal(powerBadge(main, null, [], now).machine, '');
});
