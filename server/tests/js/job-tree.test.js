import test from 'node:test';
import assert from 'node:assert/strict';
import { parseSearchQuery } from '../../static/js/search.js';
import { branchCounts, branchDone, filterJobTree, stepPercent, stepText } from '../../static/js/job-tree.js';

test('stepText and stepPercent say how much of a step is made', () => {
  assert.equal(stepText({ total: 4000, left: 2800 }), '1.2k / 4.0k');
  assert.equal(stepPercent({ total: 64, left: 16 }), 75);
  assert.equal(stepPercent({ total: 0, left: 0 }), 0);
  assert.equal(stepPercent({ total: 10, left: 12 }), 0);  // grew since its peak was seen
});

const steps = {
  gear: { state: 'waiting' },
  plate: { state: 'active' },
  bolt: { state: 'stuck' },
  rod: { state: 'stuck' },
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
