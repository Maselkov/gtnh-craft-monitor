const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./load');

const js = loadScripts('util.js');

test('formatQty abbreviates like the in-game terminal', () => {
  assert.equal(js.formatQty(null), '?');
  assert.equal(js.formatQty(999), '999');
  assert.equal(js.formatQty(1234), '1.2k');
  assert.equal(js.formatQty(2500000), '2.50M');
  assert.equal(js.formatQty(-3e9), '-3.00B');
});

test('jsArg output is safe inside a double-quoted onclick attribute', () => {
  assert.equal(js.jsArg('a"b'), '&quot;a\\&quot;b&quot;');
  assert.equal(js.jsArg("it's <x>"), '&quot;it&#39;s &lt;x&gt;&quot;');
});
