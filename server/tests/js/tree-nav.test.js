import test from 'node:test';
import assert from 'node:assert/strict';
import { graft, matchingEntries, searchNoteText } from '../../static/js/tree-nav.js';

test('graft puts in the steps a reveal sent, keeping what was loaded', () => {
  const loadedPlate = { name: 'Plate', children: [{ name: 'Ingot' }] };
  const into = { name: 'Gear', children: [loadedPlate, { name: 'Bolt', more: 1 }] };
  const from = { name: 'Gear', children: [
    { name: 'Plate', more: 1 },
    { name: 'Bolt', children: [{ name: 'Rod', more: 2 }] },
  ] };
  graft(into, from);
  assert.equal(into.children[0], loadedPlate);  // already there: left alone
  assert.deepEqual(into.children[1], { name: 'Bolt', children: [{ name: 'Rod', more: 2 }] });
});

test('matchingEntries picks the index entries a filter matches', () => {
  const index = [{ key: 'a', name: 'Glowstone Dust' }, { key: 'b', name: 'Iron Ingot' }];
  assert.deepEqual(matchingEntries(index, e => e.name.includes('Glow')).map(e => e.key), ['a']);
  assert.deepEqual(matchingEntries(undefined, () => true), []);
});

test('searchNoteText says what a filtered tree is waiting on or leaving out', () => {
  const entries = [{ places: 150 }, { places: 100 }];
  assert.equal(searchNoteText(null, false, 'job'), '');
  assert.equal(searchNoteText({ entries: [], pending: false }, false, 'job'), 'Nothing in this job matches the filter.');
  assert.equal(searchNoteText({ entries, pending: true }, false, 'plan'), 'Searching the whole tree…');
  assert.equal(searchNoteText({ entries, pending: true }, true, 'plan'), '');
  assert.equal(searchNoteText({ entries, pending: false, revealed: 200 }, true, 'plan'), 'Showing the first 200 of 250 places.');
  assert.equal(searchNoteText({ entries, pending: false, revealed: 250 }, true, 'plan'), '');
});
