import test from 'node:test';
import assert from 'node:assert/strict';
import { amountText, craftingHtml, craftingTitle, energyHtml, gridHtml, machineSidebarHtml, stackHtml, stepHtml, voltageHtml } from '../../static/js/recipe-render.js';
import { RECIPE_RECENT_LIMIT, moveRecipePage, parseRecipeGroups, parseRecipeRecents, recipeGroupQuery, setAllRecipeGroups, setRecipeGroup, recipeCatalogCapacity, recipeCatalogGrid, recipeFromUrl, recipeItemKey, recipeUrl, sameRecipeDataset, trailDistance } from '../../static/js/recipe-state.js';

const item = { key: 'mod:item:*', name: 'Any Item', mod: 'mod', internal: 'item', damage: null, kind: 'item', size: 1, wildcard: true };

test('catalog pages fit complete rows and account for recent lookups and viewport changes', () => {
  assert.equal(recipeCatalogCapacity(338,720), 128);
  assert.equal(recipeCatalogCapacity(338,720,100), 112);
  assert.equal(recipeCatalogCapacity(338,400), 72);
  assert.equal(recipeCatalogCapacity(258,720), 96);
  assert.equal(recipeCatalogCapacity(338,100000), 1024);
  assert.equal(recipeCatalogCapacity(0,0), 1);
  assert.deepEqual(recipeCatalogGrid(338), {columns:8, cell:38.75});
});

test('recent recipe lookups tolerate broken storage and retain only bounded canonical keys', () => {
  for (const value of [null, 'bad', 'null', '{}', '[]']) assert.deepEqual(parseRecipeRecents(value), {version:'',keys:[]});
  const keys = ['fluid:water','mod:item:*','mod:item:*','bad','fluid:','mod:has space:0',null,...Array.from({length:30},(_,i)=>`mod:item:${i}`)];
  const parsed = parseRecipeRecents(JSON.stringify({version:'2.9.0',keys}));
  assert.equal(parsed.version, '2.9.0');
  assert.deepEqual(parsed.keys, ['fluid:water','mod:item:*',...Array.from({length:RECIPE_RECENT_LIMIT-2},(_,i)=>`mod:item:${i}`)]);
});

test('wheel pages wrap in both directions and across full laps while arrows remain bounded', () => {
  assert.equal(moveRecipePage(29,1,30,true), 0);
  assert.equal(moveRecipePage(0,-1,30,true), 29);
  assert.equal(moveRecipePage(27,-100,30,true), 17);
  assert.equal(moveRecipePage(27,30,30,true), 27);
  assert.equal(moveRecipePage(0,-1,30), 0);
  assert.equal(moveRecipePage(29,1,30), 29);
  assert.equal(moveRecipePage(0,100,1,true), 0);
  assert.equal(moveRecipePage(0,-1,0,true), 0);
});

test('crafting previews use a full recessed grid, preserve holes and reuse navigation identities', () => {
  const row = {id:7,outputs:[item],grid:{w:2,h:2,slots:[{alternatives:[item],alternatives_total:1},null,null,
    {alternatives:[item],alternatives_total:2,ore:'ingotAny'}]}};
  const html = craftingHtml(row,{interactive:true,tooltipKey:()=> 'part'});
  assert.equal(craftingTitle(row), 'Shaped Crafting');
  assert.equal(craftingTitle({...row,grid:{...row.grid,shapeless:true}}), 'Shapeless Crafting');
  assert.equal((html.match(/class="recipe-preview-slot recipe-crafting-slot/g)||[]).length, 10);
  assert.match(html, /data-choice="7:3" data-x="43" data-y="30"/);
  assert.match(html, /data-tooltip-note="ingotAny · 2 alternatives \(showing 1\)"/);
  assert.match(html, /recipe-crafting-arrow/);
  assert.match(html, /data-choice="7:output-0"/);
  assert.doesNotMatch(html, /recipe-grid-hole|recipe-grid-note|style=|onclick=/);
});

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

test('large item slot counts remain readable while detailed amounts stay exact', () => {
  const stack = {...item, size:8388608};
  assert.match(stackHtml(stack, {interactive:true,compact:true,preview:true}), />8\.39M</);
  assert.equal(amountText(stack), '8,388,608x');
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
  assert.match(html, /class="recipe-stack-amount recipe-stack-nc">NC</);
  assert.match(html, /mod:item:\*/);
  assert.match(html, /Instability: 0/);
  assert.match(html, /&lt;Research&gt;/);
  assert.doesNotMatch(html, /style=|onclick=|100%/);
});

test('arcane vis glyphs occupy their NEI positions with counts and a concise research label', () => {
  const html = stepHtml({id:1, map:'arcane', inputs:[], outputs:[], machines:[], requirement_notes:[], preview:{
    family:'arcane',width:166,height:142,slots:[],layers:[],
    aspects:[{tag:'ordo',name:'Ordo',amount:10,icon:'aspect/ordo.webp',x:56,y:122,w:16,h:16}],
    research:{name:'Digi-sentia Storage',state:'unknown'}}}, {interactive:true});
  assert.match(html, /data-id="aspect-ordo" data-x="56" data-y="122"/);
  assert.match(html, /Ordo: 10 vis/);
  assert.match(html, /recipe-stack-amount">10</);
  assert.match(html, /Research: Digi-sentia Storage/);
  assert.doesNotMatch(html, /recipe-preview-aspects|recipe-energy|isResearched|prevX/);
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

test('production totals use game scientific formatting without losing exact hover values', () => {
  const html = energyHtml({eut:503316480, ticks:1000000, tier:'UXV'});
  assert.match(html, /Total: 5\.03e14 EU/);
  assert.match(html, /title="503,316,480,000,000 EU"/);
  assert.match(html, /Time: 50,000 seconds/);
  assert.match(energyHtml({eut:1000000, ticks:1000000}), /Total: 1e12 EU/);
  assert.match(energyHtml({eut:1000000, ticks:999999}), /Total: 999,999,000,000 EU/);
});


test('infusion essentia and arcane vis use shared focusable tooltips without recipe links', () => {
  for (const family of ['infusion', 'arcane']) {
    const entries = [];
    const html = stepHtml({id:1, map:family, inputs:[], outputs:[], machines:[], preview:{
      family,width:166,height:142,slots:[],layers:[],
      aspects:[{tag:'ignis',name:'Ignis',amount:8,icon:'aspect/ignis.webp',x:56,y:122,w:16,h:16}]
    }}, {interactive:true,tooltipKey:entry => { entries.push(entry); return 'aspect'; }});
    assert.match(html, /data-recipe-tooltip="aspect" tabindex="0" aria-label="Ignis"/);
    assert.doesNotMatch(html, /title="Ignis|data-action="recipe-open"/);
    assert.equal(entries[0].kind, family === 'arcane' ? 'vis' : 'essentia');
    assert.equal(entries[0].internal, 'ignis');
    assert.equal(entries[0].info.mod_name, 'Thaumcraft');
    assert.equal(entries[0].vis, family === 'arcane');
  }
});

test('a bee of one species opens that species, in URLs and recent lookups', () => {
  const bee = {mod:'Forestry',internal:'beeDroneGE',damage:0,kind:'item',variant:'abc123',species:'forestry.speciesForest'};
  assert.equal(recipeItemKey(bee), 'Forestry:beeDroneGE:0#forestry.speciesForest');
  assert.equal(recipeItemKey({...bee,species:undefined}), 'Forestry:beeDroneGE:0');
  const frame = {key:'Forestry:beeDroneGE:0#forestry.speciesForest',dir:'making',map:''};
  assert.deepEqual(recipeFromUrl(new URL(recipeUrl(frame), 'https://example.test')), frame);
  const keys = ['Forestry:beeDroneGE:0#forestry.speciesForest','Forestry:beeDroneGE:*#forestry.speciesForest','mod:item:0#a b','mod:item:0#'];
  assert.deepEqual(parseRecipeRecents(JSON.stringify({version:'2.9.0',keys})).keys, ['Forestry:beeDroneGE:0#forestry.speciesForest']);
});

test('item group state is validated, bounded and turned into the search query', () => {
  for (const value of [null, 'bad', '{}', '[]']) assert.deepEqual(parseRecipeGroups(value), {version:'',all:null,open:[],shut:[]});
  const parsed = parseRecipeGroups(JSON.stringify({version:'2.9.0', all:'yes', open:[3, 3, -1, 1.5, 'x', 7], shut:[2]}));
  assert.deepEqual(parsed, {version:'2.9.0', all:null, open:[3, 7], shut:[2]});
  assert.deepEqual(recipeGroupQuery(parsed), [['open','3'], ['open','7'], ['shut','2']]);
  // Opening a shut group moves it, and the latest choice wins.
  const opened = setRecipeGroup(parsed, 2, true);
  assert.deepEqual([opened.open, opened.shut], [[3, 7, 2], []]);
  assert.deepEqual(setRecipeGroup(opened, 3, false).shut, [3]);
  // Opening or shutting every group starts afresh.
  const all = setAllRecipeGroups(opened, false);
  assert.deepEqual(recipeGroupQuery(all), [['groups','shut']]);
  assert.equal(all.version, '2.9.0');
  let many = all;
  for (let id = 0; id < 300; id++) many = setRecipeGroup(many, id, true);
  assert.equal(many.open.length, 128);
  assert.equal(many.open.at(-1), 299);
});
