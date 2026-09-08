"""Source-typed PII scrubber.

Per plan-v5: distinguishes field NAMES (keep) from field VALUES (scrub) by
parser-driven source classification, not regex-only context inference.

Source types (precedence order in `detect_source_type`):
1. Bracketed Apex SOQL `[SELECT ... FROM ...]` (recognized BEFORE JSON)
2. JSON (parser-driven via json.loads validation)
3. SOQL (anywhere in snippet, multi-line via DOTALL; sanity-check identifiers after FROM)
4. CLI table (real box-drawing chars, not bare `===`)
5. Narrative (everything else; conservative drop-by-default in PARANOID)

Modes:
- PARANOID (default): narrative drops on > 1 PII hit; SOQL/JSON/CLI scrub-in-place
- LENIENT: narrative drops on > 5 PII hits

Per `.claude/rules/privacy-and-logging.md`: best-effort. Comprehensive fixture
suite (`tests/fixtures/pii_shapes.yaml`) is the safety net, NOT regex perfection.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

# Per plan-v5 R4 CR4-2 fix: cap parser input
MAX_SNIPPET_BYTES_FOR_DETECTION = 65536

# Salesforce ID prefix set (3-char object-type prefix). Tightened from v1
# to avoid catching git SHAs / UUIDs.
_SF_ID_PREFIX_RE = re.compile(
    r"\b(001|003|005|006|00Q|500|701|800|a[0-9A-Za-z]{2})[0-9A-Za-z]{12,15}\b"
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(
    # Phone REQUIRES a separator (paren / space / dot / dash) — bare 10-digit
    # runs are too prone to false positives (git SHAs, hex IDs, etc.)
    r"(?:\+?\d{1,3}[\s.-])?\(\d{3}\)\s?\d{3}[\s.-]?\d{4}|"  # (555) 123-4567
    r"(?<!\d)\d{3}[\s.-]\d{3}[\s.-]\d{4}(?!\d)"  # 555-123-4567 / 555.123.4567
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DOB_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_PII_VALUE_PATTERNS = [
    ("sf_id", _SF_ID_PREFIX_RE, "<sf_id>"),
    ("email", _EMAIL_RE, "<email>"),
    ("ssn", _SSN_RE, "<ssn>"),
    ("dob", _DOB_RE, "<dob>"),
    ("phone", _PHONE_RE, "<phone>"),
    ("ipv4", _IPV4_RE, "<ip>"),
]


def detect_source_type(snippet: str) -> str:
    """Parser-driven source classification with explicit precedence + fallback.

    R4 CR4-2 fix: snippet bigger than MAX_SNIPPET_BYTES_FOR_DETECTION returns
    'narrative' immediately to avoid pathological parser cost.
    """
    if not isinstance(snippet, str):
        return "narrative"
    if len(snippet) > MAX_SNIPPET_BYTES_FOR_DETECTION:
        return "narrative"
    stripped = snippet.strip()
    if not stripped:
        return "narrative"

    # 1. Bracketed Apex SOQL takes precedence over JSON
    if re.search(
        r"\[\s*SELECT\b[\s\S]*?\bFROM\b[\s\S]*?\]",
        snippet,
        re.IGNORECASE,
    ):
        return "soql"

    # 2. JSON: parser-driven (try json.loads); fall through if invalid
    if stripped[:1] in "{[":
        try:
            json.loads(stripped)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. SOQL anywhere; require SObject-like identifier after FROM
    #    (must start with uppercase OR contain __c — distinguishes from prose
    #    like "Select from the account list" where "the" is lowercase non-SObject).
    if re.search(
        r"\bSELECT\b[\s\S]{0,1000}?\bFROM\b\s+(?:[A-Z][\w]*|\w+__c)",
        snippet,
        re.IGNORECASE | re.DOTALL,
    ):
        # Additional check: the SOQL-target must have a capital letter or __c
        # in its actual case (re.IGNORECASE makes the OUTER regex flexible
        # but we want the SObject name to look like a real Salesforce identifier)
        m = re.search(
            r"\bFROM\s+([A-Za-z_][\w.]*)",
            snippet,
        )
        if m:
            target = m.group(1)
            # Real SObject name: starts with uppercase OR contains __c suffix
            if target[0].isupper() or "__c" in target.lower():
                return "soql"

    # 4. CLI table: real box-drawing chars (not bare ===) or pipe-delimited rows
    if re.search(r"[─┼┌┐└┘│]{3,}", snippet) or re.search(
        r"^\s*\|.{3,}\|\s*$", snippet, re.MULTILINE
    ):
        return "cli_table"

    return "narrative"


def _scrub_value(value: str) -> tuple[str, int]:
    """Apply value-level PII regexes. Returns (scrubbed_value, hit_count)."""
    if not isinstance(value, str):
        return value, 0
    out = value
    hits = 0
    for _, pattern, replacement in _PII_VALUE_PATTERNS:
        new, n = pattern.subn(replacement, out)
        out = new
        hits += n
    return out, hits


def _scrub_soql(snippet: str) -> tuple[str, int]:
    """Scrub SOQL: keep SELECT-list field names; scrub WHERE literals + result rows."""
    out_lines = []
    total_hits = 0
    in_where = False
    for line in snippet.splitlines():
        # Anything past WHERE is value territory
        if re.search(r"\bWHERE\b", line, re.IGNORECASE):
            in_where = True
        if in_where:
            scrubbed, hits = _scrub_value(line)
            out_lines.append(scrubbed)
            total_hits += hits
        else:
            # SELECT-list lines: keep identifiers (they're field names)
            out_lines.append(line)
    return "\n".join(out_lines), total_hits


def _scrub_json(snippet: str) -> tuple[str, int]:
    """Scrub JSON: keep keys; scrub values via PII regex."""
    try:
        data = json.loads(snippet)
    except (json.JSONDecodeError, ValueError):
        return _scrub_value(snippet)

    total_hits = [0]

    def _walk(node):
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        if isinstance(node, str):
            scrubbed, hits = _scrub_value(node)
            total_hits[0] += hits
            return scrubbed
        return node

    cleaned = _walk(data)
    return json.dumps(cleaned), total_hits[0]


def _scrub_cli_table(snippet: str) -> tuple[str, int]:
    """Scrub CLI table: each cell value passes through PII regex."""
    return _scrub_value(snippet)


def _scrub_narrative(snippet: str, mode: str) -> tuple[str, Optional[str]]:
    """Scrub narrative: count PII hits; drop entire snippet if > threshold.

    Returns (snippet_or_dropped, drop_reason | None).
    """
    threshold = 1 if mode == "paranoid" else 5
    _, hits = _scrub_value(snippet)
    if hits > threshold:
        return "", f"narrative_dropped_pii_hits={hits}_threshold={threshold}"
    scrubbed, _ = _scrub_value(snippet)
    return scrubbed, None


def scrub_snippet(snippet: str, mode: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Scrub a single snippet using source-typed parser.

    Returns (scrubbed_text, drop_reason | None). If drop_reason is set, the
    snippet was discarded (caller treats as empty).
    """
    if mode is None:
        mode = os.environ.get("JSC_LESSON_SCRUBBER_MODE", "paranoid").lower()
    if mode not in ("paranoid", "lenient"):
        mode = "paranoid"

    source_type = detect_source_type(snippet)

    try:
        if source_type == "soql":
            scrubbed, _ = _scrub_soql(snippet)
            return scrubbed, None
        if source_type == "json":
            scrubbed, _ = _scrub_json(snippet)
            return scrubbed, None
        if source_type == "cli_table":
            scrubbed, _ = _scrub_cli_table(snippet)
            return scrubbed, None
        # narrative: drop-on-threshold
        return _scrub_narrative(snippet, mode)
    except Exception:
        # R3 parser-failure fallback: drop conservative
        return "", "scrubber_exception_fallback"


def scrub_candidate(candidate: dict, mode: Optional[str] = None) -> tuple[dict, Optional[str]]:
    """Scrub all string fields in a captured candidate dict.

    Returns (scrubbed_dict, drop_reason | None). If drop_reason is set, the
    overall candidate is discarded (e.g., > 25% of fields dropped).
    """
    if not isinstance(candidate, dict):
        return candidate, "non_dict_candidate"

    if mode is None:
        mode = os.environ.get("JSC_LESSON_SCRUBBER_MODE", "paranoid").lower()
    drop_threshold = 0.25 if mode == "paranoid" else 0.50

    out = {}
    total_strs = 0
    dropped_strs = 0
    for k, v in candidate.items():
        if isinstance(v, str):
            total_strs += 1
            scrubbed, drop_reason = scrub_snippet(v, mode=mode)
            if drop_reason:
                dropped_strs += 1
            out[k] = scrubbed
        elif isinstance(v, list):
            cleaned = []
            for item in v:
                if isinstance(item, str):
                    total_strs += 1
                    s, dr = scrub_snippet(item, mode=mode)
                    if dr:
                        dropped_strs += 1
                    cleaned.append(s)
                elif isinstance(item, dict):
                    sub, _ = scrub_candidate(item, mode=mode)
                    cleaned.append(sub)
                else:
                    cleaned.append(item)
            out[k] = cleaned
        elif isinstance(v, dict):
            sub, _ = scrub_candidate(v, mode=mode)
            out[k] = sub
        else:
            out[k] = v

    if total_strs > 0 and dropped_strs / total_strs > drop_threshold:
        return out, f"candidate_dropped_drop_rate={dropped_strs}/{total_strs}_threshold={drop_threshold}"
    return out, None
