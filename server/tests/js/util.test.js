import test from 'node:test';
import assert from 'node:assert/strict';
import { escapeHtml, formatQty } from '../../static/js/util.js';

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
