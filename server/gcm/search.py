"""NEI-style name search, for filtering on the server what the page
can't hold all of (Recent crafts). A port of static/js/search.js's
tokenizer and matcher: the same query must match the same items in
both, which tests/test_search.py checks against the JS tests' cases."""


# NEI's prefixes for searching something other than the name, and
# GregTech's for formulas.
_PREFIXES = {"#": "tooltip", "$": "ore", "=": "formula"}


def _tokenize(text):
    """The raw terms of a query: split on whitespace, except a "..."
    run stays one term, and a leading "-" stays with what follows."""
    terms = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        start = i
        if text[i] == "-" and i + 1 < len(text):
            i += 1
        if text[i] in _PREFIXES and text[i + 1:i + 2] == '"':
            i += 1
        if text[i] == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 1
            if i < len(text):
                i += 1  # the closing quote, if there is one
        else:
            while i < len(text) and not text[i].isspace():
                i += 1
        terms.append(text[start:i])
    return terms


def _classify(raw):
    negate = raw.startswith("-") and len(raw) > 1
    rest = raw[1:] if negate else raw
    if rest[:1] in _PREFIXES and len(rest) > 1:
        kind = _PREFIXES[rest[0]]
        content = rest[1:]
        if content.startswith('"'):
            content = content[1:]
            if content.endswith('"'):
                content = content[:-1]
            return (kind, negate, [content.lower()])
        return (kind, negate, [v.lower() for v in content.split("|") if v])
    if rest.startswith('"'):
        content = rest[1:]
        if content.endswith('"'):
            content = content[:-1]
        return ("text", negate, content.lower())
    if rest.startswith("@") and len(rest) > 1:
        return ("mod", negate, rest[1:].lower())
    if "|" in rest:
        return ("or", negate, [v.lower() for v in rest.split("|") if v])
    return ("text", negate, rest.lower())


def parse(query):
    """Terms for matches(); no terms (an empty query) matches anything."""
    return [_classify(raw) for raw in _tokenize((query or "").strip())]


def matches(name, mod, terms, description=None, ore=(), mod_name=None, formula=None):
    """Whether an item called `name` from mod id `mod` matches every term.
    From the game data: its description as plain text (# terms), its
    ore-dictionary names ($ terms), its mod's name (@ terms) and its
    formula with plain digits (= terms; the page's formulaSearchText()).
    Plain terms match the name, description or ore names."""
    name = (name or "").lower()
    mod = (mod or "").lower()
    description = (description or "").lower()
    ore = [o.lower() for o in ore]
    mod_name = (mod_name or "").lower()
    formula = (formula or "").lower()
    for kind, negate, value in terms:
        if kind == "mod":
            hit = value in mod or value in mod_name
        elif kind == "tooltip":
            hit = any(v in description for v in value)
        elif kind == "formula":
            hit = any(v in formula for v in value)
        elif kind == "ore":
            hit = any(v in o for v in value for o in ore)
        else:
            # Plain terms, OR groups and phrases: the name, description
            # or ore names, as NEI's default search modes do.
            values = value if kind == "or" else [value]
            hit = any(v in name or v in description or any(v in o for o in ore) for v in values)
        if hit == negate:
            return False
    return True
