"""
Silent-failure parsers — each returns a list of Findings for one category.

Each parser is a pure function: input is the log text + line list, output is
a list[Finding]. Parsers do not raise on parse errors — they return [] and
let other parsers run.

Adopted 2026-05-04 from claudeblazer (Apache-2.0). TAA Phase 5 P2-3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from jsc_loganalyzer.score import Finding


# A log line is `HH:mm:ss.SSS (nanoseconds)|EVENT|fields`, as the Apex Developer
# Guide documents it (Debug Log Details, Debug Log Levels). A line without that
# start continues the event before it: a wrapped query, a stack trace, the limits
# under LIMIT_USAGE_FOR_NS, a multi-line USER_DEBUG message. Parsers read only an
# event's own name and fields, so a debug message that contains "|FATAL_ERROR|" or
# a code unit's text is still a debug message. The timestamp is optional so that an
# excerpt starting at the first pipe still reads.
_EVENT_LINE = re.compile(
    r"^(?:\d{1,2}:\d{2}:\d{2}[.:]\d+(?:\s+\(\d+\))?)?\s*\|(?P<name>[A-Z][A-Z0-9_]*)(?:\|(?P<fields>.*))?$"
)


@dataclass
class _Event:
    line: int    # index of the event's own line
    name: str    # "" for a line before the first event: the header, or an excerpt
    fields: str
    more: list[tuple[int, str]] = field(default_factory=list)  # (index, text) of lines continuing it


def _events(lines: list[str]) -> list[_Event]:
    events: list[_Event] = []
    for i, line in enumerate(lines):
        m = _EVENT_LINE.match(line.rstrip())
        if m:
            events.append(_Event(i, m.group("name"), m.group("fields") or ""))
        elif events and events[-1].name:
            events[-1].more.append((i, line))
        else:
            events.append(_Event(i, "", line))
    return events


# A trigger's code unit is named "<Trigger> on <Object> trigger event <Event>"
# (older logs add " for [<record ids>]") and ends with a typeRef,
# __sfdc_trigger/<Trigger> or __sfdc_trigger/<namespace>/<Trigger>:
#   09:00:00.001 (1000000)|CODE_UNIT_STARTED|[EXTERNAL]|01q000000000001|SampleTrigger on Account trigger event BeforeInsert|__sfdc_trigger/SampleTrigger
#   09:00:00.002 (2000000)|CODE_UNIT_FINISHED|SampleTrigger on Account trigger event BeforeInsert|__sfdc_trigger/SampleTrigger
_TRIGGER_UNIT = re.compile(
    r"^(?:[^|]*\|)*?(?P<trigger>[^|]+?) on [^|]+? trigger event (?P<event>\w+)(?: for \[[^\]]*\])?"
    r"(?:\|__sfdc_trigger/(?P<ref>[^|]+?))?\s*$"
)
_UNIT_EDGES = {"CODE_UNIT_STARTED": "STARTED", "CODE_UNIT_FINISHED": "FINISHED"}


def _trigger_unit(event: _Event) -> tuple[str, str, str] | None:
    """(STARTED or FINISHED, trigger, event) for a trigger code-unit event, else None.

    The trigger is named by its typeRef, namespace included, when the event has one.
    """
    if event.name not in _UNIT_EDGES or " trigger event " not in event.fields:
        return None
    m = _TRIGGER_UNIT.match(event.fields)
    if not m:
        return None
    return _UNIT_EDGES[event.name], m.group("ref") or m.group("trigger").strip(), m.group("event")


def parse_unhandled_exception(log: str, lines: list[str]) -> list[Finding]:
    """FATAL_ERROR: an exception nothing caught, which ended the transaction.

    Apex logs FATAL_ERROR (exception type, message and stack trace) when an
    exception ends the transaction; a caught one logs EXCEPTION_THROWN alone.
    What follows (CODE_UNIT_FINISHED, EXECUTION_FINISHED) is the normal unwinding
    and says nothing about handling. The same error can be logged again as it
    leaves enclosing units; it is reported once.
    """
    findings: list[Finding] = []
    seen: set[str] = set()
    for event in _events(lines):
        if event.name != "FATAL_ERROR":
            continue
        error = event.fields.strip()
        if error in seen:
            continue
        seen.add(error)
        findings.append(
            Finding(
                category="unhandled-exception",
                severity=1,
                message=f"Unhandled exception ended the transaction: {error[:150]}",
                line=event.line + 1,
                context=lines[event.line][:200],
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


_LIMIT_EVENTS = {"LIMIT_USAGE_FOR_NS", "CUMULATIVE_LIMIT_USAGE", "TESTING_LIMITS"}


def _limit_lines(lines: list[str]):
    """(index, text) of the lines that report limits: those under a limit event, or
    an excerpt of them before any event. The same words in a debug message are not
    limits."""
    for event in _events(lines):
        if event.name in _LIMIT_EVENTS:
            yield from event.more
        elif not event.name:
            yield event.line, event.fields


def parse_governor_asymptotic(log: str, lines: list[str]) -> list[Finding]:
    """Detect any limit consumption ≥75%."""
    findings: list[Finding] = []
    for i, line in _limit_lines(lines):
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
    events = _events(lines)
    for n, event in enumerate(events):
        if event.name != "CALLOUT_REQUEST":
            continue
        later = (other for other in events[n + 1:] if other.line <= event.line + 50)
        if not any(other.name == "CALLOUT_RESPONSE" for other in later):
            findings.append(
                Finding(
                    category="missing-callout-response",
                    severity=1,
                    message="CALLOUT_REQUEST with no CALLOUT_RESPONSE in next 50 lines",
                    line=event.line + 1,
                    context=lines[event.line][:200],
                )
            )
    return findings


def parse_trigger_recursion(log: str, lines: list[str]) -> list[Finding]:
    """A trigger starting again for the same event while it is still running.

    Re-entry is measured by nesting, not by total runs: Salesforce runs triggers
    in chunks of 200 records, so one 2,000-row DML legitimately runs a trigger ten
    times in a row. Only a start inside an open unit of the same trigger and event
    is recursion. A finish without a matching start (a truncated log) is ignored.
    """
    findings: list[Finding] = []
    running: dict[tuple[str, str], int] = {}
    deepest: dict[tuple[str, str], tuple[int, int]] = {}  # (depth, index of the line)
    for record in _events(lines):
        unit = _trigger_unit(record)
        if unit is None:
            continue
        edge, trigger, event = unit
        key = (trigger, event)
        if edge == "STARTED":
            running[key] = running.get(key, 0) + 1
            if running[key] > deepest.get(key, (0, 0))[0]:
                deepest[key] = (running[key], record.line)
        elif running.get(key):
            running[key] -= 1
    for (trigger, event), (depth, i) in deepest.items():
        if depth > 7:
            severity, suffix = 1, ""
        elif depth > 5:
            severity, suffix = 2, " (boundary)"
        else:
            continue
        findings.append(Finding(category="trigger-recursion-deep", severity=severity,
                                message=f"Trigger {trigger} ({event}) re-entered itself while still running, "
                                        f"{depth} levels deep{suffix}",
                                line=i + 1, context=lines[i][:200]))
    return findings


def parse_vr_inside_trigger(log: str, lines: list[str]) -> list[Finding]:
    """A validation rule that rejected DML a trigger performed.

    The rules checked for a trigger's own DML are logged inside the trigger's code
    unit: VALIDATION_RULE names the rule, then VALIDATION_PASS or VALIDATION_FAIL
    gives the outcome. Rules that pass are normal; a failure means the trigger
    wrote data the target object rejects.
    """
    findings: list[Finding] = []
    # Open trigger units, innermost last. COR-2(b): a nested trigger finishing
    # must not end the outer one.
    running: list[str] = []
    rule = ""
    for record in _events(lines):
        unit = _trigger_unit(record)
        if unit is not None:
            edge, trigger, event = unit
            label = f"{trigger} ({event})"
            if edge == "STARTED":
                running.append(label)
            elif label in running:
                del running[len(running) - 1 - running[::-1].index(label)]
        elif record.name == "VALIDATION_RULE":
            rule = record.fields.rstrip().rsplit("|", 1)[-1]
        elif record.name == "VALIDATION_FAIL" and running:
            findings.append(
                Finding(
                    category="flow-trigger-vr-chain",
                    severity=2,
                    message=f"Validation rule {rule or '(unnamed)'} rejected DML from trigger {running[-1]} "
                            "(data quality issue upstream)",
                    line=record.line + 1,
                    context=lines[record.line][:200],
                )
            )
    return findings


# Clause-aware bounds checks: match WHERE / LIMIT as whole words anywhere in the
# reconstructed statement (case-insensitive). `\b` word boundaries prevent a
# field name like `Limit_Value__c` or `Somewhere__c` from satisfying the check
# — only a real `WHERE` / `LIMIT` keyword counts. (The earlier space-padded
# `" LIMIT "` check already refuted the Limit_Value__c false-positive; the
# word-boundary form preserves that guarantee while also catching a LIMIT/WHERE
# that lands at the very start/end of a reconstructed fragment.)
_HAS_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_HAS_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def parse_unbounded_soql(log: str, lines: list[str]) -> list[Finding]:
    """SOQL_EXECUTE_BEGIN with a (possibly multi-line) query that has no WHERE
    and no LIMIT.

    The statement is the event's SELECT and the lines that continue it, up to the
    next event (TAA B3), so a multi-line query whose WHERE/LIMIT lands on a
    continuation line is correctly recognized as bounded.
    """
    findings: list[Finding] = []
    for event in _events(lines):
        if event.name != "SOQL_EXECUTE_BEGIN":
            continue
        m = re.search(r"SELECT\b.*", event.fields, re.IGNORECASE)
        if not m:
            continue
        full_query = " ".join([m.group(0).strip(), *(text.strip() for _, text in event.more if text.strip())])
        if not _HAS_WHERE.search(full_query) and not _HAS_LIMIT.search(full_query):
            findings.append(
                Finding(
                    category="unbounded-soql",
                    severity=2,
                    message="SOQL with no WHERE and no LIMIT — latent governor-limit blast at scale",
                    line=event.line + 1,
                    context=full_query[:200],
                )
            )
    return findings


PARSERS = [
    parse_unhandled_exception,
    parse_governor_asymptotic,
    parse_missing_callout_response,
    parse_trigger_recursion,
    parse_vr_inside_trigger,
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
