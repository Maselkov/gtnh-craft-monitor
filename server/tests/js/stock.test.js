import test from 'node:test';
import assert from 'node:assert/strict';
import { parseStockAmount, stockAmountInput } from '../../static/js/stock.js';

test('parseStockAmount takes the craft dialog\'s shorthand, positive only', () => {
  assert.equal(parseStockAmount('10k'), 10000);
  assert.equal(parseStockAmount(' 2.5M '), 2500000);
  assert.equal(parseStockAmount('1000'), 1000);
  assert.equal(parseStockAmount('0'), null);
  assert.equal(parseStockAmount('-5'), null);
  assert.equal(parseStockAmount(''), null);
  assert.equal(parseStockAmount('lots'), null);
});

test('stockAmountInput shortens only when exact, and reads back the same', () => {
  assert.equal(stockAmountInput(20000), '20k');
  assert.equal(stockAmountInput(3000000), '3M');
  assert.equal(stockAmountInput(2e12), '2T');
  assert.equal(stockAmountInput(12345), '12345');
  assert.equal(stockAmountInput(12500), '12500');
  assert.equal(stockAmountInput(999), '999');
  for (const n of [20000, 12345, 7e9, 1]) assert.equal(parseStockAmount(stockAmountInput(n)), n);
});
