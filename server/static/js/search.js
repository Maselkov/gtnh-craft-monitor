// Network tab search: NEI-style query tokenizer, matcher and the
// syntax-highlight overlay markup. Pure functions (tested under
// server/tests/js/).

// Advanced search, matching NEI's own syntax (confirmed against the
// GTNH wiki, a community guide, and real screenshots - not
// independently verified against real NEI beyond that, so built for
// exactly the documented/shown forms below, not guessed extensions):
//   plain terms        AND'd together, e.g. "neutronium molten"
//                      matches "Molten Neutronium" regardless of
//                      word order (each term is its own independent
//                      substring check, not one combined phrase)
//   term1|term2        OR within one term, e.g. "copper|tin"
//   @ModName           restrict to a mod - matched against the raw
//                      modid (e.g. "thaumcraft"), not a display
//                      name table this project doesn't have, so it
//                      degrades gracefully rather than exactly
//                      matching NEI for mods where the id diverges
//                      more from the marketed name
//   -term              exclude - term can itself be an @mod filter,
//                      an OR group, or a quoted phrase, a natural
//                      consequence of composing negate with the
//                      other term types rather than a separately
//                      tested feature
//   "exact phrase"     literal substring match INCLUDING spaces,
//                      not split into separate AND'd words - so
//                      "air shard" matches only where that exact
//                      phrase appears, not items with "air" and
//                      "shard" somewhere unrelated in the name.
//                      Still a substring check, not full-name
//                      equality - "Small Air Shard" still matches.
//                      An unterminated quote (no closing ") just
//                      runs to the end of the input rather than
//                      erroring - the same forgiving spirit as
//                      every other malformed-input case here.

// Shared by both the matcher and the highlighter below, so they can
// never tokenize the same input two different ways - splits on
// whitespace as before, EXCEPT a "..." run is kept as one single
// term (spaces and all) rather than being split apart, and a
// leading "-" stays glued to whatever follows it (a quote, an @, or
// a plain word) rather than being its own separate token.
function tokenizeSearchText(text) {
  const tokens = [];
  let i = 0;
  while (i < text.length) {
    if (/\s/.test(text[i])) {
      const start = i;
      while (i < text.length && /\s/.test(text[i])) i++;
      tokens.push({ type: 'ws', raw: text.slice(start, i) });
      continue;
    }
    const start = i;
    if (text[i] === '-' && i + 1 < text.length) i++;
    if (text[i] === '"') {
      i++;
      while (i < text.length && text[i] !== '"') i++;
      if (i < text.length) i++;  // consume the closing quote if there is one
    } else {
      while (i < text.length && !/\s/.test(text[i])) i++;
    }
    tokens.push({ type: 'term', raw: text.slice(start, i) });
  }
  return tokens;
}

// Splits a raw term into {negate, rest} - shared by the matcher and
// the highlighter so "what counts as a leading negation dash" can
// never disagree between the two.
function stripNegatePrefix(term) {
  if (term.startsWith('-') && term.length > 1) {
    return { negate: true, rest: term.slice(1) };
  }
  return { negate: false, rest: term };
}

function classifySearchTerm(rawTerm) {
  const { negate, rest } = stripNegatePrefix(rawTerm);
  if (rest.startsWith('"')) {
    let content = rest.slice(1);
    if (content.endsWith('"')) content = content.slice(0, -1);
    return { type: 'text', negate, value: content.toLowerCase() };
  }
  if (rest.startsWith('@') && rest.length > 1) {
    return { type: 'mod', negate, value: rest.slice(1).toLowerCase() };
  }
  if (rest.includes('|')) {
    const values = rest.split('|').map(s => s.toLowerCase()).filter(Boolean);
    return { type: 'or', negate, values };
  }
  return { type: 'text', negate, value: rest.toLowerCase() };
}

function parseSearchQuery(query) {
  return tokenizeSearchText(query.trim())
    .filter(t => t.type === 'term')
    .map(t => classifySearchTerm(t.raw));
}

function searchTermMatches(name, mod, term) {
  let matched;
  if (term.type === 'mod') {
    matched = mod.toLowerCase().includes(term.value);
  } else if (term.type === 'or') {
    matched = term.values.some(v => name.includes(v));
  } else {
    // Covers both a plain word AND a quoted phrase - a quoted
    // phrase is just a 'text' term whose value happens to contain
    // spaces, so it reuses this exact same substring check rather
    // than needing its own separate match branch.
    matched = name.includes(term.value);
  }
  return term.negate ? !matched : matched;
}

// Every term must match (AND by default) - an empty term list (no
// query typed) vacuously matches everything, which is exactly the
// "no filter" behavior wanted.
function itemMatchesSearch(it, parsedTerms) {
  const name = (it.name || '').toLowerCase();
  const mod = it.mod || '';
  return parsedTerms.every(term => searchTermMatches(name, mod, term));
}

// Syntax highlighting for the search box - matching NEI's own colors
// (confirmed from real screenshots: @mod pink, -exclude blue, "
// quote marks orange - only the quote CHARACTERS themselves, the
// text inside stays plain), via an overlay technique rather than
// styling the input directly: a plain <input> can't render mixed
// inline colors in its own value at all (a real limitation of the
// element, not something CSS can work around), and a contenteditable
// div rewritten on every keystroke is a well-known source of
// cursor-jumping bugs unless you carefully save/restore selection
// via the Range API. Simpler instead to make the real input's OWN
// text invisible (color: transparent, caret-color kept) and let it
// keep doing all the actual work - typing, cursor, selection, focus
// - completely natively, then lay a non-interactive div on top
// showing the same text re-rendered with colored spans, rebuilt on
// every keystroke.
//
// Built on the SAME tokenizeSearchText()/stripNegatePrefix() the
// matcher uses above, not a separate parallel implementation - the
// two could only ever disagree about what a "-" or a quote means if
// they were maintained as two different parsers, so they aren't.
// OR groups (|) aren't specially colored at all - no confirmed color
// exists for that case in any screenshot, so this doesn't invent one.
function buildTermHighlightHtml(rawTerm) {
  const { negate, rest } = stripNegatePrefix(rawTerm);
  const prefixHtml = negate ? '<span class="search-hl-exclude">-</span>' : '';
  if (rest.startsWith('"')) {
    const hasCloseQuote = rest.length > 1 && rest.endsWith('"');
    const inner = hasCloseQuote ? rest.slice(1, -1) : rest.slice(1);
    const openQuoteHtml = '<span class="search-hl-quote">"</span>';
    const closeQuoteHtml = hasCloseQuote ? '<span class="search-hl-quote">"</span>' : '';
    return prefixHtml + openQuoteHtml + escapeHtml(inner) + closeQuoteHtml;
  }
  if (rest.startsWith('@') && rest.length > 1) {
    return prefixHtml + `<span class="search-hl-mod">${escapeHtml(rest)}</span>`;
  }
  return prefixHtml + escapeHtml(rest);
}

function buildSearchHighlightHtml(text) {
  if (!text) return '';
  return tokenizeSearchText(text)
    .map(t => t.type === 'ws' ? escapeHtml(t.raw) : buildTermHighlightHtml(t.raw))
    .join('');
}
