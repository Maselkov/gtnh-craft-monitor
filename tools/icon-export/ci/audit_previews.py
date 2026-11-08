#!/usr/bin/env python3
"""Find structural preview gaps; custom Java drawing still needs visual review.

    python tools/icon-export/ci/audit_previews.py OUT/recipes.json.gz > audit.json

The report separates definite unplaced stacks from metadata/display callbacks
that need investigation. It does not equate recipe coverage with NEI fidelity.
"""
import collections
import gzip
import json
import sys


SUPPORTED_CALLBACKS = {
    "gregtech.api.recipe.maps.AssemblyLineFrontend#addProgressBar",
    "tectech.recipe.BECAssemblyFrontend#drawSpecialInfo",
}


def metadata_stacks(value):
    if isinstance(value, dict):
        for kind in ("item", "fluid"):
            if isinstance(value.get(kind), str) and type(value.get("amount")) is int:
                yield kind, value[kind], value["amount"]
        for child in value.values():
            yield from metadata_stacks(child)
    elif isinstance(value, list):
        for child in value:
            yield from metadata_stacks(child)


def audit(data):
    findings = []
    totals = collections.Counter()
    for name, category in sorted(data["maps"].items()):
        shared = category.get("preview") or {}
        reasons = collections.Counter()
        samples = {}
        callbacks = sorted(set(shared.get("custom_callbacks", [])) - SUPPORTED_CALLBACKS)
        if callbacks:
            reasons["custom_callbacks_to_review"] = len(callbacks)
        formatter = shared.get("special_info_formatter")
        if formatter:
            formatter = formatter.split("/", 1)[0]  # JVM hidden-class addresses vary between exports.
        if formatter and formatter != "gregtech.nei.formatter.DefaultSpecialValueFormatter":
            reasons["special_formatter_to_review"] = 1
        if shared.get("family") == "gt" and not shared.get("frontend"):
            reasons["older_export_without_callback_audit"] = 1
        for n, row in enumerate(category.get("recipes", [])):
            layout = row.get("preview") or shared
            if not layout:
                continue  # Crafting has a procedural renderer; other fallback layouts are intentional.
            positions = collections.defaultdict(set)
            for slot in layout.get("slots", []):
                positions[slot.get("role")].add(slot.get("index"))
            shown = set()
            for side in ("i", "o", "f", "g", "aux", "groups", "extras"):
                values = row.get("preview_stacks", {}).get(side, row.get(side, []))
                indices = row.get("preview_indices", {}).get(side, list(range(len(values))))
                for seq, value in enumerate(values):
                    index = indices[seq] if seq < len(indices) else None
                    if index not in positions[side]:
                        reasons["unplaced_stacks"] += 1
                        samples.setdefault("unplaced_stacks", {"recipe": n, "role": side, "index": index})
                        continue
                    kind = "fluid" if side in ("f", "g") else "item"
                    stacks = value.get("alts", []) if isinstance(value, dict) else [value]
                    if isinstance(value, dict):
                        kind = value.get("kind", "item")
                    pool = data["fluids" if kind == "fluid" else "items"]
                    shown.update((kind, pool[s[0]], s[1]) for s in stacks)
            missing = set(metadata_stacks(row.get("requirements", {}).get("metadata", {}))) - shown
            if missing:
                reasons["metadata_stacks_to_review"] += 1
                samples.setdefault("metadata_stacks_to_review", {"recipe": n, "stacks": sorted(missing)[:8]})
        if reasons:
            findings.append({"map": name, "title": shared.get("title"), "frontend": shared.get("frontend"),
                             "counts": dict(reasons), "callbacks": callbacks, "formatter": formatter, "examples": samples})
            totals.update(reasons)
    return {"totals": dict(totals), "maps": findings,
            "limitations": "Structural checks and a Java callback review list; not a pixel comparison or proof of matching game values."}


if __name__ == "__main__":
    with gzip.open(sys.argv[1], "rt", encoding="utf-8") as source:
        print(json.dumps(audit(json.load(source)), indent=2))
