import test from 'node:test';
import assert from 'node:assert/strict';
import {
  compactEU, fillText, formatEU, machineButtonText, machineFill, orderMachines, powerEtaText, powerFill,
  powerTabText, wirelessLscText,
} from '../../static/js/power.js';

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
  // The wireless network has no capacity, so it doesn't count.
  assert.equal(powerFill([
    { t: now, stored: 500, capacity: 1000 },
    { t: now, stored: 2e18, capacity: 0 },
  ], now), 0.5);
  // ...unless none are reporting, when the last readings still say something.
  assert.equal(powerFill([{ t: now - 3600, stored: 250, capacity: 1000 }], now), 0.25);
});

test('formatEU reaches the wireless network\'s sizes', () => {
  assert.equal(formatEU(1234), '1.23k');
  assert.equal(formatEU(4e12), '4.00T');
  assert.equal(formatEU(5e15), '5.00P');
  assert.equal(formatEU(6e18), '6.00E');
  assert.equal(formatEU(1.234e24), '1.23e+24');
});

test('fillText rounds, without calling a nearly empty one empty', () => {
  assert.equal(fillText(null), '');
  assert.equal(fillText(0), '0%');
  assert.equal(fillText(0.001), '<1%');
  assert.equal(fillText(0.614), '61%');
  assert.equal(fillText(1), '100%');
});

test('compactEU fits the tab button: three digits, no unit', () => {
  assert.equal(compactEU(null), '');
  assert.equal(compactEU(0), '0');
  assert.equal(compactEU(999), '999');
  assert.equal(compactEU(1234), '1.23k');
  assert.equal(compactEU(100e12), '100T');
  assert.equal(compactEU(6.489e16), '64.9P');
  assert.equal(compactEU(999.96e12), '1.00P');  // not "1000T"
  assert.equal(compactEU(2e18), '2.00E');
  assert.equal(compactEU(1.234e24), '1.2e+24');
});

test('powerTabText shows the wireless network while it reports', () => {
  const now = 10000;
  const lsc = { t: now, stored: 1e12, capacity: 1e15 };
  assert.equal(powerTabText([lsc], now), '<1%');
  assert.equal(powerTabText([lsc, { t: now, stored: 6.489e16, capacity: 0 }], now), '64.9P');
  // Gone quiet: back to the fill.
  assert.equal(powerTabText([lsc, { t: now - 600, stored: 6.489e16, capacity: 0 }], now), '<1%');
  assert.equal(powerTabText([], now), '');
});

test('machineFill measures an LSC in wireless mode by its threshold, past 100%', () => {
  assert.equal(machineFill({ stored: 250, capacity: 1000 }), 0.25);
  assert.equal(machineFill({ stored: 6.3e15, capacity: 9.2e18, wireless_threshold: 6e15 }), 1.05);
  assert.equal(fillText(machineFill({ stored: 6.3e15, capacity: 9.2e18, wireless_threshold: 6e15 })), '105%');
  assert.equal(machineFill({ stored: 5, capacity: 0 }), null);
});

test('the wireless network comes first among the machine buttons, with its EU', () => {
  const lsc = { name: 'LSC', stored: 6.3e15, capacity: 9.2e18, wireless_threshold: 6e15 };
  const main = { name: '', stored: 3, capacity: 10 };
  const wireless = { name: 'Wireless', stored: 6.489e16, capacity: 0 };
  assert.deepEqual(orderMachines([main, lsc, wireless]).map(m => m.name), ['Wireless', '', 'LSC']);
  assert.deepEqual(orderMachines(null), []);
  assert.equal(machineButtonText(wireless), '64.9P');
  assert.equal(machineButtonText(lsc), '105%');
  assert.equal(machineButtonText(main), '30%');
});

test('wirelessLscText: what an LSC in wireless mode trades with the network', () => {
  const lsc = { wireless_threshold: 6e15 };  // 1 UEV: 6P a rebalance, 20T EU/s
  assert.equal(wirelessLscText(lsc), 'Refills up to 20.00T EU/s');
  // Its averages are EU/t: 4.58G EU/t is 91.6G EU/s.
  assert.equal(wirelessLscText({ ...lsc, avg_eu_in_1h: 107.636e9, avg_eu_out_1h: 103.056e9 }),
    'Sending 91.60G EU/s to the wireless network over the past hour');
  assert.equal(wirelessLscText({ ...lsc, avg_eu_in_1h: 1e9, avg_eu_out_1h: 6e9 }),
    'Drawing 100.00G EU/s from the wireless network over the past hour \u00b7 refills up to 20.00T EU/s');
});
