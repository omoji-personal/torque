"""Ranked access to the generic Salesforce operational catalogue."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files
from typing import Iterable


CONFIDENCE_ORDER = {"verified-live": 0, "documented": 1, "practitioner": 2}
REQUIRED_FIELDS = {"id", "domain", "title", "symptom", "cause", "remedy",
                   "triggers", "confidence", "updated"}


@lru_cache(maxsize=1)
def entries() -> tuple[dict, ...]:
    path = files("jsc_advisory").joinpath("data/salesforce-platform.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        raise ValueError("Salesforce catalogue root must contain an entries list")
    seen: set[str] = set()
    clean: list[dict] = []
    for item in raw["entries"]:
        if not isinstance(item, dict):
            raise ValueError("Salesforce catalogue entries must be objects")
        missing = REQUIRED_FIELDS - set(item)
        if missing:
            raise ValueError(f"catalogue entry {item.get('id', '?')} missing {sorted(missing)}")
        if item["id"] in seen:
            raise ValueError(f"duplicate catalogue id: {item['id']}")
        seen.add(item["id"])
        if not isinstance(item["triggers"], list):
            raise ValueError(f"catalogue entry {item['id']} triggers must be a list")
        clean.append(item)
    return tuple(clean)


def _matches(command: str, candidates: Iterable[dict]) -> list[tuple[int, dict]]:
    hits: list[tuple[int, dict]] = []
    for item in candidates:
        matched = 0
        for pattern in item.get("triggers") or []:
            try:
                if re.search(pattern, command, re.I):
                    matched += 1
            except re.error:
                continue
        if matched:
            hits.append((matched, item))
    hits.sort(key=lambda pair: (
        -pair[0], CONFIDENCE_ORDER.get(pair[1].get("confidence", ""), 3),
        pair[1]["id"],
    ))
    return hits


def platform_notes(command: str, limit: int = 2) -> list[dict]:
    """Return the most specific relevant notes; this never authorizes or blocks."""
    if not command or limit <= 0:
        return []
    return [item for _, item in _matches(command, entries())[:limit]]


def closure_report(command: str) -> dict:
    """Return all known direct requirements and distinguish both empty cases."""
    if not command:
        return {"requirements": [], "matched": [], "unrecorded": []}
    requirements: list[dict] = []
    matched: list[str] = []
    unrecorded: list[str] = []
    for _, item in _matches(command, entries()):
        matched.append(item["id"])
        requirement = " ".join(str(item.get("requires") or "").split())
        if requirement:
            requirements.append({"entry": item["id"], "requires": requirement})
        else:
            unrecorded.append(item["id"])
    return {"requirements": requirements, "matched": matched, "unrecorded": unrecorded}


def provenance() -> dict:
    path = files("jsc_advisory").joinpath("data/provenance.json")
    return json.loads(path.read_text(encoding="utf-8"))
