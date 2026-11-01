import test from 'node:test';
import assert from 'node:assert/strict';
import {
  closeNote, escapeHtml, essentiaBadgeHtml, formatDuration, formatQty, formatRate, iconClass, iconKeyUrl, iconUrl, kindTooltipHtml, loadDismissedNotes, noteClosed,
} from '../../static/js/util.js';

test('formatQty abbreviates like the in-game terminal', () => {
  assert.equal(formatQty(null), '?');
  assert.equal(formatQty(999), '999');
  assert.equal(formatQty(1234), '1.2k');
  assert.equal(formatQty(2500000), '2.50M');
  assert.equal(formatQty(-3e9), '-3.00G');
  assert.equal(formatQty(4e12), '4.00T');
  assert.equal(formatQty(5e15), '5.00P');
  assert.equal(formatQty(6e18), '6.00E');
  assert.equal(formatQty(1.234e24), '1.23e+24');
});

test('escapeHtml covers everything that matters inside an attribute', () => {
  assert.equal(escapeHtml(`<a href="x" title='y'>&</a>`), '&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;');
});

test('iconClass marks icons drawn past the item box', () => {
  assert.equal(iconClass('craft-icon', '2.9.0~ab12cd34~bleed12/item/a/b~0.png'), 'craft-icon icon-bleed');
  assert.equal(iconClass('craft-icon', '2.9.0~faithful32~ab12cd34~bleed12/item/a/b~0.png'), 'craft-icon icon-bleed');
  assert.equal(iconClass('craft-icon', '2.9.0~ab12cd34/item/a/b~0.png'), 'craft-icon');
  // Only the prefix counts, not a matching item name.
  assert.equal(iconClass('craft-icon', '2.9.0~ab12cd34/item/a/x~bleed12/y.png'), 'craft-icon');
  assert.equal(iconClass('craft-icon', null), 'craft-icon');
});

test('icons are asked for by path, or by item key for fixed ones', () => {
  assert.equal(iconUrl('2.9.0~ab12cd34/item/a/b~0.webp'), '/icons?path=2.9.0~ab12cd34%2Fitem%2Fa%2Fb~0.webp');
  assert.equal(iconKeyUrl('appliedenergistics2:item.ItemMultiMaterial:52'),
    '/icons?key=appliedenergistics2%3Aitem.ItemMultiMaterial%3A52');
});

test('formatDuration shows the two largest units', () => {
  assert.equal(formatDuration(0), '0s');
  assert.equal(formatDuration(42.4), '42s');
  assert.equal(formatDuration(252), '4m 12s');
  assert.equal(formatDuration(3 * 3600 + 5 * 60 + 9), '3h 5m');
  assert.equal(formatDuration(2 * 86400 + 4 * 3600 + 59), '2d 4h');
  assert.equal(formatDuration(-5), '0s');
});

test('formatDuration pads the smaller unit when asked', () => {
  assert.equal(formatDuration(5, { pad: true }), '05s');
  assert.equal(formatDuration(245, { pad: true }), '4m 05s');
  assert.equal(formatDuration(3 * 3600 + 5 * 60, { pad: true }), '3h 05m');
  assert.equal(formatDuration(2 * 86400 + 4 * 3600, { pad: true }), '2d 04h');
  assert.equal(formatDuration(252, { pad: true }), '4m 12s');
});

test('formatRate signs the amount and picks hours or days', () => {
  assert.equal(formatRate(1e6 / 3600), '+1.00M/h');
  assert.equal(formatRate(-1200 / 3600, ' mB'), '−1.2k mB/h');
  assert.equal(formatRate(12 / 86400), '+12/d');
  assert.equal(formatRate(0), null);
  assert.equal(formatRate(0.1 / 86400), null);
});

test('essentia without an icon gets a badge with its initial; nothing else does', () => {
  assert.equal(essentiaBadgeHtml({ kind: 'essentia', name: 'ordo' }, 'plan-icon'),
    '<span class="plan-icon essentia-badge" aria-hidden="true">O</span>');
  assert.equal(essentiaBadgeHtml({ kind: 'essentia', name: 'Ordo', icon: 'v/aspect/ordo.png' }, 'plan-icon'), '');
  assert.equal(essentiaBadgeHtml({ kind: 'item', name: 'Iron Ingot' }, 'plan-icon'), '');
});

test('the tooltip says what a fluid or essentia is', () => {
  assert.match(kindTooltipHtml('fluid'), />Fluid</);
  assert.match(kindTooltipHtml('essentia'), />Essentia</);
  assert.equal(kindTooltipHtml('item'), '');
});

// A fake server for the notice endpoints, and a fake localStorage.
function fakeNotices(dismissed, legacy = {}, { failPut = false } = {}) {
  const puts = [];
  const stored = new Map(Object.entries(legacy));
  const real = {
    fetch: Object.getOwnPropertyDescriptor(globalThis, 'fetch'),
    localStorage: Object.getOwnPropertyDescriptor(globalThis, 'localStorage'),
  };
  globalThis.fetch = async (url, opts = {}) => {
    if (opts.method === 'PUT') {
      puts.push([url, JSON.parse(opts.body).value]);
      return { ok: !failPut };
    }
    return { ok: true, json: async () => ({ dismissed }) };
  };
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (k) => (stored.has(k) ? stored.get(k) : null), removeItem: (k) => stored.delete(k),
  } });
  const restore = () => {
    for (const [name, d] of Object.entries(real)) {
      if (d) Object.defineProperty(globalThis, name, d);
      else delete globalThis[name];
    }
  };
  return { puts, stored, restore };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

test('a notice stays hidden until the server says what admins dismissed', async () => {
  assert.equal(noteClosed('recipe_check', '2.9.0-RC-1>2.9.0'), true);
  const fake = fakeNotices({ recipe_check: '2.9.0-RC-1>2.9.0' });
  try {
    await loadDismissedNotes(true);
    assert.equal(noteClosed('recipe_check', '2.9.0-RC-1>2.9.0'), true);
    assert.equal(noteClosed('recipe_check', '2.9.0>2.9.1'), false);  // the next upgrade shows
    assert.equal(noteClosed('terminal'), false);
  } finally {
    fake.restore();
  }
});

test('dismissing a notice saves it on the server, and holds here if that fails', async () => {
  const fake = fakeNotices({}, {}, { failPut: true });
  try {
    await loadDismissedNotes(true);
    assert.equal(await closeNote('scripts_update', '7.2.0'), false);
    assert.deepEqual(fake.puts, [['/api/notices/scripts_update', '7.2.0']]);
    assert.equal(noteClosed('scripts_update', '7.2.0'), true);
  } finally {
    fake.restore();
  }
});

test('a dismissal this browser kept is moved to the server once', async () => {
  const fake = fakeNotices({ gamedata: '2.9.0>2.9.1' }, {
    'gtnhCraftMonitor.terminalNoteDismissed': '1',
    'gtnhCraftMonitor.gamedataNoticeDismissed': '2.8.4>2.9.0',
  });
  try {
    await loadDismissedNotes(true);
    await settle();
    // The server's own dismissal wins over an older one kept here.
    assert.deepEqual(fake.puts, [['/api/notices/terminal', '1']]);
    assert.equal(noteClosed('terminal'), true);
    assert.equal(noteClosed('gamedata', '2.9.0>2.9.1'), true);
    assert.equal(fake.stored.size, 0);
  } finally {
    fake.restore();
  }
});
