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

const MACHINES = [
  { name: 'Lathe <T3>', x: 12, y: 64, z: -30, dim: 0, critical: 3600, blocked: 0, busy: 120, stalled: 0, jobs: 2 },
  { name: 'Bender', x: null, y: null, z: null, dim: null, critical: 0, blocked: 900, busy: 0, stalled: 0.4, jobs: 1 },
];

test('machines jobs were held up at, the costliest first, with where they are', () => {
  const html = renderStats({ ...DATA, bottlenecks: MACHINES });
  assert.match(html, /Held up at/);
  assert.match(html, /Lathe &lt;T3&gt; <span class="cpu-id">12, 64, -30<\/span>/);
  assert.match(html, /jobs waited 1h 0m &middot; busy elsewhere 2m 0s &middot; 2 jobs/);
  // Under a second of a reason isn't worth a mention.
  assert.match(html, /Bender<\/div>\s*<div class="cpu-id">couldn't send 15m 0s &middot; 1 job</);
  // Bars by what whole jobs waited, which only the Lathe made them do.
  assert.match(html, /Held up at[\s\S]*data-fill="100"[\s\S]*data-fill="0"/);
  const unwaited = renderStats({ ...DATA, bottlenecks: [{ ...MACHINES[0], critical: 0 }, MACHINES[1]] });
  assert.match(unwaited, /Held up at[\s\S]*data-fill="13"[\s\S]*data-fill="100"/);
  assert.doesNotMatch(renderStats(DATA), /Held up at/);
});

test('held-up machines show even when no craft ended in the range', () => {
  const html = renderStats({ ...DATA, total: 0, items: [], cpus: [], bottlenecks: MACHINES });
  assert.match(html, /No crafts ended in this range/);
  assert.match(html, /Held up at/);
});
