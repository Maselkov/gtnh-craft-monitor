import test from 'node:test';
import assert from 'node:assert/strict';
import { escapeHtml, formatQty, iconClass } from '../../static/js/util.js';

test('formatQty abbreviates like the in-game terminal', () => {
  assert.equal(formatQty(null), '?');
  assert.equal(formatQty(999), '999');
  assert.equal(formatQty(1234), '1.2k');
  assert.equal(formatQty(2500000), '2.50M');
  assert.equal(formatQty(-3e9), '-3.00B');
});

test('escapeHtml covers everything that matters inside an attribute', () => {
  assert.equal(escapeHtml(`<a href="x" title='y'>&</a>`), '&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;');
});

test('iconClass marks icons drawn past the item box', () => {
  assert.equal(iconClass('craft-icon', '2.9.0~ab12cd34~bleed12/item/a/b~0.png'), 'craft-icon icon-bleed');
  assert.equal(iconClass('craft-icon', '2.9.0~faithful32~ab12cd34~bleed12/item/a/b~0.png'), 'craft-icon icon-bleed');
  assert.equal(iconClass('craft-icon', '2.9.0~ab12cd34/item/a/b~0.png'), 'craft-icon');
  // Only the prefix counts, not a matching item name.
  assert.equal(iconClass('craft-icon', '2.9.0~ab12cd34/item/a/x~bleed12/y.png'), 'craft-icon');
  assert.equal(iconClass('craft-icon', null), 'craft-icon');
});
