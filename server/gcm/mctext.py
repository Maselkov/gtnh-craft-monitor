"""Minecraft's formatting codes - § and a character: a colour, bold,
reset and so on - which some names from the game carry ("§a16k§r ME
Storage Component"). The page colours them (static/js/description.js);
text going anywhere else drops them."""

import re

_CODE = re.compile("§.", re.S)


def plain(text):
    """The text without its formatting codes."""
    return _CODE.sub("", str(text))
