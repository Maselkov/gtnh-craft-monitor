import test from 'node:test';
import assert from 'node:assert/strict';
import { itemTooltipHtml } from '../../static/js/item-tooltip.js';
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
