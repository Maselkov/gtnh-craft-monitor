// Small shared helpers: event delegation, HTML escaping for generated
// markup, and quantity and time formatting.

// One-time cleanup of the old all-client-side pin/completion keys
// this page used before pins moved server-side - harmless to leave,
// but nothing reads them anymore, so no reason to keep them around.
export function removeLegacyStorageKeys() {
  ['gtnhCraftMonitor.pinnedCpus', 'gtnhCraftMonitor.completedPins',
   'gtnhCraftMonitor.lastBusyByName', 'gtnhCraftMonitor.lastFinalOutputByName']
    .forEach(k => { try { localStorage.removeItem(k); } catch (e) {} });
}

// Animated icons are animated WebPs (APNGs in older game data). Viewers
// who've asked their browser for reduced motion get each one's first
// frame instead (the server's ?still=1).
const REDUCED_MOTION = typeof matchMedia === 'function'
  && matchMedia('(prefers-reduced-motion: reduce)').matches;

export function iconUrl(path) {
  return `/icons?path=${encodeURIComponent(path)}${REDUCED_MOTION ? '&still=1' : ''}`;
}

// The icon of a fixed item ("modid:internalname:damage"), for icons the
// page shows itself rather than ones the server resolved: its image's
// path differs between game data versions (.webp or .png).
export function iconKeyUrl(key) {
  return `/icons?key=${encodeURIComponent(key)}${REDUCED_MOTION ? '&still=1' : ''}`;
}

// Icons drawn past the item box (a cosmic halo) are bigger images with the
// box in their middle, and the server marks their paths with how much
// bigger: <prefix>~bleed<n>/item/... (gcm/icons.py). Given the class
// .icon-bleed, such an image is scaled up around its centre, so the item
// fills the usual space and the halo spills over what's around it. Every
// bundle so far uses n = 12, which is all the CSS knows.
export function iconClass(base, path) {
  return /^[^/]*~bleed12\//.test(path || '') ? `${base} icon-bleed` : base;
}

// Game data from before aspect icons were exported has none for
// essentia: a round badge with the aspect's initial stands in, in the
// icon's own place (cls). '' for anything else without an icon.
export function essentiaBadgeHtml(it, cls) {
  if (it.icon || it.kind !== 'essentia') return '';
  const initial = String(it.name || it.internal || '?').charAt(0).toUpperCase();
  return `<span class="${cls} essentia-badge" aria-hidden="true">${escapeHtml(initial)}</span>`;
}

// The tooltip's line saying what a fluid or essentia entry is.
export function kindTooltipHtml(kind) {
  if (kind === 'fluid') return '<div class="network-tooltip-fluid">Fluid</div>';
  if (kind === 'essentia') return '<div class="network-tooltip-fluid">Essentia</div>';
  return '';
}

// A one-colour pushpin in the text colour around it (currentColor),
// rather than the 📌 emoji, whose own colours ignore the page's.
export const PIN_ICON = '<svg class="ui-icon" viewBox="0 0 16 16" width="1em" height="1em" aria-hidden="true"><g transform="rotate(45 8 8)" fill="currentColor"><rect x="5" y="1" width="6" height="2" rx="0.6"/><path d="M6.2 3h3.6l1 5H5.2z"/><rect x="3.5" y="7.6" width="9" height="1.8" rx="0.9"/><rect x="7.35" y="9" width="1.3" height="6" rx="0.65"/></g></svg>';

// Same idea for stock rules (stock.js): an alert, and a keep-in-stock target.
export const BELL_ICON = '<svg class="ui-icon" viewBox="0 0 16 16" width="1em" height="1em" aria-hidden="true"><path fill="currentColor" d="M8 1.2a1 1 0 0 1 1 1v.7a4.6 4.6 0 0 1 3.6 4.5v3l1.3 1.7a.6.6 0 0 1-.5 1H2.6a.6.6 0 0 1-.5-1l1.3-1.7v-3A4.6 4.6 0 0 1 7 2.9v-.7a1 1 0 0 1 1-1zM6.2 14.3h3.6a1.8 1.8 0 0 1-3.6 0z"/></svg>';
export const RESTOCK_ICON = '<svg class="ui-icon" viewBox="0 0 16 16" width="1em" height="1em" aria-hidden="true"><g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M13.2 7A5.2 5.2 0 0 0 3.7 4.6"/><path d="M3.4 1.9v2.9h2.9"/><path d="M2.8 9a5.2 5.2 0 0 0 9.5 2.4"/><path d="M12.6 14.1v-2.9H9.7"/></g></svg>';

// A page of the user guides, e.g. docsUrl('in-game.md#crafting-cpus').
export function docsUrl(page) {
  return `https://github.com/Maselkov/gtnh-craft-monitor/blob/main/docs/${page}`;
}

// Where the guide says how to connect an ME Interface Terminal, which
// the crafting plan and a job's tree are read through.
export const TERMINAL_DOCS_URL = docsUrl('in-game.md#me-interface-terminal');

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Clicks on any [data-action] element inside container - including
// elements a redraw adds or changes later - call
// actions[name](element, event). Arguments travel as data-*
// attributes, escaped with escapeHtml like any other attribute value,
// never spliced into JS source inside an HTML attribute.
//
// Action names must be unique page-wide: several containers can see
// the same click (everything bubbles to document), so a name
// registered twice would run twice - refused here instead.
const registeredActions = new Set();
export function delegateActions(container, actions) {
  for (const name of Object.keys(actions)) {
    if (registeredActions.has(name)) throw new Error(`data-action "${name}" registered twice`);
    registeredActions.add(name);
  }
  container.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (!el || !container.contains(el)) return;
    const action = actions[el.dataset.action];
    if (action) action(el, e);
  });
}

// Modal overlays close on a click on the dimmed backdrop itself, not on
// anything inside the dialog. The press has to start on the backdrop
// too: a drag that starts in a text box and is let go of outside the
// dialog clicks the overlay as well (a click goes to the closest element
// holding both ends of it), and mustn't close the dialog.
export function onBackdropClick(overlayId, close) {
  const overlay = document.getElementById(overlayId);
  let pressedOnBackdrop = false;
  overlay.addEventListener('mousedown', (e) => {
    pressedOnBackdrop = e.target === overlay;
  });
  overlay.addEventListener('click', (e) => {
    if (pressedOnBackdrop && e.target === overlay) close();
    pressedOnBackdrop = false;
  });
}

// A dialog's filter box: onChange on every edit. Escape clears it
// rather than closing the dialog, and Enter does nothing rather than
// submit the craft request (the page's key handler is on document, so
// stopping them here is enough).
export function bindFilterInput(input, onChange) {
  input.addEventListener('input', onChange);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.stopPropagation();
      e.preventDefault();
    } else if (e.key === 'Escape' && input.value) {
      e.stopPropagation();
      input.value = '';
      onChange();
    }
  });
}

// The element in root that a redraw made in place of el: same tag and
// the same identifying attributes. null when el has none of them.
const IDENTITY = ['id', 'data-action', 'data-cpu', 'data-key', 'data-mod', 'data-internal', 'data-damage', 'data-tab', 'data-view'];

export function findCopy(root, el) {
  const attrs = IDENTITY.filter(a => el.hasAttribute(a));
  if (!attrs.length) return null;
  const selector = el.tagName.toLowerCase()
    + attrs.map(a => `[${a}="${CSS.escape(el.getAttribute(a))}"]`).join('');
  return root.querySelector(selector);
}

// el.innerHTML = html, done by updating what's already there: an element
// that's still wanted stays the same element, and only the text and
// attributes that differ change. Replacing everything on every redraw (the
// Crafts tab's is every 3s) lost a click pressed during it, a text
// selection and keyboard focus.
//
// Elements are matched to the new markup by their key (id, data-key or
// data-id), looked for anywhere in el, so a keyed item that moves to
// another list (a card pinned, a CPU going idle) moves rather than being
// rebuilt. Give each item of a redrawn list a key, and each section that
// can appear or go a key too, or its neighbours would be matched to the
// wrong section by position. Unkeyed nodes are matched by position among
// their siblings of the same tag.
//
// Icons: a new <img> starts an animated icon over from its first frame, and
// so does changing an <img>'s src. So an icon <img> is never given another
// src. The new markup's icon keeps an <img> showing it in the same place;
// one that can't takes an <img> showing it from what's being dropped
// (never one that's staying where it is), and only an icon with none of
// those gets a new <img>. Moves stay within this document: a template's
// content is a document of its own, and an <img> moved through one
// restarts too - hence importNode.
//
// The style attribute is left alone: the markup can't carry one (the
// Content-Security-Policy), so any there was set through the DOM after a
// redraw, such as a progress bar's width. Such a style stays with the
// element, so set it again after each redraw (as the bars' widths are),
// rather than once when the element is new.
//
// An unkeyed element is never matched to one with another data-action:
// otherwise a button appearing or going would turn the next one into a
// different action, keeping a focus or a click meant for the old one.
export function updateHtml(el, html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const fresh = document.importNode(tpl.content, true);

  // 1. Match new nodes to old ones, changing nothing yet.
  const keyed = new Map();  // key -> old elements with it, in page order
  for (const node of el.querySelectorAll('[id], [data-key], [data-id]')) {
    const key = updateKey(node);
    if (!keyed.has(key)) keyed.set(key, []);
    keyed.get(key).push(node);
  }
  const matchOf = new Map();  // new node -> the old node it updates
  const claimed = new Set();
  matchChildren(el, fresh, keyed, matchOf, claimed);

  // 2. Icons with no <img> in their place take one from what's dropped.
  const spare = new Map();  // src -> unclaimed icon <img>s
  for (const img of el.querySelectorAll('img')) {
    const src = iconSrc(img);
    if (!src || claimed.has(img)) continue;
    if (!spare.has(src)) spare.set(src, []);
    spare.get(src).push(img);
  }
  for (const img of fresh.querySelectorAll('img')) {
    const src = iconSrc(img);
    if (!src || matchOf.has(img)) continue;
    const kept = spare.get(src)?.shift();
    if (kept) {
      matchOf.set(img, kept);
      claimed.add(kept);
    }
  }

  // 3. Apply, from the top down, so a new section is in the page before
  // the items moving into it are; what's left over goes last.
  const leftovers = [];
  applyChildren(el, fresh, matchOf, leftovers);
  for (const [parent, keep] of leftovers) {
    for (const node of [...parent.childNodes]) {
      if (!keep.has(node)) node.remove();
    }
  }
}

const UPDATE_KEYS = ['id', 'data-key', 'data-id'];

function updateKey(node) {
  if (node.nodeType !== Node.ELEMENT_NODE) return null;
  for (const name of UPDATE_KEYS) {
    if (node.hasAttribute(name)) return `${node.tagName} ${name}=${node.getAttribute(name)}`;
  }
  return null;
}

// What makes an <img> an icon: the one test for it in this file.
function iconSrc(node) {
  if (node.nodeType !== Node.ELEMENT_NODE || node.tagName !== 'IMG') return null;
  const src = node.getAttribute('src');
  return src && src.startsWith('/icons?') ? src : null;
}

// What an unkeyed node must share with the one it's matched to.
function nodeKind(node) {
  if (node.nodeType !== Node.ELEMENT_NODE) return node.nodeName;
  return `${node.nodeName} ${node.getAttribute('data-action') || ''}`;
}

function matchChildren(oldParent, newParent, keyed, matchOf, claimed) {
  // Each old node's key, icon and kind, worked out once for the scans below.
  const old = oldParent ? [...oldParent.childNodes].map(node => {
    const src = iconSrc(node);
    const key = src ? null : updateKey(node);
    return { node, src, key, kind: src || key ? null : nodeKind(node) };
  }) : [];
  // Where to look on from for the next unkeyed match, per kind: one cursor
  // for all would let the whitespace after a new element skip past an old
  // element of another kind still waiting for its match.
  const scan = new Map();
  for (const node of newParent.childNodes) {
    let match = null;
    const src = iconSrc(node);
    const key = src ? null : updateKey(node);
    if (src) {
      match = old.find(o => !claimed.has(o.node) && o.src === src)?.node || null;
    } else if (key) {
      // A repeated key takes the next one in page order. Moving any of them
      // here is safe: applying goes top-down, so where this goes is already
      // in its final place, and never inside the element moving there.
      const list = keyed.get(key);
      while (list && list.length && claimed.has(list[0])) list.shift();
      match = (list && list.shift()) || null;
    } else {
      const kind = nodeKind(node);
      for (let i = scan.get(kind) || 0; i < old.length; i++) {
        const o = old[i];
        if (o.kind === kind && !claimed.has(o.node)) {
          match = o.node;
          scan.set(kind, i + 1);
          break;
        }
      }
    }
    if (match) {
      claimed.add(match);
      matchOf.set(node, match);
    }
    // Into a new element too: keyed items and icons inside it may exist.
    if (node.nodeType === Node.ELEMENT_NODE && !src) matchChildren(match, node, keyed, matchOf, claimed);
  }
}

function applyChildren(parent, from, matchOf, leftovers) {
  const sources = [...from.childNodes];  // before the moves below take them
  const wanted = sources.map(node => {
    const match = matchOf.get(node);
    if (!match) return node;
    if (match.nodeType === Node.ELEMENT_NODE) {
      // A style set through the DOM belongs to what the element was: one
      // matched only by position that now has another class drops it.
      if (!updateKey(node) && !iconSrc(node) && match.getAttribute('class') !== node.getAttribute('class')) {
        match.removeAttribute('style');
      }
      updateAttributes(match, node);
    }
    else if (match.nodeValue !== node.nodeValue) match.nodeValue = node.nodeValue;
    return match;
  });
  const keep = new Set(wanted);
  // Into order with the fewest moves: the longest run of nodes already in
  // the right order among themselves stays put, and every other node goes
  // in before the next one after it. Nodes that are going are left where
  // they are (removed last), so dropping one moves nothing.
  const staying = inOrderAlready(parent, wanted);
  let next = null;
  for (let i = wanted.length - 1; i >= 0; i--) {
    const node = wanted[i];
    if (!staying.has(node)) moveInto(parent, node, next);
    next = node;
  }
  leftovers.push([parent, keep]);
  // A kept element takes the new one's children; a new element is its own
  // source, its children swapped for the old nodes they match.
  for (let i = 0; i < wanted.length; i++) {
    const node = wanted[i];
    if (node.nodeType === Node.ELEMENT_NODE && !iconSrc(node)) applyChildren(node, sources[i], matchOf, leftovers);
  }
}

// Of wanted, the nodes already children of parent whose current order is
// the longest increasing run: these needn't move. O(n log n).
function inOrderAlready(parent, wanted) {
  const index = new Map();
  let i = 0;
  for (const child of parent.childNodes) index.set(child, i++);
  const present = wanted.filter(node => index.has(node));
  const tails = [];  // tails[k]: position in present of the smallest tail of a run k+1 long
  const prev = new Array(present.length).fill(-1);
  for (let j = 0; j < present.length; j++) {
    const at = index.get(present[j]);
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (index.get(present[tails[mid]]) < at) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0) prev[j] = tails[lo - 1];
    tails[lo] = j;
  }
  const run = new Set();
  for (let j = tails.length ? tails[tails.length - 1] : -1; j >= 0; j = prev[j]) run.add(present[j]);
  return run;
}

// parent.insertBefore(node, ref), but a node already in the page moves with
// moveBefore, which doesn't take it out of the page on the way: focus, a
// press in progress and a selection inside it all carry on.
function moveInto(parent, node, ref) {
  if (typeof parent.moveBefore === 'function' && parent.isConnected && node.isConnected) parent.moveBefore(node, ref);
  else parent.insertBefore(node, ref);
}

function updateAttributes(el, from) {
  for (const { name, value } of from.attributes) {
    if (name !== 'style' && el.getAttribute(name) !== value) el.setAttribute(name, value);
  }
  for (const { name } of [...el.attributes]) {
    if (name !== 'style' && !from.hasAttribute(name)) el.removeAttribute(name);
  }
}

// <img data-remove-on-error> removes itself when it fails to load (an
// icon missing from images.zip) rather than showing a broken-image
// glyph. error events don't bubble, hence the capture-phase listener.
export function setupImageErrorRemoval() {
  document.addEventListener('error', (e) => {
    if (e.target instanceof HTMLImageElement && e.target.hasAttribute('data-remove-on-error')) {
      e.target.remove();
    }
  }, true);
}
export function formatQty(n) {
  if (n == null) return '?';
  const abs = Math.abs(n);
  if (abs >= 1e21) return n.toExponential(2);
  if (abs >= 1e18) return (n / 1e18).toFixed(2) + 'E';
  if (abs >= 1e15) return (n / 1e15).toFixed(2) + 'P';
  if (abs >= 1e12) return (n / 1e12).toFixed(2) + 'T';
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'G';
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(Math.round(n));
}

// "42s ago", "5m ago", ... for a Unix time in seconds.
export function formatRelativeTime(seconds) {
  const diffSec = Math.max(0, Math.round(Date.now() / 1000 - seconds));
  if (diffSec < 60) return diffSec + 's ago';
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return diffMin + 'm ago';
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return diffHr + 'h ago';
  return Math.round(diffHr / 24) + 'd ago';
}

// "Today", "Yesterday", or the date, for a Unix time in seconds.
export function dayLabel(seconds) {
  const day = new Date(seconds * 1000).setHours(0, 0, 0, 0);
  const today = new Date().setHours(0, 0, 0, 0);
  if (day === today) return 'Today';
  if (today - day <= 86400000 * 1.5) return 'Yesterday';  // 1.5: a DST day is 23 or 25 hours
  return new Date(day).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

// A length of time in seconds, to its two largest units: "42s",
// "4m 12s", "3h 5m", "2d 4h". pad zero-pads the smaller unit ("4m 05s",
// "05s"), for a duration redrawn as it ticks: its length then only
// changes with the units, so the text around it doesn't reflow.
export function formatDuration(seconds, { pad = false } = {}) {
  const s = Math.max(0, Math.round(seconds));
  const two = n => (pad ? String(n).padStart(2, '0') : n);
  if (s < 60) return two(s) + 's';
  if (s < 3600) return `${Math.floor(s / 60)}m ${two(s % 60)}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${two(Math.floor(s % 3600 / 60))}m`;
  return `${Math.floor(s / 86400)}d ${two(Math.floor(s % 86400 / 3600))}h`;
}

// Where a machine (a pattern's provider) is, to go and find it:
// "12, 64, -30", with the dimension when it isn't the overworld. Null
// when OC didn't say.
export function machinePlace(m) {
  if (m.x == null) return null;
  return `${m.x}, ${m.y}, ${m.z}${m.dim ? ` in dim ${m.dim}` : ''}`;
}

// A rate of change given per second, as a signed amount per hour
// ("+1.20M/h"), or per day when under one an hour. unit goes between
// the amount and the "/": ' EU' gives "+1.20G EU/h". Null for a rate
// that rounds to nothing.
export function formatRate(perSecond, unit = '') {
  const perHour = perSecond * 3600;
  const [value, per] = Math.abs(perHour) >= 1 ? [perHour, 'h'] : [perHour * 24, 'd'];
  if (Math.abs(value) < 0.5) return null;
  return `${value > 0 ? '+' : '−'}${formatQty(Math.abs(value))}${unit}/${per}`;
}

// Notices someone can dismiss (gcm/notices.py: the pattern check, for
// operators too; the scripts and game data notices and the Network tab's
// Interface Terminal note, for admins), kept on the server so dismissing
// one holds for everyone who sees it, on every browser. `value` says
// which instance was dismissed - a version, say, so the next one shows
// again. null until loaded: a notice stays hidden rather than flash up
// and vanish.
let dismissedNotes = null;

// Where browsers kept these before the server did: moved over once.
const LEGACY_NOTE_KEYS = {
  recipe_check: 'gtnhCraftMonitor.recipeNoticeDismissed',
  scripts_update: 'gtnhCraftMonitor.scriptsNoticeDismissed',
  gamedata: 'gtnhCraftMonitor.gamedataNoticeDismissed',
  terminal: 'gtnhCraftMonitor.terminalNoteDismissed',
};

// operator: whether the user sees any of them (an operator or admin).
export async function loadDismissedNotes(operator) {
  if (!operator) {
    dismissedNotes = {};
    return;
  }
  try {
    const res = await fetch('/api/notices');
    dismissedNotes = res.ok ? (await res.json()).dismissed : {};
  } catch (e) {
    dismissedNotes = {};
  }
  for (const [name, key] of Object.entries(LEGACY_NOTE_KEYS)) {
    let value = null;
    try { value = localStorage.getItem(key); } catch (e) { continue; }
    if (value === null) continue;
    const saved = name in dismissedNotes ? Promise.resolve(true) : closeNote(name, value);
    // Kept until the server has it: tried again on the next load.
    saved.then((ok) => { if (ok) try { localStorage.removeItem(key); } catch (e) { /* next load */ } });
  }
}

export function noteClosed(name, value = '1') {
  return dismissedNotes === null || dismissedNotes[name] === value;
}

// Resolves to whether the server saved it; if not, it's dismissed here
// until a reload.
export function closeNote(name, value = '1') {
  dismissedNotes = { ...dismissedNotes, [name]: value };
  return fetch(`/api/notices/${encodeURIComponent(name)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  }).then((res) => res.ok, () => false);
}

// The page's colours for the charts (app.css's, for the theme showing):
// a canvas can't use var(--accent), so they're read when a chart is built.
export function chartColors() {
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const accent = color('--accent');
  return {
    line: accent,
    fill: withAlpha(accent, 0.15),
    muted: color('--muted'),
    alert: color('--stale'),
    refill: color('--busy'),
  };
}

// '#rrggbb' at an opacity, as rgba(); anything else is returned as is.
export function withAlpha(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${alpha})`;
}

// Calls fn whenever the page's colours change: the settings menu's Theme
// (theme.js), or the system's light/dark while the Theme is on auto.
export function onThemeChange(fn) {
  window.addEventListener('gcm-themechange', fn);
  if (typeof matchMedia === 'function') {
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', fn);
  }
}
