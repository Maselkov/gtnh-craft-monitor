import test from 'node:test';
import assert from 'node:assert/strict';
import { renderStats } from '../../static/js/craft-stats.js';

const DATA = {
  windowSeconds: 86400, total: 4, incomplete: 1, auto: 2, distinctItems: 2,
  items: [
    { itemName: 'Gear', mod: 'gregtech', internal: 'gt.gear', damage: 5, icon: null, count: 3, incomplete: 0, medianSeconds: 200 },
    { itemName: 'Rotor <b>', mod: null, internal: null, damage: null, icon: null, count: 1, incomplete: 1, medianSeconds: null },
  ],
  cpus: [{ cpu: 'W02', count: 1, busySeconds: 43200, busyFraction: 0.5 }],
};

test('summary line, items with links and bars, CPUs', () => {
  const html = renderStats(DATA);
  assert.match(html, /4 crafts &middot; 2 items &middot; 1 incomplete \(25%\) &middot; 2 auto/);
  assert.match(html, /data-internal="gt.gear" data-damage="5"/);
  assert.match(html, /3&times; &middot; usually 3m 20s/);
  assert.match(html, /1&times; &middot; 1 incomplete/);
  assert.match(html, /Rotor &lt;b&gt;/);
  assert.match(html, /data-fill="100"[\s\S]*data-fill="33"/);
  assert.match(html, /CPU W02[\s\S]*busy 50% &middot; 12h 0m &middot; 1 job/);
});

test('nothing ended in the range', () => {
  assert.match(renderStats({ ...DATA, total: 0, items: [], cpus: [] }), /No crafts ended in this range/);
});
