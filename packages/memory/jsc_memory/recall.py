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
CANDIDATE_MARK = "candidate, not reviewed: a hint, check it"
HINTS = 2           # matching candidates of the selected client or initiative `torque lesson for` shows


def verify_line(item: Lesson) -> str:
    """How a candidate that held becomes active, with its basis recorded."""
    return f"If it worked: torque lesson verify {item.id} --evidence reproduced"
_SCOPE_ORDER = {"client": 0, "initiative": 0, "firm": 1, "platform": 2}
_CONFIDENCE = {"verified-live": "reproduced", "documented": "official-doc", "practitioner": "session"}
_DOMAIN_TASKS = {"deploy": ("deploy",), "data": ("data-load",), "automation": ("flow-build",),
                 "security": ("access",), "auth": ("sign-in",), "packaging": ("packaging",), "orgs": ("sandbox",)}
# The same operation under two names: Torque's wrapper and the command it runs.
_SAME_COMMAND = (("torque deploy", "sf project deploy start"), ("torque deploy-quick", "sf project deploy quick"),
                 ("torque deploy-resume", "sf project deploy resume"), ("torque deploy-abort", "sf project deploy cancel"),
                 ("torque apex run", "sf apex run"), ("torque package", "sf package"),
                 ("torque data bulk update", "sf data update bulk"), ("torque data bulk upsert", "sf data upsert bulk"),
                 ("torque data bulk delete", "sf data delete bulk"), ("torque data bulk import", "sf data import bulk"),
                 ("torque data", "sf data"), ("torque org", "sf org"))
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
        # The note's confidence is its evidence; its `updated` date is when the catalogue entry
        # was edited, not a verification, so the lesson carries no created or verified date.
        confidence = note.get("confidence") if note.get("confidence") in _CONFIDENCE else "practitioner"
        source = note.get("source") if isinstance(note.get("source"), str) else ""
        body = "\n".join(f"{label}: {record.one_line(note[key])}" for label, key in
                         (("Symptom", "symptom"), ("Exception", "exception"), ("Gotcha", "gotcha"),
                          ("Check", "detect")) if note.get(key))
        lessons.append(Lesson(
            id="A-" + note["id"], title=record.one_line(note["title"]), scope="platform", kind="pitfall",
            state="active", do=record.one_line(note["remedy"]), because=record.one_line(note["cause"]),
            applies={"tasks": tasks, "types": types, "commands": [], "keywords": [], "errors": errors},
            evidence=[{"type": _CONFIDENCE[confidence], "ref": source or f"advisory catalogue ({confidence})"}],
            source="doc" if confidence == "documented" else "harvested", body=body,
            extra={"updated": str(note["updated"]), "confidence": confidence},
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
    topic: list = field(default_factory=list)    # the topic's words in order, lower case, for phrases
    touched: list = field(default_factory=list)  # types given, from paths or named by the command as members
    notes: list = field(default_factory=list)    # what the query could not see (a folder walk that stopped)

    def __bool__(self) -> bool:
        return bool(self.words or self.tasks or self.types or self.command or self.error)

    def label(self) -> str:
        spelled = {}                    # one spelling of each name: `report, Dashboard`, not `..., dashboard`
        for name in [*self.tasks, *self.types, *self.words]:
            spelled.setdefault(name.lower(), name)
        named = ", ".join(spelled.values())
        if self.error:
            return "this error" + (f", {named}" if named and not self.only_error else "")
        return named or ("the command" if self.command else "")

    def as_dict(self) -> dict:
        return {"tasks": self.tasks, "types": self.types, "words": self.words, "command": bool(self.command),
                "error": bool(self.error)}


def _names_under(folder: Path, scopes: Scopes | None, capped: list | None = None):
    """Names of the files and folders under a folder, at most _WALK_LIMIT of them. Links to
    folders are not followed, and the folders of other clients and initiatives are not entered.
    A folder whose walk stops at the limit is added to `capped`."""
    count = 0
    for top, folders, files in os.walk(folder):
        folders[:] = [name for name in folders if not (scopes and scopes.foreign(Path(top, name)))]
        for name in (*folders, *files):
            count += 1
            if count > _WALK_LIMIT:
                if capped is not None:
                    capped.append(folder.name)
                return
            yield name


def path_types(paths, scopes: Scopes | None = None, notes: list | None = None) -> list[str]:
    """Metadata types behind files, folders and package.xml manifests, from names only.
    With `scopes`, a path inside another client's or initiative's folder is not looked into.
    A folder too large to walk whole is named in `notes`."""
    types: list[str] = []
    capped: list[str] = []
    for raw in paths or ():
        types += vocab.types_for_path(str(raw))          # from the name as it was given; nothing is read
        try:
            path = Path(str(raw)).resolve()
            if scopes and scopes.foreign(path):
                continue
            if path.is_dir():
                for name in _names_under(path, scopes, capped):
                    types += vocab.types_for_path(name)
            elif path.is_file() and path.suffix.lower() == ".xml" and not path.name.lower().endswith("-meta.xml"):
                text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
                types += [vocab.TYPES[name.lower()] for name in re.findall(r"<name>\s*(\w+)\s*</name>", text)
                          if name.lower() in vocab.TYPES]
        except OSError:
            continue
    if notes is not None and capped:
        notes.append(f"Read the first {_WALK_LIMIT} names under {', '.join(capped)}; component types further down "
                     "were not seen: name the folder or files of the change (--path).")
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
        # A task Torque knows is asked as itself (sandbox-refresh is not every sandbox lesson);
        # only the types it implies are added (flow-build asks for Flow).
        all_tasks += [] if vocab.tag(task) in vocab.TASKS else more_tasks
        all_types += more_types
    notes: list[str] = []
    given = [vocab.canonical_type(kind) for kind in types or ()] + path_types(paths, scopes, notes)
    command = vocab.without_org_names(" ".join(str(command or "").split()))
    if command:
        more_tasks, more_types = vocab.for_command(command)
        all_tasks += more_tasks
        all_types += more_types
    return Query(words=words, tasks=list(dict.fromkeys(all_tasks + found_tasks)),
                 types=list(dict.fromkeys(all_types + found_types + given)), command=command,
                 error=error, only_error=only_error,
                 topic=re.findall(r"[a-z0-9_]+", " ".join(topic).lower()),
                 touched=list(dict.fromkeys(given + vocab.command_members(command))), notes=notes)


def _command_forms(command: str) -> list[str]:
    forms = [command.lower()]
    for torque, native in _SAME_COMMAND:
        for one, other in ((torque, native), (native, torque)):
            if forms[0].startswith(one + " ") or forms[0] == one:
                forms.append(other + forms[0][len(one):])
    return forms


def _command_hits(item: Lesson, command: str) -> tuple[int, bool]:
    """(how many of a lesson's command patterns fit, whether one that fits names more than
    the command's name: an option or a value, as `*destructive*` or `* --manifest*` do)."""
    forms = _command_forms(command)
    hits = sum(1 for pattern in item.command_patterns if any(pattern.search(form) for form in forms))
    specific = False
    for glob in item.applies.get("commands", []):
        glob = " ".join(glob.lower().split())
        shapes = (glob, glob + " *", "* " + glob, "* " + glob + " *")
        fits = [form for form in forms if any(fnmatchcase(form, shape) for shape in shapes)]
        hits += bool(fits)
        for form in fits:
            name = set(re.split(r"\s+-", " " + form, maxsplit=1)[0].split())     # the words before the first option
            specific |= bool(set(re.findall(r"[^\s*]+", glob)) - name)
    return hits, specific


def _specific_trigger(item: Lesson, command: str) -> bool:
    """A catalogue note's trigger matches the command beyond its name (into an option or a
    value: `--hard-delete`, `project deploy.*manifest`), not only `project deploy`."""
    for form in _command_forms(command):
        name = len(re.split(r"\s+-", form, maxsplit=1)[0])
        if any(found.end() > name or found.start() >= name
               for pattern in item.command_patterns for found in pattern.finditer(form)):
            return True
    return False


def _before_step(item: Lesson, query: "Query") -> bool:
    """Whether a lesson earns a line before a step that has not failed (`for_command`): its
    command pattern names an option or value of this command, or (unless it answers an
    error) it is about a component type the step touches."""
    if query.command and (_command_hits(item, query.command)[1] or _specific_trigger(item, query.command)):
        return True
    touched = {kind.lower() for kind in query.touched}
    return not item.applies.get("errors") and bool({kind.lower() for kind in item.applies.get("types", [])} & touched)


def _phrases(item: Lesson, topic: list[str]) -> list[str]:
    """The lesson's keywords of two or more words that the topic spells out, word for word
    (a plural or singular ending may differ): `custom settings`, `screen action`."""
    found = []
    for keyword in item.applies.get("keywords", []):
        words = re.findall(r"[a-z0-9_]+", keyword.lower())
        if len(words) > 1 and any(all(vocab.word_forms(word) & {topic[start + index]} for index, word in
                                      enumerate(words)) for start in range(len(topic) - len(words) + 1)):
            found.append(keyword)
    return found


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
#   phrase   a keyword of two or more words is spelled out in the topic   4 each, at most 8
#   command  a command pattern of the lesson fits the command        2, +1 a pattern, at most 5;
#            +3 when a pattern that fits names an option or value beyond the command's name
#   word     a topic word is a whole word of the lesson's title, `when`, keywords, tasks or
#            types (a plural or singular ending may differ)          1 each, at most 3
# A topic word the vocabulary knows (flow, dashboard, deploy) is asked as its task or type,
# not as a word (vocab.topic_terms); an org alias in a command (`--target-org dev-sandbox`)
# is a name, not a topic. A lesson about other component types than the ones asked for (both
# name types, none shared, no phrase spelled out) does not match on its task, command or words.
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
    phrases = _phrases(item, query.topic) if query.topic else []
    if phrases:
        score += min(8, 4 * len(phrases))
        reasons += [f"keyword {phrase}" for phrase in phrases]
    other_components = bool(types and query.types and not hits and not phrases)
    tasks = {vocab.tag(task) for task in item.applies.get("tasks", [])}
    hits = [] if other_components else [task for task in query.tasks if task in tasks]
    if hits:
        score += min(6, 3 * len(hits))
        reasons += [f"task {task}" for task in hits]
    if query.command and not other_components:
        number, specific = _command_hits(item, query.command)
        if number:
            score += min(5, 2 + number) + 3 * specific
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
    hints: list = field(default_factory=list)   # the engagement's own matching candidates shown as hints


def eligible(item: Lesson, tally: Tally | None) -> bool:
    """Shown at the point of use: active with a basis in its record (a file edited by hand
    to `state: active` is not trusted on that alone: lesson.unbacked_active), not marked
    stale, and not already promoted into a rule, a recipe or a wider lesson (it lives on
    there)."""
    return (item.state == "active" and not record.unbacked_active(item)
            and not (tally and tally.stale_mark) and not item.promoted_to)


def _recency(item: Lesson) -> int:
    """The day a lesson was verified or saved; for a catalogue note, the day it was updated."""
    try:
        return date.fromisoformat(item.last_verified or item.created or updated(item)).toordinal()
    except ValueError:
        return 0


def updated(item: Lesson) -> str:
    """The update date of a catalogue note, or "" for a lesson file."""
    return str(item.extra.get("updated") or "") if item.origin == "advisory" else ""


def basis(item: Lesson) -> list[str]:
    """What a lesson stands on, as printed: its evidence and age, or for a catalogue note
    its confidence and the day the note was updated (never a verification it did not have)."""
    if item.origin == "advisory":
        return ["catalogue note", str(item.extra.get("confidence")), f"updated {updated(item) or 'not dated'}"]
    return [item.evidence_type, record.age_text(item)]


# Order: match strength with a modest bonus for nearness (the selected client's or
# initiative's own lessons +2, the firm's +1), so a weak client match never pushes an exact
# error or type match below the cut; then helpful minus stale marks; then how well a lesson
# is founded; then recency in coarse steps (verified or saved within 90 days, within a year,
# older); then id: a few days' difference never decides the order. When a lesson's own
# error matcher meets the error, the best such lesson is always within the cut (`find`).
_SCOPE_BONUS = {"client": 2, "initiative": 2, "firm": 1, "platform": 0}
_EVIDENCE_RANK = {"reproduced": 0, "official-doc": 0, "consultant-said": 1, "second-occurrence": 1, "session": 2}


def _recency_step(item: Lesson) -> int:
    day = _recency(item)
    if not day:
        return 2
    age = date.today().toordinal() - day
    return 0 if age <= 90 else 1 if age <= 365 else 2


def _rank(hit: "Hit") -> tuple:
    return (-(hit.score + _SCOPE_BONUS[hit.lesson.scope]), -hit.tally.score,
            _EVIDENCE_RANK.get(hit.lesson.evidence_type, 3), _recency_step(hit.lesson), _SCOPE_ORDER[hit.lesson.scope],
            hit.lesson.id)


def _exact_error(hit: "Hit") -> bool:
    """The lesson's own error matcher met the error (not a catalogue note's wording)."""
    return "error" in hit.reasons and bool(hit.lesson.applies.get("errors"))


def _cut(hits: list, limit: int | None) -> list:
    """The first `limit` hits; if an exact error match exists and none is among them, the
    best one takes the last place."""
    if not limit or len(hits) <= limit:
        return hits
    shown = hits[:limit]
    if not any(_exact_error(hit) for hit in shown):
        exact = next((hit for hit in hits[limit:] if _exact_error(hit)), None)
        if exact:
            shown[-1] = exact
    return shown


def find(scopes: Scopes, *, topic=(), tasks=(), types=(), command: str = "", paths=(), error: str = "",
         limit: int | None = 5, include_candidates: bool = False, advisory: bool = True, hints: int = 0) -> Result:
    """The lessons that match, best first. With selectors a lesson must match at least
    one of them; with none, the lessons that matter most are returned. `error` is the text
    of an error just met. `advisory=False` leaves the catalogue's notes out.

    `hints` (only `torque lesson for` asks for them): up to that many matching candidates
    of the selected client or initiative, kept apart from the lessons in `Result.hints`. A
    candidate is never among the lessons, in the context block or before a deploy; a firm
    candidate is never a hint."""
    problems: list = []
    query = build_query(topic, tasks, types, command, paths, error, scopes)
    tallies = storage.tally(storage.events_for(scopes))
    hits, waiting = [], []
    for item in everything(scopes, problems, advisory=advisory):
        tally = tallies.get(item.id) or Tally()
        score, reasons = match(item, query) if query else (0, [])
        if query and not score:
            continue
        if (item.state == "candidate" or record.unbacked_active(item)) and not tally.stale_mark:
            if not include_candidates:
                waiting.append(Hit(item, score, reasons, tally))
                continue
        elif not eligible(item, tally):
            continue
        hits.append(Hit(item, score, reasons, tally))
    hits.sort(key=_rank)
    own = sorted((hit for hit in waiting if hit.lesson.scope in record.ENGAGEMENT_SCOPES
                  and not hit.lesson.promoted_to), key=_rank)[:hints if query else 0]
    return Result(_cut(hits, limit), len(hits), query, scopes, problems, len(waiting) - len(own), own)


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
            "origin": item.origin, "path": str(item.path) if item.path else None,
            **({"updated": updated(item), "confidence": item.extra.get("confidence")} if item.origin == "advisory"
               else {})}


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
    standing = [item.scope,
                *([CANDIDATE_MARK] if item.state == "candidate" or record.unbacked_active(item) else []),
                *(["retired"] if item.state == "retired" else []), *basis(item),
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
    if result.hints:
        if not result.hits:
            text = [f"No reviewed lesson matches {what} ({scope})."]
        text.append(f"Not reviewed yet, from this {result.scopes.kind}'s own work ({len(result.hints)}):")
        for hit in result.hints:
            text += lines(hit.lesson, hit.tally) + [f"  {verify_line(hit.lesson)}"]
    if result.hits or result.hints:
        text.append(FOOTER)
    if result.candidates:
        text.append(f"{result.candidates} matching candidate(s) wait for review and are not shown: "
                    "torque lesson list --state candidate")
    return "\n".join(text + result.query.notes + problem_lines(result.problems))


def as_json(result: Result) -> dict:
    return {"query": result.query.as_dict(), "scopes": result.scopes.label(), "matched": result.matched,
            "lessons": [{**summary(hit.lesson, hit.tally), "match": {"score": hit.score, "reasons": hit.reasons}}
                        for hit in result.hits],
            "candidate_hints": [{**summary(hit.lesson, hit.tally), "match": {"score": hit.score, "reasons": hit.reasons},
                                 "mark": CANDIDATE_MARK, "next": verify_line(hit.lesson)} for hit in result.hints],
            "candidates_not_shown": result.candidates, "unreadable": result.problems, "notes": result.query.notes,
            "note": FOOTER}


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

    Before a step that has not failed (no `error`), only a lesson about a component type the
    step touches, or one whose command pattern names an option or value of this command, earns
    a line; a lesson that answers an error (it has `applies.errors`) waits for that error. A
    task or a broad pattern such as "any deploy" alone is not enough: nothing is printed then.

    It never raises, never blocks and never fails the caller's command."""
    try:
        scopes = scopes or storage.scopes_from_env()
        result = find(scopes, tasks=tasks, types=types, command=command_text, paths=paths, error=error,
                      limit=None)
        if not error:
            result.hits = [hit for hit in result.hits if _before_step(hit.lesson, result.query)]
            result.matched = len(result.hits)
        result.hits = _cut(result.hits, limit)
        if not result.hits:
            return ""
        mark_shown(scopes, [hit.lesson for hit in result.hits], via)
        result.candidates, result.problems = 0, []
        return render(result)
    except Exception:  # A lesson must never be the reason a deploy or a data change fails.
        return ""


FIRM_IN_CONTEXT = 2     # places kept for the firm's lessons in the context block when the firm has some


def context_section(scopes: Scopes, limit: int = 5, *, via: str | None = None) -> dict:
    """The lessons block of `torque context` and `torque handoff`: the engagement's own
    active lessons that matter most, then the firm's (a consultant's standing corrections
    usually hold for every client), and how many more there are to ask for.

    At most `limit` lessons in all (five: about fifteen lines, read at the start of every
    session; more is skimmed). The firm keeps up to FIRM_IN_CONTEXT places when it has
    lessons and takes any the engagement leaves free; the firm's come consultant-sourced
    first. With `via`, the lessons listed are recorded as shown."""
    problems: list = []
    tallies = storage.tally(storage.events_for(scopes))
    lessons = everything(scopes, problems)
    live = [item for item in lessons if eligible(item, tallies.get(item.id))]
    own = [Hit(item, 0, [], tallies.get(item.id) or Tally()) for item in live
           if item.scope in record.ENGAGEMENT_SCOPES]
    own.sort(key=lambda hit: (-hit.tally.score, -_recency(hit.lesson), hit.lesson.id))
    firm = [Hit(item, 0, [], tallies.get(item.id) or Tally()) for item in live if item.scope == "firm"]
    firm.sort(key=lambda hit: (hit.lesson.source != "consultant", -hit.tally.score,
                               _EVIDENCE_RANK.get(hit.lesson.evidence_type, 3), -_recency(hit.lesson), hit.lesson.id))
    mine = own[:limit - min(FIRM_IN_CONTEXT, len(firm))]
    listed = mine + firm[:limit - len(mine)]
    if via:
        mark_shown(scopes, [hit.lesson for hit in listed], via)
    return {"shown": [summary(hit.lesson, hit.tally) for hit in listed],
            "lines": [line for hit in listed for line in lines(hit.lesson, hit.tally)],
            "engagement_shown": len(mine), "firm_shown": len(listed) - len(mine),
            "engagement_active": len(own),
            "firm_active": sum(item.scope == "firm" for item in live),
            "platform_active": sum(item.scope == "platform" for item in live),
            "candidates_waiting": sum(item.state == "candidate" or record.unbacked_active(item)
                                      for item in lessons),
            "marked_stale": sum(bool(tallies.get(item.id) and tallies[item.id].stale_mark) for item in lessons),
            "unreadable": problems, "note": FOOTER}
