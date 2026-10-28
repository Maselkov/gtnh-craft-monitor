"""Discord component embeds: a link preview built from Components V2
instead of the plain OpenGraph card (discord.com/developers/docs, Link
Previews -> Component Embeds). Discord reads a JSON payload from a
<script id="discord:component-embed" type="application/json"> in the
page's <head>; the OG tags stay as the fallback whenever the payload is
rejected (too big, an invalid component, an image that won't load in
time). Never run, so the page's CSP doesn't apply to it.

Discord's limits, which a bad payload breaks silently (no embed at all):
3,000 bytes of JSON, escapes counted at full length; one top-level
Container; at most 40 components and 10 media gallery items; link
buttons (style 5) only."""

import json
import re

MAX_BYTES = 3000
# The charts' line colour (gcm/charts.py), so the bar matches the image.
ACCENT = 0x5FB3FF
# Busy CPUs listed on the crafts embed; the rest are a "+N more" line.
MAX_CPUS = 8

CONTAINER, ACTION_ROW, BUTTON, SECTION, TEXT, THUMBNAIL, GALLERY, SEPARATOR = 17, 1, 2, 9, 10, 11, 12, 14

# Escaping [ and ] is enough to stop a link, : an autolink or an emoji.
_MARKDOWN = re.compile(r"([\\*_~`|>#\-\[\]<:])")


def md_escape(text):
    """Game-supplied text (item and CPU names) as literal Discord
    markdown: a name can't make a heading, a link or a mention."""
    return _MARKDOWN.sub(r"\\\1", str(text))


def text(content):
    return {"type": TEXT, "content": content}


def section(content, image_url):
    """Text with an image beside it, or just the text without one: a
    Section must have its accessory."""
    if not image_url:
        return text(content)
    return {"type": SECTION, "components": [text(content)],
            "accessory": {"type": THUMBNAIL, "media": {"url": image_url}}}


def gallery(image_url):
    return {"type": GALLERY, "items": [{"media": {"url": image_url}}]}


def open_button(page_url):
    return {"type": ACTION_ROW, "components": [
        {"type": BUTTON, "style": 5, "url": page_url, "label": "Open"}]}


def container(children):
    return {"component": {"type": CONTAINER, "accent_color": ACCENT, "components": children}}


def crafts(jobs, desc, icon_url, page_url):
    """jobs: the game's latest CPU report; icon_url(job) gives the
    absolute URL of its output's icon, or None. Lists as many busy CPUs
    as fit in the size limit, up to MAX_CPUS."""
    busy = [j for j in jobs if j.get("busy")]
    cpus = []
    for job in busy[:MAX_CPUS]:
        line = f"**{md_escape(job.get('name') or 'CPU')}**"
        if job.get("final_output"):
            line += f"\n{md_escape(job['final_output'])}"
        cpus.append(section(line, icon_url(job)))
    for shown in range(len(cpus), -1, -1):
        children = [text(f"## Crafts\n{md_escape(desc)}"), *cpus[:shown]]
        if len(busy) > shown:
            children.append(text(f"-# +{len(busy) - shown} more"))
        children += [{"type": SEPARATOR}, open_button(page_url)]
        payload = container(children)
        if fits(payload):
            break
    return payload


def item(label, desc, icon_url, chart_url, page_url):
    children = [section(f"## {md_escape(label)}\n{md_escape(desc)}", icon_url)]
    if chart_url:
        children.append(gallery(chart_url))
    children.append(open_button(page_url))
    return container(children)


def power(title, desc, chart_url, page_url):
    children = [text(f"## {md_escape(title)}\n{md_escape(desc)}")]
    if chart_url:
        children.append(gallery(chart_url))
    children.append(open_button(page_url))
    return container(children)


def _body(payload):
    """The payload as sent: <, > and & escaped so no text in it can
    close the tag."""
    return (
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def fits(payload):
    return len(_body(payload).encode("utf-8")) <= MAX_BYTES


def script_tag(payload):
    """The <script> carrying a payload, or "" when there's none or it's
    over Discord's size limit (the OG card shows instead)."""
    if not payload or not fits(payload):
        return ""
    body = _body(payload)
    return f'<script id="discord:component-embed" type="application/json">{body}</script>\n'
