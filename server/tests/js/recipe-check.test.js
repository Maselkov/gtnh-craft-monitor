import test from 'node:test';
import assert from 'node:assert/strict';
import {
  amountText, machineText, noticeKey, noticeText, removedText, summaryText, versionsToCheck,
} from '../../static/js/recipe-check.js';

test('picking another version checks it against the one in use', () => {
  assert.deepEqual(versionsToCheck('2.9.1', '2.9.0', '2.9.0-RC-1'), { from: '2.9.0', to: '2.9.1' });
});

test('picking the version in use checks the upgrade that led to it', () => {
  assert.deepEqual(versionsToCheck('2.9.0', '2.9.0', '2.9.0-RC-1'), { from: '2.9.0-RC-1', to: '2.9.0' });
  assert.equal(versionsToCheck('2.9.0', '2.9.0', null), null);
  assert.equal(versionsToCheck('2.9.0', null, null), null);
});

test('amounts: fluids in mB, items as a count, whole', () => {
  assert.equal(amountText({ kind: 'fluid', size: 1000 }), '1,000 mB');
  assert.equal(amountText({ kind: 'item', size: 64 }), '64x');
  assert.equal(amountText({ kind: 'item', size: 0 }), 'not used up');
  assert.equal(amountText({ kind: 'item', size: null }), '?');
});

test('an output nothing makes or uses any more is called removed', () => {
  const name = 'Transmission Component (IV)';
  assert.equal(removedText({ name, used: false, taken_apart_in: ['Basic Unpackager'] }, '2.9.0'),
    'Removed in 2.9.0: nothing makes Transmission Component (IV) any more, and nothing uses it.'
    + ' Any you have left can be taken apart in the Basic Unpackager.');
  assert.equal(removedText({ name, used: false, taken_apart_in: [] }, '2.9.0'),
    'Removed in 2.9.0: nothing makes Transmission Component (IV) any more, and nothing uses it.');
  // Still used: it may be made outside GregTech now.
  assert.match(removedText({ name, used: true, taken_apart_in: [] }, '2.9.0'),
    /^Nothing in GregTech makes Transmission Component \(IV\) in 2\.9\.0\. It may be made another way/);
});

test('a step names its first machine and how many others run it', () => {
  assert.equal(machineText({ map: 'gt.recipe.vacuumfreezer', machines: ['Vacuum Freezer'] }), 'Vacuum Freezer');
  assert.equal(machineText({ map: 'm', machines: ['Basic Mixer', 'Advanced Mixer', 'Elite Mixer'] }),
    'Basic Mixer (or 2 more)');
  assert.equal(machineText({ map: 'gt.recipe.odd', machines: [] }), 'gt.recipe.odd');
});

test('the summary counts every status', () => {
  const text = summaryText({
    from: '2.9.0-RC-1', to: '2.9.0',
    counts: { broken: 2, moved: 1, changed: 3, unchecked: 40, crafting: 7, ok: 900 },
  });
  assert.equal(text, 'From GTNH 2.9.0-RC-1 to 2.9.0: 2 broken, 1 moved to another machine, '
    + '3 changed outside the pattern, 40 not checked, 900 unchanged. '
    + "7 crafting-table patterns aren't checked: AE2 marks those invalid itself.");
});

test('the notice only names what there is', () => {
  assert.equal(noticeText({ from: '2.9.0-RC-1', to: '2.9.0', counts: { broken: 1, moved: 0, changed: 0 } }),
    'GTNH 2.9.0 changed recipes a pattern uses (1 broken), compared with 2.9.0-RC-1.');
  assert.equal(noticeText({ from: 'a', to: 'b', counts: { broken: 2, moved: 1, changed: 3 } }),
    'GTNH b changed recipes 6 patterns use (2 broken, 1 moved to another machine, 3 changed), compared with a.');
});

test("a check that couldn't run is said, and dismissed apart from its result", () => {
  const failed = { from: '2.9.0-RC-2', to: '2.9.0', error: "Couldn't index GTNH 2.9.0-RC-2's recipes: it was killed." };
  assert.equal(noticeText(failed),
    "The pattern check for GTNH 2.9.0-RC-2 to 2.9.0 couldn't run. Couldn't index GTNH 2.9.0-RC-2's recipes: it was killed.");
  assert.notEqual(noticeKey(failed), noticeKey({ from: '2.9.0-RC-2', to: '2.9.0', counts: {} }));
});
