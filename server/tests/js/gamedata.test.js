import test from 'node:test';
import assert from 'node:assert/strict';
import { gamedataNoticeText } from '../../static/js/gamedata.js';

const ids = (...labels) => labels.map((label, i) => ({ id: `test:${i}`, label, version: '2.9.0' }));

test('one unknown item type', () => {
  assert.equal(
    gamedataNoticeText({ kind: 'behind', selected: '2.8.4', suggest: '2.9.0', count: 1, suggest_count: 1, ids: ids('Ichorium Cap') }),
    "Your network has an item type the GTNH 2.8.4 game data doesn't know (Ichorium Cap), so the network scanner skips it. It's in 2.9.0.",
  );
});

test('several, the first few named', () => {
  assert.equal(
    gamedataNoticeText({ kind: 'behind', selected: '2.8.4', suggest: '2.9.0', count: 4, suggest_count: 4, ids: ids('A', 'B', 'C', 'D') }),
    "Your network has 4 item types the GTNH 2.8.4 game data doesn't know (A, B, C, …), so the network scanner skips them. They're in 2.9.0.",
  );
});

test('when no one version has them all', () => {
  assert.match(
    gamedataNoticeText({ kind: 'behind', selected: '2.8.4', suggest: '2.9.1', count: 2, suggest_count: 1, ids: ids('A', 'B') }),
    /skips them\. 2\.9\.1 has 1 of them\.$/,
  );
});

test('a pre-release whose final release is out', () => {
  assert.equal(
    gamedataNoticeText({ kind: 'prerelease', selected: '2.9.0-RC-1', suggest: '2.9.0' }),
    "You're using game data for GTNH 2.9.0-RC-1, and 2.9.0 is out. If your server has updated, switch to it.",
  );
});
