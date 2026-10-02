import json
import subprocess
import sys
import os

import pytest

from jsc_memory import storage


def candidate(number):
    return storage.Lesson(id=f"lesson-{number:05d}", title="Synthetic lesson", short="", full_text="",
                         confidence="LOW", trigger="test", captured_at=1, last_seen=1, state="review_pending")


@pytest.mark.parametrize("content", [b"{broken", b"{}", b"null", b'[{}]', b'\xff',
    b'[{"id":1,"title":"synthetic"}]', b'[{"id":"x","title":"synthetic","helpful_count":"bad"}]'])
def test_corrupt_active_state_survives_promotion(content):
    pending = storage.write_review_candidate(candidate(1))
    active = storage._lessons_dir() / "active.json"
    active.write_bytes(content)
    with pytest.raises((ValueError, OSError)):
        storage.promote_to_active(candidate(1).id)
    assert active.read_bytes() == content
    assert pending.exists()


def test_unreadable_active_state_survives_promotion(monkeypatch):
    pending = storage.write_review_candidate(candidate(1))
    active = storage._lessons_dir() / "active.json"
    active.write_text("[]", encoding="utf-8")
    original = type(active).read_text
    def read(path, *args, **kwargs):
        if path == active:
            raise PermissionError("synthetic denied read")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(active), "read_text", read)
    with pytest.raises((ValueError, OSError)):
        storage.promote_to_active(candidate(1).id)
    assert active.read_bytes() == b"[]"
    assert pending.exists()


def test_failed_publication_preserves_pending_and_active_and_releases_lock(monkeypatch):
    storage.write_active([candidate(0)])
    pending = storage.write_review_candidate(candidate(1))
    active = storage._lessons_dir() / "active.json"
    before = active.read_bytes()
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("synthetic publication failure")
        patch.setattr(os, "replace", fail)
        with pytest.raises(OSError):
            storage.promote_to_active(candidate(1).id)
    assert active.read_bytes() == before and pending.exists()
    storage.promote_to_active(candidate(1).id)
    assert len(storage.list_active()) == 2


def test_concurrent_promotions_and_score_updates_are_not_lost(isolated_memory_dir):
    for number in range(4):
        storage.write_review_candidate(candidate(number))
    script = """
import sys, time
from jsc_memory import storage
read = storage.list_active
def slow_read():
    result = read()
    time.sleep(0.02)
    return result
storage.list_active = slow_read
print("ready", flush=True)
sys.stdin.readline()
for _ in range(5):
    storage.promote_to_active(sys.argv[1])
"""
    env = {**os.environ, "JSC_MEMORY_DIR": str(isolated_memory_dir)}
    env.pop("TORQUE_WORKSPACE", None)
    workers = [subprocess.Popen([sys.executable, "-c", script, candidate(number).id],
               env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               text=True, encoding="utf-8") for number in range(4)]
    try:
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        for worker in workers:
            output, error = worker.communicate(timeout=30)
            assert worker.returncode == 0, error
            assert output.strip() == "ready"
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=10)
    active = storage.list_active()
    assert {lesson.id for lesson in active} == {candidate(i).id for i in range(4)}
    assert [lesson.helpful_count for lesson in active] == [5] * 4
    assert storage.list_review_pending() == []
