// Item descriptions: an item's tooltip below its name, from the game
// data (gcm/tooltips.py), with Minecraft's formatting codes - § and a
// character: 0-9a-f a colour, l bold, o italic, n underline, m
// strikethrough, k obfuscated (shown plain), r reset. Pure functions
// (tested under server/tests/js/).

import { escapeHtml } from './util.js';

// GregTech's own font glyphs (its CustomGlyphs), which a browser has no
// font for. Most have a Unicode stand-in GregTech registers itself
// (superscript digits for isotopes, ₀, ⚡, ∅); the rest are pictures
// in its font (the elements, a magnet, brimstone), given the nearest
// Unicode symbol here.
const GT_GLYPHS = {
  '\uE000': '\u{1F701}', // air
  '\uE001': '\u{1F703}', // earth
  '\uE002': '\u{1F702}', // fire
  '\uE003': '\u{1F704}', // water
  '\uE004': '\u2737', // chaos
  '\uE005': '\u2727', // order
  '\uE006': '\u272A', // circle star
  '\uE007': '\u26CF', // pickaxe
  '\uE008': '\u21F2', // arrow corner south east
  '\uE009': '\u21F1', // arrow corner north west
  '\uE00A': '\u26A1', // high voltage
  '\uE00B': '\u2605', // star
  '\uE00C': '\u{1F30C}', // galaxy
  '\uE00D': '\u2728', // sparkles
  '\uE00E': '\u{1F9F2}', // magnet
  '\uE00F': '\u2205', // empty set
  '\uE010': '\u2070', '\uE011': '\u00B9', '\uE012': '\u00B2', '\uE013': '\u00B3', '\uE014': '\u2074',
  '\uE015': '\u2075', '\uE016': '\u2076', '\uE017': '\u2077', '\uE018': '\u2078', '\uE019': '\u2079',
  '\uE01A': '\u2080', // subscript 0
  '\uE01B': '\u05D0', // aleph
  '\uE01C': '\u03C9', // omega
  '\uE01D': '\u300C', // Japanese opening quote
  '\uE01E': '\u{1F70D}', // brimstone
  '\uE01F': '\u2A02', // circle cross
  '\uE020': '?', // subscript question mark
};

// The text with GregTech's glyphs as Unicode.
export function withUnicodeGlyphs(text) {
  return String(text).replace(/[\uE000-\uE020]/g, c => GT_GLYPHS[c] || c);
}

const SCRIPT_DIGITS = '\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089'
  + '\u2070\u00B9\u00B2\u00B3\u2074\u2075\u2076\u2077\u2078\u2079';
const SCRIPT_DIGIT = new RegExp(`[${SCRIPT_DIGITS}]`, 'g');

// A formula as GregTech's NEI formula search (=) compares it: no
// formatting codes, its glyphs as Unicode, sub- and superscript digits
// as plain ones ("SiO₂" -> "SiO2").
export function formulaSearchText(text) {
  return withUnicodeGlyphs(String(text).replace(/§./gs, ''))
    .replace(SCRIPT_DIGIT, c => String(SCRIPT_DIGITS.indexOf(c) % 10));
}

const COLOURS = '0123456789abcdef';
const STYLES = { l: 'mc-bold', o: 'mc-italic', n: 'mc-underline', m: 'mc-strike' };

// The text as markup: spans with mc-<colour> and style classes (the
// page's CSP rules out inline styles). Lines stay \n, and the container
// keeps them and their indents with white-space: pre-wrap. Uncoloured
// text is the game's tooltip grey, which .game-text sets.
export function descriptionHtml(text) {
  // Each line is its own string in the game, so formatting ends with it
  // (a struck-through divider doesn't strike the next line).
  return withUnicodeGlyphs(withoutKeyHints(text)).split('\n').map(lineHtml).join('\n');
}

function lineHtml(text) {
  let html = '';
  let colour = null;
  let styles = [];
  const parts = String(text).split('§');
  parts.forEach((part, i) => {
    let run = part;
    if (i > 0) {
      const code = part.charAt(0).toLowerCase();
      run = part.slice(1);
      if (COLOURS.includes(code)) {
        // As in the game, a colour also ends bold, italic and the rest.
        colour = code;
        styles = [];
      } else if (code === 'r') {
        colour = null;
        styles = [];
      } else if (STYLES[code] && !styles.includes(STYLES[code])) {
        styles = [...styles, STYLES[code]];
      }
    }
    if (!run) return;
    const classes = [...(colour ? ['mc-' + colour] : []), ...styles];
    html += classes.length ? `<span class="${classes.join(' ')}">${escapeHtml(run)}</span>` : escapeHtml(run);
  });
  return html;
}

// The text without its formatting codes, for searching.
export function descriptionPlainText(text) {
  return withUnicodeGlyphs(withoutKeyHints(text)).replace(/§./gs, '');
}

// A name from the game (an item's label, a machine's), which can carry
// formatting codes too ("§a16k§r ME Storage Component"). The page shows
// names in its own colours, so as plain text; the game-style tooltip
// box, always dark as in the game, in the game's (nameHtml()).
export function nameText(name) {
  return withUnicodeGlyphs(String(name)).replace(/§./gs, '');
}

export function nameHtml(name) {
  return lineHtml(withUnicodeGlyphs(String(name)));
}

// GTNH's "§!shift" and "§!ctrl" lines: markers its tooltip renderer
// draws as "[shift]" under the name. The exporter leaves them out (the
// tooltip shows its own "[shift]" where it has the Shift text); this
// covers data from before it did.
function withoutKeyHints(text) {
  return String(text).replace(/\n?§![a-z]+/gi, '');
}
