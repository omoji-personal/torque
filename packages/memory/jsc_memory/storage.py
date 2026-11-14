"""Where lessons live. Scopes are folders:

  client      <workspace>/clients/<slug>/lessons/*.md
  initiative  <workspace>/initiatives/<slug>/lessons/*.md
  firm        <workspace>/lessons/*.md
  platform    shipped with the package and read-only (see recall.platform_lessons)

Read rule: the selected client or initiative, then the firm, then the platform. No
other engagement's lessons, events or files are read, under any option (the scrub reads
each client's registration for its name: scrubber.workspace_terms). Write rule: the
selected engagement by default, the firm when asked; nothing writes into the package.

Events (shown, helpful, stale, verified, promoted, retired, restored) are appended to
`.torque/lessons/events.jsonl` under the scope root: a client's events stay in that
client's folder, firm and platform events go to the workspace. An event holds a lesson
id, a time and the surface that produced it, never the lesson's text. Counters are
derived from the events.

Each lesson is one file, written whole through a temporary file and a rename. A
read-modify-write holds the scope root's one lock. A file that cannot be read is
skipped and named, and never stops a command.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from . import lesson as record
from .lesson import Lesson, LessonError

MAX_BYTES = 256 * 1024
LOCK_SECONDS = 20.0
EVENTS = ("shown", "helpful", "stale", "verified", "promoted", "retired", "restored")
_FOLDERS = {"client": "clients", "initiative": "initiatives"}


class Busy(OSError):
    """Another process holds the lessons lock; nothing was written."""


class NotFound(LessonError):
    pass


class Ambiguous(LessonError):
    def __init__(self, message: str, candidates: list[Lesson]):
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True)
class Scopes:
    """What one call may read and write: a workspace (the firm's lessons) and at most
    one engagement in it. Without a workspace only the platform lessons are read."""
    workspace: Path | None = None
    engagement: Path | None = None
    kind: str | None = None

    @property
    def slug(self) -> str | None:
        return self.engagement.name if self.engagement else None

    def root(self, scope: str) -> Path | None:
        """The folder that owns a scope's lessons, events and proposals."""
        if scope in record.ENGAGEMENT_SCOPES:
            return self.engagement if scope == self.kind else None
        return self.workspace if scope == "firm" else None

    def folder(self, scope: str) -> Path | None:
        root = self.root(scope)
        return root / "lessons" if root else None

    def event_root(self, scope: str) -> Path | None:
        """Where events about a lesson of this scope are kept. The platform is
        read-only, so its events go to the workspace."""
        return self.engagement if scope in record.ENGAGEMENT_SCOPES else self.workspace

    def default_scope(self) -> str | None:
        return self.kind if self.engagement else None

    def label(self) -> str:
        parts = [f"{self.kind} {self.slug}"] if self.engagement else []
        return " + ".join(parts + (["firm"] if self.workspace else []) + ["platform"])

    def foreign(self, path: str | os.PathLike) -> bool:
        """True for a path inside the folder of a client or an initiative other than the
        one selected: of this workspace, or of any other (a folder that holds a
        `client.json` or a `binding.json`, or lies under a workspace's `clients/` or
        `initiatives/`). The path is resolved first, so a link is judged by where it
        leads. No lesson command reads there, whatever path it is given (`--path`,
        `--from-file`, `--body-file`, `--dir`, a proposal, a file found by `harvest`)."""
        if self.workspace is None:
            return False
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return True
        here = os.path.normcase(str(resolved))
        own = os.path.normcase(str(self.engagement)) if self.engagement else None
        if own and (here == own or here.startswith(own + os.sep)):
            return False
        if any(here.startswith(os.path.normcase(str(self.workspace / name)) + os.sep) for name in _FOLDERS.values()):
            return True
        return _in_an_engagement(resolved)

    def shown(self, path: Path) -> str:
        """A path for a message: relative to the workspace when it lies inside it."""
        try:
            return path.relative_to(self.workspace).as_posix() if self.workspace else path.name
        except ValueError:
            return path.name


_MARKERS = ("client.json", "binding.json")


def _in_an_engagement(resolved: Path) -> bool:
    """A resolved path that is, or lies inside, the folder of a client or an initiative of
    any workspace: a folder holding its registration, or a workspace's `clients/` or
    `initiatives/` folder above it. Only names are looked at; nothing is read."""
    try:
        for folder in (resolved, *resolved.parents):
            if any((folder / marker).is_file() for marker in _MARKERS):
                return True
            if folder != resolved and folder.name in _FOLDERS.values() and (folder.parent / "workspace.json").is_file():
                return True
    except OSError:
        return True
    return False


def scopes_for(folder: str | os.PathLike | None) -> Scopes:
    """The scopes a folder selects: a workspace root (firm lessons only) or an
    engagement folder in one (that client or initiative, and its workspace)."""
    if not folder:
        return Scopes()
    path = Path(folder).expanduser().resolve()
    if (path / "workspace.json").is_file():
        return Scopes(workspace=path)
    workspace = path.parent.parent
    for kind, marker in (("client", "client.json"), ("initiative", "binding.json")):
        if (path.parent.name == _FOLDERS[kind] and (path / marker).is_file()
                and (workspace / "workspace.json").is_file()):
            return Scopes(workspace=workspace, engagement=path, kind=kind)
    return Scopes()


def scopes_from_env() -> Scopes:
    """The scopes the launcher selected. TORQUE_WORKSPACE is the engagement folder for a
    selected client or initiative, or the workspace root; a legacy JSC_ROOT selects nothing."""
    return scopes_for(os.environ.get("TORQUE_WORKSPACE"))


def state_dir(root: Path) -> Path:
    return root / ".torque" / "lessons"


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_folder(folder: Path | None, scope: str, scopes: Scopes, problems: list | None = None) -> list[Lesson]:
    """The lessons in one folder. An unreadable file is skipped and named in `problems`."""
    if folder is None or not folder.is_dir():
        return []
    problems = problems if problems is not None else []
    if folder.is_symlink():
        problems.append({"path": scopes.shown(folder), "reason": "the lessons folder is a symbolic link; not read"})
        return []
    found: dict[str, Lesson] = {}
    # The file named after its id is read first, so a stray copy of it is the one reported.
    for path in sorted(folder.glob("*.md"), key=lambda path: (not record.ID.match(path.stem), path.name.lower())):
        if path.name.lower() == "readme.md":
            continue
        try:
            if path.is_symlink():
                raise LessonError("a symbolic link is not read")
            if path.stat().st_size > MAX_BYTES:
                raise LessonError("larger than 256 KB; a lesson is a short note")
            item = record.parse(path.read_text(encoding="utf-8"), path=path)
            if item.scope != scope:
                raise LessonError(f"its scope says {item.scope}, but this folder holds {scope} lessons")
            if item.id in found:
                raise LessonError(f"its id {item.id} is already used by {found[item.id].path.name}")
        except UnicodeDecodeError:
            problems.append({"path": scopes.shown(path), "reason": "not UTF-8 text"})
        except LessonError as exc:
            problems.append({"path": scopes.shown(path), "reason": str(exc)})
        except OSError as exc:
            problems.append({"path": scopes.shown(path), "reason": f"cannot be read ({exc.strerror or 'error'})"})
        else:
            found[item.id] = item
    return sorted(found.values(), key=lambda item: item.id)


def load(scopes: Scopes, problems: list | None = None, *, only: str | None = None) -> list[Lesson]:
    """Every lesson in the workspace the call may read: the selected engagement's and
    the firm's (`only` narrows to one of them). Platform lessons come from recall."""
    lessons: list[Lesson] = []
    for scope in ([scopes.kind] if scopes.engagement else []) + ["firm"]:
        if only in (None, scope):
            lessons += read_folder(scopes.folder(scope), scope, scopes, problems)
    return lessons


def resolve(lessons: list[Lesson], wanted: str) -> Lesson:
    """The one lesson an id names: the exact id, or a unique prefix of at least four
    characters after the `L-`. Raises NotFound or Ambiguous (with the candidates)."""
    wanted = (wanted or "").strip()
    if not wanted:
        raise Ambiguous("no lesson id was given", lessons[:10])
    exact = [item for item in lessons if item.id == wanted]
    if len(exact) == 1:
        return exact[0]
    tail = wanted[2:] if len(wanted) > 1 and wanted[1] == "-" else wanted
    if len(tail) < 4:
        raise Ambiguous(f"`{wanted}` is too short: give the whole id or at least four characters after the L-",
                        [item for item in lessons if item.id.lower().startswith(wanted.lower())][:10])
    matches = exact or [item for item in lessons
                        if item.id.lower().startswith(wanted.lower()) or item.id[2:].lower().startswith(tail.lower())]
    if not matches:
        raise NotFound(f"no lesson `{wanted}` among the lessons this command can read")
    if len(matches) > 1:
        raise Ambiguous(f"`{wanted}` matches {len(matches)} lessons", matches[:10])
    return matches[0]


def save(item: Lesson, folder: Path) -> Path:
    """Write the lesson's file whole. A lesson read from a file keeps that file's name."""
    _private_dir(folder)
    path = item.path or folder / f"{item.id}.md"
    _atomic_write(path, record.render(item))
    item.path = path
    return path


def taken_ids(folder: Path | None) -> set[str]:
    return {path.stem for path in folder.glob("*.md")} if folder and folder.is_dir() else set()


def _atomic_write(path: Path, content: str) -> None:
    """Write to a temporary file in the same folder, then replace: readers see the old
    file or the new one, never part of one. The temporary file is removed on failure."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


_MUTEX = threading.RLock()
_HELD = threading.local()


def _acquire(fd: int, path: Path) -> None:
    """Take the lock, waiting at most LOCK_SECONDS, the same way on every system."""
    deadline = time.monotonic() + LOCK_SECONDS
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise Busy(f"another Torque command is writing lessons here ({path} stayed locked for "
                           f"{LOCK_SECONDS:g} seconds). Nothing was written; run the command again.") from None
            time.sleep(0.05)


@contextmanager
def transaction(root: Path):
    """Serialize a read-modify-write of one scope root, across threads and processes.
    The lock file is never removed; closing it releases the lock even if the process dies."""
    held = getattr(_HELD, "roots", None)
    if held is None:
        held = _HELD.roots = set()
    key = os.path.normcase(str(root))
    with _MUTEX:
        if key in held:
            yield
            return
        path = _private_dir(state_dir(root)) / ".lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _acquire(fd, path)
            held.add(key)
            try:
                yield
            finally:
                held.discard(key)
        finally:
            os.close(fd)


def now() -> str:
    """The time of an event: local, with its offset, so that its first ten characters are
    the same day a lesson's `created` and `last_verified` would say at that moment."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def events_path(root: Path) -> Path:
    return state_dir(root) / "events.jsonl"


def log(root: Path | None, rows: list[dict], *, must: bool = False) -> bool:
    """Append events to a scope root's log. Showing a lesson must never fail the command
    that shows it, so a failure is swallowed unless `must` (feedback the user asked for)."""
    if root is None or not rows:
        return False
    try:
        with transaction(root):
            with open(events_path(root), "a", encoding="utf-8", newline="\n") as stream:
                for row in rows:
                    stream.write(json.dumps({"at": now(), **row}, ensure_ascii=False) + "\n")
        return True
    except OSError:
        if must:
            raise
        return False


def read_events(root: Path | None) -> list[dict]:
    """The events of one scope root, oldest first. A damaged line is skipped."""
    if root is None:
        return []
    try:
        text = events_path(root).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("event") in EVENTS and isinstance(row.get("id"), str) \
                and isinstance(row.get("at"), str):
            rows.append(row)
    return rows


def events_for(scopes: Scopes) -> list[dict]:
    """The events this call may read: the workspace's and the selected engagement's."""
    return sorted(read_events(scopes.workspace) + read_events(scopes.engagement), key=lambda row: row["at"])


@dataclass
class Tally:
    shown: int = 0
    helpful: int = 0
    stale: int = 0
    last_shown: str = ""
    stale_mark: dict | None = None        # the open stale mark: {at, reason, superseded_by}
    last: dict = field(default_factory=dict)

    @property
    def score(self) -> int:
        return self.helpful - self.stale

    def counters(self) -> dict:
        return {"shown": self.shown, "helpful": self.helpful, "stale": self.stale}


def tally(events: list[dict]) -> dict[str, Tally]:
    """Counters and the open stale mark per lesson id. A stale mark stays until the
    lesson is verified or restored."""
    result: dict[str, Tally] = {}
    for row in events:
        item = result.setdefault(row["id"], Tally())
        name = row["event"]
        item.last[name] = row["at"]
        if name == "shown":
            item.shown += 1
            item.last_shown = row["at"]
        elif name == "helpful":
            item.helpful += 1
        elif name == "stale":
            item.stale += 1
            item.stale_mark = {"at": row["at"], "reason": row.get("reason", ""),
                               "superseded_by": row.get("superseded_by", "")}
        elif name in ("verified", "restored"):
            item.stale_mark = None
    return result
