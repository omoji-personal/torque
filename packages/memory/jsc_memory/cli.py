"""`torque lesson`: capture what was learned, find it again at the point of use, judge it.

Scope: `torque lesson ... --workspace PATH` with `--client NAME` or `--initiative NAME`.
A command reads the selected client or initiative, the firm and the platform, and never
another client's folder. Without a client or initiative it reads the firm and the
platform; without a workspace, the platform only.

Every command takes --json, never prompts, and reads standard input only when the text
is given as `-`. Exit codes:

  0  done (also: nothing matched)
  1  no such lesson
  2  the command was not understood, or there is nothing to save or nowhere to keep it
  3  the id matches more than one lesson, or is too short (the candidates are listed)
  4  refused: the text holds a credential-looking value, or a firm lesson names a client
  5  read-only: the lesson ships with Torque and is not changed from a workspace
  6  a check failed
  7  busy: another Torque command holds the lessons lock; run the command again
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import feedback, intake, promote, recall, review, storage
from . import lesson as record
from .lesson import Lesson, LessonError
from .storage import Scopes, Tally

EXIT_OK, EXIT_NOT_FOUND, EXIT_USAGE, EXIT_AMBIGUOUS, EXIT_REFUSED, EXIT_READ_ONLY, EXIT_FAILED, EXIT_BUSY = range(8)
_LEGACY_STATES = {"pending": "candidate", "active": "active", "archive": "retired"}


def _out(args: argparse.Namespace, payload: dict, text: str) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else text)


def _fail(args: argparse.Namespace, code: int, message: str, **more) -> int:
    """Say what went wrong and what to do next: on stderr, and as JSON when asked."""
    if getattr(args, "json", False):
        print(json.dumps({"error": message, "exit_code": code, **more}, indent=2, ensure_ascii=False))
    print(f"torque lesson: {message}", file=sys.stderr)
    return code


def _candidates(lessons: list[Lesson]) -> list[dict]:
    return [{"id": item.id, "scope": item.scope, "state": item.state, "title": item.title} for item in lessons]


def _row(item: Lesson, tally: Tally | None = None) -> str:
    tally = tally or Tally()
    state = "stale" if tally.stale_mark else "promoted" if item.promoted_to and item.state == "active" else item.state
    counted = ", ".join(f"{name} {count}" for name, count in tally.counters().items() if count)
    return (f"{item.id}  {item.scope:<10} {state:<9} {item.title}  ({item.evidence_type}, {record.age_text(item)}"
            + (f"; {counted}" if counted else "") + ")")


def _read_text(args: argparse.Namespace) -> str:
    words = list(args.text or [])
    if words == ["-"]:
        return sys.stdin.read()
    return " ".join(words)


def _file_text(path: str, scopes: Scopes) -> str:
    if scopes.foreign(path):
        raise LessonError(f"{path} lies in the folder of another client or initiative, and a lesson command does "
                          "not read there. Select the client it belongs to (--client NAME) to keep its lesson "
                          "with it.")
    try:
        with open(path, encoding="utf-8") as stream:
            return stream.read(storage.MAX_BYTES + 1)[:storage.MAX_BYTES]
    except (OSError, UnicodeDecodeError) as exc:
        raise LessonError(f"cannot read {path} as UTF-8 text ({type(exc).__name__}).") from None


def cmd_add(args: argparse.Namespace, scopes: Scopes) -> int:
    text, title, source, evidence = _read_text(args), args.title or "", args.source, list(args.evidence)
    body = _file_text(args.body_file, scopes) if args.body_file else ""
    if args.from_file:
        # A note from a host's own memory: its text becomes the lesson, in the scope chosen here.
        meta, note = intake.host_note(_file_text(args.from_file, scopes))
        title = title or meta.get("description") or meta.get("name") or ""
        body = "\n\n".join(part for part in (body, note) if part)
        source = source or "harvested"
        evidence = evidence or [f"session:host note {Path(args.from_file).name}"]
    result = intake.add(scopes, text=text, title=title, when=args.when or "", do=args.do or "",
                        because=args.because or "", body=body, scope=args.scope, kind=args.kind, tasks=args.task,
                        types=args.type, commands=args.command, keywords=args.keyword, errors=args.error,
                        evidence=evidence, source=source or "agent", session=args.session)
    item = result.lesson
    verb = {"saved": "Saved", "duplicate": "Already saved:", "second-occurrence": "Second occurrence of"}[result.status]
    lines = [f"{verb} {item.id} ({item.scope}, {item.state}): {item.title}"]
    if result.status == "saved":
        applies = "; ".join(f"{name} {', '.join(values)}" for name, values in item.applies.items() if values)
        guessed = ", ".join(result.inferred)
        lines.append(f"  applies: {applies or 'nothing yet'}"
                     + (f" ({guessed} inferred from the text; correct with torque lesson edit {item.id} "
                        "--set applies.types=...)" if guessed else ""))
        if item.state == "candidate":
            lines.append("  A candidate is not shown at the point of use until it has a basis: "
                         f"torque lesson verify {item.id} --evidence reproduced|official-doc|consultant-said")
        if result.masked:
            lines.append("  masked: " + ", ".join(f"{name} ({count})" for name, count in result.masked.items()))
        lines.append(f"  file: {result.path}")
        lines.append(f"  short id: {record.short_id(item.id)} (every lesson command takes it)")
    lines += [f"  {note}" for note in result.notes]
    _out(args, result.as_dict(), "\n".join(lines))
    return EXIT_OK


def cmd_for(args: argparse.Namespace, scopes: Scopes) -> int:
    result = recall.find(scopes, topic=args.topic, tasks=args.task, types=args.type, command=args.command or "",
                         paths=args.path, error=args.error or "", limit=max(1, args.limit),
                         include_candidates=args.include_candidates)
    recall.mark_shown(scopes, [hit.lesson for hit in result.hits], "for")
    _out(args, recall.as_json(result), recall.render(result))
    return EXIT_OK


def _select(scopes: Scopes, args: argparse.Namespace, problems: list) -> list[tuple[Lesson, Tally]]:
    """The lessons a listing shows, with their counters, filtered as asked."""
    tallies = storage.tally(storage.events_for(scopes))
    scope, state = getattr(args, "scope", None), getattr(args, "state", None)
    lessons = recall.everything(scopes, problems) if scope == "platform" or getattr(args, "everything", False) \
        else storage.load(scopes, problems)
    task = recall.vocab.tag(args.task) if getattr(args, "task", None) else None
    kind = recall.vocab.canonical_type(args.type).lower() if getattr(args, "type", None) else None
    rows = []
    for item in lessons:
        tally = tallies.get(item.id) or Tally()
        if scope and item.scope != scope:
            continue
        if state == "stale" and not tally.stale_mark or state not in (None, "stale") and item.state != state:
            continue
        if task and task not in {recall.vocab.tag(value) for value in item.applies["tasks"]}:
            continue
        if kind and kind not in {value.lower() for value in item.applies["types"]}:
            continue
        rows.append((item, tally))
    rows.sort(key=lambda row: (recall._SCOPE_ORDER[row[0].scope], row[0].state != "active", row[0].id))
    return rows


def _listing(args: argparse.Namespace, scopes: Scopes, rows, problems: list, heading: str) -> int:
    platform = len(recall.platform_lessons())
    text = [heading] if rows else [f"No lessons to list ({scopes.label()})."]
    text += [_row(item, tally) for item, tally in rows]
    if rows:
        text.append(f"An id's last part is enough for every command (torque lesson show "
                    f"{record.short_id(rows[0][0].id)}).")
    if getattr(args, "scope", None) != "platform" and not getattr(args, "everything", False):
        text.append(f"Plus {platform} platform lessons shipped with Torque: torque lesson list --scope platform")
    text += recall.problem_lines(problems, limit=20)
    _out(args, {"scopes": scopes.label(), "lessons": [recall.summary(item, tally) for item, tally in rows],
                "platform_lessons": platform, "unreadable": problems}, "\n".join(text))
    return EXIT_OK


def cmd_list(args: argparse.Namespace, scopes: Scopes) -> int:
    problems: list = []
    rows = _select(scopes, args, problems)
    return _listing(args, scopes, rows, problems, f"Lessons ({scopes.label()}): {len(rows)}")


def cmd_search(args: argparse.Namespace, scopes: Scopes) -> int:
    problems: list = []
    args.everything = True
    terms = [term.lower() for term in args.terms]
    rows = [(item, tally) for item, tally in _select(scopes, args, problems)
            if all(term in " ".join([item.id, item.title, item.when, item.do, item.because, item.body,
                                     *(word for name in record.APPLIES for word in item.applies.get(name, []))]
                                    ).lower() for term in terms)]
    return _listing(args, scopes, rows, problems, f"Lessons that mention {' '.join(args.terms)}: {len(rows)}")


def _full(item: Lesson, tally: Tally) -> str:
    applies = "; ".join(f"{name} {', '.join(values)}" for name, values in item.applies.items() if values)
    evidence = "; ".join(" ".join(str(entry[key]) for key in ("type", "ref", "date") if entry.get(key))
                         for entry in item.evidence)
    lines = [f"{item.id}  {item.title}",
             f"scope {item.scope}; kind {item.kind}; state {item.state}; source {item.source}"]
    lines += [f"{label:<5} {value}" for label, value in (("WHEN", item.when), ("DO", item.do), ("WHY", item.because))
              if value]
    lines += [f"applies: {applies or 'nothing set'}", f"evidence: {evidence or 'none'}",
              f"created {item.created or '?'}; last verified {item.last_verified or 'never'}; "
              f"review by {item.review_by or 'not set'}",
              "counters: " + ", ".join(f"{name} {count}" for name, count in tally.counters().items())]
    if tally.stale_mark:
        lines.append(f"marked stale {tally.stale_mark['at'][:10]}: {tally.stale_mark['reason'] or 'no reason given'}"
                     + (f" (superseded by {tally.stale_mark['superseded_by']})" if tally.stale_mark["superseded_by"]
                        else ""))
    lines += [f"{label}: {', '.join(values)}" for label, values in (("supersedes", item.supersedes),
                                                                    ("promoted to", item.promoted_to)) if values]
    lines.append(f"file: {item.path}" if item.path else "file: shipped with Torque (read-only)")
    return "\n".join(lines) + (f"\n\n{item.body}" if item.body else "")


def cmd_show(args: argparse.Namespace, scopes: Scopes) -> int:
    if args.id in (None, *_LEGACY_STATES):
        # `show pending|active|archive` is the old name of `list --state ...`.
        args.scope = args.task = args.type = None
        args.state = _LEGACY_STATES.get(args.id)
        return cmd_list(args, scopes)
    item = storage.resolve(recall.everything(scopes), args.id)
    tally = storage.tally(storage.events_for(scopes)).get(item.id) or Tally()
    _out(args, {**recall.summary(item, tally), "body": item.body, "evidence": item.evidence,
                "supersedes": item.supersedes}, _full(item, tally))
    return EXIT_OK


def _counted(scopes: Scopes, item: Lesson) -> tuple[dict, str]:
    tally = feedback.tally_of(scopes, item)
    return tally.counters(), ", ".join(f"{name} {count}" for name, count in tally.counters().items())


def cmd_helpful(args: argparse.Namespace, scopes: Scopes) -> int:
    item = feedback.helpful(scopes, args.id, args.note or "", args.session)
    counters, text = _counted(scopes, item)
    _out(args, {"id": item.id, "event": "helpful", "counters": counters},
         f"Marked helpful: {item.id} ({item.title}); {text}")
    return EXIT_OK


def cmd_stale(args: argparse.Namespace, scopes: Scopes) -> int:
    item, newer = feedback.stale(scopes, args.id, args.reason, args.superseded_by)
    counters, text = _counted(scopes, item)
    _out(args, {"id": item.id, "event": "stale", "counters": counters, "superseded_by": newer.id if newer else None},
         f"Marked stale: {item.id} ({item.title}); it is out of recall from now on"
         + (f", superseded by {newer.id}" if newer else "") + f". {text}\n"
         f"Fixed it? torque lesson verify {item.id}. Marked by mistake? torque lesson restore {item.id}. "
         f"Gone for good? torque lesson retire {item.id} --reason \"...\"")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace, scopes: Scopes) -> int:
    item, outcome = feedback.verify(scopes, args.id, args.evidence)
    _out(args, {"id": item.id, "event": "verified", "state": item.state, "outcome": outcome,
                "last_verified": item.last_verified, "review_by": item.review_by},
         f"Verified {item.id} ({item.title}): {outcome}"
         + (f"; review by {item.review_by}" if item.scope != "platform" else ""))
    return EXIT_OK


def cmd_retire(args: argparse.Namespace, scopes: Scopes) -> int:
    item = feedback.retire(scopes, args.id, args.reason)
    _out(args, {"id": item.id, "event": "retired", "state": item.state},
         f"Retired {item.id} ({item.title}). Its file stays; bring it back with: torque lesson restore {item.id}")
    return EXIT_OK


def cmd_restore(args: argparse.Namespace, scopes: Scopes) -> int:
    item, outcome = feedback.restore(scopes, args.id)
    _out(args, {"id": item.id, "state": item.state, "outcome": outcome}, f"{item.id} ({item.title}): {outcome}")
    return EXIT_OK


def cmd_edit(args: argparse.Namespace, scopes: Scopes) -> int:
    item, changed = feedback.edit(scopes, args.id, args.set)
    _out(args, {"id": item.id, "changed": changed, "path": str(item.path), **recall.summary(item)},
         f"Changed {', '.join(changed)} of {item.id} ({item.title}).\n"
         f"For the body or anything else, edit the file: {item.path}")
    return EXIT_OK


def cmd_review(args: argparse.Namespace, scopes: Scopes) -> int:
    report = review.build(scopes)
    path = review.write(scopes, report)
    _out(args, {**report, "report": str(path) if path else None},
         review.render(report) + (f"\nSaved as {path}\n" if path else ""))
    return EXIT_OK


def cmd_promote(args: argparse.Namespace, scopes: Scopes) -> int:
    if args.apply:
        if args.id or args.to:
            raise LessonError("give either a lesson id with --to, or --apply PROPOSAL, not both.")
        done = promote.apply(scopes, args.apply)
        _out(args, done, f"Already applied: firm lesson {done['firm']} exists for {done['source']}."
             if done["status"] == "already applied" else
             f"Applied: firm lesson {done['firm']} written ({done['path']}); {done['source']} is marked "
             f"promoted_to firm:{done['firm']} and is no longer shown on its own.")
        return EXIT_OK
    if not args.id or not args.to:
        raise LessonError("say what to promote and where: torque lesson promote ID --to "
                          + "|".join(promote.TARGETS) + " [--name NAME], or --apply PROPOSAL.")
    made = promote.propose(scopes, args.id, args.to, args.name or "")
    replaced = ", ".join(f"{kind} ({count})" for kind, count in made["replaced"].items()) or "nothing"
    _out(args, made, "\n".join([
        f"Proposal written, nothing applied: {made['proposal']}",
        f"It proposes {made['lesson']} ({made['from']}) for: {made['to']}"
        + (f" `{made['name']}`" if made["name"] else "") + f". The scrub replaced: {replaced}."
        + (f" Left out: {len(made['left_out'])} front-matter key(s) Torque does not know "
           f"({', '.join(made['left_out'])}); their values are not copied." if made["left_out"] else "")
        + (f" Custom names in the text: {', '.join(made['custom_names'])}; confirm none identifies the client."
           if made["custom_names"] else ""),
        "Read it with the consultant. " + (
            f"On their word: torque lesson promote --apply {Path(made['proposal']).name}" if made["applies_itself"]
            else "A person applies it in the destination it names; Torque does not change shared guidance."),
        "", made["text"]]))
    return EXIT_OK


def cmd_stats(args: argparse.Namespace, scopes: Scopes) -> int:
    report = review.stats(scopes, args.since)
    _out(args, report, review.render_stats(report))
    return EXIT_OK


def cmd_harvest(args: argparse.Namespace, scopes: Scopes) -> int:
    report = intake.harvest(scopes, args.host, args.dir, args.since)
    files = report["files"]
    if not report["found"]:
        text = [report["note"]]
    else:
        text = [f"{len(files)} file(s) changed in {report['dir']}"
                + (f" since {report['since']}" if report["since"] else "") + ". Nothing was copied."]
        text += [f"- {item['name']} [{item['type'] or 'note'}, {item['modified']}] {item['description']}\n"
                 f"  {item['path']}" for item in files[:args.limit]]
        if len(files) > args.limit:
            text.append(f"... and {len(files) - args.limit} more (--limit N, or --json for all)")
        skipped = report.get("skipped") or {}
        if any(skipped.values()):
            text.append(f"Not read: {skipped['other engagements']} folder(s) or file(s) of another client or "
                        f"initiative, and {skipped['links']} link(s), which a harvest does not follow.")
        if files:
            text.append("A host's memory mixes clients. To keep one as a lesson, choose its scope: "
                        "torque lesson add --from-file PATH --when ... --do ... --because ... [--scope firm]")
    _out(args, report, "\n".join(text))
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace, scopes: Scopes) -> int:
    report = intake.migrate(scopes)
    text = [f"Imported {len(report['imported'])} lesson(s) from the earlier store; {report['already']} were "
            f"already imported. The old files are left in place: {report['old_store']}"]
    text += [f"- {item['id']} ({item['state']}): {item['title']}" for item in report["imported"]]
    text += [f"Not imported: {problem['path']} ({problem['reason']})" for problem in report["problems"]]
    _out(args, report, "\n".join(text))
    return EXIT_OK


def cmd_selfcheck(args: argparse.Namespace, scopes: Scopes) -> int:
    report = review.selfcheck()
    text = [f"Lessons self-check: {'passed' if report['passed'] else 'FAILED'}"]
    text += [f"  {'ok  ' if item['passed'] else 'FAIL'} {item['step']}" + (f" ({item['detail']})" if item["detail"] else "")
             for item in report["steps"]]
    if not report["passed"]:
        text.append("The lessons loop is not working in this installation; reinstall Torque, then run this again.")
    _out(args, report, "\n".join(text))
    return EXIT_OK if report["passed"] else EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lessons: capture what was learned, find it again at the point of use, judge it.",
        epilog="Select the scope with --workspace PATH and --client NAME or --initiative NAME. "
               "Exit codes: 0 done, 1 no such lesson, 2 not understood, 3 ambiguous id, 4 refused by the scrub, "
               "5 read-only, 6 a check failed, 7 busy.", allow_abbrev=False)
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    def command(name: str, function, summary: str, aliases=()) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=summary, description=summary, aliases=list(aliases), allow_abbrev=False)
        child.add_argument("--json", action="store_true", help="print one JSON object")
        child.set_defaults(function=function)
        return child

    add = command("add", cmd_add, "save a lesson (default scope: the selected client or initiative)", ["capture"])
    add.add_argument("text", nargs="*", help="the lesson as free text (`-` reads standard input); "
                                             "`When: ... Do: ... Because: ...` is read as those three parts")
    add.add_argument("--when", help="the situation: you are about to ...")
    add.add_argument("--do", help="what to do, concrete enough to check")
    add.add_argument("--because", help="the reason")
    add.add_argument("--title")
    add.add_argument("--scope", choices=("client", "initiative", "firm"),
                     help="firm: shown for every client, so it must not identify one")
    add.add_argument("--kind", choices=record.KINDS, default="pitfall")
    for flag, target, text in (("--task", "task", "a task word, e.g. deploy, flow-build, data-load"),
                               ("--type", "type", "a metadata type, e.g. Flow, CustomField"),
                               ("--command", "command", "a command pattern, e.g. \"sf project deploy *\""),
                               ("--keyword", "keyword", "a word to find the lesson by"),
                               ("--error", "error", "a piece of an error text this lesson answers, e.g. "
                                                    "\"No such column\"; * stands for any characters")):
        add.add_argument(flag, dest=target, action="append", default=[], help=text + " (repeatable)")
    add.add_argument("--evidence", action="append", default=[], metavar="TYPE[:REF]",
                     help="one of " + ", ".join(record.EVIDENCE_TYPES) + "; reproduced, official-doc and "
                          "second-occurrence admit the lesson as active")
    add.add_argument("--source", dest="source", choices=("consultant", "agent", "doc"),
                     help="who the lesson comes from (default agent); the consultant's word admits it as active")
    add.add_argument("--body-file", metavar="PATH", help="a short body: the error text, a reproduction, one example")
    add.add_argument("--from-file", metavar="PATH", help="a note from a host's own memory (see `harvest`)")
    add.add_argument("--session", metavar="ID", help="the session record this lesson comes from")

    find = command("for", cmd_for, "the lessons that match the work at hand, best first", ["recall"])
    find.add_argument("topic", nargs="*", help="words for what you are about to do")
    find.add_argument("--task", action="append", default=[], help="a task word or recipe step (repeatable)")
    find.add_argument("--type", action="append", default=[], help="a metadata type (repeatable)")
    find.add_argument("--command", help="the command you are about to run")
    find.add_argument("--error", metavar="TEXT", help="the first line of an error you just got: what fixed it before")
    find.add_argument("--path", action="append", default=[],
                      help="a source file, folder or package.xml you are about to change (repeatable)")
    find.add_argument("--limit", type=int, default=5)
    find.add_argument("--include-candidates", action="store_true", help="also show lessons not yet reviewed")

    listing = command("list", cmd_list, "the lessons of the selected scope, with state and counters")
    listing.add_argument("--scope", choices=record.SCOPES)
    listing.add_argument("--state", choices=(*record.STATES, "stale"))
    listing.add_argument("--task")
    listing.add_argument("--type")

    show = command("show", cmd_show, "one lesson in full")
    show.add_argument("id", nargs="?", help="a lesson id, or a unique start of one")

    search = command("search", cmd_search, "lessons whose text holds every term, in any state")
    search.add_argument("terms", nargs="+")

    helpful = command("helpful", cmd_helpful, "a lesson helped: count it")
    helpful.add_argument("id")
    helpful.add_argument("--note", help="what it saved, in a few words")
    helpful.add_argument("--session", metavar="ID", help="the session record it helped in")

    stale = command("stale", cmd_stale, "a lesson is wrong or out of date: take it out of recall at once")
    stale.add_argument("id")
    stale.add_argument("--reason", required=True, help="what changed, or what is wrong with it")
    stale.add_argument("--superseded-by", metavar="ID", help="the lesson that replaces it")

    verify = command("verify", cmd_verify, "a lesson was checked and holds: date it, clear a stale mark, "
                                           "admit a candidate")
    verify.add_argument("id")
    verify.add_argument("--evidence", action="append", default=[], metavar="TYPE[:REF]",
                        help="the basis: reproduced, official-doc, second-occurrence or consultant-said "
                             "(a candidate needs one to become active)")

    retire = command("retire", cmd_retire, "a lesson no longer applies: keep its file, never show it")
    retire.add_argument("id")
    retire.add_argument("--reason", required=True)

    restore = command("restore", cmd_restore, "bring back a retired lesson, or clear a stale mark")
    restore.add_argument("id")

    edit = command("edit", cmd_edit, "change fields of a lesson (or edit its file)")
    edit.add_argument("id")
    edit.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                      help="one of " + ", ".join(feedback.EDITABLE) + "; lists as comma-separated words (repeatable)")

    command("review", cmd_review, "what waits, what repeats, what to re-check and what to promote; changes nothing")

    promoted = command("promote", cmd_promote, "write a proposal to promote a lesson; --apply performs a "
                                               "client-to-firm promotion")
    promoted.add_argument("id", nargs="?")
    promoted.add_argument("--to", choices=promote.TARGETS)
    promoted.add_argument("--name", help="the rule, skill, recipe or check the text is for")
    promoted.add_argument("--apply", metavar="PROPOSAL", help="apply a proposal written for --to firm")

    stats = command("stats", cmd_stats, "what was captured, shown and judged, and what waits")
    stats.add_argument("--since", metavar="DATE", help="count from this day (YYYY-MM-DD)")

    harvested = command("harvest", cmd_harvest, "list the files a host's own memory changed; copies nothing")
    harvested.add_argument("--host", required=True, choices=intake.HOSTS)
    harvested.add_argument("--dir", metavar="PATH", help="the host's memory or rules folder, when it is not found")
    harvested.add_argument("--since", metavar="DATE", help="list what changed from this day (default: the last harvest)")
    harvested.add_argument("--limit", type=int, default=40)

    command("migrate", cmd_migrate, "import the earlier JSON lessons of the selected client, once")
    command("selfcheck", cmd_selfcheck, "prove the wiring in a temporary folder: write a lesson, find it again")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        # Run as `jsc-memory` or `python -m jsc_memory.cli`: go through Torque's own entry
        # point, which reads --workspace, --client and --initiative and prepares the output.
        from torque import cli as torque_cli
        return torque_cli.main(["lesson", *sys.argv[1:]])
    args = build_parser().parse_args(argv)
    try:
        return args.function(args, storage.scopes_from_env())
    except intake.Refused as exc:
        return _fail(args, EXIT_REFUSED, str(exc), refused=exc.found.refused)
    except storage.NotFound as exc:
        return _fail(args, EXIT_NOT_FOUND, f"{exc}. List them with: torque lesson list")
    except storage.Ambiguous as exc:
        named = "; ".join(f"{item.id} ({item.title})" for item in exc.candidates)
        return _fail(args, EXIT_AMBIGUOUS, f"{exc}; nothing was changed." + (f" Candidates: {named}" if named else ""),
                     candidates=_candidates(exc.candidates))
    except storage.Busy as exc:
        return _fail(args, EXIT_BUSY, str(exc))
    except feedback.ReadOnly as exc:
        return _fail(args, EXIT_READ_ONLY, str(exc))
    except LessonError as exc:
        return _fail(args, EXIT_USAGE, str(exc))


if __name__ == "__main__":
    sys.exit(main())
