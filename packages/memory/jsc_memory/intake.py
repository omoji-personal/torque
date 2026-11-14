"""Bring lessons in: capture one, and save what a session taught.

Capture rides on steps that already happen (`torque session add --learned`), with
`torque lesson add` for a lesson on its own. Every capture is scrubbed, gets its
applicability from the text when none is given, and is admitted to `active` only on the
consultant's word or with evidence that it holds; otherwise it waits as a candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from . import lesson as record
from . import scrubber, storage, vocab
from .lesson import Lesson, LessonError
from .storage import Scopes

SIMILAR = 0.6
_LABELS = {"when": "when", "do": "do", "how to apply": "do", "because": "because", "why": "because"}
_LABEL = re.compile(r"(?i)(?:^|(?<=[\s;.(]))\**\s*(when|do|how to apply|because|why)\s*:\**")


class Refused(LessonError):
    """The scrub refused the text; `found` holds the classes and counts, never a value."""

    def __init__(self, message: str, found: scrubber.Found):
        super().__init__(message)
        self.found = found


@dataclass
class Added:
    lesson: Lesson
    path: Path | None
    status: str                                  # saved | duplicate | second-occurrence
    inferred: dict = field(default_factory=dict)  # what Torque took from the text: tasks, types, keywords
    masked: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"id": self.lesson.id, "status": self.status, "state": self.lesson.state, "scope": self.lesson.scope,
                "title": self.lesson.title, "path": str(self.path) if self.path else None,
                "applies": self.lesson.applies, "inferred": self.inferred, "masked": self.masked,
                "notes": self.notes}


def structure(text: str) -> dict:
    """The `When: ... Do: ... Because: ...` parts of a free text, when it is written that
    way (also `Why:` and `How to apply:`). Empty when the text has no such labels."""
    marks = list(_LABEL.finditer(text))
    if not any(_LABELS[mark.group(1).lower()] in ("when", "do") for mark in marks):
        return {}
    parts: dict = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        value = record.one_line(text[mark.end():end]).strip(" ;*").rstrip(".")
        name = _LABELS[mark.group(1).lower()]
        if value and name not in parts:
            parts[name] = value
    lead = record.one_line(text[:marks[0].start()]).strip(" ;*")
    return {**parts, **({"lead": lead} if lead else {})}


def host_note(text: str) -> tuple[dict, str]:
    """(name, description and type; body) of a note in a host's own memory: Markdown that
    may start with a front matter. Read leniently: a host's file is not a lesson file."""
    meta: dict = {}
    try:
        front, text = record.split_front_matter(text)
        loaded = record.load_yaml(front)
        if isinstance(loaded, dict):
            nested = loaded.get("metadata") if isinstance(loaded.get("metadata"), dict) else {}
            meta = {key: record.one_line(loaded.get(key) or nested.get(key) or "")
                    for key in ("name", "description", "type")}
    except LessonError:
        pass
    return {key: value for key, value in meta.items() if value}, text.strip()


def target_scope(scopes: Scopes, wanted: str | None) -> str:
    """Where a new lesson goes: the selected client or initiative unless the firm is named."""
    if wanted == "platform":
        raise LessonError("platform lessons ship with Torque and are not written from a workspace. Save the lesson "
                          "here, then propose it with `torque lesson promote ID --to platform`.")
    if wanted == "firm":
        if scopes.workspace is None:
            raise LessonError("--scope firm needs a workspace: add --workspace PATH.")
        return "firm"
    if wanted not in (None, *record.ENGAGEMENT_SCOPES):
        raise LessonError(f"unknown scope `{wanted}`: use client, initiative or firm.")
    if scopes.engagement is None:
        raise LessonError("no client or initiative is selected, so there is nowhere to keep this lesson. Add "
                          "--client NAME (or --initiative NAME), or save it for the whole firm with --scope firm.")
    return scopes.kind


def parse_evidence(values, day: str) -> list[dict]:
    """`TYPE[:REF]` words to evidence entries."""
    entries = []
    for value in values or ():
        kind, _, ref = str(value).partition(":")
        entries.append(record.evidence_entry(kind.strip(), ref, day))
    return entries


def add(scopes: Scopes, *, text: str = "", title: str = "", when: str = "", do: str = "", because: str = "",
        body: str = "", scope: str | None = None, kind: str = "pitfall", tasks=(), types=(), commands=(),
        keywords=(), evidence=(), source: str = "agent", session: str | None = None) -> Added:
    """Save one lesson. Raises LessonError when there is nothing to save or nowhere to
    keep it, and Refused when the scrub refuses the text; nothing is written then."""
    if kind not in record.KINDS:
        raise LessonError(f"unknown kind `{kind}`: use one of {', '.join(record.KINDS)}.")
    if source not in record.SOURCES:
        raise LessonError(f"unknown source `{source}`: use one of {', '.join(record.SOURCES)}.")
    text, body = (text or "").strip(), (body or "").strip()
    when, do, because = (record.one_line(value) for value in (when, do, because))
    if not (when or do):
        parts = structure(text)
        if parts:
            when, do, because = parts.get("when", ""), parts.get("do", ""), because or parts.get("because", "")
            text = parts.get("lead", "")
    if not (text or body or when or do):
        raise LessonError("nothing to save: give the lesson as text, or with --when, --do and --because.")
    scope = target_scope(scopes, scope)
    day = record.today()
    item = Lesson(id="L-pending", title=record.one_line(title) or record.first_sentence(do or when or text or body),
                  scope=scope, kind=kind, when=when, do=do, because=because, source=source, created=day,
                  review_by=record.later(day), body="\n\n".join(part for part in (text, body) if part))
    whole = "\n".join((item.title, when, do, because, item.body))
    found_tasks, found_types = vocab.infer(whole)
    inferred: dict = {}
    for name, given, found in (("tasks", [vocab.tag(value) for value in tasks or ()], found_tasks),
                               ("types", [vocab.canonical_type(value) for value in types or ()], found_types),
                               ("keywords", [record.one_line(value) for value in keywords or ()],
                                vocab.keywords(whole))):
        item.applies[name] = list(dict.fromkeys(given)) or found
        if not given and found:
            inferred[name] = found
    item.applies["commands"] = [record.one_line(value) for value in commands or () if record.one_line(value)]
    reference = f"session {session}" if session else ""
    item.evidence = parse_evidence(evidence, day) or [record.evidence_entry(
        "consultant-said" if source == "consultant" else "session", reference, day)]
    if record.admitted(source, item.evidence):
        item.state, item.last_verified = "active", day
    found = scrubber.scrub_lesson(item, firm_terms=scrubber.workspace_terms(scopes.workspace) if scope == "firm" else None)
    if found.refused:
        raise Refused(found.refusal(scope), found)
    if not item.title:
        raise LessonError("nothing to save: the text is empty once it is cleaned.")
    notes = []
    if len(item.body.splitlines()) > record.BODY_LINES:
        notes.append(f"The body is {len(item.body.splitlines())} lines; about {record.BODY_LINES} is the aim. "
                     "Recall prints only the when, do and because lines.")
    if not (item.when or item.do):
        notes.append("Saved as free text. A lesson works best as when, do and because: "
                     f"torque lesson edit ID --set when=... --set do=... --set because=...")
    folder = scopes.folder(scope)
    with storage.transaction(scopes.root(scope)):
        existing = storage.read_folder(folder, scope, scopes)
        key = record.content_key(item)
        same = next((other for other in existing if record.content_key(other) == key), None)
        if same is not None:
            return _again(same, item, folder, reference, day, found.masked)
        close = [other for other in existing if record.overlap(other, item) >= SIMILAR]
        if close:
            notes.append("Similar to " + ", ".join(f"{other.id} ({other.title})" for other in close[:3])
                         + ". If it is the same lesson, retire this one and mark the other: "
                         "torque lesson verify ID --evidence second-occurrence")
        item.id = record.new_id(day, storage.taken_ids(folder))
        path = storage.save(item, folder)
    return Added(item, path, "saved", inferred, found.masked, [note.replace(" ID ", f" {item.id} ") for note in notes])


def _again(same: Lesson, new: Lesson, folder: Path, reference: str, day: str, masked: dict) -> Added:
    """The same lesson captured again. On a new occasion (another session, or another
    day) that is a second occurrence: evidence that admits a candidate. Otherwise nothing changes."""
    occasions = {entry.get("ref") or entry.get("date") for entry in same.evidence}
    if same.state == "retired":
        return Added(same, same.path, "duplicate", masked=masked, notes=[
            f"This lesson exists and is retired. If it holds after all: torque lesson restore {same.id}"])
    if (reference or day) in occasions:
        return Added(same, same.path, "duplicate", masked=masked,
                     notes=["This lesson is already saved; nothing was added."])
    same.evidence.append(record.evidence_entry("second-occurrence", reference, day))
    if same.state == "candidate":
        same.state = "active"
    same.last_verified = day
    storage.save(same, folder)
    return Added(same, same.path, "second-occurrence", masked=masked, notes=[
        "This lesson was already saved; this capture is recorded as a second occurrence, which admits it."])


def learned(scopes: Scopes, texts, *, scope: str | None = None, source: str = "agent",
            session: str | None = None) -> tuple[list[dict], list[dict]]:
    """Save what a session taught, one lesson per text. Returns (saved, problems): a lesson
    that cannot be saved is reported and never raised, so the session record stands."""
    saved, problems = [], []
    for number, text in enumerate(texts or (), start=1):
        try:
            saved.append(add(scopes, text=text, scope=scope, source=source, session=session).as_dict())
        except Refused as exc:
            problems.append({"learned": number, "reason": str(exc), "refused": exc.found.refused})
        except LessonError as exc:
            problems.append({"learned": number, "reason": str(exc)})
        except Exception as exc:  # The session is already recorded; report, never lose it.
            problems.append({"learned": number, "reason": f"the lesson could not be written ({type(exc).__name__})"})
    return saved, problems
