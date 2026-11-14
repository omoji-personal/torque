"""One lesson, one Markdown file: a small YAML front matter and a short body.

A person and an agent can both open and edit the file. Counters (shown, helpful, stale)
are not stored in it; they are derived from the events log (`storage.tally`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import secrets

import yaml

SCOPES = ("client", "initiative", "firm", "platform")
ENGAGEMENT_SCOPES = ("client", "initiative")
KINDS = ("pitfall", "works", "convention", "fact")
STATES = ("candidate", "active", "retired")
SOURCES = ("consultant", "agent", "doc", "harvested")
EVIDENCE_TYPES = ("reproduced", "official-doc", "second-occurrence", "session", "consultant-said")
# Evidence that admits a lesson to `active` when it is written. A lesson from the
# consultant (their correction or instruction) is admitted whatever its evidence.
ADMITTING = frozenset({"reproduced", "official-doc", "second-occurrence"})
# What a review (`torque lesson verify`) may give a candidate as its basis.
REVIEW_ADMITTING = ADMITTING | {"consultant-said"}
# What a lesson applies to: task words, metadata types, command patterns, keywords, and
# error matchers (a piece of the error text; `*` stands for any run of characters).
APPLIES = ("tasks", "types", "commands", "keywords", "errors")
REVIEW_DAYS = 90
BODY_LINES = 15
ID = re.compile(r"[A-Z]-[A-Za-z0-9][A-Za-z0-9._-]{3,79}\Z")
_KNOWN = ("id", "title", "scope", "kind", "state", "when", "do", "because", "applies", "evidence", "source",
          "created", "last_verified", "review_by", "supersedes", "promoted_to")


class LessonError(ValueError):
    """A lesson file or a lesson field that cannot be used; the message says what to fix."""


@dataclass
class Lesson:
    id: str
    title: str
    scope: str = "client"
    kind: str = "pitfall"
    state: str = "candidate"
    when: str = ""
    do: str = ""
    because: str = ""
    applies: dict = field(default_factory=lambda: {key: [] for key in APPLIES})
    evidence: list = field(default_factory=list)
    source: str = "agent"
    created: str = ""
    last_verified: str = ""
    review_by: str = ""
    supersedes: list = field(default_factory=list)
    promoted_to: list = field(default_factory=list)
    body: str = ""
    extra: dict = field(default_factory=dict)       # front matter keys Torque does not know; kept on rewrite
    # Not stored in the file:
    path: Path | None = None
    origin: str = "file"                            # "file", or "advisory" for a catalogue note read as a lesson
    command_patterns: tuple = ()                    # regular expressions over a command (catalogue notes)
    error_text: str = ""                            # the error wording a catalogue note describes

    @property
    def evidence_type(self) -> str:
        """The strongest evidence the lesson carries, for the one-line summary."""
        types = [entry.get("type") for entry in self.evidence]
        for name in ("reproduced", "official-doc", "second-occurrence", "consultant-said", "session"):
            if name in types:
                return name
        return "none"

    def front_matter(self) -> dict:
        data = {"id": self.id, "title": self.title, "scope": self.scope, "kind": self.kind, "state": self.state,
                "when": self.when, "do": self.do, "because": self.because,
                "applies": {key: list(self.applies.get(key, [])) for key in APPLIES},
                "evidence": [dict(entry) for entry in self.evidence], "source": self.source,
                "created": self.created, "last_verified": self.last_verified, "review_by": self.review_by,
                "supersedes": list(self.supersedes), "promoted_to": list(self.promoted_to)}
        return {**data, **{key: value for key, value in self.extra.items() if key not in data}}


def today() -> str:
    return date.today().isoformat()


def later(day: str, days: int = REVIEW_DAYS) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def new_id(day: str | None = None, taken=()) -> str:
    """L-YYYYMMDD-xxxxxxxx: the date and eight random hex characters (32 bits), so two
    machines, or two scopes that are never read together, practically never collide.
    `taken` holds the ids of every scope the caller can read; ids written by earlier
    versions (four characters) stay valid."""
    stamp = (day or today()).replace("-", "")
    while True:
        candidate = f"L-{stamp}-{secrets.token_hex(4)}"
        if candidate not in taken:
            return candidate


def admitted(source: str, evidence: list) -> bool:
    return source == "consultant" or any(entry.get("type") in ADMITTING for entry in evidence)


def one_line(text: object) -> str:
    return " ".join(str(text or "").split())


def first_sentence(text: str, limit: int = 90) -> str:
    """A title from free text: its first sentence, cut at a word."""
    line = one_line(next((part for part in str(text).splitlines() if part.strip()), ""))
    line = re.sub(r"^[#>*\-\s]+", "", line)
    match = re.search(r"(?<=[.!?])\s", line)
    line = (line[:match.start()] if match else line).rstrip(".")
    if len(line) > limit:
        line = line[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return line[:1].upper() + line[1:]


def words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9_]{3,}", text.lower())}


def content_key(lesson: Lesson) -> str:
    """The lesson's text, normalized, to recognise the same lesson saved again."""
    text = " ".join((lesson.when, lesson.do, lesson.because, lesson.body)).strip() or lesson.title
    return " ".join(re.findall(r"[a-z0-9_]+", text.lower()))


def overlap(one: Lesson, other: Lesson) -> float:
    """How much two lessons say the same thing: shared words over all words, 0 to 1."""
    first, second = (words(" ".join((item.title, item.when, item.do, item.because))) for item in (one, other))
    return len(first & second) / len(first | second) if first and second else 0.0


def split_front_matter(text: str) -> tuple[str, str]:
    """(YAML front matter, body) of a Markdown file that starts with a `---` line."""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        raise LessonError("no front matter: the file must start with a --- line")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1:]).strip("\n")
    raise LessonError("the front matter is not closed with a --- line")


def _day(value: object, name: str) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise LessonError(f"{name} is not a date (YYYY-MM-DD)") from None


def _strings(value: object, name: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (str, int, float)):
        value = [value]
    if not isinstance(value, list) or any(isinstance(item, (dict, list)) for item in value):
        raise LessonError(f"{name} must be a list of words")
    return [one_line(item) for item in value if one_line(item)]


def _choice(data: dict, name: str, allowed: tuple, default: str) -> str:
    value = data.get(name, default)
    if value not in allowed:
        raise LessonError(f"{name} must be one of: {', '.join(allowed)}")
    return value


def evidence_entry(kind: str, ref: str = "", day: str | None = None) -> dict:
    if kind not in EVIDENCE_TYPES:
        raise LessonError(f"evidence type must be one of: {', '.join(EVIDENCE_TYPES)}")
    entry = {"type": kind}
    if one_line(ref):
        entry["ref"] = one_line(ref)
    entry["date"] = day or today()
    return entry


def from_front_matter(data: object, body: str = "", *, path: Path | None = None) -> Lesson:
    if not isinstance(data, dict):
        raise LessonError("the front matter must be a list of `key: value` lines")
    identifier = data.get("id")
    if not isinstance(identifier, str) or not ID.match(identifier):
        raise LessonError("id is missing or malformed (expected something like L-20261114-a3f2)")
    title = one_line(data.get("title"))
    if not title:
        raise LessonError("title is missing")
    applies = data.get("applies") or {}
    if not isinstance(applies, dict):
        raise LessonError("applies must hold tasks, types, commands, keywords and errors")
    evidence = []
    raw = data.get("evidence") or []
    if not isinstance(raw, list) or any(not isinstance(entry, dict) for entry in raw):
        raise LessonError("evidence must be a list of {type, ref, date} entries")
    for entry in raw:
        item = evidence_entry(entry.get("type"), entry.get("ref") or "", "-")
        item.pop("date")
        if _day(entry.get("date"), "evidence date"):
            item["date"] = _day(entry.get("date"), "evidence date")
        evidence.append(item)
    return Lesson(
        id=identifier, title=title,
        scope=_choice(data, "scope", SCOPES, "client"), kind=_choice(data, "kind", KINDS, "pitfall"),
        state=_choice(data, "state", STATES, "candidate"),
        when=one_line(data.get("when")), do=one_line(data.get("do")), because=one_line(data.get("because")),
        applies={key: _strings(applies.get(key), f"applies.{key}") for key in APPLIES},
        evidence=evidence, source=_choice(data, "source", SOURCES, "agent"),
        created=_day(data.get("created"), "created"), last_verified=_day(data.get("last_verified"), "last_verified"),
        review_by=_day(data.get("review_by"), "review_by"),
        supersedes=_strings(data.get("supersedes"), "supersedes"),
        promoted_to=_strings(data.get("promoted_to"), "promoted_to"),
        body=body.strip("\n"), extra={key: value for key, value in data.items() if key not in _KNOWN}, path=path)


class _Loader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
    """Safe YAML in which a date stays the text it was written as, so a mistyped one is
    reported as a field to fix and not as a loader error."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()}


def load_yaml(text: str):
    """The value of a YAML text. Raises LessonError naming the line when it is not valid."""
    try:
        return yaml.load(text, Loader=_Loader)
    except (yaml.YAMLError, ValueError) as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 2})" if mark is not None else ""
        raise LessonError(f"the front matter is not valid YAML{where}") from None


def parse(text: str, *, path: Path | None = None) -> Lesson:
    """The lesson in a file's text. Raises LessonError with what to fix."""
    front, body = split_front_matter(text)
    return from_front_matter(load_yaml(front), body, path=path)


def _yaml(value: object) -> str:
    """One YAML value on one line, quoted only where YAML needs it."""
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10 ** 9, sort_keys=False)
    return text.removesuffix("\n").removesuffix("\n...")


def _dated(value: str):
    """A date for YAML to write bare (2026-11-14), or the text unchanged."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return value


def render(lesson: Lesson) -> str:
    """The file's text: front matter in a fixed order, then the body."""
    data = lesson.front_matter()
    lines = ["---"]
    for key, value in data.items():
        if key == "applies":
            lines.append("applies:")
            lines += [f"  {name}: {_yaml(value[name])}" for name in APPLIES]
        elif key == "evidence":
            entries = [{**entry, **({"date": _dated(entry["date"])} if entry.get("date") else {})} for entry in value]
            lines.append("evidence:" if entries else "evidence: []")
            lines += [f"  - {_yaml(entry)}" for entry in entries]
        elif key in ("created", "last_verified", "review_by"):
            lines.append(f"{key}: {_yaml(_dated(value)) if value else ''}".rstrip())
        else:
            lines.append(f"{key}: {_yaml(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n" + (lesson.body.strip("\n") + "\n" if lesson.body.strip() else "")


def age_days(lesson: Lesson, now: str | None = None) -> int | None:
    """Days since the lesson was last verified (or created)."""
    day = lesson.last_verified or lesson.created
    if not day:
        return None
    return max(0, (date.fromisoformat(now or today()) - date.fromisoformat(day)).days)


def age_text(lesson: Lesson, now: str | None = None) -> str:
    days = age_days(lesson, now)
    if days is None:
        return "not dated"
    label = "verified" if lesson.last_verified else "saved"
    return f"{label} today" if days == 0 else f"{label} {days} day{'s' if days != 1 else ''} ago"
