"""Look over the lessons on a cadence: what waits, what repeats, what to re-check, what
to promote. The review is a report with the command for each line; it changes nothing.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import re

from . import lesson as record
from . import recall, scrubber, storage
from .lesson import Lesson
from .storage import Scopes, Tally

DUPLICATE = 0.6          # share of words two lessons have in common to be called likely duplicates
SHOWN_UNMARKED = 5       # shown this often with no helpful mark: is it useful?
UNSEEN_DAYS = 180        # not shown for this long: do its triggers still fit?
PROMOTE_HELPFUL = 3      # marked helpful this often: worth a stronger form
_CLIENT_SCHEMA = re.compile(r"\b\w+__(?:c|r|mdt|e|x|b)\b")


def _days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _entry(item: Lesson, tally: Tally, **more) -> dict:
    return {"id": item.id, "scope": item.scope, "state": item.state, "title": item.title, "when": item.when,
            "do": item.do, "created": item.created, "last_verified": item.last_verified,
            "review_by": item.review_by, "counters": tally.counters(), **more}


def _related(one: Lesson, other: Lesson) -> bool:
    """Two lessons apply to the same kind of work: they share a task or a type, or neither names any."""
    for key in ("types", "tasks"):
        first, second = ({word.lower() for word in item.applies.get(key, [])} for item in (one, other))
        if first & second:
            return True
    return not any(item.applies.get(key) for item in (one, other) for key in ("types", "tasks"))


def firm_worthy(item: Lesson, terms: list) -> bool:
    """A client's or an initiative's lesson that would read the same for anyone: it names
    no client, org or client-specific field, and says what kind of work it applies to."""
    if item.scope not in record.ENGAGEMENT_SCOPES or item.state != "active" or item.promoted_to:
        return False
    text = " ".join([item.title, item.when, item.do, item.because, item.body, *item.applies.get("keywords", []),
                     *item.applies.get("commands", [])])
    _, found = scrubber.scrub(text, firm_terms=terms)
    return (not found.refused and not _CLIENT_SCHEMA.search(text)
            and bool(item.applies.get("types") or item.applies.get("tasks")))


def build(scopes: Scopes) -> dict:
    """Everything a review looks at, as data."""
    problems: list = []
    own = storage.load(scopes, problems)
    tallies = storage.tally(storage.events_for(scopes))
    today = record.today()
    tally = lambda item: tallies.get(item.id) or Tally()
    live = [item for item in own if item.state != "retired"]
    report: dict = {"scopes": scopes.label(), "date": today, "lessons": len(own)}
    report["candidates"] = [_entry(item, tally(item)) for item in sorted(
        (item for item in own if item.state == "candidate"), key=lambda item: (item.created, item.id))]
    report["duplicates"] = [
        {"keep": one.id, "other": other.id, "overlap": round(record.overlap(one, other), 2),
         "titles": [one.title, other.title]}
        for index, one in enumerate(live) for other in live[index + 1:]
        if _related(one, other) and record.overlap(one, other) >= DUPLICATE]
    recheck = []
    for item in own:
        if item.state != "active" or tally(item).stale_mark:
            continue
        counted = tally(item)
        reasons = []
        if item.review_by and item.review_by < today:
            reasons.append(f"past its review date ({item.review_by})")
        if counted.shown >= SHOWN_UNMARKED and not counted.helpful:
            reasons.append(f"shown {counted.shown} times and never marked helpful")
        last = counted.last_shown[:10] or item.created
        if last and last < _days_ago(UNSEEN_DAYS):
            reasons.append(f"not shown in {UNSEEN_DAYS} days" if counted.shown else
                           f"never shown since it was saved ({item.created})")
        if reasons:
            recheck.append(_entry(item, counted, reasons=reasons))
    report["recheck"] = recheck
    report["stale"] = [_entry(item, tally(item), reason=tally(item).stale_mark["reason"],
                              since=tally(item).stale_mark["at"][:10],
                              superseded_by=tally(item).stale_mark["superseded_by"])
                       for item in [*own, *recall.platform_lessons()]
                       if tally(item).stale_mark and item.state != "retired"]
    report["promote"] = [
        _entry(item, tally(item), reasons=[reason for reason, holds in (
            (f"marked helpful {tally(item).helpful} times", tally(item).helpful >= PROMOTE_HELPFUL),
            ("seen a second time", any(entry["type"] == "second-occurrence" for entry in item.evidence))) if holds])
        for item in own if item.state == "active" and not item.promoted_to and not tally(item).stale_mark
        and (tally(item).helpful >= PROMOTE_HELPFUL
             or any(entry["type"] == "second-occurrence" for entry in item.evidence))]
    terms = scrubber.workspace_terms(scopes.workspace)
    report["firm_worthy"] = [_entry(item, tally(item)) for item in own
                             if firm_worthy(item, terms) and not tally(item).stale_mark]
    report["unreadable"] = problems
    return report


def render(report: dict) -> str:
    """The review as Markdown: each line with the command that acts on it."""
    lines = [f"# Lessons review: {report['scopes']} ({report['date']})", "",
             "This report changes nothing. Each line names the command that acts on it; a lesson is accepted, "
             "retired or promoted on the consultant's word.", ""]

    def section(title: str, rows: list, empty: str) -> None:
        lines.append(f"## {title} ({len(rows)})")
        lines.append("")
        lines.extend(rows or [empty])
        lines.append("")

    section("Candidates waiting", [
        f"- {item['id']} ({item['scope']}, saved {item['created'] or '?'}): {item['title']}\n"
        f"  Accept with its basis: `torque lesson verify {item['id']} --evidence "
        f"reproduced|official-doc|second-occurrence|consultant-said`. Drop: `torque lesson retire {item['id']} "
        f"--reason \"...\"`. Read: `torque lesson show {item['id']}`"
        for item in report["candidates"]], "None.")
    section("Likely duplicates", [
        f"- {pair['keep']} and {pair['other']} share {int(pair['overlap'] * 100)}% of their words: "
        f"\"{pair['titles'][0]}\" / \"{pair['titles'][1]}\"\n"
        f"  Keep one: `torque lesson stale {pair['other']} --reason \"duplicate\" --superseded-by {pair['keep']}`"
        for pair in report["duplicates"]], "None.")
    section("To re-check", [
        f"- {item['id']} ({item['scope']}): {item['title']}: {'; '.join(item['reasons'])}\n"
        f"  Still true: `torque lesson verify {item['id']}`. Not any more: `torque lesson stale {item['id']} "
        f"--reason \"...\"`"
        for item in report["recheck"]], "None.")
    section("Marked stale", [
        f"- {item['id']} ({item['scope']}, since {item['since']}): {item['title']}: {item['reason'] or 'no reason'}"
        + (f" (superseded by {item['superseded_by']})" if item["superseded_by"] else "") + "\n"
        + ("  It ships with Torque: report it to the Torque repository; it stays out of this workspace's recall."
           if item["scope"] == "platform" else
           f"  Fix its file and `torque lesson verify {item['id']}`, or `torque lesson retire {item['id']} "
           f"--reason \"...\"`")
        for item in report["stale"]], "None.")
    section("Worth promoting", [
        f"- {item['id']} ({item['scope']}): {item['title']}: {'; '.join(item['reasons'])}\n"
        f"  Propose a stronger form: `torque lesson promote {item['id']} --to firm|rule|skill|recipe|check|platform`"
        for item in report["promote"]], "None.")
    section("Could serve every client", [
        f"- {item['id']} ({item['scope']}): {item['title']}: names no client and says what it applies to\n"
        f"  Propose a firm copy: `torque lesson promote {item['id']} --to firm`"
        for item in report["firm_worthy"]], "None.")
    section("Files that could not be read", [
        f"- {problem['path']}: {problem['reason']}" for problem in report["unreadable"]], "None.")
    return "\n".join(lines).rstrip() + "\n"


def proposals_dir(scopes: Scopes) -> Path | None:
    """Where review reports and promotion proposals are written: with the selected
    engagement (they quote its lessons), or with the workspace when none is selected."""
    root = scopes.engagement or scopes.workspace
    return storage.state_dir(root) / "proposals" if root else None


def write(scopes: Scopes, report: dict) -> Path | None:
    """Save the report as .torque/lessons/proposals/<date>.md; today's file is replaced."""
    folder = proposals_dir(scopes)
    if folder is None:
        return None
    path = storage._private_dir(folder) / f"{report['date']}.md"
    storage._atomic_write(path, render(report))
    return path
