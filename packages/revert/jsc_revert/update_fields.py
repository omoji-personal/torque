"""Recover the explicit update scope; a full before-image is not a restore payload."""
from __future__ import annotations

import re

_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SYSTEM = {"id", "attributes", "createddate", "createdbyid", "lastmodifieddate",
           "lastmodifiedbyid", "systemmodstamp"}


def parse_assignments(values: str) -> list[tuple[str, str]] | None:
    """Read sf's quoted key=value input without interpreting values.

    sf's parser treats whitespace outside paired single/double quotes as a separator
    and does not apply shell backslash escaping. An unbalanced pair is not sufficient
    evidence of scope. Malformed input returns None; callers decide whether that
    blocks the write or prevents automatic recovery.
    """
    if not isinstance(values, str) or not values.strip():
        return None
    tokens, current = [], []
    quote = None
    paired = {char for char in ("'", '"') if values.count(char) >= 2}
    for char in values.strip():
        if char in paired and (quote is None or quote == char):
            quote = char if quote is None else None
        elif char.isspace() and quote is None:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if quote is not None:
        return None
    if current:
        tokens.append("".join(current))
    assignments = []
    for token in tokens:
        field, separator, value = token.partition("=")
        if not separator or not _FIELD.fullmatch(field):
            return None
        assignments.append((field, value))
    return assignments or None


def parse_update_fields(values: str) -> list[str] | None:
    """Retain only explicit field names for a scoped recovery payload."""
    assignments = parse_assignments(values)
    if assignments is None:
        return None
    fields, seen = [], set()
    for field, _ in assignments:
        if field.lower() not in seen:
            fields.append(field)
            seen.add(field.lower())
    return fields or None


def requested_fields(payload: dict, wrapper_command: str | None = None) -> list[str] | None:
    """Prefer structured capture; support the exact legacy wrapper's terminal log format."""
    if "fields_updated" in payload:
        fields = payload["fields_updated"]
        if not isinstance(fields, list) or not fields:
            return None
        if not all(isinstance(field, str) and _FIELD.fullmatch(field) for field in fields):
            return None
        return list(dict.fromkeys(fields))
    # Historical data_update.run records raw values between one added outer pair.
    # Extract those bytes, not shell-tokenized prose and never a before/after diff:
    # the latter includes formula, system and automation changes.
    if isinstance(wrapper_command, str) and wrapper_command.startswith("jsc data update -o "):
        prefix, separator, suffix = wrapper_command.partition(" --values '")
        if separator and suffix.endswith("'") and " --record-id " in prefix and " --sobject " in prefix:
            return parse_update_fields(suffix[:-1])
    return None


def selected_before_values(payload: dict, wrapper_command: str | None = None) -> dict | None:
    fields = requested_fields(payload, wrapper_command)
    before = payload.get("before_row")
    if not fields or not isinstance(before, dict) or not before:
        return None
    canonical = {key.lower(): key for key in before if isinstance(key, str)}
    if len(canonical) != len(before):
        return None
    selected = {}
    for field in fields:
        key = canonical.get(field.lower())
        if key is None or field.lower() in _SYSTEM:
            return None
        value = before[key]
        if value is not None and not isinstance(value, (str, bool, int, float)):
            return None
        selected[key] = value
    return selected or None
