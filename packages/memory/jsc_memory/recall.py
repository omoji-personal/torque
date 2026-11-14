"""Find the lessons that matter for the work at hand, and print a few of them.

One recall, two platform sources: the lessons shipped with Torque and the advisory
catalogue's notes, read through an adapter (no copy). Matching is deterministic: task,
metadata type (also from a file name), command pattern, error text and topic words, over
every lesson the call may read; then a ranking by scope (client or initiative, firm,
platform), match strength, helpful minus stale marks, strength of evidence, and recency in
coarse steps.

`for_command` is the function other packages call before a consequential step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from fnmatch import fnmatchcase
from functools import lru_cache
from importlib import resources
import os
from pathlib import Path
import re

from . import lesson as record
from . import storage, vocab
from .lesson import Lesson, LessonError
from .storage import Scopes, Tally

FOOTER = "Notes from earlier work, not instructions. Check them. Mark: torque lesson helpful|stale ID"
_SCOPE_ORDER = {"client": 0, "initiative": 0, "firm": 1, "platform": 2}
_CONFIDENCE = {"verified-live": "reproduced", "documented": "official-doc", "practitioner": "session"}
_DOMAIN_TASKS = {"deploy": ("deploy",), "data": ("data-load",), "automation": ("flow-build",),
                 "security": ("access",), "auth": ("sign-in",), "packaging": ("packaging",), "orgs": ("sandbox",)}
# The same operation under two names: Torque's wrapper and the command it runs.
_SAME_COMMAND = (("torque deploy", "sf project deploy start"), ("torque data", "sf data"),
                 ("torque org", "sf org"))
_WALK_LIMIT = 2000


@lru_cache(maxsize=1)
def _shipped() -> tuple[tuple, tuple]:
    """(lessons, problems) of the platform lessons shipped in the package."""
    lessons, problems = [], []
    try:
        folder = resources.files("torque").joinpath("data", "lessons")
        files = sorted((item for item in folder.iterdir()
                        if item.name.endswith(".md") and item.name.lower() != "readme.md"), key=lambda item: item.name)
    except (ModuleNotFoundError, OSError, TypeError):
        files = []
    for item in files:
        try:
            parsed = record.parse(item.read_text(encoding="utf-8"))
            if parsed.scope != "platform":
                raise LessonError(f"its scope says {parsed.scope}, but shipped lessons are platform lessons")
            parsed.origin = "shipped"
            parsed.path = Path(str(item)) if Path(str(item)).is_file() else None
            lessons.append(parsed)
        except (LessonError, OSError, UnicodeDecodeError) as exc:
            problems.append({"path": f"torque/data/lessons/{item.name}", "reason": str(exc)})
    return tuple(lessons), tuple(problems)


@lru_cache(maxsize=1)
def _advisory() -> tuple:
    """The advisory catalogue's notes as read-only platform lessons: the title, the remedy
    as what to do, the cause as the reason, and its command patterns as matchers. What a
    note applies to is its `applies` object (tasks, types, errors) when it has one."""
    try:
        from jsc_advisory.catalogue import entries
        notes = entries()
    except Exception:  # The catalogue is optional reading; recall works without it.
        return ()
    lessons = []
    for note in notes:
        patterns = []
        for trigger in note.get("triggers") or []:
            try:
                patterns.append(re.compile(trigger, re.I))
            except re.error:
                continue
        given = _applies(note)
        if given is not None:            # the note says what it applies to
            tasks, types, errors = given
        else:                            # inferred: the triggers name the types, the domain and title the task
            triggers = " ".join(note.get("triggers") or [])
            tasks = list(dict.fromkeys([*_DOMAIN_TASKS.get(note.get("domain"), ()), *vocab.infer(note["title"])[0]]))
            types, errors = vocab.infer(triggers)[1], []
        kind = _CONFIDENCE.get(note.get("confidence"), "session")
        source = note.get("source") if isinstance(note.get("source"), str) else ""
        body = "\n".join(f"{label}: {record.one_line(note[key])}" for label, key in
                         (("Symptom", "symptom"), ("Exception", "exception"), ("Gotcha", "gotcha"),
                          ("Check", "detect")) if note.get(key))
        lessons.append(Lesson(
            id="A-" + note["id"], title=record.one_line(note["title"]), scope="platform", kind="pitfall",
            state="active", do=record.one_line(note["remedy"]), because=record.one_line(note["cause"]),
            applies={"tasks": tasks, "types": types, "commands": [], "keywords": [], "errors": errors},
            evidence=[{"type": kind, "ref": source or f"advisory catalogue ({note.get('confidence')})",
                       "date": str(note["updated"])}],
            source="doc", created=str(note["updated"]), last_verified=str(note["updated"]), body=body,
            origin="advisory", command_patterns=tuple(patterns),
            # Given error matchers replace the comparison with the note's own wording.
            error_text="" if errors else record.one_line(f"{note['title']} {note['symptom']}")))
    return tuple(lessons)


def _applies(note: dict) -> tuple[list, list, list] | None:
    """(tasks, types, errors) a catalogue note gives in its `applies` object, or None when
    it gives none (or not as lists of words), so that they are inferred as before."""
    given = note.get("applies")
    if not isinstance(given, dict):
        return None
    values = {}
    for key in ("tasks", "types", "errors"):
        value = given.get(key) or []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return None
        values[key] = [record.one_line(item) for item in value if record.one_line(item)]
    return ([vocab.tag(task) for task in values["tasks"]], [vocab.canonical_type(kind) for kind in values["types"]],
            values["errors"])


def platform_lessons(problems: list | None = None, *, advisory: bool = True) -> list[Lesson]:
    shipped, unreadable = _shipped()
    if problems is not None:
        problems += unreadable
    return [*shipped, *(_advisory() if advisory else ())]


def everything(scopes: Scopes, problems: list | None = None, *, advisory: bool = True) -> list[Lesson]:
    """Every lesson this call may read: the selected engagement's, the firm's, the platform's."""
    return storage.load(scopes, problems) + platform_lessons(problems, advisory=advisory)


@dataclass
class Query:
    words: list = field(default_factory=list)
    tasks: list = field(default_factory=list)
    types: list = field(default_factory=list)
    command: str = ""
    error: str = ""                 # an error text, as given
    only_error: bool = False        # the error was the only thing asked about

    def __bool__(self) -> bool:
        return bool(self.words or self.tasks or self.types or self.command or self.error)

    def label(self) -> str:
        named = ", ".join(dict.fromkeys([*self.tasks, *self.types, *self.words]))
        if self.error:
            return "this error" + (f", {named}" if named and not self.only_error else "")
        return named or ("the command" if self.command else "")

    def as_dict(self) -> dict:
        return {"tasks": self.tasks, "types": self.types, "words": self.words, "command": bool(self.command),
                "error": bool(self.error)}


def _names_under(folder: Path, scopes: Scopes | None):
    """Names of the files and folders under a folder, at most _WALK_LIMIT of them. Links to
    folders are not followed, and the folders of other clients and initiatives are not entered."""
    count = 0
    for top, folders, files in os.walk(folder):
        folders[:] = [name for name in folders if not (scopes and scopes.foreign(Path(top, name)))]
        for name in (*folders, *files):
            count += 1
            if count > _WALK_LIMIT:
                return
            yield name


def path_types(paths, scopes: Scopes | None = None) -> list[str]:
    """Metadata types behind files, folders and package.xml manifests, from names only.
    With `scopes`, a path inside another client's or initiative's folder is not looked into."""
    types: list[str] = []
    for raw in paths or ():
        types += vocab.types_for_path(str(raw))          # from the name as it was given; nothing is read
        try:
            path = Path(str(raw)).resolve()
            if scopes and scopes.foreign(path):
                continue
            if path.is_dir():
                for name in _names_under(path, scopes):
                    types += vocab.types_for_path(name)
            elif path.is_file() and path.suffix.lower() == ".xml" and not path.name.lower().endswith("-meta.xml"):
                text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
                types += [vocab.TYPES[name.lower()] for name in re.findall(r"<name>\s*(\w+)\s*</name>", text)
                          if name.lower() in vocab.TYPES]
        except OSError:
            continue
    return list(dict.fromkeys(types))


def build_query(topic=(), tasks=(), types=(), command: str = "", paths=(), error: str = "",
                scopes: Scopes | None = None) -> Query:
    topic = [str(word) for word in topic or ()]
    error = record.one_line(error)
    only_error = bool(error) and not (topic or tasks or types or command or paths)
    found_tasks, found_types, words = vocab.topic_terms(topic)
    all_tasks = [vocab.tag(task) for task in tasks or ()]
    all_types = [vocab.canonical_type(kind) for kind in types or ()]
    for task in tasks or ():
        more_tasks, more_types = vocab.infer(str(task).replace("-", " "))
        all_tasks += more_tasks
        all_types += more_types
    command = " ".join(str(command or "").split())
    if command:
        more_tasks, more_types = vocab.for_command(command)
        all_tasks += more_tasks
        all_types += more_types
    return Query(words=words, tasks=list(dict.fromkeys(all_tasks + found_tasks)),
                 types=list(dict.fromkeys(all_types + found_types + path_types(paths, scopes))), command=command,
                 error=error, only_error=only_error)


def _command_forms(command: str) -> list[str]:
    forms = [command.lower()]
    for torque, native in _SAME_COMMAND:
        for one, other in ((torque, native), (native, torque)):
            if forms[0].startswith(one + " ") or forms[0] == one:
                forms.append(other + forms[0][len(one):])
    return forms


def _command_hits(item: Lesson, command: str) -> int:
    forms = _command_forms(command)
    hits = sum(1 for pattern in item.command_patterns if any(pattern.search(form) for form in forms))
    for glob in item.applies.get("commands", []):
        glob = " ".join(glob.lower().split())
        shapes = (glob, glob + " *", "* " + glob, "* " + glob + " *")
        hits += any(fnmatchcase(form, shape) for form in forms for shape in shapes)
    return hits


def _about_words(item: Lesson) -> tuple[set[str], set[str]]:
    """The words that say what a lesson is about: (title and keywords, `when`, tasks and
    types). `do`, `because` and the body explain it and are not matched."""
    named = record.words(" ".join([item.title, *item.applies.get("keywords", [])]))
    return named, record.words(" ".join([item.when, *item.applies.get("tasks", []),
                                         *item.applies.get("types", [])]))


def _error_hits(item: Lesson, error: str) -> int:
    """How many of a lesson's error matchers the error text meets. A catalogue note has
    no matchers: it answers when its title or symptom names the same error code or
    shares the error's wording."""
    hits = sum(vocab.error_matches(matcher, vocab.normal_error(error)) for matcher in item.applies.get("errors", []))
    return hits or int(bool(item.error_text) and vocab.shares_wording(error, item.error_text))


# How a lesson matches a query. Strength 0 means it does not match; precision comes first.
#   error    an error matcher of the lesson is in the error text     5, +1 a matcher, at most 8
#   type     the lesson names a metadata type the query names        4 each, at most 8
#   task     the lesson names a task the query names                 3 each, at most 6
#   command  a command pattern of the lesson fits the command        2, +1 a pattern, at most 5
#   word     a topic word is a whole word of the lesson's title, `when`, keywords, tasks or
#            types (a plural or singular ending may differ)          1 each, at most 3
# A topic word the vocabulary knows (flow, dashboard, deploy) is asked as its task or type,
# not as a word (vocab.topic_terms). A lesson about other component types than the ones
# asked for (both name types, none shared) does not match on its task, command or words.
# A lesson found by words alone needs a hit in its title or keywords, or two different words.
def match(item: Lesson, query: Query) -> tuple[int, list[str]]:
    """(strength, reasons) of a lesson for a query; strength 0 means it does not match."""
    score, reasons = 0, []
    if query.error:
        number = _error_hits(item, query.error)
        if number:
            score += min(8, 5 + number)
            reasons.append("error")
    types = {kind.lower() for kind in item.applies.get("types", [])}
    hits = [kind for kind in query.types if kind.lower() in types]
    if hits:
        score += min(8, 4 * len(hits))
        reasons += [f"type {kind}" for kind in hits]
    other_components = bool(types and query.types and not hits)
    tasks = {vocab.tag(task) for task in item.applies.get("tasks", [])}
    hits = [] if other_components else [task for task in query.tasks if task in tasks]
    if hits:
        score += min(6, 3 * len(hits))
        reasons += [f"task {task}" for task in hits]
    if query.command and not other_components:
        number = _command_hits(item, query.command)
        if number:
            score += min(5, 2 + number)
            reasons.append("command")
    if query.words and not other_components:
        named, described = _about_words(item)
        hits = [word for word in query.words if vocab.word_forms(word) & (named | described)]
        if hits and (score or len(hits) > 1 or any(vocab.word_forms(word) & named for word in hits)):
            score += min(3, len(hits))
            reasons += [f"word {word}" for word in hits]
    return score, reasons


@dataclass
class Hit:
    lesson: Lesson
    score: int
    reasons: list
    tally: Tally


@dataclass
class Result:
    hits: list
    matched: int
    query: Query
    scopes: Scopes
    problems: list
    candidates: int = 0      # candidates that would have matched but are not shown before review


def eligible(item: Lesson, tally: Tally | None) -> bool:
    """Shown at the point of use: active, not marked stale, and not already promoted
    into a rule, a recipe or a wider lesson (it lives on there)."""
    return item.state == "active" and not (tally and tally.stale_mark) and not item.promoted_to


def _recency(item: Lesson) -> int:
    try:
        return date.fromisoformat(item.last_verified or item.created).toordinal()
    except ValueError:
        return 0


# Ties of scope, strength and marks are decided by how well a lesson is founded, then by
# how recent it is in coarse steps (verified or saved within 90 days, within a year,
# older), then by id: a few days' difference never decides the order.
_EVIDENCE_RANK = {"reproduced": 0, "official-doc": 0, "consultant-said": 1, "second-occurrence": 1, "session": 2}


def _recency_step(item: Lesson) -> int:
    day = _recency(item)
    if not day:
        return 2
    age = date.today().toordinal() - day
    return 0 if age <= 90 else 1 if age <= 365 else 2


def _rank(hit: "Hit") -> tuple:
    return (_SCOPE_ORDER[hit.lesson.scope], -hit.score, -hit.tally.score,
            _EVIDENCE_RANK.get(hit.lesson.evidence_type, 3), _recency_step(hit.lesson), hit.lesson.id)


def find(scopes: Scopes, *, topic=(), tasks=(), types=(), command: str = "", paths=(), error: str = "",
         limit: int | None = 5, include_candidates: bool = False, advisory: bool = True) -> Result:
    """The lessons that match, best first. With selectors a lesson must match at least
    one of them; with none, the lessons that matter most are returned. `error` is the text
    of an error just met. `advisory=False` leaves the catalogue's notes out."""
    problems: list = []
    query = build_query(topic, tasks, types, command, paths, error, scopes)
    tallies = storage.tally(storage.events_for(scopes))
    hits, candidates = [], 0
    for item in everything(scopes, problems, advisory=advisory):
        tally = tallies.get(item.id) or Tally()
        score, reasons = match(item, query) if query else (0, [])
        if query and not score:
            continue
        if item.state == "candidate" and not tally.stale_mark:
            if not include_candidates:
                candidates += 1
                continue
        elif not eligible(item, tally):
            continue
        hits.append(Hit(item, score, reasons, tally))
    hits.sort(key=_rank)
    return Result(hits[:limit] if limit else hits, len(hits), query, scopes, problems, candidates)


def _clip(text: str, limit: int) -> str:
    text = record.one_line(text)
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0] + "..."


def summary(item: Lesson, tally: Tally | None = None) -> dict:
    """A lesson for JSON output: what it says, where it comes from, how it has done."""
    tally = tally or Tally()
    return {"id": item.id, "title": item.title, "scope": item.scope, "kind": item.kind, "state": item.state,
            "when": item.when, "do": item.do, "because": item.because, "applies": item.applies,
            "evidence_type": item.evidence_type, "source": item.source, "created": item.created,
            "last_verified": item.last_verified, "review_by": item.review_by, "age_days": record.age_days(item),
            "counters": tally.counters(), "stale": tally.stale_mark, "promoted_to": item.promoted_to,
            "origin": item.origin, "path": str(item.path) if item.path else None}


def _sentence(label: str, text: str, limit: int) -> str:
    text = _clip(text, limit)
    if not text:
        return ""
    return f"{label} {text}" + ("" if text.endswith((".", "!", "?")) else ".")


def lines(item: Lesson, tally: Tally | None = None) -> list[str]:
    """A lesson in at most three short lines: what, then what to do and why, then its standing."""
    tally = tally or Tally()
    if item.when or item.do:
        first = f"[{item.id}] WHEN {_clip(item.when, 170)}" if item.when else f"[{item.id}] {_clip(item.title, 170)}"
        second = " ".join(part for part in (_sentence("DO", item.do, 220), _sentence("WHY", item.because, 170))
                          if part)
    else:
        first, second = f"[{item.id}] {_clip(item.title, 170)}", _clip(item.body, 300)
    standing = [item.scope, *(["candidate, not reviewed"] if item.state == "candidate" else []),
                *(["retired"] if item.state == "retired" else []), item.evidence_type, record.age_text(item),
                *(["marked stale"] if tally.stale_mark else [])]
    counted = ", ".join(f"{name} {count}" for name, count in tally.counters().items() if count)
    return [first, *(["  " + second] if second else []),
            "  (" + ", ".join(standing) + (f"; {counted}" if counted else "") + ")"]


def problem_lines(problems: list, limit: int = 3) -> list[str]:
    if not problems:
        return []
    named = "; ".join(f"{problem['path']} ({problem['reason']})" for problem in problems[:limit])
    more = f"; and {len(problems) - limit} more (torque doctor names them)" if len(problems) > limit else ""
    return [f"Skipped {len(problems)} lesson file(s) that could not be read: {named}{more}. Fix or remove them."]


def render(result: Result) -> str:
    what = result.query.label()
    scope = result.scopes.label()
    if not result.hits and result.query.error:
        text = [f"No lesson matches {what} ({scope}). When you have fixed it, save what worked: "
                'torque session add ... --learned "When: <the error>. Do: <what fixed it>. Because: <why>"']
    elif not result.hits and result.query:
        text = [f"No lesson matches {what} ({scope}). If this work teaches something, save it: "
                'torque session add ... --learned "When: ... Do: ... Because: ..."']
    elif not result.hits:
        text = [f"No lessons yet ({scope})."]
    else:
        text = [f"Lessons{' for ' + what if what else ''} ({scope}): {len(result.hits)} of {result.matched}"]
        for hit in result.hits:
            text += lines(hit.lesson, hit.tally)
        text.append(FOOTER)
    if result.candidates:
        text.append(f"{result.candidates} matching candidate(s) wait for review and are not shown: "
                    "torque lesson list --state candidate")
    return "\n".join(text + problem_lines(result.problems))


def as_json(result: Result) -> dict:
    return {"query": result.query.as_dict(), "scopes": result.scopes.label(), "matched": result.matched,
            "lessons": [{**summary(hit.lesson, hit.tally), "match": {"score": hit.score, "reasons": hit.reasons}}
                        for hit in result.hits],
            "candidates_not_shown": result.candidates, "unreadable": result.problems, "note": FOOTER}


def mark_shown(scopes: Scopes, lessons: list[Lesson], via: str, session: str | None = None) -> None:
    """Record that these lessons were put in front of someone. Never raises."""
    rows: dict = {}
    for item in lessons:
        root = scopes.event_root(item.scope)
        if root is not None:
            rows.setdefault(root, []).append({"event": "shown", "id": item.id, "via": via,
                                              **({"session": session} if session else {})})
    for root, events in rows.items():
        storage.log(root, events)


def for_command(command_text: str, *, types=(), tasks=(), paths=(), error: str = "", limit: int = 3,
                scopes: Scopes | None = None, via: str = "command") -> str:
    """Notes from earlier work that match a command about to run: the text to print
    before the consequential step, or "" when nothing matches.

    command_text  the command as the user would read it (`torque deploy --metadata Flow:X`,
                  `sf project deploy start ...`, `torque browser login`); used for matching only
    types, tasks  metadata types (Flow, CustomField) and task words (deploy, sign-in) the caller knows
    paths         source files, folders or package.xml manifests the step touches; only names are read
    error         the text of an error the command just gave, to find what fixed it before
    scopes        default: the client or initiative the launcher selected (TORQUE_WORKSPACE)
    via           a short name of the calling surface, stored with the `shown` events

    It never raises, never blocks and never fails the caller's command."""
    try:
        scopes = scopes or storage.scopes_from_env()
        result = find(scopes, tasks=tasks, types=types, command=command_text, paths=paths, error=error,
                      limit=limit)
        if not result.hits:
            return ""
        mark_shown(scopes, [hit.lesson for hit in result.hits], via)
        result.candidates, result.problems = 0, []
        return render(result)
    except Exception:  # A lesson must never be the reason a deploy or a data change fails.
        return ""


def context_section(scopes: Scopes, limit: int = 5, *, via: str | None = None) -> dict:
    """The lessons block of `torque context` and `torque handoff`: the engagement's
    active lessons that matter most, and how many more there are to ask for. With `via`,
    the lessons listed are recorded as shown."""
    problems: list = []
    tallies = storage.tally(storage.events_for(scopes))
    lessons = everything(scopes, problems)
    live = [item for item in lessons if eligible(item, tallies.get(item.id))]
    own = [Hit(item, 0, [], tallies.get(item.id) or Tally()) for item in live
           if item.scope in record.ENGAGEMENT_SCOPES]
    own.sort(key=lambda hit: (-hit.tally.score, -_recency(hit.lesson), hit.lesson.id))
    if via:
        mark_shown(scopes, [hit.lesson for hit in own[:limit]], via)
    return {"shown": [summary(hit.lesson, hit.tally) for hit in own[:limit]],
            "lines": [line for hit in own[:limit] for line in lines(hit.lesson, hit.tally)],
            "engagement_active": len(own),
            "firm_active": sum(item.scope == "firm" for item in live),
            "platform_active": sum(item.scope == "platform" for item in live),
            "candidates_waiting": sum(item.state == "candidate" for item in lessons),
            "marked_stale": sum(bool(tallies.get(item.id) and tallies[item.id].stale_mark) for item in lessons),
            "unreadable": problems, "note": FOOTER}
