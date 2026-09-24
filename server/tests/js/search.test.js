import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildSearchHighlightHtml,
  itemMatchesSearch,
  parseSearchQuery,
  stripNegatePrefix,
  tokenizeSearchText,
} from '../../static/js/search.js';

const ITEMS = [
  { name: 'Molten Neutronium', mod: 'gregtech' },
  { name: 'Neutronium Ingot', mod: 'gregtech' },
  { name: 'Copper Ingot', mod: 'minecraft' },
  { name: 'Tin Ingot', mod: 'ic2' },
  { name: 'Small Air Shard', mod: 'thaumcraft' },
  { name: 'Air Crystal Shard', mod: 'thaumcraft' },
];

function search(query) {
  const terms = parseSearchQuery(query);
  return ITEMS.filter(it => itemMatchesSearch(it, terms)).map(it => it.name);
}

test('plain terms are ANDed regardless of order', () => {
  assert.deepEqual(search('neutronium molten'), ['Molten Neutronium']);
});

test('term1|term2 is an OR', () => {
  assert.deepEqual(search('copper|tin'), ['Copper Ingot', 'Tin Ingot']);
});

test('@mod matches the raw modid', () => {
  assert.deepEqual(search('@thaum'), ['Small Air Shard', 'Air Crystal Shard']);
});

test('-term excludes, including an @mod or quoted phrase', () => {
  assert.deepEqual(search('ingot -@gregtech'), ['Copper Ingot', 'Tin Ingot']);
  assert.deepEqual(search('shard -"air shard"'), ['Air Crystal Shard']);
});

test('quoted phrase is one literal substring, spaces included', () => {
  assert.deepEqual(search('"air shard"'), ['Small Air Shard']);
});

test('unterminated quote runs to end of input', () => {
  assert.deepEqual(search('"air sh'), ['Small Air Shard']);
});

test('a lone "-" is a plain term, not a negation', () => {
  assert.deepEqual(stripNegatePrefix('-'), { negate: false, rest: '-' });
});

test('tokenizer keeps whitespace so the highlighter can rebuild the input', () => {
  const text = '  -@gregtech "air shard"  ingot';
  const tokens = tokenizeSearchText(text);
  assert.equal(tokens.map(t => t.raw).join(''), text);
  assert.deepEqual(
    tokens.filter(t => t.type === 'term').map(t => t.raw),
    ['-@gregtech', '"air shard"', 'ingot'],
  );
});

test('highlight markup colors mod, exclude dash and quotes, and escapes text', () => {
  assert.equal(
    buildSearchHighlightHtml('-@gt "a<b" x'),
    '<span class="search-hl-exclude">-</span><span class="search-hl-mod">@gt</span> '
      + '<span class="search-hl-quote">"</span>a&lt;b<span class="search-hl-quote">"</span> x',
  );
});
