import test from 'node:test';
import assert from 'node:assert/strict';
import { gridHtml, stackHtml, stepHtml } from '../../static/js/recipe-render.js';
import { recipeFromUrl, recipeItemKey, recipeUrl, sameRecipeDataset, trailDistance } from '../../static/js/recipe-state.js';

const item = { key: 'mod:item:*', name: 'Any Item', mod: 'mod', internal: 'item', damage: null, kind: 'item', size: 1, wildcard: true };

test('recipe URLs preserve wildcards, fluid namespaces, direction and map', () => {
  assert.equal(recipeItemKey(item), 'mod:item:*');
  assert.equal(recipeItemKey({ mod: null, internal: 'mod:water', kind: 'fluid' }), 'fluid:mod:water');
  assert.equal(recipeItemKey({ internal: 'aer', kind: 'essentia' }), null);
  const frame = { key: 'fluid:mod:water', dir: 'using', map: 'gt.recipe.test' };
  assert.deepEqual(recipeFromUrl(new URL(recipeUrl(frame), 'https://example.test')), frame);
  assert.equal(recipeFromUrl(new URL('https://example.test/network')), null);
});

test('ancestor navigation counts history entries rather than adding duplicate ancestors', () => {
  const stack = [{ key: 'a' }, { key: 'b' }, { key: 'c' }, { key: 'd' }];
  assert.equal(trailDistance(stack, 0), -3);
  assert.equal(trailDistance(stack, 2), -1);
  assert.equal(trailDistance(stack, 3), 0);
  assert.equal(trailDistance(stack, -1), 0);
  assert.equal(trailDistance(stack, 99), 0);
});

test('pages belong to one selected version and fingerprint', () => {
  const a = { version: '2.9.0', fingerprint: 'one' };
  assert.equal(sameRecipeDataset(a, { ...a }), true);
  assert.equal(sameRecipeDataset(a, { ...a, fingerprint: 'two' }), false);
  assert.equal(sameRecipeDataset(a, { ...a, version: '2.9.1' }), false);
  assert.equal(sameRecipeDataset(a, { ...a, assets: 'other-textures' }), false);
  assert.equal(sameRecipeDataset({}, {}), false);
});

test('interactive stacks have keyboard/touch uses and conditional stock actions', () => {
  const html = stackHtml({ ...item, name: '<script>"', key: 'mod:"item:*', chance: 25 }, { interactive: true, hasStock: () => true });
  assert.match(html, /data-action="recipe-open"/);
  assert.match(html, /data-dir="using"/);
  assert.match(html, /data-action="recipe-stock"/);
  assert.match(html, /data-key="mod:&quot;item:\*"/);
  assert.match(html, /25%/);
  assert.doesNotMatch(html, /<script>|style=|onclick=/);
  assert.doesNotMatch(stackHtml(item), /data-action/);
  assert.doesNotMatch(stackHtml(item, { interactive: true }), /recipe-stock/);
});

test('grids preserve holes and distinguish alternative choices from extra ingredients', () => {
  const html = gridHtml({ id: 3, grid: { w: 2, h: 1, mirrored: true, slots: [
    { alternatives: [item], alternatives_total: 30, ore: 'plateIron' }, null,
  ] } }, { interactive: true });
  assert.match(html, /recipe-grid-w2/);
  assert.match(html, /recipe-grid-hole/);
  assert.match(html, /30 alternatives \(showing 1\)/);
  assert.match(html, /data-choice="3:0"/);
  assert.match(html, /may be mirrored/);
  assert.doesNotMatch(html, /style=/);
});

test('furnace has duration without a fabricated GT voltage tier', () => {
  const html = stepHtml({ map: 'furnace', machines: [{ name: 'Furnace' }], inputs: [item], outputs: [item], ticks: 200 }, { interactive: true });
  assert.match(html, /10 s/);
  assert.doesNotMatch(html, /EU\/t|ULV/);
});
