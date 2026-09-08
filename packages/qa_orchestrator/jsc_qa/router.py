"""router.py — load qa-router.yaml + match change-type from operator description.

Closes design-v4 Closure 2 (matrix extracted to YAML).

Provides:
  - load_router() → returns parsed router dict
  - match_change_types(description) → returns list of matching change-type rows
    (operator's natural-language description → fuzzy match against id, name, examples)
  - effective_surfaces(change_type, target_org_info) → applies risk modifiers + severity
"""

from __future__ import annotations
import os

import re
from pathlib import Path
from typing import Any

import yaml


DEFAULT_ROUTER_PATH = Path(__file__).resolve().parent / "data" / "qa-router.yaml"


# Status labels (per design-v4 Closure 9 — Codex-R1-P2-3)
STATUS_REQUIRED_AUTOMATED = "required_automated"
STATUS_REQUIRED_MANUAL = "required_manual"
STATUS_ONE_OF = "one_of"
STATUS_OPTIONAL = "optional"
STATUS_DEFERRED = "deferred"
STATUS_DEFERRED_PHASE_2 = "deferred_phase_2"  # alias
STATUS_NOT_APPLICABLE = "not_applicable"

VALID_STATUS_LABELS = (
    STATUS_REQUIRED_AUTOMATED, STATUS_REQUIRED_MANUAL, STATUS_ONE_OF,
    STATUS_OPTIONAL, STATUS_DEFERRED, STATUS_DEFERRED_PHASE_2,
    STATUS_NOT_APPLICABLE,
)


class RouterValidationError(Exception):
    """Raised when qa-router.yaml fails schema validation."""


def load_router(path: Path | None = None) -> dict[str, Any]:
    """Load + validate the canonical router YAML."""
    p = path or (Path(os.environ["TORQUE_QA_ROUTER"]) if os.environ.get("TORQUE_QA_ROUTER") else DEFAULT_ROUTER_PATH)
    if not p.exists():
        raise FileNotFoundError(f"qa-router.yaml not found at {p}")
    with open(p) as f:
        data = yaml.safe_load(f)
    _validate(data)
    return data


def _validate(data: dict) -> None:
    if not isinstance(data, dict):
        raise RouterValidationError("router YAML root must be a dict")
    if data.get("schema_version") != 1:
        raise RouterValidationError(f"unsupported schema_version: {data.get('schema_version')}")
    for k in ("surfaces", "change_types"):
        if k not in data:
            raise RouterValidationError(f"missing required top-level key: {k}")
    if not isinstance(data["surfaces"], dict):
        raise RouterValidationError("surfaces must be a dict")
    if not isinstance(data["change_types"], list):
        raise RouterValidationError("change_types must be a list")
    valid_surfaces = set(data["surfaces"].keys())
    for ct in data["change_types"]:
        if "id" not in ct:
            raise RouterValidationError(f"change_type missing id: {ct}")
        if "category" not in ct:
            raise RouterValidationError(f"change_type {ct['id']} missing category")
        if "name" not in ct:
            raise RouterValidationError(f"change_type {ct['id']} missing name")
        # surfaces dict allowed to be empty (e.g., F7 — local/clients/ docs)
        ct_surfaces = ct.get("surfaces", {})
        if not isinstance(ct_surfaces, dict):
            raise RouterValidationError(f"change_type {ct['id']} surfaces must be dict")
        for surface_name, status in ct_surfaces.items():
            if surface_name not in valid_surfaces:
                raise RouterValidationError(
                    f"change_type {ct['id']} references unknown surface: {surface_name}"
                )
            if status not in VALID_STATUS_LABELS:
                raise RouterValidationError(
                    f"change_type {ct['id']} surface {surface_name} has invalid status: {status}"
                )


# Cache of compiled word-boundary patterns per (lower-cased) keyword. A change
# description is matched against many CTs × many keywords per /qa run; compiling
# `\bkw\b` once and reusing it is a meaningful saving (A1.1).
_KW_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _kw_pattern(keyword_lower: str) -> re.Pattern:
    """Return (and cache) a compiled WORD-BOUNDARY regex for a lower-cased keyword.

    Word-boundary matching stops substring false-positives — e.g. the keyword
    "data" must NOT match inside "metadata" (A1.1). CMDT-style names that
    literally contain "metadata" still match the keyword "metadata" because the
    boundary check passes there.
    """
    pat = _KW_PATTERN_CACHE.get(keyword_lower)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(keyword_lower) + r"\b")
        _KW_PATTERN_CACHE[keyword_lower] = pat
    return pat


def match_change_types(description: str, router: dict | None = None) -> list[dict]:
    """Match operator's change description to change-type rows.

    Strategy:
      1. Direct id match (e.g., "A3" or "change A3")
      2. Keyword match on name + examples (WORD-BOUNDARY, not substring)

    Returns LIST of matching change-types (description may match multiple, e.g.,
    "I shipped a Flow + a custom field" matches A1 + A3). MULTI-change-type
    matching is intentional + load-bearing — a single deploy that touches N
    metadata types routes to N change-type rows; the orchestrator unions their
    surfaces. Do NOT collapse this to a single winner.
    """
    router = router or load_router()
    desc_lower = description.lower()
    matches = []

    # Direct id match (regex: \b[A-H]\d+\b)
    id_matches = re.findall(r"\b([A-H]\d+)\b", description.upper())
    if id_matches:
        for ct in router["change_types"]:
            if ct["id"] in id_matches and ct not in matches:
                matches.append(ct)

    # Keyword match — WORD-BOUNDARY regex (A1.1). Preserves the per-CT break so a
    # single keyword hit suffices to add the CT exactly once.
    keywords_per_change_type = _build_keyword_index(router)
    for ct_id, keywords in keywords_per_change_type.items():
        for kw in keywords:
            if _kw_pattern(kw.lower()).search(desc_lower):
                ct = next((c for c in router["change_types"] if c["id"] == ct_id), None)
                if ct and ct not in matches:
                    matches.append(ct)
                    break  # one keyword match suffices for this ct

    return matches


def match_change_types_scored(
    description: str, router: dict | None = None
) -> list[tuple[dict, int]]:
    """Like match_change_types but returns (ct_row, score) per matched CT.

    Score = count of DISTINCT matched keywords for that CT
            + DIRECT_ID_BONUS if the CT was named directly by id (e.g. "A3").

    Multi-change-type matching is preserved (A1.2): every CT that matches at
    least one keyword OR is named by id appears in the result, ranked by score
    descending (ties broken by change-type id for determinism). Callers that
    want the raw rows can use match_change_types; this variant is for ranked
    display + one_of preference ordering in the CLI.
    """
    router = router or load_router()
    desc_lower = description.lower()

    DIRECT_ID_BONUS = 100  # a literal "A3" is a much stronger signal than a keyword

    id_matches = set(re.findall(r"\b([A-H]\d+)\b", description.upper()))

    scores: dict[str, int] = {}
    ct_by_id = {c["id"]: c for c in router["change_types"]}

    # Direct-id contribution
    for ct_id in id_matches:
        if ct_id in ct_by_id:
            scores[ct_id] = scores.get(ct_id, 0) + DIRECT_ID_BONUS

    # Keyword contribution — count DISTINCT matched keywords per CT (no break,
    # so the score reflects how many independent keywords hit).
    keywords_per_change_type = _build_keyword_index(router)
    for ct_id, keywords in keywords_per_change_type.items():
        distinct_hits = 0
        for kw in keywords:
            if _kw_pattern(kw.lower()).search(desc_lower):
                distinct_hits += 1
        if distinct_hits:
            scores[ct_id] = scores.get(ct_id, 0) + distinct_hits

    ranked = sorted(
        ((ct_by_id[cid], sc) for cid, sc in scores.items()),
        key=lambda pair: (-pair[1], pair[0]["id"]),
    )
    return ranked


def _build_keyword_index(router: dict) -> dict[str, list[str]]:
    """Map ct_id → list of keywords from name + examples."""
    index = {}
    # Stop-words to avoid trivial match (e.g., "create" matches everything)
    stop_words = {
        "create", "update", "delete", "change", "the", "a", "an", "and", "or",
        "of", "to", "for", "with", "in", "on", "at", "via", "if", "is", "it",
        "this", "that",
    }
    for ct in router["change_types"]:
        keywords = []
        # Name keywords (split on common separators)
        for word in re.split(r"[\s/\-(),]+", ct["name"]):
            word = word.strip()
            if word and word.lower() not in stop_words and len(word) > 2:
                keywords.append(word)
        # Example keywords (more specific)
        for example in ct.get("examples", []):
            for word in re.split(r"[\s/]+", example):
                word = word.strip()
                if word and word.lower() not in stop_words and len(word) > 3:
                    keywords.append(word)
        index[ct["id"]] = list(set(keywords))
    return index


def effective_surfaces(
    change_type: dict,
    is_production: bool = False,
    router: dict | None = None,
) -> dict[str, str]:
    """Compute the effective surface assignments for a change-type, applying
    risk_modifiers + production/sandbox severity.

    Returns dict: surface_name → status_label (effective).
    """
    router = router or load_router()
    surfaces = dict(change_type.get("surfaces", {}))

    # Apply production severity modifier per design-v4 Closure 11
    if is_production:
        # production_target effects (per qa-router.yaml severity_modifiers):
        # - escalate_required_manual_to_required_automated_or_block (we leave as
        #   required_manual; orchestrator surfaces this clearly to operator)
        # - require_serial_multi_profile_execution (handled at execution time)
        # - require_qa_skip_token_for_any_skip (handled at skip-token validation)
        pass  # passthrough; specific escalations handled by orchestrator + surface invocations

    # Apply risk_modifiers if defined
    risk_mods = change_type.get("risk_modifiers", {})
    for mod_name, action in risk_mods.items():
        # NOTE: actual modifier evaluation requires payload context (e.g.,
        # "contains_dml" requires Apex source inspection). Phase 1A: orchestrator
        # surfaces the modifier to operator; operator confirms. Phase 1B-2 may
        # evaluate modifiers from payload metadata.
        pass  # documented in surface report; not auto-evaluated yet

    return surfaces


def required_surfaces(
    change_type: dict,
    is_production: bool = False,
    router: dict | None = None,
) -> dict[str, list[str]]:
    """Group surface assignments into required / one_of_groups / optional / deferred / manual."""
    surfaces = effective_surfaces(change_type, is_production, router)
    grouped = {
        "required_automated": [],
        "required_manual": [],
        "one_of": [],
        "optional": [],
        "deferred": [],
    }
    for surface, status in surfaces.items():
        if status == STATUS_REQUIRED_AUTOMATED:
            grouped["required_automated"].append(surface)
        elif status == STATUS_REQUIRED_MANUAL:
            grouped["required_manual"].append(surface)
        elif status == STATUS_ONE_OF:
            grouped["one_of"].append(surface)
        elif status == STATUS_OPTIONAL:
            grouped["optional"].append(surface)
        elif status in (STATUS_DEFERRED, STATUS_DEFERRED_PHASE_2):
            grouped["deferred"].append(surface)
    return grouped
