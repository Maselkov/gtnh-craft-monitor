import test from 'node:test';
import assert from 'node:assert/strict';
import { descriptionHtml, descriptionPlainText, formulaSearchText } from '../../static/js/description.js';

test('colour codes become classes, and plain text stays plain', () => {
  assert.equal(descriptionHtml('Machine Type: §ePlasma Forge'), 'Machine Type: <span class="mc-e">Plasma Forge</span>');
});

test('styles stack until a colour or reset ends them', () => {
  assert.equal(
    descriptionHtml('§9§lBold blue§o and italic§r plain §cred'),
    '<span class="mc-9 mc-bold">Bold blue</span><span class="mc-9 mc-bold mc-italic"> and italic</span>'
      + ' plain <span class="mc-c">red</span>',
  );
  assert.equal(descriptionHtml('§lbold§aGreen'), '<span class="mc-bold">bold</span><span class="mc-a">Green</span>');
});

test('codes are case-insensitive, unknown ones and a trailing § are dropped', () => {
  assert.equal(descriptionHtml('§Ex§kobf§'), '<span class="mc-e">x</span><span class="mc-e">obf</span>');
});

test('text is escaped and lines kept', () => {
  assert.equal(descriptionHtml('a<b>\n§7"c"'), 'a&lt;b&gt;\n<span class="mc-7">&quot;c&quot;</span>');
});

test('plain text drops every code', () => {
  assert.equal(descriptionPlainText('§eMachine Type:§r Plasma Forge, §bDTPF'), 'Machine Type: Plasma Forge, DTPF');
});

test("GTNH's key-hint markers are dropped", () => {
  assert.equal(descriptionPlainText('Info\n§!shift'), 'Info');
  assert.equal(descriptionHtml('Info\n§!ctrl'), 'Info');
});

test('formatting ends with its line, and indents stay', () => {
  assert.equal(descriptionHtml('§m----\nnext'), '<span class="mc-strike">----</span>\nnext');
  assert.equal(descriptionHtml('   §eindented'), '   <span class="mc-e">indented</span>');
});

test("GregTech's font glyphs become Unicode", () => {
  assert.equal(descriptionPlainText('(Fe\u2085\uE01AC)W'), '(Fe\u2085\u2080C)W');
  assert.equal(descriptionHtml('\uE012\uE013\uE018Pu'), '\u00B2\u00B3\u2078Pu');
  assert.equal(descriptionPlainText('Nq\uE020'), 'Nq?');
});

test('formulas search with plain digits, as GregTech\'s = search does', () => {
  assert.equal(formulaSearchText('\u00A7eSiO\u2082'), 'SiO2');
  assert.equal(formulaSearchText('\uE012\uE013\uE018Pu(Fe\u2085\uE01AC)'), '238Pu(Fe50C)');
  assert.equal(formulaSearchText('C\u2071'), 'C\u2071');  // not a digit
});
