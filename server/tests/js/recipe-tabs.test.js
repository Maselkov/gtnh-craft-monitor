import test from 'node:test';
import assert from 'node:assert/strict';
import { categoryTabsHtml, categoryTooltipHtml } from '../../static/js/recipe-tabs.js';

test('category tabs choose an available machine icon and preserve accessible selection', () => {
  const maps = [{id:'machine:"one',name:'Arcane <Infusion>',machines:[{icon:null},{icon:'item/altar.webp'}]},
    {id:'second',name:'Research Station',machines:[]}];
  const html = categoryTabsHtml(maps, {'machine:"one':1234,second:2}, 'second');
  assert.match(html, /src="\/icons\?path=item%2Faltar.webp"/);
  assert.match(html, /role="tab"/);
  assert.match(html, /data-map="second" aria-controls="recipeViewerList" aria-selected="true" tabindex="0"/);
  assert.doesNotMatch(html, /All categories/);
  assert.match(html, /aria-label="Arcane &lt;Infusion&gt;, 1,234 recipes"/);
  assert.doesNotMatch(html, /recipe-map-count/);
  assert.match(html, />RS<\/span>/);
  assert.doesNotMatch(html, /style=|onclick=|<Infusion>|\btitle=/);
});

test('category tooltip uses the standard name, count and mod styling', () => {
  const html = categoryTooltipHtml({name:'Scanner <x>',mod_name:'GregTech & Co'}, 1);
  assert.match(html, /network-tooltip-name/);
  assert.match(html, /Scanner &lt;x&gt;/);
  assert.match(html, /1 recipe</);
  assert.match(html, /network-tooltip-mod">GregTech &amp; Co/);
});
