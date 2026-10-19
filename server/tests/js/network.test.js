import test from 'node:test';
import assert from 'node:assert/strict';
import { changeFraction, compareByChange, formatChangePercent } from '../../static/js/network.js';

test('changeFraction is the move as a share of the larger quantity', () => {
  assert.equal(changeFraction(0, { from: 500 }), -1);
  assert.equal(changeFraction(250, { from: 1000 }), -0.75);
  assert.equal(changeFraction(1000, { from: 250 }), 0.75);
  assert.equal(changeFraction(5, { from: 0 }), 1);
  assert.equal(changeFraction(100, null), null);
});

test('compareByChange: most used up first, unchanged last by quantity', () => {
  const items = [
    { name: 'unchanged small', size: 5, fraction: null },
    { name: 'rose', size: 100, fraction: 0.5 },
    { name: 'unchanged big', size: 900, fraction: null },
    { name: 'emptied', size: 0, fraction: -1 },
    { name: 'dipped', size: 80, fraction: -0.2 },
  ];
  assert.deepEqual(items.sort(compareByChange).map((i) => i.name),
    ['emptied', 'dipped', 'rose', 'unchanged big', 'unchanged small']);
});

test('formatChangePercent: whole percents, one decimal under 1%', () => {
  assert.equal(formatChangePercent(-0.8), '−80%');
  assert.equal(formatChangePercent(0.0857), '+9%');
  assert.equal(formatChangePercent(-0.004), '−0.4%');
  assert.equal(formatChangePercent(-1), '−100%');
});
