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
    # A promoted lesson and its firm copy say the same on purpose: not a duplicate to resolve.
    live = [item for item in own if item.state != "retired" and not item.promoted_to]
    report: dict = {"scopes": scopes.label(), "date": today, "lessons": len(own)}
    report["candidates"] = [_entry(item, tally(item)) for item in sorted(
        (item for item in own if item.state == "candidate"), key=lambda item: (item.created, item.id))]
    # Marked active in its file with no basis in its record: counted as a candidate, never shown as trusted.
    unbacked = [item for item in own if record.unbacked_active(item)]
    report["unbacked"] = [_entry(item, tally(item)) for item in unbacked]
    own_backed = [item for item in own if not record.unbacked_active(item)]
    report["duplicates"] = [
        {"keep": one.id, "other": other.id, "overlap": round(record.overlap(one, other), 2),
         "titles": [one.title, other.title]}
        for index, one in enumerate(live) for other in live[index + 1:]
        if _related(one, other) and record.overlap(one, other) >= DUPLICATE]
    recheck = []
    for item in own_backed:
        # A promoted lesson is not shown any more: it lives on in its promoted form.
        if item.state != "active" or item.promoted_to or tally(item).stale_mark:
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
        for item in own_backed if item.state == "active" and not item.promoted_to and not tally(item).stale_mark
        and (tally(item).helpful >= PROMOTE_HELPFUL
             or any(entry["type"] == "second-occurrence" for entry in item.evidence))]
    terms = scrubber.workspace_terms(scopes.workspace)
    report["firm_worthy"] = [_entry(item, tally(item)) for item in own_backed
                             if firm_worthy(item, terms) and not tally(item).stale_mark]
    report["unreadable"] = problems
    _displayed(report, {item.id: item.scope for item in [*own, *recall.platform_lessons()]}, terms)
    return report


def _displayed(report: dict, scope_of: dict, terms: list) -> None:
    """The review prints what lesson files say, and a file may have been edited by hand.
    Each printed text is scrubbed as it would be at capture: a credential-looking value
    withholds the text, Ids and personal data are masked, and in a firm or platform
    lesson a client's name is replaced with a neutral word."""
    def shown(text: str, lesson_id: str) -> str:
        wider = scope_of.get(lesson_id) in ("firm", "platform")
        cleaned, found = scrubber.scrub(text or "", firm_terms=terms if wider else None, generalize=wider)
        return scrubber.WITHHELD if found.refused else cleaned

    for name in ("candidates", "unbacked", "recheck", "stale", "promote", "firm_worthy"):
        for entry in report[name]:
            for key in ("title", "when", "do", "reason"):
                if entry.get(key):
                    entry[key] = shown(entry[key], entry["id"])
    for pair in report["duplicates"]:
        pair["titles"] = [shown(title, lesson_id) for title, lesson_id in zip(pair["titles"],
                                                                             (pair["keep"], pair["other"]))]


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
    section("Marked active without a basis", [
        f"- {item['id']} ({item['scope']}): {item['title']}: its file says active, but its record carries neither "
        "the consultant's word nor evidence that it holds, so it is treated as a candidate and not shown as "
        f"trusted.\n  Give it a basis: `torque lesson verify {item['id']} --evidence "
        f"reproduced|official-doc|second-occurrence|consultant-said`. Drop: `torque lesson retire {item['id']} "
        "--reason \"...\"`"
        for item in report["unbacked"]], "None.")
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


def stats(scopes: Scopes, since: str | None = None) -> dict:
    """Counts that show whether the loop runs: what was captured (by source and scope),
    what was shown and on which surface, how it was judged, and what waits."""
    if since:
        since = record._day(since, "--since")
    problems: list = []
    damaged: list = []
    own = storage.load(scopes, problems)
    events = storage.events_for(scopes, damaged)
    tallies = storage.tally(events)
    recent = [row for row in events if not since or row["at"][:10] >= since]
    captured = [item for item in own if not since or (item.created or "") >= since]
    count = lambda values: {value: values.count(value) for value in sorted(set(values))}
    candidates = sorted((item for item in own if item.state == "candidate"), key=lambda item: (item.created, item.id))
    oldest = candidates[0] if candidates else None
    never = [item.id for item in own
             if recall.eligible(item, tallies.get(item.id)) and not (tallies.get(item.id) or Tally()).shown]
    return {
        "scopes": scopes.label(), "since": since, "lessons": len(own),
        "states": count([item.state for item in own]),
        "captured": {"total": len(captured), "by_source": count([item.source for item in captured]),
                     "by_scope": count([item.scope for item in captured])},
        "events": {name: sum(row["event"] == name for row in recent) for name in storage.EVENTS},
        "shown_by_surface": count([row.get("via") or "unknown" for row in recent if row["event"] == "shown"]),
        "candidates_waiting": len(candidates),
        "oldest_candidate": ({"id": oldest.id, "created": oldest.created, "days": record.age_days(oldest)}
                             if oldest else None),
        "never_shown": never, "marked_stale": sum(bool(tally.stale_mark) for tally in tallies.values()),
        "platform_lessons": len(recall.platform_lessons()), "unreadable": problems,
        "damaged_events": len(damaged)}


def render_stats(report: dict) -> str:
    listed = lambda values: ", ".join(f"{name} {number}" for name, number in values.items()) or "none"
    oldest = report["oldest_candidate"]
    return "\n".join([
        f"Lessons ({report['scopes']})" + (f" since {report['since']}" if report["since"] else "")
        + f": {report['lessons']} in the workspace ({listed(report['states'])}), {report['platform_lessons']} platform",
        f"Captured: {report['captured']['total']} (by source: {listed(report['captured']['by_source'])}; by scope: "
        f"{listed(report['captured']['by_scope'])})",
        f"Events: {listed(report['events'])}",
        f"Shown by surface: {listed(report['shown_by_surface'])}",
        f"Candidates waiting: {report['candidates_waiting']}"
        + (f"; the oldest is {oldest['id']} ({oldest['days']} days): torque lesson review" if oldest else ""),
        f"Active and never shown: {len(report['never_shown'])}"
        + (f" ({', '.join(report['never_shown'][:5])}{' ...' if len(report['never_shown']) > 5 else ''})"
           if report["never_shown"] else ""),
        f"Marked stale: {report['marked_stale']}", *recall.problem_lines(report["unreadable"]),
        *([f"Damaged event rows skipped: {report['damaged_events']} (not counted above; the events log holds "
           "lines that are not valid events)"] if report["damaged_events"] else [])])


LIVE_DAYS = 30


def health(scopes: Scopes) -> dict:
    """What `torque doctor` reports about lessons: counts by state, unreadable files, the
    last capture and the last time a lesson was shown, for the firm and for the selected
    engagement; and whether the loop is live (a lesson was saved or shown in the last
    30 days) or idle."""
    def of(scope: str, root) -> dict:
        problems: list = []
        damaged: list = []
        lessons = storage.read_folder(scopes.folder(scope), scope, scopes, problems)
        shown = [row["at"][:10] for row in storage.read_events(root, damaged) if row["event"] == "shown"]
        return {"states": {state: sum(item.state == state for item in lessons) for state in record.STATES},
                "unreadable": problems, "last_capture": max((item.created for item in lessons), default="") or None,
                "last_shown": max(shown, default=None), "damaged_events": len(damaged),
                "unbacked": [item.id for item in lessons if record.unbacked_active(item)]}
    platform_problems: list = []
    platform = recall.platform_lessons(platform_problems)
    report: dict = {
        "firm": of("firm", scopes.workspace) if scopes.workspace else None,
        "engagement": ({"kind": scopes.kind, "slug": scopes.slug, **of(scopes.kind, scopes.engagement)}
                       if scopes.engagement else None),
        "platform": {"shipped": sum(item.origin == "shipped" for item in platform),
                     "advisory": sum(item.origin == "advisory" for item in platform),
                     "unreadable": platform_problems}}
    dates = [value for part in (report["firm"], report["engagement"]) if part
             for value in (part["last_capture"], part["last_shown"]) if value]
    report["loop"] = "live" if any(value >= _days_ago(LIVE_DAYS) for value in dates) else "idle"
    return report


def health_lines(report: dict) -> list[str]:
    """The lessons lines of `torque doctor`."""
    lines = []
    for label, part in (("firm", report["firm"]),
                        (f"{report['engagement']['kind']} {report['engagement']['slug']}"
                         if report["engagement"] else "", report["engagement"])):
        if part:
            states = ", ".join(f"{number} {state}" for state, number in part["states"].items())
            lines.append(f"Lessons ({label}): {states}; unreadable files {len(part['unreadable'])}; last saved "
                         f"{part['last_capture'] or 'never'}; last shown {part['last_shown'] or 'never'}"
                         + (f"; damaged event rows skipped {part['damaged_events']}" if part.get("damaged_events")
                            else "")
                         + (f"; marked active without a basis {len(part['unbacked'])} (treated as candidates: "
                            "torque lesson review)" if part.get("unbacked") else ""))
    platform = report["platform"]
    lines.append(f"Lessons (platform): {platform['shipped']} shipped, {platform['advisory']} catalogue notes")
    lines.append("Lessons loop: live (a lesson was saved or shown in the last 30 days)" if report["loop"] == "live"
                 else "Lessons loop: idle (no lesson was saved or shown in the last 30 days). Save one with "
                      "`torque session add ... --learned TEXT`; ask with `torque lesson for TOPIC`.")
    return lines


def selfcheck() -> dict:
    """Prove the wiring in a temporary folder: write a lesson, then find it again by
    metadata type, by command, through the context block and through the function other
    packages call, and see that showing it was recorded. Touches no workspace."""
    import json
    import tempfile
    from . import intake
    steps: list[dict] = []

    def step(name: str, passed: bool, detail: str = "") -> bool:
        steps.append({"step": name, "passed": bool(passed), "detail": detail})
        return bool(passed)

    with tempfile.TemporaryDirectory(prefix="torque-lessons-") as temporary:
        try:
            root = Path(temporary).resolve() / "ws"
            client = root / "clients" / "selfcheck"
            client.mkdir(parents=True)
            (root / "workspace.json").write_text(json.dumps({"name": "selfcheck"}), encoding="utf-8")
            (client / "client.json").write_text(json.dumps({"name": "Selfcheck", "slug": "selfcheck"}),
                                                encoding="utf-8")
            scopes = storage.scopes_for(client)
            added = intake.add(scopes, when="you are about to deploy a flow in the selfcheck",
                               do="read the active version back", because="a deploy can leave a flow inactive",
                               types=["Flow"], commands=["sf project deploy *"], source="consultant")
            wanted = added.lesson.id
            step("write a lesson", added.status == "saved" and added.path.is_file(), wanted)
            found = lambda **query: wanted in [hit.lesson.id for hit in recall.find(scopes, **query).hits]
            step("recall by metadata type", found(types=["Flow"]))
            command = "sf project deploy start --metadata Flow:Selfcheck --target-org selfcheck"
            step("recall by command", found(command=command))
            step("the context block", wanted in [item["id"] for item in recall.context_section(scopes)["shown"]])
            text = recall.for_command(command, scopes=scopes, via="selfcheck")
            step("the function other packages call", wanted in text)
            step("showing it was recorded", storage.tally(storage.events_for(scopes)).get(wanted, Tally()).shown == 1)
            platform = recall.platform_lessons()
            step("platform lessons are readable", bool(platform),
                 f"{sum(item.origin == 'shipped' for item in platform)} shipped, "
                 f"{sum(item.origin == 'advisory' for item in platform)} catalogue notes")
        except Exception as exc:  # A self-check reports; it never raises into doctor.
            step("run to the end", False, f"{type(exc).__name__}: {exc}")
    return {"passed": all(item["passed"] for item in steps), "steps": steps}
