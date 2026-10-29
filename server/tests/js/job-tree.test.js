import test from 'node:test';
import assert from 'node:assert/strict';
import { parseSearchQuery } from '../../static/js/search.js';
import { branchCounts, branchDone, filterJobTree, heldNote, stepPercent, stepText } from '../../static/js/job-tree.js';
import { machinePlace } from '../../static/js/util.js';

test('stepText and stepPercent say how much of a step is made', () => {
  assert.equal(stepText({ total: 4000, left: 2800 }), '1.2k / 4.0k');
  assert.equal(stepPercent({ total: 64, left: 16 }), 75);
  assert.equal(stepPercent({ total: 0, left: 0 }), 0);
  assert.equal(stepPercent({ total: 10, left: 12 }), 0);  // grew since its peak was seen
});

const steps = {
  gear: { state: 'waiting' },
  plate: { state: 'active' },
  bolt: { state: 'stalled' },
  rod: { state: 'blocked' },
  ingot: { state: 'done' },
};

test('branchCounts counts what is under a shut step, each item once', () => {
  const node = { step: 'gear', children: [
    { step: 'plate', children: [{ step: 'ingot', status: 'stock' }] },
    { step: 'bolt', children: [{ step: 'rod', children: [] }] },
    { step: 'plate', children: [] },  // the same item again: counted once
  ] };
  assert.deepEqual(branchCounts(node, steps), [['stuck', 2], ['active', 1]]);
  assert.deepEqual(branchCounts({ step: 'ingot', children: [] }, steps), []);
});

test('heldNote says why a held-up step is not moving, and for how long', () => {
  const now = 1000;
  assert.equal(heldNote({ state: 'blocked', since: 760 }, now, 'T00'), "can't be sent to its machine for 4m 0s");
  assert.equal(heldNote({ state: 'stalled', since: 990 }, now, 'T00'), 'nothing back from its machine for 10s');
  assert.equal(heldNote({ state: 'busy', since: 940, with: { cpu: 'T01', name: 'Steel Bolt' } }, now, 'T00'),
    "machine busy with CPU T01's Steel Bolt for 1m 0s");
  assert.equal(heldNote({ state: 'busy', since: 940, with: { cpu: 'T00', name: 'Steel Bolt' } }, now, 'T00'),
    "machine busy with this job's Steel Bolt for 1m 0s");
  assert.equal(heldNote({ state: 'active' }, now, 'T00'), null);
});

test('machinePlace gives coordinates, and the dimension off the overworld', () => {
  assert.equal(machinePlace({ name: 'Lathe', x: 12, y: 64, z: -30, dim: 0 }), '12, 64, -30');
  assert.equal(machinePlace({ name: 'Lathe', x: 1, y: 2, z: 3, dim: -1 }), '1, 2, 3 in dim -1');
  assert.equal(machinePlace({ name: 'Lathe' }), null);
});

test('branchDone: every crafted step under it done, storage leaves aside', () => {
  const done = { step: 'ingot', status: 'craft', children: [{ step: 'x', status: 'stock' }] };
  assert.equal(branchDone(done, steps), true);
  assert.equal(branchDone({ step: 'ingot', status: 'craft', children: [{ step: 'plate', status: 'craft' }] }, steps), false);
  assert.equal(branchDone({ step: 'unknown', status: 'craft' }, steps), false);
});

test('filterJobTree marks matching steps and the ones leading to them', () => {
  const dust = { name: 'Iron Dust', children: [{ name: 'Iron Ore' }] };
  const root = { name: 'Iron Ingot', children: [dust, { name: 'Coal' }] };
  const hits = filterJobTree(root, parseSearchQuery('dust'));
  assert.deepEqual(hits.get(root), { self: false, below: true });
  assert.deepEqual(hits.get(dust), { self: true, below: false });
  assert.equal(hits.size, 2);
  // Steps carry no mod.
  assert.equal(filterJobTree(root, parseSearchQuery('@gregtech')).size, 0);
});
