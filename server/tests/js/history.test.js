import test from 'node:test';
import assert from 'node:assert/strict';
import { itemTrendText, itemUrlPath, parseItemUrlPath } from '../../static/js/history.js';

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

test('itemTrendText gives the rate for the range and when stock runs out', () => {
  assert.equal(itemTrendText(null, 'day', 'item'), '');
  assert.equal(itemTrendText({ per_second: 0 }, 'day', 'item'), '');
  assert.equal(
    itemTrendText({ per_second: -1200 / 3600, seconds_to_empty: 3 * 86400 + 4 * 3600 }, 'week', 'item'),
    '−1.2k/h over the past week · runs out in 3d 4h');
  assert.equal(itemTrendText({ per_second: 5 / 3600, seconds_to_empty: null }, 'lifetime', 'fluid'), '+5 mB/h all time');
});
