import test from 'node:test';
import assert from 'node:assert/strict';
import { jumpCounterText, nodeQtyText, qty, shownChildren, sortListItems, usedBand, warningText } from '../../static/js/craft-plan.js';

test('usedBand follows the game\'s colours', () => {
  assert.equal(usedBand(100), 'used-all');
  assert.equal(usedBand(89.4), 'used-high');
  assert.equal(usedBand(50), 'used-high');
  assert.equal(usedBand(42.2), 'used-mid');
  assert.equal(usedBand(17.5), 'used-low');
  assert.equal(usedBand(0), 'used-low');
});

test('qty adds mB for fluids only', () => {
  assert.equal(qty(1440, 'fluid'), '1.4k mB');
  assert.equal(qty(64, 'item'), '64');
});

test('nodeQtyText says what each step needs and where it comes from', () => {
  assert.equal(nodeQtyText({ status: 'craft', need: 2, from_stock: 0, craft: 2, batches: 2 }, true), 'craft 2');
  assert.equal(nodeQtyText({ status: 'craft', need: 2, from_stock: 0, craft: 4, batches: 1 }), 'need 2 · craft 4 (1×)');
  assert.equal(nodeQtyText({ status: 'stock', need: 64, from_stock: 64 }), 'need 64 · in stock');
  assert.equal(nodeQtyText({ status: 'craft', need: 64, from_stock: 20, craft: 44, batches: 11 }),
    'need 64 · 20 in stock · craft 44 (11×)');
  assert.equal(nodeQtyText({ status: 'missing', need: 1440, from_stock: 0, missing: 1440, kind: 'fluid' }),
    'need 1.4k mB · missing 1.4k mB');
  assert.equal(nodeQtyText({ status: 'cycle', need: 1, from_stock: 0, missing: 1 }),
    'need 1 · missing 1: its recipe needs itself');
});

test('nodeQtyText for leftovers, and a step split over several patterns', () => {
  assert.equal(nodeQtyText({ status: 'stock', need: 1, from_stock: 0, from_leftovers: 1 }), 'need 1 · left over from other steps');
  assert.equal(nodeQtyText({ status: 'craft', need: 10, from_stock: 2, from_leftovers: 3, craft: 5, batches: 5 }),
    'need 10 · 3 left over · 2 in stock · craft 5');
  assert.equal(nodeQtyText({ status: 'craft', need: 10, from_stock: 0, craft: 10, split: true, children: [{}, {}] }),
    'need 10 · craft 10 from 2 patterns');
  assert.equal(nodeQtyText({ status: 'via', craft: 12, batches: 3 }), 'makes 12 (3×)');
  assert.equal(nodeQtyText({ status: 'stock', need: 90, from_stock: 9, substitutes: [{ from_stock: 9 }, { from_leftovers: 3 }] }),
    'need 90 · 9 in stock · 12 as substitutes');
});

test('sortListItems: missing first, then stock only by use, then crafted', () => {
  const items = [
    { name: 'Crafted', need: 2, from_stock: 0, craft: 2, missing: 0, available: 0 },
    { name: 'Crafted after its stock', need: 9, from_stock: 1, craft: 8, missing: 0, available: 1 },
    { name: 'Little used', need: 1, from_stock: 1, craft: 0, missing: 0, available: 100 },
    { name: 'Short a bit', need: 5, from_stock: 0, craft: 0, missing: 1, available: 0 },
    { name: 'All used', need: 10, from_stock: 10, craft: 0, missing: 0, available: 10 },
    { name: 'Short a lot', need: 50, from_stock: 0, craft: 0, missing: 50, available: 0 },
  ];
  assert.deepEqual(sortListItems(items).map(i => i.name),
    ['Short a lot', 'Short a bit', 'All used', 'Little used', 'Crafted', 'Crafted after its stock']);
});

test('warningText formats the rule\'s level like any amount', () => {
  assert.equal(warningText({ rule: 'below your alert', threshold: 10620 }, 'item'), 'below your alert (10.6k)');
  assert.equal(warningText({ rule: 'below its keep-at-least target', threshold: 1000 }, 'fluid'),
    'below its keep-at-least target (1.0k mB)');
});

test('jumpCounterText counts places, with a + past the listed ones', () => {
  assert.equal(jumpCounterText(1, { at: ['0', '1', '2', '3', '4'], places: 5 }), '2/5');
  assert.equal(jumpCounterText(0, { at: ['0', '1'], places: 900 }), '1/2+');
});

test('shownChildren hides branches with nothing short only when asked, keeping positions', () => {
  const node = { children: [{ name: 'fine' }, { name: 'short', missing_below: 2 }, { name: 'also fine', missing_below: 0 }] };
  assert.deepEqual(shownChildren(node, false).map(([c, i]) => [c.name, i]), [['fine', 0], ['short', 1], ['also fine', 2]]);
  assert.deepEqual(shownChildren(node, true).map(([c, i]) => [c.name, i]), [['short', 1]]);
  assert.deepEqual(shownChildren({ more: 3 }, true), []);
});
