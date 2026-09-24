// Craft amount expression evaluator (10k, 4+3*2). Pure functions
// (tested under server/tests/js/).

// Simple four-function expression evaluator (+, -, *, /, parens),
// with metric-prefixed numbers (10k, 1.5m, 2b) as the base number
// token - not a separate feature bolted on, the OLD plain-number
// parseAmountInput() is now just the trivial one-token case of this
// same grammar. Hand-written rather than eval()/new Function() (a
// real code-injection vector to avoid even in a low-stakes tool) or
// a math library (mathjs etc. handle units/matrices/complex numbers
// - wildly more than four-function arithmetic needs, for the cost
// of an actual new script dependency). Standard recursive-descent
// structure: expr -> term (+|- term)*, term -> factor (*|/  factor)*,
// factor -> NUMBER | '(' expr ')' | '-' factor (unary minus, for
// something like "(10-15)+20" - the final amount is still validated
// as > 0 by the caller regardless of intermediate negative values).
function tokenizeAmountExpression(text) {
  const s = text.replace(/\s+/g, '');  // whitespace carries no
                                        // meaning here ("10 k" and
                                        // "4 + 3" mean the same as
                                        // "10k" and "4+3") - stripped
                                        // once up front rather than
                                        // skipped token-by-token
  if (!s) return null;
  const tokens = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if ('+-*/()'.indexOf(c) !== -1) {
      tokens.push({ type: c });
      i++;
      continue;
    }
    const m = s.slice(i).match(/^([0-9]*\.?[0-9]+)([kmb])?/i);
    if (!m || !m[0]) return null;  // unrecognized character - malformed
    let n = parseFloat(m[1]);
    const suffix = m[2] ? m[2].toLowerCase() : null;
    if (suffix === 'k') n *= 1e3;
    else if (suffix === 'm') n *= 1e6;
    else if (suffix === 'b') n *= 1e9;
    tokens.push({ type: 'num', value: n });
    i += m[0].length;
  }
  return tokens;
}

// Returns {ok: true, value: N} or {ok: false, error: '...'}.
export function evaluateAmountExpression(text) {
  if (text == null) return { ok: false, error: 'empty' };
  const tokens = tokenizeAmountExpression(String(text));
  if (!tokens || tokens.length === 0) return { ok: false, error: 'empty or unrecognized input' };

  let pos = 0;
  const peek = () => tokens[pos];

  function parseFactor() {
    const t = peek();
    if (!t) throw new Error('unexpected end of expression');
    if (t.type === '(') {
      pos++;
      const v = parseExpr();
      if (!peek() || peek().type !== ')') throw new Error('missing closing parenthesis');
      pos++;
      return v;
    }
    if (t.type === '-') {
      pos++;
      return -parseFactor();
    }
    if (t.type === 'num') {
      pos++;
      return t.value;
    }
    throw new Error('unexpected "' + t.type + '"');
  }

  function parseTerm() {
    let v = parseFactor();
    while (peek() && (peek().type === '*' || peek().type === '/')) {
      const op = tokens[pos++].type;
      const rhs = parseFactor();
      if (op === '*') {
        v = v * rhs;
      } else {
        if (rhs === 0) throw new Error('division by zero');
        v = v / rhs;
      }
    }
    return v;
  }

  function parseExpr() {
    let v = parseTerm();
    while (peek() && (peek().type === '+' || peek().type === '-')) {
      const op = tokens[pos++].type;
      const rhs = parseTerm();
      v = op === '+' ? v + rhs : v - rhs;
    }
    return v;
  }

  try {
    const result = parseExpr();
    if (pos !== tokens.length) throw new Error('unexpected trailing input');
    if (!isFinite(result)) throw new Error('result is not a finite number');
    return { ok: true, value: result };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}
