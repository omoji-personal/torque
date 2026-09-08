"""Storage layer: L1 (review queue) / L2 (active) / L3 (archive).

Per plan-v5: review-first architecture. NO auto-promotion. Capture writes
candidates to L1 (`_review_queue/`); Claude's `/lesson-helpful` moves L1 → L2;
`/lesson-stale` (count >= 2) moves L2 → L3.

L3 framework promotion stays via existing `/collect-lessons` admin flow.

Compatibility: `load_framework_memory()` is a shim for the existing
`session_start_context.py:_proactive_l3_section()` placeholder.
"""

from __future__ import annotations
from jsc_common.workspace import state_dir

import json
import os
import pathlib
import time
from dataclasses import dataclass, field
from typing import Optional


def memory_root() -> pathlib.Path:
    """Resolve storage on every call so sequential clients never share state."""
    return state_dir("memory", legacy_env="JSC_MEMORY_DIR")


def _lessons_dir() -> pathlib.Path:
    return memory_root() / "lessons"


def review_queue_dir() -> pathlib.Path:
    return _lessons_dir() / "_review_queue"


def archive_dir() -> pathlib.Path:
    return _lessons_dir() / "_archive"


def active_lessons_path() -> pathlib.Path:
    """Active lessons belong only to the selected client scope."""
    return _lessons_dir() / "lessons.md"


def spool_dir() -> pathlib.Path:
    return memory_root() / "spool"


def index_path() -> pathlib.Path:
    return _lessons_dir() / "index.json"


def index_top_path() -> pathlib.Path:
    return _lessons_dir() / "index_top.json"


def ensure_dirs() -> None:
    """Create the storage tree if absent (mode 0o700 — operator-private memory).

    Safe to call repeatedly. Tightens pre-existing dirs too: mkdir(exist_ok=True)
    won't change the mode of a dir created before this hardening, and mkdir(mode=)
    is masked by the umask, so an explicit chmod (best-effort) closes the
    umask-default gap (audit 2026-06-13 follow-up to LESSON-1; the spool + L1
    candidate FILES are already 0o600, this protects the containing dirs).
    """
    for p in (
        memory_root(),
        _lessons_dir(),
        review_queue_dir(),
        archive_dir(),
        spool_dir(),
        spool_dir() / "_quarantine",
    ):
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass


@dataclass
class Lesson:
    id: str
    title: str
    short: str  # one-line summary
    full_text: str  # full lesson body
    confidence: str  # HIGHEST | HIGH | MEDIUM | LOW
    trigger: str
    captured_at: int  # unix ts
    last_seen: int  # unix ts (updated on surfacing)
    state: str  # review_pending | active | archive
    helpful_count: int = 0
    stale_count: int = 0
    surfacing_attempts: int = 0
    missed_review_count: int = 0
    client_scope: list[str] = field(default_factory=list)
    signature_keywords: list[str] = field(default_factory=list)
    signature_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "short": self.short,
            "full_text": self.full_text,
            "confidence": self.confidence,
            "trigger": self.trigger,
            "captured_at": self.captured_at,
            "last_seen": self.last_seen,
            "state": self.state,
            "helpful_count": self.helpful_count,
            "stale_count": self.stale_count,
            "surfacing_attempts": self.surfacing_attempts,
            "missed_review_count": self.missed_review_count,
            "client_scope": self.client_scope,
            "signature_keywords": self.signature_keywords,
            "signature_hash": self.signature_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Lesson":
        return cls(
            id=d["id"],
            title=d["title"],
            short=d.get("short", ""),
            full_text=d.get("full_text", ""),
            confidence=d.get("confidence", "LOW"),
            trigger=d.get("trigger", "unknown"),
            captured_at=d.get("captured_at", 0),
            last_seen=d.get("last_seen", 0),
            state=d.get("state", "review_pending"),
            helpful_count=d.get("helpful_count", 0),
            stale_count=d.get("stale_count", 0),
            surfacing_attempts=d.get("surfacing_attempts", 0),
            missed_review_count=d.get("missed_review_count", 0),
            client_scope=d.get("client_scope", []) or [],
            signature_keywords=d.get("signature_keywords", []) or [],
            signature_hash=d.get("signature_hash", ""),
        )


def write_review_candidate(lesson: Lesson) -> pathlib.Path:
    """Write a Lesson to L1 review queue. Returns the path."""
    ensure_dirs()
    fname = f"{lesson.captured_at}-{lesson.id[:12]}.json"
    p = review_queue_dir() / fname
    _atomic_write(p, json.dumps(lesson.to_dict(), indent=2))
    return p


def list_review_pending() -> list[Lesson]:
    """List L1 (review_pending) lessons."""
    ensure_dirs()
    out = []
    for p in sorted(review_queue_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text())
            out.append(Lesson.from_dict(data))
        except Exception:
            continue
    return out


def list_active() -> list[Lesson]:
    """List L2 (active) lessons. Reads from active_lessons_path() if it's our
    JSON sidecar; the markdown file remains operator-edited.
    """
    ensure_dirs()
    sidecar = _lessons_dir() / "active.json"
    if not sidecar.exists():
        return []
    try:
        data = json.loads(sidecar.read_text())
        return [Lesson.from_dict(d) for d in data]
    except Exception:
        return []


def write_active(lessons: list[Lesson]) -> None:
    """Persist L2 lessons to JSON sidecar (atomic)."""
    ensure_dirs()
    sidecar = _lessons_dir() / "active.json"
    _atomic_write(sidecar, json.dumps([l.to_dict() for l in lessons], indent=2))


def find_lesson(lesson_id: str) -> Optional[tuple[Lesson, str]]:
    """Find a lesson by id (or 12-char prefix). Returns (lesson, state) or None.

    State is "review_pending" | "active" | "archive".
    """
    for l in list_review_pending():
        if l.id == lesson_id or l.id.startswith(lesson_id):
            return l, "review_pending"
    for l in list_active():
        if l.id == lesson_id or l.id.startswith(lesson_id):
            return l, "active"
    for p in sorted(archive_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text())
            l = Lesson.from_dict(data)
            if l.id == lesson_id or l.id.startswith(lesson_id):
                return l, "archive"
        except Exception:
            continue
    return None


def promote_to_active(lesson_id: str) -> Optional[Lesson]:
    """Move a review_pending lesson → active. Returns the promoted lesson or None."""
    pending = list_review_pending()
    for l in pending:
        if l.id == lesson_id or l.id.startswith(lesson_id):
            # Update state
            l.state = "active"
            l.helpful_count += 1
            l.last_seen = int(time.time())
            # Add to active list
            active = list_active()
            active.append(l)
            write_active(active)
            # Remove from review queue
            for p in review_queue_dir().glob(f"*-{l.id[:12]}.json"):
                p.unlink()
            return l
    # Maybe it's already active — boost score
    active = list_active()
    for l in active:
        if l.id == lesson_id or l.id.startswith(lesson_id):
            l.helpful_count += 1
            l.last_seen = int(time.time())
            write_active(active)
            return l
    return None


def mark_stale(lesson_id: str) -> Optional[Lesson]:
    """Increment stale_count. If >= 2, archive. Returns the affected lesson."""
    pending = list_review_pending()
    for l in pending:
        if l.id == lesson_id or l.id.startswith(lesson_id):
            l.stale_count += 1
            if l.stale_count >= 2:
                l.state = "archive"
                _archive_lesson(l)
                for p in review_queue_dir().glob(f"*-{l.id[:12]}.json"):
                    p.unlink()
            else:
                # rewrite the file with updated count
                for p in review_queue_dir().glob(f"*-{l.id[:12]}.json"):
                    _atomic_write(p, json.dumps(l.to_dict(), indent=2))
            return l
    active = list_active()
    for l in active:
        if l.id == lesson_id or l.id.startswith(lesson_id):
            l.stale_count += 1
            if l.stale_count >= 2:
                l.state = "archive"
                _archive_lesson(l)
                active = [a for a in active if a.id != l.id]
            write_active(active)
            return l
    return None


def _archive_lesson(lesson: Lesson) -> pathlib.Path:
    ensure_dirs()
    fname = f"{lesson.captured_at}-{lesson.id[:12]}.json"
    p = archive_dir() / fname
    _atomic_write(p, json.dumps(lesson.to_dict(), indent=2))
    return p


def review_queue_size() -> int:
    """Count lessons currently in review queue."""
    ensure_dirs()
    return sum(1 for _ in review_queue_dir().glob("*.json"))


def _atomic_write(path: pathlib.Path, content: str) -> None:
    """Write to temp file in same dir, then os.replace for atomicity.

    Cleans up temp file on failure. Per model-orchestration.md pattern.
    """
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise


# === Compatibility shim (preserves session_start_context placeholder) ===

class _FrameworkMemory:
    """Stub object with .lessons attribute for compatibility shim."""
    lessons: list = []


def load_framework_memory() -> _FrameworkMemory:
    """Compat shim for `session_start_context.py:_proactive_l3_section()`.

    Returns an empty framework-memory stub. Real lesson surfacing in v1
    happens via `injector.top_k_for_session()` called from the new
    `_proactive_lessons_section()` (renamed in session_start_context.py).
    The old `_proactive_l3_section` placeholder remains a no-op.
    """
    return _FrameworkMemory()
