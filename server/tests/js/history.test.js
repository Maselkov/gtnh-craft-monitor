import test from 'node:test';
import assert from 'node:assert/strict';
import { itemUrlPath, parseItemUrlPath } from '../../static/js/history.js';

test('item URLs round-trip, NBT variant included', () => {
  for (const it of [
    { mod: 'minecraft', internal: 'iron_ingot', damage: 0, kind: 'item', variant: null },
    { mod: 'gregtech', internal: 'gt.metaitem.01', damage: 11129, kind: 'item', variant: null },
    { mod: 'cropsnh', internal: 'genericSeed', damage: 0, kind: 'item', variant: '0123456789ab' },
    { mod: null, internal: 'molten.silicone', damage: null, kind: 'fluid', variant: null },
  ]) {
    assert.deepEqual(parseItemUrlPath(itemUrlPath(it)), it);
  }
});

test('a variant keeps an explicit damage in the URL', () => {
  assert.equal(
    itemUrlPath({ mod: 'cropsnh', internal: 'genericSeed', damage: 0, kind: 'item', variant: 'Labc' }),
    '/network/item/cropsnh:genericSeed:0~Labc');
  assert.equal(
    itemUrlPath({ mod: 'minecraft', internal: 'stone', damage: 0, kind: 'item' }),
    '/network/item/minecraft:stone');
});
