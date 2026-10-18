"""NEI-style name search, for filtering on the server what the page
can't hold all of (Recent crafts). A port of static/js/search.js's
tokenizer and matcher: the same query must match the same items in
both, which tests/test_search.py checks against the JS tests' cases."""


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


def matches(name, mod, terms):
    """Whether an item called `name` from mod id `mod` matches every term."""
    name = (name or "").lower()
    mod = (mod or "").lower()
    for kind, negate, value in terms:
        if kind == "mod":
            hit = value in mod
        elif kind == "or":
            hit = any(v in name for v in value)
        else:
            hit = value in name
        if hit == negate:
            return False
    return True
