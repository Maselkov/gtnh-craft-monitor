import test from 'node:test';
import assert from 'node:assert/strict';
import { fillText, powerEtaText, powerFill } from '../../static/js/power.js';

test('powerEtaText leads with the ETA, then the hourly rate', () => {
  assert.equal(powerEtaText(null), '');
  assert.equal(powerEtaText({ per_second: 0, seconds_to_full: null, seconds_to_empty: null }), '');
  assert.equal(
    powerEtaText({ per_second: 1e6, seconds_to_full: 3 * 3600 + 300, seconds_to_empty: null }),
    'Full in 3h 5m \u00b7 +3.60G EU/h over the past hour');
  assert.equal(
    powerEtaText({ per_second: -1e6 / 3600, seconds_to_full: null, seconds_to_empty: 2400 }),
    'Empty in 40m 0s · −1.00M EU/h over the past hour');
  // Draining too slowly to matter: no ETA, just the rate.
  assert.equal(
    powerEtaText({ per_second: -1, seconds_to_full: null, seconds_to_empty: null }),
    '−3.6k EU/h over the past hour');
});

test('powerFill weighs each machine by its capacity', () => {
  const now = 10000;
  assert.equal(powerFill([], now), null);
  assert.equal(powerFill([{ t: now, stored: 0, capacity: 0 }], now), null);
  // A full 100T LSC and an empty 1M buffer: nearly full, not half.
  const fill = powerFill([
    { t: now, stored: 100e12, capacity: 100e12 },
    { t: now, stored: 0, capacity: 1e6 },
  ], now);
  assert.ok(fill > 0.999 && fill < 1);
  // One that stopped reporting is left out...
  assert.equal(powerFill([
    { t: now, stored: 500, capacity: 1000 },
    { t: now - 3600, stored: 0, capacity: 1e9 },
  ], now), 0.5);
  // ...unless none are reporting, when the last readings still say something.
  assert.equal(powerFill([{ t: now - 3600, stored: 250, capacity: 1000 }], now), 0.25);
});

test('fillText rounds, without calling a nearly empty one empty', () => {
  assert.equal(fillText(null), '');
  assert.equal(fillText(0), '0%');
  assert.equal(fillText(0.001), '<1%');
  assert.equal(fillText(0.614), '61%');
  assert.equal(fillText(1), '100%');
});
