"""Builds OC-style `tag` values (gzipped binary NBT, hex-encoded) for tests."""

import gzip
import struct

from gcm import nbt


def _name(s):
    raw = s.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _payload(tag_type, value):
    if tag_type == nbt.BYTE:
        return struct.pack(">b", value)
    if tag_type == nbt.SHORT:
        return struct.pack(">h", value)
    if tag_type == nbt.INT:
        return struct.pack(">i", value)
    if tag_type == nbt.FLOAT:
        return struct.pack(">f", value)
    if tag_type == nbt.STRING:
        return _name(value)
    if tag_type == nbt.LIST:
        element_type, items = value
        return struct.pack(">bi", element_type, len(items)) + b"".join(
            _payload(element_type, v) for v in items
        )
    if tag_type == nbt.COMPOUND:
        return compound_body(value)
    raise ValueError(tag_type)


def compound_body(entries):
    """entries: list of (name, type, value), written in that order."""
    out = b""
    for name, tag_type, value in entries:
        out += struct.pack(">b", tag_type) + _name(name) + _payload(tag_type, value)
    return out + b"\x00"


def tag_hex(entries):
    return gzip.compress(b"\x0a" + _name("") + compound_body(entries)).hex()


def seed_tag(crop, growth, gain, resistance):
    return tag_hex([
        ("crop", nbt.STRING, crop),
        ("growth", nbt.BYTE, growth),
        ("gain", nbt.BYTE, gain),
        ("resistance", nbt.BYTE, resistance),
    ])
