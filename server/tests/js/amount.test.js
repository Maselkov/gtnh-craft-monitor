const test = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('./load');

const js = loadScripts('amount.js');
const value = text => js.evaluateAmountExpression(text).value;
const error = text => js.evaluateAmountExpression(text).error;

test('plain numbers and metric suffixes', () => {
  assert.equal(value('64'), 64);
  assert.equal(value('10k'), 10000);
  assert.equal(value('1.5M'), 1500000);
  assert.equal(value('2b'), 2e9);
});

test('operator precedence, parentheses and unary minus', () => {
  assert.equal(value('4+3*2'), 10);
  assert.equal(value('(4+3)*2'), 14);
  assert.equal(value('(10-15)+20'), 15);
  assert.equal(value('-5+10'), 5);
  assert.equal(value('2k/4'), 500);
});

test('whitespace is ignored', () => {
  assert.equal(value(' 10 k + 4 '), 10004);
});

test('malformed input is rejected, not guessed at', () => {
  assert.equal(js.evaluateAmountExpression('').ok, false);
  assert.equal(js.evaluateAmountExpression('abc').ok, false);
  assert.equal(error('(1+2'), 'missing closing parenthesis');
  assert.equal(error('1+'), 'unexpected end of expression');
  assert.equal(error('1/0'), 'division by zero');
  assert.equal(error('2(3)'), 'unexpected trailing input');
});
