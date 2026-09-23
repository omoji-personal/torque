"""Storage tests — L1/L2/L3 transitions + lesson lookup."""
import json
import time

from jsc_memory import storage


def _make_lesson(suffix="abc") -> storage.Lesson:
    now = int(time.time())
    # Build a 16-char id where the suffix lives at the START so id[:12]
    # differs across test lessons (filename uses id[:12]).
    full_id = (suffix + ("0" * 16))[:16]
    return storage.Lesson(
        id=full_id,
        title=f"Test {suffix}",
        short=f"short {suffix}",
        full_text=f"full {suffix}",
        confidence="MEDIUM",
        trigger="tool_error",
        captured_at=now + (abs(hash(suffix)) % 1000),
        last_seen=now,
        state="review_pending",
    )


def test_ensure_dirs_mode_0700(isolated_memory_dir):
    """Memory tree dirs must be 0o700 — operator-private (audit 2026-06-13).
    ensure_dirs also tightens a pre-existing looser dir. POSIX only: Windows
    has no equivalent mode bits."""
    import os
    import stat
    storage.ensure_dirs()
    if os.name == "nt":
        assert storage.memory_root().is_dir()
        return
    for d in (storage.spool_dir(), storage.review_queue_dir(), storage.archive_dir(),
              storage._lessons_dir(), storage.memory_root()):
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700, f"{d} not 0o700"
    # loosen one, then ensure_dirs must re-tighten it
    os.chmod(storage.spool_dir(), 0o755)
    storage.ensure_dirs()
    assert stat.S_IMODE(os.stat(storage.spool_dir()).st_mode) == 0o700


def test_write_and_list_review_pending(isolated_memory_dir):
    l = _make_lesson("aaa")
    storage.write_review_candidate(l)
    pending = storage.list_review_pending()
    assert len(pending) == 1
    assert pending[0].id == l.id


def test_promote_to_active(isolated_memory_dir):
    l = _make_lesson("bbb")
    storage.write_review_candidate(l)
    promoted = storage.promote_to_active(l.id)
    assert promoted is not None
    assert promoted.state == "active"
    assert promoted.helpful_count == 1
    assert len(storage.list_review_pending()) == 0
    assert len(storage.list_active()) == 1


def test_mark_stale_archives_at_2(isolated_memory_dir):
    l = _make_lesson("ccc")
    storage.write_review_candidate(l)
    # First stale: still pending
    storage.mark_stale(l.id)
    pending = storage.list_review_pending()
    assert len(pending) == 1
    assert pending[0].stale_count == 1
    # Second stale: archived
    storage.mark_stale(l.id)
    assert len(storage.list_review_pending()) == 0
    archive_files = list(storage.archive_dir().glob("*.json"))
    assert len(archive_files) == 1


def test_find_lesson_by_prefix(isolated_memory_dir):
    l = _make_lesson("ddd")
    storage.write_review_candidate(l)
    result = storage.find_lesson(l.id[:12])
    assert result is not None
    found_lesson, state = result
    assert found_lesson.id == l.id
    assert state == "review_pending"


def test_review_queue_size(isolated_memory_dir):
    assert storage.review_queue_size() == 0
    for s in ("a", "b", "c"):
        storage.write_review_candidate(_make_lesson(s))
    assert storage.review_queue_size() == 3


def test_load_framework_memory_compat_shim():
    """Shim returns object with empty .lessons list (compat with session_start_context)."""
    fm = storage.load_framework_memory()
    assert hasattr(fm, "lessons")
    assert fm.lessons == []
