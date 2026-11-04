"""MCP prompts: ready-made requests a user picks from their app (slash
commands in Claude Code, /mcp__<server>__base_report; the connector's
menu in claude.ai). Each is a user message naming the tools to call and
how to lay out the answer, so a common question gets the same thorough
answer every time without the user spelling it out."""

from gcm import auth


class PromptError(Exception):
    """A prompts/get that can't be answered (an invalid params error)."""


BASE_REPORT = """Give me a short report on how my GTNH base is doing right now.

Use the tools, don't guess:
1. `power`: stored EU, how full, and the trend (hours to empty or full).
2. `current_crafts`: how many CPUs are busy and what the longest-running jobs are making.
3. `inventory_changes` (sort "falling", range "day"): the items used up fastest, with hours to empty.
4. `stock_rules`: keep-in-stock targets that are low, failing or waiting, and my alerts that are low.
5. For any job that's been running long, `craft_job` to see whether a step is held up.

Lay it out as four short sections: Power, Crafting, Running low, Needs attention. Lead each with one
line saying whether it's fine. Give amounts with units and times as "in 5 h" or "2 days". Say how old
the data is if it's more than a few minutes old. Don't suggest new keep-in-stock targets here."""

SLOW_CRAFT = """The crafting job on CPU {cpu} seems slow. Find out why.

1. `craft_job` for CPU {cpu}: which steps are held up (stalled, busy with another job, or blocked)
   and the machines holding them, with coordinates.
2. For each held-up step's item, `base_patterns` to see which machines make it in this base.
3. If a machine is busy with another job (`machine_busy_with`), say which job and CPU.

Tell me which machine is the bottleneck and where it is, and what would most likely speed it up
(another machine, a faster tier, freeing the busy one). Don't cancel or request anything."""

SLOW_CRAFT_ANY = """My crafts seem slow. Find out why.

1. `current_crafts`: the busy CPUs. Pick the job that's been running longest for how far along it is.
2. `craft_job` for it: which steps are held up and the machines holding them, with coordinates.
3. `craft_stats` (range "week"): the machines that held jobs up the longest.
4. For the worst bottleneck's item, `base_patterns` to see which machines make it in this base.

Tell me which machines are the bottlenecks and where they are, and what would most likely help.
Don't cancel or request anything."""

RESTOCK_CHECK = """Check what's running low in my base and what to do about it.

1. `stock_rules`: keep-in-stock targets that are low, failing or waiting, and my low alerts.
2. `inventory_changes` (sort "falling", range "week"): items being used up, with hours to empty.
3. For each item that will run out within a day or two:
   - `get_item`: its `crafts_last_week` and whether it's craftable.
   - `item_history` (range "week"): does it refill on its own, or in a sawtooth? Then something
     outside this server (a Level Maintainer, passive production) keeps it, and I should hear that
     rather than get a new target.
   - If it's craftable and nothing keeps it up, `plan_craft` for a sensible amount to see whether
     the ingredients are there.

List the items worst first: how long until it's empty, how it's kept up now, and what you'd do.
Don't request crafts or change targets: ask me first."""

UPDATE_CHECK = """I'm updating GTNH (or just did). Check what it breaks in my base.

1. `pattern_check`: the patterns the update broke, moved to another machine, or changed. If it
   says it's preparing, wait a minute and call it again.
2. For a broken pattern, `find_recipes` for what it makes, to see how the new version makes it.

Group the patterns by machine (with coordinates) so I can fix them in one trip each, and for each
say what to change: the new machine, the new fluid, circuit or coil, or the new amounts."""

PROMPTS = [
    {
        "name": "base_report",
        "title": "Base report",
        "description": "Power, crafting, what's running low and what needs attention, in one go.",
        "arguments": [],
        "text": lambda args: BASE_REPORT,
    },
    {
        "name": "slow_craft",
        "title": "Why is a craft slow?",
        "description": "Finds the machine holding a job up, and where it is.",
        "arguments": [{"name": "cpu", "description": "The CPU running the job (leave out for the longest-running job).",
                       "required": False}],
        "text": lambda args: SLOW_CRAFT.format(cpu=args["cpu"]) if args.get("cpu") else SLOW_CRAFT_ANY,
    },
    {
        "name": "restock_check",
        "title": "Restock check",
        "description": "What's running low, how each item is kept up now, and what to do (without changing anything).",
        "arguments": [],
        "text": lambda args: RESTOCK_CHECK,
    },
    {
        "name": "update_check",
        "title": "GTNH update check",
        "description": "The patterns a GTNH update broke, grouped by machine, with what to change.",
        "arguments": [],
        "operator": True,
        "text": lambda args: UPDATE_CHECK,
    },
]

_BY_NAME = {p["name"]: p for p in PROMPTS}


def _offered(prompt, user):
    return not prompt.get("operator") or auth.is_operator(user)


def listing(user):
    """prompts/list's prompts for a user, in a fixed order."""
    return [
        {k: v for k, v in p.items() if k in ("name", "title", "description", "arguments")}
        for p in PROMPTS if _offered(p, user)
    ]


def get(name, arguments, user):
    """prompts/get's result, or PromptError."""
    prompt = _BY_NAME.get(name)
    if prompt is None or not _offered(prompt, user):
        raise PromptError(f"Unknown prompt: {name}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict) or not all(isinstance(v, str) for v in arguments.values()):
        raise PromptError("arguments must be an object of strings")
    known = {a["name"] for a in prompt["arguments"]}
    unknown = set(arguments) - known
    if unknown:
        raise PromptError(f"Unknown argument(s): {', '.join(sorted(unknown))}")
    for a in prompt["arguments"]:
        if a.get("required") and not arguments.get(a["name"]):
            raise PromptError(f"Missing argument: {a['name']}")
    return {
        "description": prompt["description"],
        "messages": [{"role": "user", "content": {"type": "text", "text": prompt["text"](arguments)}}],
    }
