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

test("a name's formatting codes don't get in the way", () => {
  const terms = parseSearchQuery('"16k me"');
  assert.ok(itemMatchesSearch({ name: '§a16k§r ME Storage Component', mod: 'appliedenergistics2' }, terms));
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

// Items with game data info, for #, $ and @ terms; the same cases as
// tests/test_search.py's.
const DESCRIBED = [
  { name: 'Dimensionally Transcendent Plasma Forge', mod: 'gregtech',
    info: { description: 'Machine Type: Plasma Forge, DTPF', modName: 'GregTech' } },
  { name: 'Electric Blast Furnace', mod: 'gregtech',
    info: { description: 'Machine Type: Blast Furnace, EBF', modName: 'GregTech' } },
  { name: 'Plasma Cell', mod: 'gregtech', info: { ore: ['cellPlasma'], modName: 'GregTech', formula: 'SiO2' } },
  { name: 'Iron Ingot', mod: 'minecraft', info: { ore: ['ingotIron', 'ingotAnyIron'], modName: 'Minecraft' } },
  { name: 'Industrial Diamond', mod: 'IC2', info: { ore: ['gemDiamond'], modName: 'IndustrialCraft 2' } },
];

function searchDescribed(query) {
  const terms = parseSearchQuery(query);
  return DESCRIBED.filter(it => itemMatchesSearch(it, terms, it.info)).map(it => it.name);
}

test('#term searches only the description, not the name, as NEI does', () => {
  assert.deepEqual(searchDescribed('#dtpf'), ['Dimensionally Transcendent Plasma Forge']);
  assert.deepEqual(searchDescribed('#plasma'), ['Dimensionally Transcendent Plasma Forge']);
  assert.deepEqual(searchDescribed('#cell'), []);
});

test('plain terms match the name, description or ore names, as NEI\'s defaults do', () => {
  assert.deepEqual(searchDescribed('dtpf'), ['Dimensionally Transcendent Plasma Forge']);
  assert.deepEqual(searchDescribed('plasma'), ['Dimensionally Transcendent Plasma Forge', 'Plasma Cell']);
  assert.deepEqual(searchDescribed('anyiron'), ['Iron Ingot']);
  assert.deepEqual(searchDescribed('ebf|gemdiamond'), ['Electric Blast Furnace', 'Industrial Diamond']);
  assert.deepEqual(searchDescribed('"type: blast"'), ['Electric Blast Furnace']);
  assert.deepEqual(searchDescribed('-machine @gregtech'), ['Plasma Cell']);
});

test('# takes an OR group, a quoted phrase and a negation', () => {
  assert.deepEqual(searchDescribed('#ebf|dtpf'), ['Dimensionally Transcendent Plasma Forge', 'Electric Blast Furnace']);
  assert.deepEqual(searchDescribed('#"blast furnace"'), ['Electric Blast Furnace']);
  assert.deepEqual(searchDescribed('-#plasma @gregtech'), ['Electric Blast Furnace', 'Plasma Cell']);
  assert.deepEqual(searchDescribed('#"type: plasma'), ['Dimensionally Transcendent Plasma Forge']);
});

test('$term searches ore-dictionary names', () => {
  assert.deepEqual(searchDescribed('$ingotiron'), ['Iron Ingot']);
  assert.deepEqual(searchDescribed('$gem|cell'), ['Plasma Cell', 'Industrial Diamond']);
  assert.deepEqual(searchDescribed('-$iron'), DESCRIBED.slice(0, 3).map(it => it.name).concat('Industrial Diamond'));
});

test('=term searches the formula, with plain digits', () => {
  assert.deepEqual(searchDescribed('=sio2'), ['Plasma Cell']);
  assert.deepEqual(searchDescribed('=si|fe'), ['Plasma Cell']);
  assert.deepEqual(searchDescribed('-=sio @gregtech'), ['Dimensionally Transcendent Plasma Forge', 'Electric Blast Furnace']);
  assert.deepEqual(search('=ingot'), []);
  assert.equal(buildSearchHighlightHtml('=sio2'), '<span class="search-hl-formula">=sio2</span>');
});

test('@mod matches the mod id or its name', () => {
  assert.deepEqual(searchDescribed('@industrialcraft'), ['Industrial Diamond']);
  assert.deepEqual(searchDescribed('@ic2'), ['Industrial Diamond']);
});

test('without game data # and $ match nothing', () => {
  assert.deepEqual(search('#ingot'), []);
  assert.deepEqual(search('$ingot'), []);
});

test('a lone # or $ is a plain term', () => {
  assert.deepEqual(parseSearchQuery('#'), [{ type: 'text', negate: false, value: '#' }]);
  assert.deepEqual(parseSearchQuery('$'), [{ type: 'text', negate: false, value: '$' }]);
});

test('#"phrase" keeps its spaces; # and $ terms are coloured as NEI does', () => {
  assert.deepEqual(tokenizeSearchText('#"a b" $"c d" e').filter(t => t.type === 'term').map(t => t.raw),
    ['#"a b"', '$"c d"', 'e']);
  assert.equal(
    buildSearchHighlightHtml('-#"a" $b'),
    '<span class="search-hl-exclude">-</span><span class="search-hl-tooltip">#&quot;a&quot;</span> '
      + '<span class="search-hl-ore">$b</span>',
  );
});
