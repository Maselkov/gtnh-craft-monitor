import test from 'node:test';
import assert from 'node:assert/strict';
import { amountText, energyHtml, gridHtml, machineSidebarHtml, stackHtml, stepHtml, voltageHtml } from '../../static/js/recipe-render.js';
import { recipeFromUrl, recipeItemKey, recipeUrl, sameRecipeDataset, trailDistance } from '../../static/js/recipe-state.js';

const item = { key: 'mod:item:*', name: 'Any Item', mod: 'mod', internal: 'item', damage: null, kind: 'item', size: 1, wildcard: true };

test('machine sidebars keep concrete identities, shared tooltips and safe missing-icon fallbacks', () => {
  const entries = [];
  const html = machineSidebarHtml([item, item, {name:'<Unknown machine>'}], {
    tooltipKey: entry => { entries.push(entry); return String(entries.length); },
  });
  assert.equal(entries.length, 2);
  assert(entries.every(entry => entry.machine));
  assert.match(html, /recipe-machine-multiple/);
  assert.match(html, /data-action="recipe-open" data-key="mod:item:\*"/);
  assert.match(html, /tabindex="0" role="img" aria-label="&lt;Unknown machine&gt;"/);
  assert.equal((html.match(/data-action=/g) || []).length, 1);
  assert.doesNotMatch(html, /<Unknown|style=|onclick=/);
  assert.equal(machineSidebarHtml([]), '');
});

test('fluid slot labels abbreviate litres while tooltip amounts remain exact', () => {
  for (const [size, label] of [[1000, '1kL'], [2000, '2kL'], [3000, '3kL'],
    [500, '500L'], [147456, '148kL'], [2500000, '2.5ML']]) {
    const fluid = {key:'fluid:water', kind:'fluid', name:'Water', size};
    assert.match(stackHtml(fluid, {interactive:true, compact:true, preview:true}), new RegExp(`>${label.replace('.', '\\.')}<`));
    assert.equal(amountText(fluid), `${size.toLocaleString('en-US')} mB`);
  }
});

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

test('interactive stacks navigate without extra action buttons over the tooltip', () => {
  const html = stackHtml({ ...item, name: '<script>"', key: 'mod:"item:*', chance: 25 }, { interactive: true });
  assert.match(html, /data-action="recipe-open"/);
  assert.doesNotMatch(html, /recipe-stack-links|data-action="recipe-stock"|data-dir="using"/);
  assert.match(html, /data-key="mod:&quot;item:\*"/);
  assert.match(html, /25%/);
  assert.doesNotMatch(html, /<script>|style=|onclick=/);
  assert.doesNotMatch(stackHtml(item), /data-action/);
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
  assert.doesNotMatch(html, /<details class="recipe-research"/);
  assert.match(html, /data-choice="8:group-0"/);
  const display = stepHtml({...base,type:'nei',research:null,requirement_notes:['Mana: <unsafe>']}, {interactive:true});
  assert.doesNotMatch(display, /NEI display recipe|Additional requirements|<details/);
  assert.match(display, /&lt;unsafe&gt;/);
  assert.match(display, /recipe-card-requirement/);
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
  assert.match(html, /Total: 30,720,000 EU/);
  assert.match(html, /Special: 123/);
  const heat = stepHtml({...row,heat:true}, {interactive:true});
  assert.match(heat, /123 K/);
  assert.doesNotMatch(heat, /Special:/);
});

test('real requirements render on the card while false conditions stay absent', () => {
  const row = {map:'test', inputs:[], outputs:[], eut:2400, ticks:100, tier:'IV',
    requirement_notes:['Needs Cleanroom', 'Mana: 2,000', 'Needs Cleanroom', 'Cleanroom: No']};
  const html = stepHtml(row, {interactive:true, machineSidebar:true});
  assert.match(html, /Time: 5 seconds/);
  assert.equal((html.match(/data-text="Needs Cleanroom"/g) || []).length, 1);
  assert.match(html, /data-text="Mana: 2,000"/);
  assert.doesNotMatch(html, /Cleanroom: No|<details|Additional requirements/);
  assert.match(energyHtml({...row, requirement_notes:['Cleanroom: Yes']}), /Needs Cleanroom/);
});

test('voltage labels use GT colours and high-tier decoration with safe fallback text', () => {
  assert.match(voltageHtml(30720, 'LuV'), /30,720 EU\/t \(<span class="recipe-voltage-tier mc-d">LuV<\/span>\)/);
  assert.match(voltageHtml(120, 'MV'), /mc-6/);
  assert.match(voltageHtml(524288, 'UV'), /mc-2 mc-underline/);
  assert.match(voltageHtml(33554432, 'UIV'), /mc-1 mc-bold mc-underline/);
  assert.doesNotMatch(voltageHtml(0, '<bad>'), /<bad>|class=/);
});

test('research cards show NEI power, computation, time and compact maximum energy', () => {
  const stack = {...item, name:'Research sample'};
  const step = {id:42, outputs:[{...stack,name:'Target'}], scan:{item:stack, name:'Research Station',
    method:'research_station', eut:8192, tier:'IV', usage:32768, amperage:4,
    computation:1000000, computation_per_second:128, max_eu:'9007199254740993'}};
  const html = energyHtml(step);
  assert.match(html, /Max EU: 9.01e15 EU/);
  assert.match(html, /Usage: 32,768 EU\/t/);
  assert.match(html, /Voltage: 8,192 EU\/t/);
  assert.match(html, /Min Computation: 128 \/s/);
  const scanner = energyHtml({...step, scan:{item:stack,method:'scanner',eut:120,tier:'MV',ticks:100,total_eu:'12000'}});
  assert.match(scanner, /Time: 5 seconds/);
  assert.match(scanner, /Total: 12,000 EU/);
  assert.doesNotMatch(scanner, /Computation:|Min computation:|Max EU:/);
});
