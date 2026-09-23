"""Index management — atomic + locked updates of lessons/index.json + index_top.json.

Per plan-v5: lesson FILES are source of truth; index is rebuildable cache.
Atomic write via temp + os.replace + fcntl.flock for concurrent safety.

`index.json` = full snapshot (capped at 256KB; rebuilt if exceeded)
`index_top.json` = compact top-50 by surface_score for SessionStart fast path
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
from typing import Optional

from . import storage
from . import ranker

INDEX_MAX_BYTES = 256 * 1024  # 256KB
INDEX_TOP_MAX_BYTES = 50 * 1024  # 50KB
INDEX_TOP_MAX_LESSONS = 50

# Windows has no fcntl; msvcrt.locking() is the closest advisory-lock analog.
if os.name == "nt":
    import msvcrt

    def _lock_exclusive(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_exclusive(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _write_atomic(path: pathlib.Path, content: str) -> None:
    """Atomic write via temp + os.replace (same dir for atomicity)."""
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


def rebuild_index() -> None:
    """Walk lesson files, compute scores, write index.json + index_top.json.

    Acquires advisory fcntl lock on the index directory to serialize parallel
    rebuilders. Last-writer-wins is acceptable here because we always rebuild
    from FILES (source of truth), not from existing index.
    """
    storage.ensure_dirs()
    lock_path = storage.index_path().parent / ".index.lock"
    lock_fd = None
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
        _lock_exclusive(lock_fd)

        # Walk all lesson files
        all_lessons = storage.list_review_pending() + storage.list_active()
        # Score them
        now = int(time.time())
        for l in all_lessons:
            l._score = ranker.surface_score(l, now=now)  # type: ignore[attr-defined]

        # Build full index (with size cap)
        full = {
            "schema_version": 1,
            "last_updated_ts": now,
            "lessons": [
                {
                    "id": l.id,
                    "title": l.title,
                    "short": l.short,
                    "score": getattr(l, "_score", 0.0),
                    "state": l.state,
                    "confidence": l.confidence,
                    "client_scope": l.client_scope,
                    "signature_keywords": l.signature_keywords,
                    "captured_at": l.captured_at,
                    "last_seen": l.last_seen,
                    "helpful_count": l.helpful_count,
                    "stale_count": l.stale_count,
                    "missed_review_count": l.missed_review_count,
                }
                for l in all_lessons
            ],
        }
        full_json = json.dumps(full)
        if len(full_json.encode("utf-8")) > INDEX_MAX_BYTES:
            # Trim: drop oldest archive-state lessons + lowest-score
            full["lessons"] = sorted(
                full["lessons"],
                key=lambda x: (x.get("score", 0.0), x.get("captured_at", 0)),
                reverse=True,
            )[:200]
            full_json = json.dumps(full)

        _write_atomic(storage.index_path(), full_json)

        # Build top-N (for SessionStart fast path)
        top = sorted(
            full["lessons"],
            key=lambda x: x.get("score", 0.0),
            reverse=True,
        )[:INDEX_TOP_MAX_LESSONS]
        top_doc = {
            "schema_version": 1,
            "last_updated_ts": now,
            "lessons": top,
        }
        top_json = json.dumps(top_doc)
        if len(top_json.encode("utf-8")) > INDEX_TOP_MAX_BYTES:
            # Should be rare given 50-lesson cap, but defensive
            top_doc["lessons"] = top[:25]
            top_json = json.dumps(top_doc)
        _write_atomic(storage.index_top_path(), top_json)

    finally:
        if lock_fd is not None:
            try:
                _unlock(lock_fd)
                os.close(lock_fd)
            except OSError:
                pass


def read_index_top(deadline_ts: Optional[float] = None) -> Optional[dict]:
    """Read index_top.json. Fail-open on size cap exceeded or parse failure.

    Returns dict with `lessons` key, or None if cache miss.
    """
    p = storage.index_top_path()
    if not p.exists():
        return None
    try:
        if p.stat().st_size > INDEX_TOP_MAX_BYTES * 2:
            return None  # corrupt or runaway
        if deadline_ts is not None and time.monotonic() > deadline_ts:
            return None
        text = p.read_text(encoding="utf-8")
        if deadline_ts is not None and time.monotonic() > deadline_ts:
            return None
        data = json.loads(text)
        if not isinstance(data, dict) or "lessons" not in data:
            return None
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return None
