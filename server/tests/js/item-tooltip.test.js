import test from 'node:test';
import assert from 'node:assert/strict';
import { itemTooltipHtml, systemStockHtml } from '../../static/js/item-tooltip.js';
import { setNetworkStock } from '../../static/js/network-stock.js';
import { stackHtml } from '../../static/js/recipe-render.js';

test('shared item tooltip formats rarity, Shift descriptions, mod and context safely', () => {
  const item = {name:'§aA <part>',mod:'mod',kind:'item'};
  const info = {description:'§bRegular <text>',shift_description:'§eShift details',rarity:'d',mod_name:'A & B'};
  const normal = itemTooltipHtml(item, info, {stat:'Amount: 2x'});
  assert.match(normal, /network-tooltip-name mc-d/);
  assert.match(normal, /mc-a/);
  assert.match(normal, /&lt;part&gt;/);
  assert.match(normal, /mc-b/);
  assert.match(normal, /\[shift\]/);
  assert.match(normal, /Amount: 2x/);
  assert.match(normal, /A &amp; B/);
  assert.doesNotMatch(normal, /Shift details|Stored:|<part>|<text>/);
  const shifted = itemTooltipHtml(item, info, {shiftHeld:true});
  assert.match(shifted, /Shift details/);
  assert.doesNotMatch(shifted, /Regular|\[shift\]/);
  assert.doesNotMatch(itemTooltipHtml(item, {rarity:'" onclick="x'}), /onclick=/);
});

test('recipe hover bindings use an exact entry token without a duplicate native title', () => {
  const entry = {key:'mod:item:*',name:'Charged Part',size:1,wildcard:true};
  const html = stackHtml(entry, {interactive:true,tooltipKey:e => {assert.equal(e,entry);return 'exact-7';}});
  assert.match(html, /data-recipe-tooltip="exact-7"/);
  assert.match(html, /data-key="mod:item:\*"/);
  assert.doesNotMatch(html, /\btitle=/);
});

test('system stock uses the shared gold quantity after descriptions, including zero and fluid units', () => {
  const item = {name:'Part',mod:'example',internal:'part',damage:0,kind:'item'};
  setNetworkStock({updated_at:123,items:[{...item,size:11000}]});
  const html = itemTooltipHtml(item,{description:'Item details',mod_name:'Example'});
  assert.match(html, /Amount in system: <span class="mc-6">11,000<\/span>/);
  assert(html.indexOf('Item details') < html.indexOf('Amount in system:'));
  assert(html.indexOf('Amount in system:') < html.indexOf('network-tooltip-mod'));
  assert.doesNotMatch(html, /Stored:|Amount:|11,000x/);
  assert.match(systemStockHtml({...item,internal:'absent'}), /mc-6">0</);
  assert.match(systemStockHtml({kind:'fluid'},{amount:3000}), /mc-6">3,000 mB</);
  assert.equal(systemStockHtml(item,{amount:'not a number'}), '');
  setNetworkStock(null);
  assert.doesNotMatch(itemTooltipHtml(item), /Amount in system:/);
});
