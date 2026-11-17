import test from 'node:test';
import assert from 'node:assert/strict';
import { changeFraction, compareByChange, formatChangePercent, isKitOutput } from '../../static/js/network.js';

test('kit transport outputs are hidden without hiding controllers or ordinary items', () => {
  assert.equal(isKitOutput({ mod: 'minecraft', internal: 'command_block' }), true);
  assert.equal(isKitOutput({ name: '§bFusion Reactor kit', variant: 'nbt-hash' }), true);
  assert.equal(isKitOutput({ name: 'Fusion Reactor kit #2', variant: 'nbt-hash' }), true);
  assert.equal(isKitOutput({ name: 'Fusion Reactor', variant: 'nbt-hash' }), false);
  assert.equal(isKitOutput({ name: 'Fusion Reactor kit #', variant: 'nbt-hash' }), false);
  assert.equal(isKitOutput({ name: 'Tool kit' }), false);
  assert.equal(isKitOutput({ kind: 'fluid', mod: 'minecraft', internal: 'command_block' }), false);
});

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

test('sortNetworkItems: pinned first, then by the sort, each group on its own', async () => {
  const { sortNetworkItems, networkItemKey } = await import('../../static/js/network.js');
  const item = (name, size, internal = name.toLowerCase()) => ({ mod: 'm', internal, damage: 0, kind: 'item', name, size });
  const items = [item('§eBeta', 5), item('alpha', 50), item('Gamma', 500), item('Delta', 1)];
  const pinned = new Set([networkItemKey('m', 'delta', 0, 'item')]);
  const names = (list) => list.map(it => it.name);

  assert.deepEqual(names(sortNetworkItems(items, 'name', pinned, () => null)),
    ['Delta', 'alpha', '§eBeta', 'Gamma'], 'by name without formatting codes, case aside');
  assert.deepEqual(names(sortNetworkItems(items, 'size', pinned, () => null)),
    ['Delta', 'Gamma', 'alpha', '§eBeta']);
  const changes = { gamma: { from: 1000 }, alpha: { from: 10 } };
  assert.deepEqual(names(sortNetworkItems(items, 'change', new Set(), it => changes[it.internal] || null)),
    ['Gamma', 'alpha', '§eBeta', 'Delta'], 'fell, rose, then unchanged by quantity');
  assert.deepEqual(names(items), ['§eBeta', 'alpha', 'Gamma', 'Delta'], 'the list given is left alone');
});
