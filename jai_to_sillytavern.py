#!/usr/bin/env python3
"""
JanitorAI -> SillyTavern lorebook converter (CLI).

Companion to index.html — same mapping rules, for batch/scripted use.

Usage:
  python jai_to_sillytavern.py input.json output.json [options]

Accepts as input:
  * a JanitorAI entry array
  * an object with an "entries" array or object
  * an object with a "lorebook" array

Defaults below were verified field-by-field against a real JanitorAI
export and its matching, working SillyTavern lorebook. Every field
matched except "order" (SillyTavern authors typically hand-tune this
per entry) and a couple of manually-renamed "comment" titles.

Unmapped source metadata is preserved under extensions.janitorai_source
by default so nothing is silently dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_ORDER_BASE = 100
DEFAULT_ORDER_STEP = 5
DEFAULT_POSITION = 4   # @Depth
DEFAULT_DEPTH = 3      # verified: default when source omits "depth"
DEFAULT_ROLE = 0       # system

ACCOUNTED_FOR = {
    "key", "keys", "keysecondary", "secondary_keys", "comment", "name",
    "content", "constant", "activationMode", "selective", "selectiveLogic",
    "insertion_order", "order", "priority", "position", "enabled", "disable",
    "probability", "depth", "groupWeight", "extensions", "tags", "group",
    "inclusionGroupRaw", "minMessages", "role",
    # ST-side fields this tool itself writes out. Listed here so re-running
    # a conversion on an already-converted file doesn't treat them as
    # "unmapped" and stuff them into a fresh extensions.janitorai_source,
    # which would clobber the real original-source data already nested there.
    "uid", "vectorized", "addMemo", "useProbability", "groupOverride",
    "scanDepth", "caseSensitive", "matchWholeWords", "useGroupScoring",
    "automationId", "sticky", "cooldown", "delay", "displayIndex",
    "matchScenario", "matchCharacterDescription", "matchPersonaDescription",
    "matchCharacterPersonality", "matchCharacterDepthPrompt", "matchCreatorNotes",
    "excludeRecursion", "preventRecursion", "delayUntilRecursion",
}


def source_field(entry: dict[str, Any], name: str) -> Any:
    """Look for `name` on the entry itself, then in extensions, then in
    extensions.janitorai_source (where a prior conversion may have tucked
    away the original JanitorAI fields). Returns None if not found anywhere."""
    if name in entry and entry[name] is not None:
        return entry[name]
    ext = entry.get("extensions")
    if isinstance(ext, dict):
        if name in ext and ext[name] is not None:
            return ext[name]
        js = ext.get("janitorai_source")
        if isinstance(js, dict) and name in js and js[name] is not None:
            return js[name]
    return None


def extract_entries(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return data["entries"]
    if isinstance(data, dict) and isinstance(data.get("entries"), dict):
        items = data["entries"]
        return [items[k] for k in sorted(items, key=lambda x: int(x) if str(x).isdigit() else str(x))]
    if isinstance(data, dict) and isinstance(data.get("lorebook"), list):
        return data["lorebook"]
    raise ValueError(
        "Unsupported JSON shape. Expected a JanitorAI entry array, an object "
        "with an 'entries' array/object, or an object with a 'lorebook' array."
    )


def convert_entry(
    entry: dict[str, Any],
    index: int,
    *,
    order_base: int = DEFAULT_ORDER_BASE,
    order_step: int = DEFAULT_ORDER_STEP,
    default_position: int = DEFAULT_POSITION,
    default_depth: int = DEFAULT_DEPTH,
    default_role: int = DEFAULT_ROLE,
    prevent_recursion_mode: str = "auto",   # "auto" | "on" | "off"
    case_mode: str = "null",                # "null" | "passthrough" (verified: null)
    depth_one_special: bool = True,         # verified (from one example): depth==1 entries
    exclude_recursion_default: bool = False,
    delay_until_recursion: bool = False,
    match_scenario: bool = False,
    match_character_description: bool = True,
    match_persona_description: bool = True,
    match_character_personality: bool = False,
    match_character_depth_prompt: bool = False,
    match_creator_notes: bool = False,
    preserve_metadata: bool = True,
    preserve_existing_settings: bool = True,
) -> dict[str, Any]:

    key = entry.get("key") if isinstance(entry.get("key"), list) else (entry.get("keys") if isinstance(entry.get("keys"), list) else [])
    secondary = entry.get("keysecondary") if isinstance(entry.get("keysecondary"), list) else (entry.get("secondary_keys") if isinstance(entry.get("secondary_keys"), list) else [])

    display_name = str((entry.get("comment") or "").strip() or entry.get("name") or f"Entry {index}")

    activation_mode = str(entry.get("activationMode") or "").lower()
    constant = bool(entry.get("constant", False)) or activation_mode == "constant"
    vectorized = activation_mode == "vectorized"

    # order: preserved as-is when preserve_existing_settings is on and the
    # entry already has one (e.g. reprocessing a previous conversion);
    # otherwise recomputed from priority/insertion_order, also checking
    # extensions/extensions.janitorai_source in case that's where it's tucked away.
    existing_order = source_field(entry, "order")
    if preserve_existing_settings and isinstance(existing_order, (int, float)):
        order = existing_order
    else:
        priority = source_field(entry, "priority")
        if not isinstance(priority, (int, float)):
            priority = source_field(entry, "insertion_order")
        if not isinstance(priority, (int, float)):
            priority = index + 1
        order = round(order_base - (priority - 1) * order_step)

    position_src = source_field(entry, "position")
    depth_src = source_field(entry, "depth")
    role_src = source_field(entry, "role")
    position = position_src if isinstance(position_src, (int, float)) else default_position
    depth = depth_src if isinstance(depth_src, (int, float)) else default_depth
    role = role_src if isinstance(role_src, (int, float)) else default_role

    enabled_src = source_field(entry, "enabled")
    disable_src = source_field(entry, "disable")
    disable = disable_src is True or enabled_src is False

    tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
    prevent_recursion_src = source_field(entry, "preventRecursion")
    if preserve_existing_settings and isinstance(prevent_recursion_src, bool):
        prevent_recursion = prevent_recursion_src
    elif prevent_recursion_mode == "on":
        prevent_recursion = True
    elif prevent_recursion_mode == "off":
        prevent_recursion = False
    else:
        prevent_recursion = "world" in tags

    is_transient = depth_one_special and depth == 1

    def resolve_match_bool(name: str, transient_override: bool | None, settings_default: bool) -> bool:
        existing = source_field(entry, name)
        if preserve_existing_settings and isinstance(existing, bool):
            return existing
        if transient_override is not None:
            return transient_override
        return settings_default

    match_char_desc = resolve_match_bool("matchCharacterDescription", False if is_transient else None, match_character_description)
    match_persona_desc = resolve_match_bool("matchPersonaDescription", False if is_transient else None, match_persona_description)
    exclude_recursion = resolve_match_bool("excludeRecursion", True if is_transient else None, exclude_recursion_default)
    match_scenario = resolve_match_bool("matchScenario", None, match_scenario)
    match_character_personality = resolve_match_bool("matchCharacterPersonality", None, match_character_personality)
    match_character_depth_prompt = resolve_match_bool("matchCharacterDepthPrompt", None, match_character_depth_prompt)
    match_creator_notes = resolve_match_bool("matchCreatorNotes", None, match_creator_notes)
    delay_until_recursion = resolve_match_bool("delayUntilRecursion", None, delay_until_recursion)

    group = str(source_field(entry, "inclusionGroupRaw") or source_field(entry, "group") or "")

    delay_src = source_field(entry, "delay")
    min_messages = source_field(entry, "minMessages")
    if isinstance(delay_src, (int, float)):
        delay = int(delay_src)
    else:
        delay = int(min_messages) if isinstance(min_messages, (int, float)) and min_messages > 0 else 0

    probability_src = source_field(entry, "probability")
    probability = max(0, min(100, int(probability_src))) if isinstance(probability_src, (int, float)) else 100

    group_weight_src = source_field(entry, "groupWeight")
    group_weight = int(group_weight_src) if isinstance(group_weight_src, (int, float)) else 100

    selective_logic_src = source_field(entry, "selectiveLogic")
    selective_logic = int(selective_logic_src) if isinstance(selective_logic_src, (int, float)) else 0

    case_sensitive_src = source_field(entry, "caseSensitive")
    if case_sensitive_src is None:
        case_sensitive_src = source_field(entry, "case_sensitive")
    match_whole_words_src = source_field(entry, "matchWholeWords")
    if preserve_existing_settings and isinstance(case_sensitive_src, bool):
        case_sensitive: Any = case_sensitive_src
    elif case_mode == "passthrough":
        case_sensitive = bool(entry.get("case_sensitive", False))
    else:
        case_sensitive = None
    if preserve_existing_settings and isinstance(match_whole_words_src, bool):
        match_whole_words: Any = match_whole_words_src
    elif case_mode == "passthrough":
        match_whole_words = bool(entry.get("matchWholeWords", False))
    else:
        match_whole_words = None

    leftover = {k: v for k, v in entry.items() if k not in ACCOUNTED_FOR}

    extensions: dict[str, Any] = dict(entry.get("extensions") or {})
    extensions.update({
        "position": position, "role": role, "depth": depth,
        "sticky": 0, "cooldown": 0, "delay": delay,
    })
    if preserve_metadata and leftover:
        extensions["janitorai_source"] = leftover

    return {
        "uid": index,
        "key": key,
        "keysecondary": secondary,
        "comment": display_name,
        "content": str(entry.get("content", "")),
        "constant": constant,
        "vectorized": vectorized,
        "selective": bool(secondary),
        "selectiveLogic": selective_logic,
        "addMemo": bool(display_name),
        "order": order,
        "position": position,
        "disable": disable,
        "enabled": not disable,
        "excludeRecursion": exclude_recursion,
        "preventRecursion": prevent_recursion,
        "delayUntilRecursion": delay_until_recursion,
        "probability": probability,
        "useProbability": True,
        "depth": depth,
        "group": group,
        "groupOverride": False,
        "groupWeight": group_weight,
        "scanDepth": None,
        "caseSensitive": case_sensitive,
        "matchWholeWords": match_whole_words,
        "useGroupScoring": None,
        "automationId": "",
        "role": role,
        "sticky": 0,
        "cooldown": 0,
        "delay": delay,
        "displayIndex": index,
        "matchScenario": match_scenario,
        "matchCharacterDescription": match_char_desc,
        "matchPersonaDescription": match_persona_desc,
        "matchCharacterPersonality": match_character_personality,
        "matchCharacterDepthPrompt": match_character_depth_prompt,
        "matchCreatorNotes": match_creator_notes,
        "extensions": extensions,
    }


def convert_lorebook(data: Any, *, name: str = "", description: str = "", **kwargs: Any) -> dict[str, Any]:
    entries = extract_entries(data)
    out_entries = {str(i): convert_entry(entry, i, **kwargs) for i, entry in enumerate(entries)}
    result: dict[str, Any] = {}
    if name:
        result["name"] = name
    if description:
        result["description"] = description
    result["entries"] = out_entries
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Convert a JanitorAI lorebook export to SillyTavern World Info JSON.")
    p.add_argument("input", type=Path, help="Path to the JanitorAI-style source JSON.")
    p.add_argument("output", type=Path, help="Path to write the SillyTavern JSON to.")
    p.add_argument("--name", type=str, default=None, help="Book name (defaults to the source's own name, else the input filename).")
    p.add_argument("--description", type=str, default=None, help="Book description (defaults to the source's own description).")
    p.add_argument("--order-base", type=int, default=DEFAULT_ORDER_BASE)
    p.add_argument("--order-step", type=int, default=DEFAULT_ORDER_STEP)
    p.add_argument("--position", type=int, default=DEFAULT_POSITION, help="ST position code used when an entry has none (default 4 = @Depth).")
    p.add_argument("--default-depth", type=int, default=DEFAULT_DEPTH)
    p.add_argument("--role", type=int, default=DEFAULT_ROLE, help="0=system, 1=user, 2=assistant.")
    p.add_argument("--prevent-recursion", choices=["auto", "on", "off"], default="auto")
    p.add_argument("--case-mode", choices=["null", "passthrough"], default="null")
    p.add_argument("--no-depth-one-special", action="store_true", help="Disable the depth==1 'transient entry' rule.")
    p.add_argument("--exclude-recursion", action="store_true", help="Baseline excludeRecursion for non-transient entries.")
    p.add_argument("--delay-until-recursion", action="store_true")
    p.add_argument("--match-scenario", action="store_true")
    p.add_argument("--no-match-character-description", action="store_true")
    p.add_argument("--no-match-persona-description", action="store_true")
    p.add_argument("--match-character-personality", action="store_true")
    p.add_argument("--match-character-depth-prompt", action="store_true")
    p.add_argument("--match-creator-notes", action="store_true")
    p.add_argument("--no-preserve-metadata", action="store_true")
    p.add_argument(
        "--no-preserve-existing",
        action="store_true",
        help=(
            "By default, any field an entry already has a value for (position, recursion/match "
            "toggles, order, probability, etc. — including values nested in extensions or "
            "extensions.janitorai_source from a prior conversion) is kept as-is, and the other "
            "options here only fill in what's missing. Pass this to force every entry to the "
            "values given above instead."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        source_name = data.get("name") if isinstance(data, dict) else None
        source_desc = data.get("description") if isinstance(data, dict) else None

        converted = convert_lorebook(
            data,
            name=args.name if args.name is not None else (source_name or args.input.stem),
            description=args.description if args.description is not None else (source_desc or ""),
            order_base=args.order_base,
            order_step=args.order_step,
            default_position=args.position,
            default_depth=args.default_depth,
            default_role=args.role,
            prevent_recursion_mode=args.prevent_recursion,
            case_mode=args.case_mode,
            depth_one_special=not args.no_depth_one_special,
            exclude_recursion_default=args.exclude_recursion,
            delay_until_recursion=args.delay_until_recursion,
            match_scenario=args.match_scenario,
            match_character_description=not args.no_match_character_description,
            match_persona_description=not args.no_match_persona_description,
            match_character_personality=args.match_character_personality,
            match_character_depth_prompt=args.match_character_depth_prompt,
            match_creator_notes=args.match_creator_notes,
            preserve_metadata=not args.no_preserve_metadata,
            preserve_existing_settings=not args.no_preserve_existing,
        )
        args.output.write_text(json.dumps(converted, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"Conversion failed: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {len(converted['entries'])} entries to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
