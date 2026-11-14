"""Scopes are folders; files are written whole; one lock; bad files are skipped and named."""
import json
import os
import subprocess
import sys

import pytest

from jsc_memory import lesson as record
from jsc_memory import storage
from jsc_memory.lesson import Lesson


def make(folder, number, scope="client", **fields):
    item = Lesson(id=f"L-20250303-{number:04x}", title=fields.pop("title", f"Lesson {number}"), scope=scope,
                  state=fields.pop("state", "active"), created="2026-11-14", **fields)
    storage.save(item, folder)
    return item


def test_a_folder_selects_its_scopes_and_nothing_else(workspace, scopes):
    alpha, firm, playbook = scopes("alpha"), scopes(None), scopes("playbook")
    assert (alpha.kind, alpha.slug, alpha.workspace) == ("client", "alpha", workspace)
    assert alpha.folder("client") == workspace / "clients" / "alpha" / "lessons"
    assert alpha.folder("firm") == workspace / "lessons" and alpha.folder("platform") is None
    assert alpha.folder("initiative") is None            # a client is selected, not an initiative
    assert (firm.engagement, firm.default_scope(), firm.label()) == (None, None, "firm + platform")
    assert (playbook.kind, playbook.default_scope()) == ("initiative", "initiative")
    assert alpha.label() == "client alpha + firm + platform"
    # A folder that is neither a workspace nor an engagement in one selects nothing.
    for stray in (workspace / "clients", workspace / "clients" / "missing", workspace.parent):
        assert storage.scopes_for(stray) == storage.Scopes()
    assert storage.Scopes().label() == "platform"


def test_the_environment_selects_scopes_at_call_time(workspace, monkeypatch):
    monkeypatch.setenv("JSC_ROOT", str(workspace / "clients" / "beta"))      # legacy: never a selection
    assert storage.scopes_from_env() == storage.Scopes()
    for slug in ("alpha", "beta"):
        monkeypatch.setenv("TORQUE_WORKSPACE", str(workspace / "clients" / slug))
        assert storage.scopes_from_env().slug == slug


def test_loading_reads_the_selected_engagement_and_the_firm_and_never_another_client(workspace, scopes):
    make(scopes("alpha").folder("client"), 1)
    make(scopes("beta").folder("client"), 2)
    make(scopes(None).folder("firm"), 3, scope="firm")
    make(scopes("playbook").folder("initiative"), 4, scope="initiative")
    ids = lambda selected: sorted(item.id[-4:] for item in storage.load(selected))
    assert ids(scopes("alpha")) == ["0001", "0003"]
    assert ids(scopes("beta")) == ["0002", "0003"]
    assert ids(scopes("playbook")) == ["0003", "0004"]
    assert ids(scopes(None)) == ["0003"]
    assert ids(storage.Scopes()) == []


def test_an_unreadable_file_is_skipped_and_named_and_the_rest_still_loads(workspace, scopes):
    alpha = scopes("alpha")
    folder = alpha.folder("client")
    good = make(folder, 1)
    (folder / "broken.md").write_text("---\nid: [unclosed\n---\n", encoding="utf-8")
    (folder / "no-front-matter.md").write_text("just a note\n", encoding="utf-8")
    (folder / "binary.md").write_bytes(b"\xff\xfe\x00junk")
    (folder / "wrong-scope.md").write_text(record.render(Lesson(id="L-20250303-0009", title="t", scope="firm")),
                                           encoding="utf-8")
    (folder / "copy.md").write_text(record.render(good), encoding="utf-8")
    (folder / "huge.md").write_bytes(b"x" * (storage.MAX_BYTES + 1))
    (folder / "README.md").write_text("Notes about this folder.\n", encoding="utf-8")
    problems = []
    loaded = storage.load(alpha, problems)
    assert [item.id for item in loaded] == [good.id]
    named = {problem["path"]: problem["reason"] for problem in problems}
    assert set(named) == {f"clients/alpha/lessons/{name}" for name in
                          ("broken.md", "no-front-matter.md", "binary.md", "wrong-scope.md", "copy.md", "huge.md")}
    assert "not valid YAML" in named["clients/alpha/lessons/broken.md"]
    assert "scope says firm" in named["clients/alpha/lessons/wrong-scope.md"]
    assert "already used" in named["clients/alpha/lessons/copy.md"]
    assert named["clients/alpha/lessons/binary.md"] == "not UTF-8 text"


def test_a_linked_lessons_folder_is_not_followed(workspace, scopes):
    make(scopes("beta").folder("client"), 2)
    try:
        (workspace / "clients" / "alpha" / "lessons").symlink_to(workspace / "clients" / "beta" / "lessons",
                                                                target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    problems = []
    assert storage.load(scopes("alpha"), problems) == []
    assert "symbolic link" in problems[0]["reason"]


def test_an_id_resolves_exactly_or_by_a_unique_start(scopes):
    folder = scopes("alpha").folder("client")
    for suffix in ("a3f2", "a3f9", "b111"):
        storage.save(Lesson(id=f"L-20250303-{suffix}", title=suffix, created="2026-11-14"), folder)
    lessons = storage.load(scopes("alpha"))
    assert storage.resolve(lessons, "L-20250303-a3f2").title == "a3f2"
    assert storage.resolve(lessons, "L-20250303-b").title == "b111"
    assert storage.resolve(lessons, "20250303-b1").title == "b111"       # without the L-
    with pytest.raises(storage.Ambiguous) as caught:
        storage.resolve(lessons, "L-20250303-a3f")
    assert [item.title for item in caught.value.candidates] == ["a3f2", "a3f9"]
    for nothing in ("", "   ", "L-", "L-20"):
        with pytest.raises(storage.Ambiguous):
            storage.resolve(lessons, nothing)
    with pytest.raises(storage.NotFound):
        storage.resolve(lessons, "L-20991231-ffff")


def test_events_go_to_the_scope_root_and_counters_come_from_them(workspace, scopes):
    alpha = scopes("alpha")
    assert alpha.event_root("client") == workspace / "clients" / "alpha"
    assert alpha.event_root("firm") == alpha.event_root("platform") == workspace
    assert storage.Scopes().event_root("platform") is None
    assert storage.log(alpha.event_root("client"), [{"event": "shown", "id": "L-1", "via": "for"}] * 3)
    storage.log(alpha.event_root("client"), [{"event": "helpful", "id": "L-1"},
                                             {"event": "stale", "id": "L-1", "reason": "fixed in the org"}])
    storage.log(alpha.event_root("firm"), [{"event": "shown", "id": "L-2", "via": "context"}])
    with open(storage.events_path(alpha.engagement), "a", encoding="utf-8") as stream:
        stream.write("{not json\n" + json.dumps({"event": "unknown", "id": "L-1", "at": "x"}) + "\n")
    tallies = storage.tally(storage.events_for(alpha))
    assert tallies["L-1"].counters() == {"shown": 3, "helpful": 1, "stale": 1} and tallies["L-1"].score == 0
    assert tallies["L-1"].stale_mark["reason"] == "fixed in the org"
    assert tallies["L-2"].shown == 1
    # Another client reads the firm's events and none of this client's.
    assert set(storage.tally(storage.events_for(scopes("beta")))) == {"L-2"}
    storage.log(alpha.engagement, [{"event": "verified", "id": "L-1"}])
    assert storage.tally(storage.events_for(alpha))["L-1"].stale_mark is None
    text = storage.events_path(alpha.engagement).read_text(encoding="utf-8")
    assert "fixed in the org" in text and '"via": "for"' in text


def test_showing_a_lesson_never_fails_when_the_log_cannot_be_written(scopes, monkeypatch):
    alpha = scopes("alpha")
    monkeypatch.setattr(storage, "events_path", lambda root: root / "missing-folder" / "deeper" / "events.jsonl")
    assert storage.log(alpha.engagement, [{"event": "shown", "id": "L-1"}]) is False
    with pytest.raises(OSError):
        storage.log(alpha.engagement, [{"event": "helpful", "id": "L-1"}], must=True)
    assert storage.log(None, [{"event": "shown", "id": "L-1"}]) is False


def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(scopes, monkeypatch):
    folder = scopes("alpha").folder("client")
    item = make(folder, 1, title="Before")
    before = item.path.read_bytes()
    item.title = "After"
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("synthetic failure")))
        with pytest.raises(OSError):
            storage.save(item, folder)
    assert item.path.read_bytes() == before
    assert [path.name for path in folder.iterdir()] == [item.path.name]


def test_a_lock_held_elsewhere_is_reported_after_a_bounded_wait_on_every_system(scopes, monkeypatch):
    root = scopes("alpha").engagement
    script = ("import sys, time\nfrom pathlib import Path\nfrom jsc_memory import storage\n"
              "with storage.transaction(Path(sys.argv[1])):\n    print('held', flush=True)\n    sys.stdin.readline()\n")
    holder = subprocess.Popen([sys.executable, "-c", script, str(root)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert holder.stdout.readline().strip() == "held"
        monkeypatch.setattr(storage, "LOCK_SECONDS", 0.3)
        with pytest.raises(storage.Busy, match="Nothing was written"):
            with storage.transaction(root):
                pytest.fail("the lock was taken twice")
    finally:
        holder.communicate("release\n", timeout=30)
    with storage.transaction(root):       # free again once the holder is gone
        with storage.transaction(root):   # and re-entrant within one process
            pass


def test_concurrent_writers_lose_no_event_and_no_lesson(scopes):
    alpha = scopes("alpha")
    script = ("import sys\nfrom pathlib import Path\nfrom jsc_memory import storage\nfrom jsc_memory.lesson import Lesson\n"
              "root, worker = Path(sys.argv[1]), sys.argv[2]\nprint('ready', flush=True)\nsys.stdin.readline()\n"
              "for number in range(25):\n"
              "    storage.log(root, [{'event': 'helpful', 'id': 'L-shared'}], must=True)\n"
              "    storage.save(Lesson(id=f'L-20250303-{worker}{number:03d}', title='t', created='2026-11-14'), root / 'lessons')\n")
    workers = [subprocess.Popen([sys.executable, "-c", script, str(alpha.engagement), str(number)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                encoding="utf-8") for number in range(4)]
    try:
        for worker in workers:
            assert worker.stdout.readline().strip() == "ready"
        for worker in workers:
            worker.stdin.write("go\n")
            worker.stdin.flush()
        for worker in workers:
            _, error = worker.communicate(timeout=60)
            assert worker.returncode == 0, error
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=10)
    assert storage.tally(storage.events_for(alpha))["L-shared"].helpful == 100
    problems = []
    assert len(storage.load(alpha, problems)) == 100 and problems == []
