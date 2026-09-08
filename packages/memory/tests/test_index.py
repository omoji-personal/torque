"""Index tests — atomic write + fcntl lock + concurrency."""
import json
import subprocess
import sys
import os
import time

from jsc_memory import index, storage


def test_rebuild_creates_index(isolated_memory_dir):
    # Add a lesson first
    storage.write_review_candidate(storage.Lesson(
        id="aaaa11111111aaaa", title="t", short="s", full_text="f",
        confidence="MEDIUM", trigger="tool_error",
        captured_at=1000, last_seen=1000, state="review_pending",
    ))
    index.rebuild_index()
    assert storage.index_path().exists()
    assert storage.index_top_path().exists()
    data = json.loads(storage.index_top_path().read_text())
    assert "lessons" in data
    assert len(data["lessons"]) == 1


def test_index_top_size_cap(isolated_memory_dir):
    # Add many lessons; verify index_top stays small
    for i in range(60):
        storage.write_review_candidate(storage.Lesson(
            id=f"l{i:013d}aaa"[:16], title=f"t{i}", short="s", full_text="f",
            confidence="MEDIUM", trigger="tool_error",
            captured_at=1000 + i, last_seen=1000 + i, state="review_pending",
        ))
    index.rebuild_index()
    top_size = storage.index_top_path().stat().st_size
    # 50 lesson cap → file should be small
    assert top_size < 50 * 1024


def test_read_index_top_returns_none_if_missing(isolated_memory_dir):
    # No rebuild yet
    result = index.read_index_top()
    assert result is None


def _rebuild_for_pool(memory_dir):
    """Module-level function (pickleable for multiprocessing.Pool)."""
    import os, importlib
    os.environ["JSC_MEMORY_DIR"] = memory_dir
    import jsc_memory.storage as _s
    import jsc_memory.index as _idx
    importlib.reload(_s)
    importlib.reload(_idx)
    _idx.rebuild_index()
    return 1


def test_concurrent_rebuild_no_corruption(isolated_memory_dir):
    """Multiple processes rebuilding index simultaneously shouldn't corrupt."""
    # Pre-seed lessons
    for i in range(5):
        storage.write_review_candidate(storage.Lesson(
            id=f"con{i:013d}"[:16], title=f"t{i}", short="s", full_text="f",
            confidence="MEDIUM", trigger="tool_error",
            captured_at=1000 + i, last_seen=1000 + i, state="review_pending",
        ))

    env = {**os.environ, "JSC_MEMORY_DIR": str(isolated_memory_dir)}
    procs = [subprocess.Popen([sys.executable, "-c", "from jsc_memory.index import rebuild_index; rebuild_index()"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
    try:
        for proc in procs:
            out, err = proc.communicate(timeout=15)
            assert proc.returncode == 0, err.decode()
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    # Index should still be valid JSON with all 5 lessons
    data = json.loads(storage.index_top_path().read_text())
    assert "lessons" in data
    assert len(data["lessons"]) == 5
