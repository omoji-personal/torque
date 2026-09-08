"""
Silent-failure parsers — each returns a list of Findings for one category.

Each parser is a pure function: input is the log text + line list, output is
a list[Finding]. Parsers do not raise on parse errors — they return [] and
let other parsers run.

Adopted 2026-05-04 from claudeblazer (Apache-2.0). TAA Phase 5 P2-3.
"""

from __future__ import annotations

import re

from jsc_loganalyzer.score import Finding


# A managed-package trigger name has the shape `<namespace>__<TriggerName>`,
# where the namespace prefix is a single letter-led, ALPHANUMERIC-ONLY token of
# 1-15 chars (Salesforce namespace rules) followed by `__`. This is NOT the same
# as "any name containing `__`": a subscriber trigger like `My_Custom__Trigger`
# (leading token has an underscore) or a name carrying a `__c`/`__r` segment can
# contain `__` without being managed. The leading token MUST be a valid
# namespace token for the trigger to count as managed. (Audit 2026-05-30 COR-2.)
_MANAGED_TRIGGER_NS = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,14}__[A-Za-z0-9_]+$")


def parse_fatal_swallowed(log: str, lines: list[str]) -> list[Finding]:
    """FATAL_ERROR present in log AND the surrounding context shows a try/catch with no re-raise.

    Heuristic: every `|FATAL_ERROR|` event should result in either a top-level transaction abort
    or an explicit `System.debug` of the exception. If the FATAL_ERROR appears inside an
    `EXCEPTION_THROWN` immediately followed by `STATEMENT_EXECUTE` continuing normally, that's
    a swallowed exception.
    """
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if "|FATAL_ERROR|" not in line:
            continue
        # Look ahead 10 lines for evidence the exception was actually surfaced.
        # COR-2(c): a 3-line window false-positived whenever the surfacing event
        # (USER_DEBUG / EXCEPTION_THROWN / EXECUTION_FINISHED) was a few
        # statements further down the log.
        window = "\n".join(lines[i + 1 : i + 11])
        if "EXECUTION_FINISHED" not in window and "USER_DEBUG" not in window and "EXCEPTION_THROWN" not in window:
            findings.append(
                Finding(
                    category="fatal-swallowed",
                    severity=1,
                    message="FATAL_ERROR with no surfacing in next 10 lines (likely caught + suppressed)",
                    line=i + 1,
                    context=line[:200],
                )
            )
    return findings


_LIMIT_PATTERNS = [
    (r"Number of SOQL queries:\s*(\d+)\s*out of\s*(\d+)", "soql"),
    (r"Number of DML statements:\s*(\d+)\s*out of\s*(\d+)", "dml"),
    (r"Maximum CPU time:\s*(\d+)\s*out of\s*(\d+)", "cpu"),
    (r"Maximum heap size:\s*(\d+)\s*out of\s*(\d+)", "heap"),
    (r"Number of callouts:\s*(\d+)\s*out of\s*(\d+)", "callouts"),
]


def parse_governor_asymptotic(log: str, lines: list[str]) -> list[Finding]:
    """Detect any limit consumption ≥75%."""
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        for pat, kind in _LIMIT_PATTERNS:
            m = re.search(pat, line)
            if not m:
                continue
            used = int(m.group(1))
            limit = int(m.group(2))
            if limit == 0:
                continue
            pct = (used / limit) * 100
            if pct >= 90:
                sev = 1
            elif pct >= 75:
                sev = 2
            else:
                continue
            findings.append(
                Finding(
                    category="governor-asymptotic",
                    severity=sev,
                    message=f"{kind}: {used}/{limit} ({pct:.0f}%)",
                    line=i + 1,
                    context=line.strip()[:200],
                )
            )
    return findings


def parse_missing_callout_response(log: str, lines: list[str]) -> list[Finding]:
    """For every CALLOUT_REQUEST, expect a CALLOUT_RESPONSE within ~50 subsequent lines."""
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if "|CALLOUT_REQUEST|" not in line:
            continue
        window = "\n".join(lines[i + 1 : i + 51])
        if "|CALLOUT_RESPONSE|" not in window:
            findings.append(
                Finding(
                    category="missing-callout-response",
                    severity=1,
                    message="CALLOUT_REQUEST with no CALLOUT_RESPONSE in next 50 lines",
                    line=i + 1,
                    context=line[:200],
                )
            )
    return findings


def parse_trigger_recursion(log: str, lines: list[str]) -> list[Finding]:
    """Trigger entries by name; flag any name with >5 entries (recursion)."""
    findings: list[Finding] = []
    counts: dict[str, int] = {}
    for line in lines:
        m = re.search(r"\|CODE_UNIT_STARTED\|.*?Trigger:([A-Za-z0-9_]+)", line)
        if m:
            name = m.group(1)
            counts[name] = counts.get(name, 0) + 1
    for name, count in counts.items():
        if count > 7:
            findings.append(Finding(category="trigger-recursion-deep", severity=1,
                                    message=f"Trigger {name} re-entered {count} times in transaction"))
        elif count > 5:
            findings.append(Finding(category="trigger-recursion-deep", severity=2,
                                    message=f"Trigger {name} re-entered {count} times (boundary)"))
    return findings


def parse_vr_inside_trigger(log: str, lines: list[str]) -> list[Finding]:
    """Detect `VALIDATION_RULE` events nested inside `CODE_UNIT_STARTED|Trigger:...` blocks."""
    findings: list[Finding] = []
    # COR-2(b): track trigger nesting DEPTH, not a single boolean. With a
    # boolean, an inner trigger's CODE_UNIT_FINISHED reset `in_trigger` to False,
    # so a VR firing in the OUTER trigger AFTER an inner trigger finished was
    # missed. Depth increments on each trigger START and decrements (floored at
    # 0 for truncated logs) on each trigger FINISH; we are "inside a trigger"
    # whenever depth > 0.
    trigger_depth = 0
    trigger_name = ""
    for i, line in enumerate(lines):
        if "|CODE_UNIT_STARTED|" in line and "Trigger:" in line:
            trigger_depth += 1
            m = re.search(r"Trigger:([A-Za-z0-9_]+)", line)
            trigger_name = m.group(1) if m else "?"
        elif "|CODE_UNIT_FINISHED|" in line and "Trigger:" in line:
            trigger_depth = max(0, trigger_depth - 1)
        elif trigger_depth > 0 and "|VALIDATION_RULE|" in line:
            findings.append(
                Finding(
                    category="flow-trigger-vr-chain",
                    severity=2,
                    message=f"Validation rule fired inside Trigger {trigger_name} (data quality issue upstream)",
                    line=i + 1,
                    context=line[:200],
                )
            )
    return findings


def parse_managed_dml_no_bypass(log: str, lines: list[str]) -> list[Finding]:
    """Detect DML executing inside a managed-namespace trigger that doesn't query Bypass_Trigger_Settings__c.

    Heuristic: a `CODE_UNIT_STARTED|Trigger:<NS__Name>` followed by `DML_BEGIN` without an
    intervening `SOQL_EXECUTE_BEGIN.*Bypass_Trigger_Settings__c`.
    """
    findings: list[Finding] = []
    in_managed_trigger = False
    saw_bypass_query = False
    trigger_name = ""
    trigger_start_line = 0
    for i, line in enumerate(lines):
        if "|CODE_UNIT_STARTED|" in line and "Trigger:" in line:
            m = re.search(r"Trigger:([A-Za-z0-9_]+)", line)
            if m and _MANAGED_TRIGGER_NS.match(m.group(1)):
                in_managed_trigger = True
                saw_bypass_query = False
                trigger_name = m.group(1)
                trigger_start_line = i + 1
                continue
        if not in_managed_trigger:
            continue
        if "|SOQL_EXECUTE_BEGIN|" in line and "Bypass_Trigger_Settings__c" in line:
            saw_bypass_query = True
        if "|DML_BEGIN|" in line and not saw_bypass_query:
            findings.append(
                Finding(
                    category="dml-managed-no-bypass",
                    severity=2,
                    message=f"Managed trigger {trigger_name} performed DML without checking bypass setting first",
                    line=i + 1,
                    context=line[:200],
                )
            )
            in_managed_trigger = False  # one finding per trigger entry
        if "|CODE_UNIT_FINISHED|" in line and trigger_name in line:
            in_managed_trigger = False
    return findings


# An apex-debug-log EVENT line begins with a timestamp + nanosecond-counter
# prefix, then `|<EVENT_NAME>`. Example:
#   `09:00:00.5 (12345)|SOQL_EXECUTE_END|[42]|Rows:3`
# We use this to find where one log event ends and the next begins, so the
# multi-line SOQL reconstruction stops at the NEXT event marker (e.g.
# SOQL_EXECUTE_END, HEAP_ALLOCATE) rather than swallowing it. The event name
# is an ALL-CAPS-with-underscores token so a wrapped SQL continuation fragment
# (lowercase, mixed case, or a bare clause like `FROM Account`) is NOT mistaken
# for an event marker. (TAA B3 — robust multi-line SOQL detector.)
_LOG_EVENT_MARKER = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d+\s+\(\d+\)\|[A-Z][A-Z0-9_]*\|")

# Clause-aware bounds checks: match WHERE / LIMIT as whole words anywhere in the
# reconstructed statement (case-insensitive). `\b` word boundaries prevent a
# field name like `Limit_Value__c` or `Somewhere__c` from satisfying the check
# — only a real `WHERE` / `LIMIT` keyword counts. (The earlier space-padded
# `" LIMIT "` check already refuted the Limit_Value__c false-positive; the
# word-boundary form preserves that guarantee while also catching a LIMIT/WHERE
# that lands at the very start/end of a reconstructed fragment.)
_HAS_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_HAS_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def _reconstruct_soql(lines: list[str], begin_idx: int) -> str:
    """Reconstruct the full SOQL statement for the SOQL_EXECUTE_BEGIN at
    lines[begin_idx], joining proven continuation fragments and STOPPING at the
    next log-event marker.

    A continuation fragment is any subsequent line that is NOT itself a new log
    event (timestamp + `|EVENT|` prefix). The first line's SELECT...FROM is the
    seed; wrapped fragments are appended with single spaces.
    """
    begin_line = lines[begin_idx]
    # Seed from the SELECT...FROM on the BEGIN line (everything from SELECT on).
    m = re.search(r"SELECT\b.*", begin_line, re.IGNORECASE)
    if not m:
        return ""
    parts = [m.group(0).strip()]
    for j in range(begin_idx + 1, len(lines)):
        nxt = lines[j]
        if _LOG_EVENT_MARKER.match(nxt):
            break  # next event begins — stop reconstruction
        frag = nxt.strip()
        if frag:
            parts.append(frag)
    return " ".join(parts)


def parse_unbounded_soql(log: str, lines: list[str]) -> list[Finding]:
    """SOQL_EXECUTE_BEGIN with a (possibly multi-line) query that has no WHERE
    and no LIMIT.

    Reconstructs the full statement across wrapped continuation lines before
    doing the clause-aware bounds check, so a multi-line query whose WHERE/LIMIT
    lands on a continuation line is correctly recognized as bounded.
    """
    findings: list[Finding] = []
    for i, line in enumerate(lines):
        if "|SOQL_EXECUTE_BEGIN|" not in line:
            continue
        full_query = _reconstruct_soql(lines, i)
        if not full_query:
            continue
        if not _HAS_WHERE.search(full_query) and not _HAS_LIMIT.search(full_query):
            findings.append(
                Finding(
                    category="unbounded-soql",
                    severity=2,
                    message="SOQL with no WHERE and no LIMIT — latent governor-limit blast at scale",
                    line=i + 1,
                    context=full_query[:200],
                )
            )
    return findings


PARSERS = [
    parse_fatal_swallowed,
    parse_governor_asymptotic,
    parse_missing_callout_response,
    parse_trigger_recursion,
    parse_vr_inside_trigger,
    parse_managed_dml_no_bypass,
    parse_unbounded_soql,
]


def run_all_parsers(log: str) -> list[Finding]:
    """Run every parser; return aggregated findings."""
    lines = log.splitlines()
    findings: list[Finding] = []
    for parser in PARSERS:
        try:
            findings.extend(parser(log, lines))
        except Exception:  # noqa: BLE001 — parsers must not crash the analyzer
            continue
    return findings
