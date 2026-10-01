import test from 'node:test';
import assert from 'node:assert/strict';
import { powerEtaText } from '../../static/js/power.js';

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
