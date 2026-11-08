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

test('assembly research and display recipes keep costs and uncertainty visible', () => {
  const stack = {key:'example:part:0', name:'Part', size:2, kind:'item'};
  const base = {id:8, type:'assembly_line', map:'assembly_line', inputs:[], outputs:[stack], machines:[],
    groups:[{alternatives:[stack], alternatives_total:20}], eut:32768, ticks:400,
    research:{item:stack,method:'research_station',eut:8192,amperage:4,computation:1000000,computation_per_second:128}};
  const html = stepHtml(base, {interactive:true});
  assert.match(html, /32,768 EU\/t/);
  assert.match(html, /Research Station/);
  assert.match(html, /8,192 EU\/t/);
  assert.match(html, /1,000,000 total computation/);
  assert.match(html, /data-choice="8:group-0"/);
  const display = stepHtml({...base,type:'nei',research:null,requirement_notes:['Mana: <unsafe>']}, {interactive:true});
  assert.match(display, /NEI display recipe/);
  assert.match(display, /&lt;unsafe&gt;/);
});

test('positioned previews preserve frames, crops, canonical navigation and NC', async () => {
  const { previewHtml, displayChoices } = await import('../../static/js/recipe-render.js');
  const charged = {...item, name:'Charged', icon:'item/charged.webp'};
  const stack = {...item, size:0, display_variants:[charged, {...charged, name:'Empty'}]};
  assert.equal(displayChoices([stack])[1].key, 'mod:item:*');
  const p = {family:'infusion', width:166, height:150,
    slots:[{role:'groups',index:0,x:74,y:62,w:18,h:18,frame:'ui/slot.webp', alternatives:[{...item,size:0}]}],
    layers:[{x:47,y:36,w:73,h:73,image:'ui/runes.webp',uv:[.4,.15,.5,.25]}],
    aspects:[{tag:'ordo',name:'Order',amount:25,icon:'aspect/ordo.webp'}],
    research:{name:'<Research>', category:'Thaumaturgy'}};
  const html = previewHtml({id:8, preview:p, requirements:{instability:0}}, {interactive:true});
  assert.match(html, /data-x="74"/);
  assert.match(html, /viewBox="0.4 0.15/);
  assert.match(html, />NC</);
  assert.match(html, /mod:item:\*/);
  assert.match(html, /Instability: 0/);
  assert.match(html, /&lt;Research&gt;/);
  assert.doesNotMatch(html, /style=|onclick=|100%/);
});

test('total energy keeps non-heat special requirements and heat distinct', () => {
  const row = {map:'test', inputs:[], outputs:[], eut:30720, ticks:1000, special:123};
  const html = stepHtml(row, {interactive:true});
  assert.match(html, /30,720,000 total EU/);
  assert.match(html, /Special: 123/);
  const heat = stepHtml({...row,heat:true}, {interactive:true});
  assert.match(heat, /123 K/);
  assert.doesNotMatch(heat, /Special:/);
});
