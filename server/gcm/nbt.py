"""Item NBT as OpenComputers reports it (the `tag` field, present only with
allowItemStackNBTTags enabled in OpenComputers.cfg): gzipped binary NBT,
hex-encoded by oc/network_browser.lua. Used to tell apart stacks AE2
keeps separate but that share an item id - one crop's seeds with
different stats, bees of different species - which OC otherwise reports
identically."""

import gzip
import hashlib
import json
import struct
import zlib

# Tags are capped at 2 KiB on the Lua side; this bounds what a malformed
# or hostile one can inflate to.
MAX_DECOMPRESSED = 1 << 20
MAX_DEPTH = 64

END, BYTE, SHORT, INT, LONG, FLOAT, DOUBLE, BYTE_ARRAY, STRING, LIST, COMPOUND, INT_ARRAY, LONG_ARRAY = range(13)


class NbtError(ValueError):
    pass


class _Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise NbtError("truncated NBT")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def unpack(self, fmt):
        return struct.unpack(">" + fmt, self.take(struct.calcsize(">" + fmt)))[0]

    def string(self):
        return self.take(self.unpack("H")).decode("utf-8", errors="replace")

    def payload(self, tag_type, depth):
        """A tag's value as (type, value): compounds are dicts of name ->
        (type, value), lists are (element type, [values])."""
        if depth > MAX_DEPTH:
            raise NbtError("NBT nested too deeply")
        if tag_type == BYTE:
            return self.unpack("b")
        if tag_type == SHORT:
            return self.unpack("h")
        if tag_type == INT:
            return self.unpack("i")
        if tag_type == LONG:
            return self.unpack("q")
        if tag_type == FLOAT:
            return self.unpack("f")
        if tag_type == DOUBLE:
            return self.unpack("d")
        if tag_type == BYTE_ARRAY:
            return list(self.take(self.unpack("i")))
        if tag_type == STRING:
            return self.string()
        if tag_type == LIST:
            element_type = self.unpack("b")
            count = self.unpack("i")
            return (element_type, [self.payload(element_type, depth + 1) for _ in range(max(count, 0))])
        if tag_type == COMPOUND:
            out = {}
            while True:
                child_type = self.unpack("b")
                if child_type == END:
                    return out
                name = self.string()
                out[name] = (child_type, self.payload(child_type, depth + 1))
        if tag_type in (INT_ARRAY, LONG_ARRAY):
            code = "i" if tag_type == INT_ARRAY else "q"
            count = self.unpack("i")
            if count < 0:
                raise NbtError("negative array length")
            return list(struct.unpack(f">{count}{code}", self.take(count * struct.calcsize(code))))
        raise NbtError(f"unknown NBT tag type {tag_type}")


def _decompress(data):
    if data[:2] != b"\x1f\x8b":
        return data  # already uncompressed
    inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
    out = inflater.decompress(data, MAX_DECOMPRESSED)
    if len(out) >= MAX_DECOMPRESSED:
        raise NbtError("NBT too large")
    return out


def parse_hex(tag_hex):
    """The root compound of a hex-encoded (gzipped) NBT tag, as a dict of
    name -> (type, value). Raises NbtError if it isn't valid."""
    try:
        data = _decompress(bytes.fromhex(tag_hex))
    except (ValueError, TypeError, zlib.error, EOFError, gzip.BadGzipFile) as e:
        raise NbtError(f"undecodable NBT tag: {e}") from e
    reader = _Reader(data)
    try:
        if reader.unpack("b") != COMPOUND:
            raise NbtError("NBT root isn't a compound")
        reader.string()  # the root's name, always empty
        return reader.payload(COMPOUND, 0)
    except struct.error as e:
        raise NbtError(f"malformed NBT: {e}") from e


def _canonical(tag_type, value):
    if tag_type == COMPOUND:
        return [tag_type, {k: _canonical(*v) for k, v in sorted(value.items())}]
    if tag_type == LIST:
        element_type, items = value
        return [tag_type, element_type, [_canonical(element_type, v)[1:] for v in items]]
    if tag_type in (FLOAT, DOUBLE):
        return [tag_type, struct.pack(">d", value).hex()]
    return [tag_type, value]


def canonical_hash(root):
    """A short id for this NBT that doesn't depend on the order its keys
    were written in (Java serializes compounds from a HashMap)."""
    text = json.dumps(_canonical(COMPOUND, root), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def plain(tag_type, value):
    """The (type, value) tree as ordinary Python values."""
    if tag_type == COMPOUND:
        return {k: plain(*v) for k, v in value.items()}
    if tag_type == LIST:
        element_type, items = value
        return [plain(element_type, v) for v in items]
    return value


def _find(tree, wanted):
    """The first value, depth-first, under any key whose lowercased name
    is in wanted (name -> value dict), or {} if none."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                lower = key.lower()
                if lower in wanted and lower not in found and isinstance(val, (int, float)):
                    found[lower] = val
                walk(val)
        elif isinstance(node, list):
            for val in node:
                walk(val)

    walk(tree)
    return found


def _describe_crop_stats(tree):
    # Seed stats as IC2 and CropsNH store them. Found anywhere in the tag
    # rather than at one fixed path, so a layout change doesn't lose them.
    stats = _find(tree, {"growth", "gain", "resistance"})
    if len(stats) < 3:
        return None
    return f"Gr {stats['growth']:g} · Ga {stats['gain']:g} · Re {stats['resistance']:g}"


def _describe_bee(tree):
    # Forestry genomes (bees, saplings, butterflies): the species is
    # already in the label, so what's left to tell them apart is whether
    # they've been analyzed and whether they're pure-bred.
    genome = tree.get("Genome")
    if not isinstance(genome, dict):
        return None
    parts = []
    chromosomes = genome.get("Chromosomes")
    if isinstance(chromosomes, list) and chromosomes and isinstance(chromosomes[0], dict):
        species = chromosomes[0]
        active, inactive = species.get("UID0"), species.get("UID1")
        if active and inactive and active != inactive:
            parts.append("Hybrid " + str(inactive).rsplit(".", 1)[-1].removeprefix("species"))
    if "IsAnalyzed" in tree:
        parts.append("Analyzed" if tree["IsAnalyzed"] else "Unanalyzed")
    return " · ".join(parts) or None


_DESCRIBERS = (_describe_crop_stats, _describe_bee)


def describe(root):
    """A short human-readable summary of what makes this stack differ from
    others with the same label, or None if nothing recognizable."""
    tree = plain(COMPOUND, root)
    for describer in _DESCRIBERS:
        try:
            text = describer(tree)
        except (TypeError, ValueError, KeyError, AttributeError):
            text = None
        if text:
            return text
    return None
