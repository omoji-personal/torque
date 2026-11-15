"""Bring lessons in: capture one, and save what a session taught.

Capture rides on steps that already happen (`torque session add --learned`), with
`torque lesson add` for a lesson on its own. Every capture is scrubbed, gets its
applicability from the text when none is given, and is admitted to `active` only on the
consultant's word or with evidence that it holds; otherwise it waits as a candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time

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
        return {"id": self.lesson.id, "short_id": record.short_id(self.lesson.id), "status": self.status, "state": self.lesson.state, "scope": self.lesson.scope,
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


def shipped_ids() -> set[str]:
    """Ids of the lessons shipped with Torque, so a new lesson never takes one of them."""
    from . import recall
    return {item.id for item in recall.platform_lessons(advisory=False)}


def parse_evidence(values, day: str) -> list[dict]:
    """`TYPE[:REF]` words to evidence entries."""
    entries = []
    for value in values or ():
        kind, _, ref = str(value).partition(":")
        entries.append(record.evidence_entry(kind.strip(), ref, day))
    return entries


def add(scopes: Scopes, *, text: str = "", title: str = "", when: str = "", do: str = "", because: str = "",
        body: str = "", scope: str | None = None, kind: str = "pitfall", tasks=(), types=(), commands=(),
        keywords=(), errors=(), evidence=(), source: str = "agent", session: str | None = None) -> Added:
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
                                vocab.keywords(whole)),
                               ("errors", [record.one_line(value) for value in errors or ()], vocab.errors(whole))):
        item.applies[name] = list(dict.fromkeys(given)) or found
        if not given and found:
            inferred[name] = found
    item.applies["commands"] = [record.one_line(value) for value in commands or () if record.one_line(value)]
    session = record.one_line(session)
    reference = f"session {session}" if session else ""
    item.evidence = parse_evidence(evidence, day) or [record.evidence_entry(
        "consultant-said" if source == "consultant" else "session", reference, day)]
    link = next((entry for entry in item.evidence if reference and entry.get("ref") == reference), None)
    if reference and link is None:
        # The session record a lesson comes from stays with it, also beside other evidence.
        link = record.evidence_entry("session", reference, day)
        item.evidence.append(link)
    if record.admitted(source, item.evidence):
        item.state, item.last_verified = "active", day
    found = scrubber.scrub_lesson(item, firm_terms=scrubber.workspace_terms(scopes.workspace) if scope == "firm" else None)
    if found.refused:
        raise Refused(found.refusal(scope), found)
    reference = link.get("ref", "") if link else ""      # as it is stored: scrubbed like the rest
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
        item.id = record.new_id(day, storage.readable_ids(scopes) | shipped_ids())
        path = storage.save(item, folder)
    return Added(item, path, "saved", inferred, found.masked, [note.replace(" ID ", f" {item.id} ") for note in notes])


def _again(same: Lesson, new: Lesson, folder: Path, reference: str, day: str, masked: dict) -> Added:
    """The same lesson captured again. On a new occasion that is a second occurrence:
    evidence that admits a candidate. Otherwise nothing changes. A new occasion is another
    day than every one the lesson's evidence names and, where both carry one, another
    session record: two session records on one day can be one piece of work, and an agent
    could record two to admit its own note."""
    if same.state == "retired":
        return Added(same, same.path, "duplicate", masked=masked, notes=[
            f"This lesson exists and is retired. If it holds after all: torque lesson restore {same.id}"])

    def same_occasion(entry: dict) -> bool:
        linked = entry.get("ref", "") if str(entry.get("ref", "")).startswith("session ") else ""
        return entry.get("date") == day or bool(reference and linked and reference == linked)

    if any(same_occasion(entry) for entry in same.evidence):
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


HOSTS = ("claude", "codex", "antigravity")


def host_memory_dir(host: str, workspace: Path, given: str | None = None) -> tuple[Path | None, str]:
    """(folder, how to name one) where a host keeps its own memory or learned rules for
    this workspace. Only documented places are guessed; anything else needs --dir."""
    if given:
        return Path(given).expanduser(), ""
    home = Path.home()
    if host == "claude":
        base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude") / "projects"
        text = str(workspace)
        names = dict.fromkeys((re.sub(r"[\\/:]", "-", text), re.sub(r"[^A-Za-z0-9]", "-", text)))
        folders = [base / name / "memory" for name in names]
        return next((folder for folder in folders if folder.is_dir()), folders[0]), ""
    if host == "codex":
        base = Path(os.environ.get("CODEX_HOME") or home / ".codex")
        folders = [base / "memories_v2", base / "memories"]
        return next((folder for folder in folders if folder.is_dir()), folders[0]), ""
    return None, ""


def antigravity_places(workspace: Path) -> list[Path]:
    """Where Antigravity's `/learn` writes, as its documentation names them: rules and
    skills in the workspace (`.agents/rules/*.md`, `.agents/skills/*/SKILL.md`) and for
    the user (`~/.gemini/GEMINI.md`, `~/.gemini/config/skills/*/SKILL.md`). Knowledge
    items are not documented as files and are not looked for."""
    gemini = Path.home() / ".gemini"
    return [workspace / ".agents" / "rules", workspace / ".agents" / "skills", gemini / "GEMINI.md",
            gemini / "config" / "skills"]


def _torque_wrote(workspace: Path) -> dict[str, str]:
    """{path: text} of the files Torque itself derives into `.agents/` for this workspace.
    A file there that still reads exactly so is Torque's and not something learned."""
    try:
        from importlib.resources import files
        from torque import antigravity
        surface = antigravity.surface(files("torque").joinpath("data"))
    except Exception:  # Torque's own package is not importable: nothing is left out as Torque's.
        return {}
    return {os.path.normcase(str(workspace / name)): text for name, (text, _) in surface.items()}


def _antigravity_files(scopes: Scopes) -> tuple[list[Path], dict, list[str]]:
    """The learned rule and skill files of Antigravity's documented places, without the
    ones Torque wrote and nobody changed; and the places looked at."""
    places = antigravity_places(scopes.workspace)
    ours = _torque_wrote(scopes.workspace)
    found: list[Path] = []
    skipped = {"other engagements": 0, "links": 0, "written by Torque": 0}
    for place in places:
        if place.is_file():
            paths, more = [place], {}
        elif place.is_dir():
            paths, more = _walk(scopes, place)
            if place.name == "skills":
                paths = [path for path in paths if path.name == "SKILL.md"]
        else:
            continue
        for name, number in more.items():
            skipped[name] += number
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if ours.get(os.path.normcase(str(path))) == text:
                skipped["written by Torque"] += 1
            else:
                found.append(path)
    return sorted(found), skipped, [str(place) for place in places]


def _harvest_state(workspace: Path) -> Path:
    return storage.state_dir(workspace) / "harvest.json"


WITHHELD = scrubber.WITHHELD


def _shown(text: str) -> str:
    """A host note's name or first line as a harvest may print it: scrubbed whole before it
    is cut short, and withheld when the scrub would refuse it as a lesson."""
    cleaned, found = scrubber.scrub(text[:4000])
    return WITHHELD if found.refused else cleaned


def _walk(scopes: Scopes, folder: Path) -> tuple[list[Path], dict]:
    """The Markdown files under a host's memory folder that a harvest may open, and what
    was left out: the folders of other clients and initiatives (pruned, never entered),
    and links (a linked folder is not followed, a linked file is not opened). Every file
    is checked again by where it resolves to before it is opened."""
    skipped = {"other engagements": 0, "links": 0}
    found: list[Path] = []
    for top, folders, names in os.walk(folder):
        real = os.path.realpath(top)
        # A symbolic link or a junction resolves somewhere other than where it is listed.
        linked = lambda name: os.path.normcase(os.path.realpath(Path(top, name))) != os.path.normcase(
            os.path.join(real, name))
        kept = []
        for name in folders:
            path = Path(top, name)
            if linked(name):
                skipped["links"] += 1
            elif scopes.foreign(path):
                skipped["other engagements"] += 1
            else:
                kept.append(name)
        folders[:] = kept
        for name in names:
            if not name.lower().endswith(".md"):
                continue
            path = Path(top, name)
            if linked(name):
                skipped["links"] += 1
            elif scopes.foreign(path):
                skipped["other engagements"] += 1
            else:
                found.append(path)
    return sorted(found), skipped


def harvest(scopes: Scopes, host: str, given: str | None = None, since: str | None = None) -> dict:
    """The files in a host's own memory that changed since the last harvest, as pointers:
    name, type, one-line description, path. It copies nothing: a host's memory mixes
    clients, so a note enters a scope only through `torque lesson add --from-file PATH`
    once someone has chosen the scope. The harvest time is recorded per host."""
    if host not in HOSTS:
        raise LessonError(f"unknown host `{host}`: use one of {', '.join(HOSTS)}.")
    if scopes.workspace is None:
        raise LessonError("harvest records when it last ran in a workspace: add --workspace PATH.")
    folder, hint = host_memory_dir(host, scopes.workspace, given)
    if folder is not None and scopes.foreign(folder):
        raise LessonError(f"{folder} lies in the folder of another client or initiative; a host's memory is not "
                          "kept there, and a lesson command does not read there.")
    state_path = _harvest_state(scopes.workspace)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state = state if isinstance(state, dict) else {}
    earlier = state.get(host) if isinstance(state.get(host), dict) else {}
    last, seen = earlier.get("at"), earlier.get("seen")
    report = {"host": host, "dir": str(folder) if folder else None, "since": since or (last or None),
              "files": [], "copied": 0, "found": False, "note": hint}
    looked_at: list[str] = []
    if folder is None:
        paths, skipped, looked_at = _antigravity_files(scopes)
        report["looked_at"] = looked_at
        if not any(Path(place).exists() for place in looked_at):
            report["note"] = ("Antigravity keeps what /learn writes as rules and skills; none of its documented places "
                              f"exists here ({', '.join(looked_at)}). Name another folder: --dir PATH")
            return report
    elif not folder.is_dir():
        # Names only: the database is never opened (its format is not documented).
        databases = sorted(path.name for path in folder.parent.glob("memories_*.sqlite")) \
            if host == "codex" and not given else []
        report["note"] = (
            f"Codex keeps its memory here in a database file ({databases[0]} in {folder.parent}), which this command "
            "does not read; export it to a folder of notes, or name a folder with --dir PATH." if databases else
            f"No memory folder at {folder}. If {host} keeps it elsewhere, name it: --dir PATH")
        return report
    if since:
        cutoff = datetime.fromisoformat(record._day(since, "--since")).replace(tzinfo=timezone.utc).timestamp()
    else:
        cutoff = float(seen) if isinstance(seen, (int, float)) else 0.0
    started = time.time()
    files = []
    if folder is not None:
        paths, skipped = _walk(scopes, folder)
    for path in paths:
        try:
            if path.name == "MEMORY.md" or not path.is_file():
                continue
            modified = path.stat().st_mtime
            if modified < cutoff:
                continue
            with open(path, encoding="utf-8", errors="replace") as stream:
                meta, body = host_note(stream.read(64 * 1024))
        except OSError:
            continue
        first = next((line.strip(" #*-") for line in body.splitlines() if line.strip(" #*-")), "")
        files.append({"name": _shown(meta.get("name") or (path.parent.name if path.name == "SKILL.md" else path.stem)),
                      "type": meta.get("type") or (path.stem.split("_", 1)[0] if "_" in path.stem else ""),
                      "description": _shown(meta.get("description") or first)[:160],
                      "modified": datetime.fromtimestamp(modified, timezone.utc).date().isoformat(),
                      "path": str(path)})
    report.update(files=files, found=True, skipped=skipped)
    # `seen` is the moment this listing began: a file changed after it shows up next time.
    state[host] = {"at": storage.now(), "seen": started, "dir": str(folder) if folder else None, "files": len(files)}
    storage._private_dir(state_path.parent)
    storage._atomic_write(state_path, json.dumps(state, indent=2) + "\n")
    return report


def _day_of(stamp: object) -> str:
    """The day of a Unix time in an old record, or "" when it is not one."""
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat() if type(stamp) is int and stamp > 0 else ""
    except (OverflowError, OSError, ValueError):
        return ""


def _v1_records(engagement: Path, problems: list) -> list[dict]:
    """The lessons of the earlier JSON store (state/memory/lessons), as they were written."""
    folder = engagement / "state" / "memory" / "lessons"
    found: list[dict] = []
    files = [*sorted((folder / "_review_queue").glob("*.json")), folder / "active.json",
             *sorted((folder / "_archive").glob("*.json"))]
    for path in files:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append({"path": path.relative_to(engagement).as_posix(), "reason": "not readable JSON"})
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
                found.append(item)
            else:
                problems.append({"path": path.relative_to(engagement).as_posix(), "reason": "not a lesson record"})
    return found


def migrate(scopes: Scopes) -> dict:
    """Import the earlier store's JSON lessons of the selected client into the new format,
    once: a lesson already imported is skipped, and the old files are left in place."""
    if scopes.engagement is None:
        raise LessonError("select the client whose old lessons to import: --workspace PATH --client NAME.")
    problems: list = []
    folder = scopes.folder(scopes.kind)
    report = {"imported": [], "already": 0, "problems": problems,
              "old_store": str(scopes.engagement / "state" / "memory" / "lessons")}
    states = {"review_pending": "candidate", "active": "active", "archive": "retired"}
    with storage.transaction(scopes.engagement):
        taken = storage.taken_ids(folder)
        for old in _v1_records(scopes.engagement, problems):
            day = _day_of(old.get("captured_at")) or record.today()
            suffix = re.sub(r"[^a-z0-9]", "", old["id"].lower())[:16].ljust(4, "0")
            identifier = f"L-{day.replace('-', '')}-{suffix}"
            if identifier in taken:
                report["already"] += 1
                continue
            text = str(old.get("full_text") or old.get("short") or "").strip()
            title = re.sub(r"^\[[a-z_]+\]\s*", "", str(old.get("title") or "")).strip()
            if not (text or title):
                problems.append({"path": f"v1 lesson {old['id'][:12]}", "reason": "it holds no text"})
                continue
            parts = structure(text)
            helpful = old.get("helpful_count") if type(old.get("helpful_count")) is int else 0
            item = Lesson(
                id=identifier, title=record.first_sentence(parts.get("do") or text or title), scope=scopes.kind,
                state=states.get(old.get("state"), "candidate"), when=parts.get("when", ""), do=parts.get("do", ""),
                because=parts.get("because", ""), source="harvested", created=day, review_by=record.later(record.today()),
                body=parts.get("lead", "") if parts else text,
                evidence=[record.evidence_entry("session", f"earlier store, lesson {old['id'][:16]}"
                                                + (f", marked helpful {helpful} times" if helpful else ""), day)])
            if item.state == "active":
                item.last_verified = _day_of(old.get("last_seen"))
            whole = "\n".join((item.title, item.when, item.do, item.because, item.body))
            item.applies["tasks"], item.applies["types"] = vocab.infer(whole)
            item.applies["keywords"], item.applies["errors"] = vocab.keywords(whole), vocab.errors(whole)
            found = scrubber.scrub_lesson(item)
            if found.refused:
                problems.append({"path": f"v1 lesson {old['id'][:12]}",
                                 "reason": "not imported: " + ", ".join(found.refused) + " in its text"})
                continue
            storage.save(item, folder)
            taken.add(identifier)
            report["imported"].append({"id": identifier, "state": item.state, "title": item.title})
    return report
