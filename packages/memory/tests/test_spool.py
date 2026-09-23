"""Spool tests — atomic append + recovery + lease + quarantine."""
import json
import subprocess
import sys
import os
import time
from pathlib import Path

from jsc_memory import spool, storage


def test_append_creates_spool(isolated_memory_dir):
    spool.append_event("session-1", {"ts": 1.0, "tool": "Bash", "exit": 0})
    spool_path = spool.spool_path_for_session("session-1")
    assert spool_path.exists()
    lines = spool_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["tool"] == "Bash"


def test_append_creates_spool_mode_0600(isolated_memory_dir):
    """Spool must be 0o600 — it transiently holds raw Bash commands before the
    Stop hook scrubs them into L1 candidates (audit 2026-06-09 LESSON-1).
    POSIX only: Windows has no equivalent mode bits or fchmod."""
    if os.name == "nt":
        spool.append_event("session-mode", {"ts": 1.0, "tool": "Bash", "input": "echo secret"})
        assert spool.spool_path_for_session("session-mode").exists()
        return
    import stat
    spool.append_event("session-mode", {"ts": 1.0, "tool": "Bash", "input": "echo secret"})
    spool_path = spool.spool_path_for_session("session-mode")
    assert stat.S_IMODE(os.stat(spool_path).st_mode) == 0o600
    # fchmod tightens a pre-existing looser file on the next append
    os.chmod(spool_path, 0o644)
    spool.append_event("session-mode", {"ts": 2.0, "tool": "Bash"})
    assert stat.S_IMODE(os.stat(spool_path).st_mode) == 0o600


def test_append_atomic_multiple_events(isolated_memory_dir):
    for i in range(10):
        spool.append_event("session-2", {"ts": float(i), "tool": "Bash", "i": i})
    events = spool.read_events("session-2")
    assert len(events) == 10
    assert all(e["i"] == i for i, e in enumerate(events))


def test_delete_spool(isolated_memory_dir):
    spool.append_event("session-3", {"ts": 1.0})
    spool.delete_spool("session-3")
    assert not spool.spool_path_for_session("session-3").exists()


def test_lease_acquire_first_succeeds(isolated_memory_dir):
    storage.ensure_dirs()
    spool_path = spool.spool_path_for_session("orphan")
    spool_path.write_text('{"ts": 1.0}\n', encoding="utf-8")
    fd = spool.acquire_lease(spool_path)
    assert fd is not None
    spool.release_lease(spool_path, fd)


def _try_lease_module_fn(args):
    """Module-level function (must be pickleable for multiprocessing)."""
    spool_path_str, _ = args
    from pathlib import Path
    fd = spool.acquire_lease(Path(spool_path_str))
    return 1 if fd is not None else 0


def test_lease_concurrent_only_one_wins(isolated_memory_dir):
    """Race-free lease: 4 processes scan same orphan, exactly 1 acquires."""
    storage.ensure_dirs()
    spool_path = spool.spool_path_for_session("race")
    spool_path.write_text('{"ts": 1.0}\n', encoding="utf-8")

    worker = "from pathlib import Path; import sys; from jsc_memory.spool import acquire_lease; print(1 if acquire_lease(Path(sys.argv[1])) is not None else 0)"
    procs = [subprocess.Popen([sys.executable, "-c", worker, str(spool_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    results = []
    try:
        for proc in procs:
            out, err = proc.communicate(timeout=15)
            assert proc.returncode == 0, err
            results.append(int(out.strip()))
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
    assert sum(results) == 1, f"Expected exactly 1 winner, got {sum(results)}: {results}"


def test_scan_orphaned_spools(isolated_memory_dir):
    storage.ensure_dirs()
    # Create an old spool
    old_spool = storage.spool_dir() / "old.jsonl"
    old_spool.write_text('{"ts": 1.0}\n', encoding="utf-8")
    # Backdate it
    old_ts = time.time() - 7200  # 2h old
    os.utime(old_spool, (old_ts, old_ts))
    # And a fresh one
    fresh_spool = storage.spool_dir() / "fresh.jsonl"
    fresh_spool.write_text('{"ts": 1.0}\n', encoding="utf-8")

    orphans = list(spool.scan_orphaned_spools(min_age_seconds=3600))
    orphan_names = {p.name for p in orphans}
    assert "old.jsonl" in orphan_names
    assert "fresh.jsonl" not in orphan_names


def test_quarantine_spool(isolated_memory_dir):
    storage.ensure_dirs()
    spool_path = spool.spool_path_for_session("bad")
    spool_path.write_text('{"ts": 1.0}\n', encoding="utf-8")
    quarantined = spool.quarantine_spool(spool_path)
    assert quarantined is not None
    assert "_quarantine" in str(quarantined)
    assert not spool_path.exists()


def test_age_out_quarantine(isolated_memory_dir):
    storage.ensure_dirs()
    quarantine = storage.spool_dir() / "_quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    # Old quarantined file
    old_q = quarantine / "ancient.jsonl"
    old_q.write_text('{"ts": 1.0}\n', encoding="utf-8")
    old_ts = time.time() - (10 * 86400)  # 10 days old
    os.utime(old_q, (old_ts, old_ts))
    # Fresh quarantined
    fresh_q = quarantine / "recent.jsonl"
    fresh_q.write_text('{"ts": 1.0}\n', encoding="utf-8")

    deleted = spool.age_out_quarantine(max_age_days=7)
    assert deleted == 1
    assert not old_q.exists()
    assert fresh_q.exists()


def test_enforce_spool_dir_cap(isolated_memory_dir):
    storage.ensure_dirs()
    for i in range(60):
        p = storage.spool_dir() / f"s{i:03d}.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        # Stagger mtimes
        ts = time.time() - (60 - i) * 100
        os.utime(p, (ts, ts))
    dropped = spool.enforce_spool_dir_cap(max_files=50)
    assert dropped == 10
    remaining = list(storage.spool_dir().glob("*.jsonl"))
    assert len(remaining) == 50
