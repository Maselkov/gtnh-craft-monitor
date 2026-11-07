import test from 'node:test';
import assert from 'node:assert/strict';
import { inlineChannelRows } from '../../static/js/multiblock.js';

test('block settings follow the selected tier to its material row', () => {
  const coil = { name: 'coil', kind: 'block', tier: 1, options: [{ tier: 1, key: 'copper' }, { tier: 2, key: 'kanthal' }] };
  assert.deepEqual([...inlineChannelRows([coil], [{ key: 'copper' }])], [['coil', 'copper']]);
  assert.deepEqual([...inlineChannelRows([{ ...coil, tier: 2 }], [{ key: 'kanthal' }])], [['coil', 'kanthal']]);
});

test('sizes and settings without a selected material stay above the preview', () => {
  const channels = [
    { name: 'length', kind: 'size', tier: 5, options: [{ tier: 5 }] },
    { name: 'glass', kind: 'block', tier: 1, options: [{ tier: 1 }, { tier: 2, key: 'glass' }] },
    { name: 'coil', kind: 'block', tier: 2, options: [{ tier: 2, key: 'absent' }] },
  ];
  assert.equal(inlineChannelRows(channels, [{ key: 'glass' }]).size, 0);
});

test('independent channels can share a material row without losing a setting', () => {
  const channels = ['coil', 'glass'].map(name => ({ name, kind: 'block', tier: 1, options: [{ tier: 1, key: 'shared' }] }));
  assert.deepEqual([...inlineChannelRows(channels, [{ key: 'shared' }])], [['coil', 'shared'], ['glass', 'shared']]);
});
