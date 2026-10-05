import test from 'node:test';
import assert from 'node:assert/strict';
import { escapeHtml, essentiaBadgeHtml, formatDuration, formatQty, formatRate, iconClass, kindTooltipHtml } from '../../static/js/util.js';

test('formatQty abbreviates like the in-game terminal', () => {
  assert.equal(formatQty(null), '?');
  assert.equal(formatQty(999), '999');
  assert.equal(formatQty(1234), '1.2k');
  assert.equal(formatQty(2500000), '2.50M');
  assert.equal(formatQty(-3e9), '-3.00G');
  assert.equal(formatQty(4e12), '4.00T');
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

test('formatDuration shows the two largest units', () => {
  assert.equal(formatDuration(0), '0s');
  assert.equal(formatDuration(42.4), '42s');
  assert.equal(formatDuration(252), '4m 12s');
  assert.equal(formatDuration(3 * 3600 + 5 * 60 + 9), '3h 5m');
  assert.equal(formatDuration(2 * 86400 + 4 * 3600 + 59), '2d 4h');
  assert.equal(formatDuration(-5), '0s');
});

test('formatRate signs the amount and picks hours or days', () => {
  assert.equal(formatRate(1e6 / 3600), '+1.00M/h');
  assert.equal(formatRate(-1200 / 3600, ' mB'), '−1.2k mB/h');
  assert.equal(formatRate(12 / 86400), '+12/d');
  assert.equal(formatRate(0), null);
  assert.equal(formatRate(0.1 / 86400), null);
});

test('essentia without an icon gets a badge with its initial; nothing else does', () => {
  assert.equal(essentiaBadgeHtml({ kind: 'essentia', name: 'ordo' }, 'plan-icon'),
    '<span class="plan-icon essentia-badge" aria-hidden="true">O</span>');
  assert.equal(essentiaBadgeHtml({ kind: 'essentia', name: 'Ordo', icon: 'v/aspect/ordo.png' }, 'plan-icon'), '');
  assert.equal(essentiaBadgeHtml({ kind: 'item', name: 'Iron Ingot' }, 'plan-icon'), '');
});

test('the tooltip says what a fluid or essentia is', () => {
  assert.match(kindTooltipHtml('fluid'), />Fluid</);
  assert.match(kindTooltipHtml('essentia'), />Essentia</);
  assert.equal(kindTooltipHtml('item'), '');
});
