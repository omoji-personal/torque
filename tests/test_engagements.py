"""Initiatives: the same tracking as clients, never the client-only powers."""
import json

import pytest

from torque import engagements as eng
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    return root


def test_add_initiative_creates_binding_state_and_folders(root):
    folder = eng.add_initiative(root, "Secure Workspace", owner="lead")
    assert folder == root / "initiatives" / "secure-workspace"
    binding = json.loads((folder / "binding.json").read_text())
    assert binding["schema"] == "torque.binding/1" and binding["kind"] == "initiative"
    assert binding["slug"] == "secure-workspace" and binding["repositories"] == []
    state = json.loads((folder / "state" / "engagement.json").read_text())
    assert state["schema"] == "torque.engagement/1" and state["state"] == "active"
    assert state["name"] == "Secure Workspace" and state["owner"] == "lead"
    for name in ("sessions", "changes", "artifacts", "context", "config"):
        assert (folder / name).is_dir()
    assert (folder / "context.md").is_file()


def test_duplicate_initiative_is_refused(root):
    eng.add_initiative(root, "Plan")
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        eng.add_initiative(root, "Plan")


def test_list_engagements_covers_both_kinds(root):
    eng.add_initiative(root, "Plan")
    rows = eng.list_engagements(root)
    assert {(r["kind"], r["slug"]) for r in rows} == {("client", "alpha"), ("initiative", "plan")}
    assert [r["slug"] for r in eng.list_engagements(root, "initiative")] == ["plan"]


def test_set_state_records_history_and_rules(root):
    eng.add_initiative(root, "Plan")
    with pytest.raises(ws.WorkspaceError, match="reason"):
        eng.set_state(root, "Plan", "paused")
    config = eng.set_state(root, "Plan", "paused", reason="waiting", review_date="2026-11-01")
    assert config["state"] == "paused" and config["history"][-1]["reason"] == "waiting"
    with pytest.raises(ws.WorkspaceError, match="outcome"):
        eng.set_state(root, "Plan", "closed")
    assert eng.set_state(root, "Plan", "closed", outcome="done")["state"] == "closed"


def test_capabilities(root):
    eng.require("client", "consent")
    eng.require("initiative", "sessions")
    for capability in ("org", "consent", "approvals", "connected", "verify_deploy"):
        with pytest.raises(ws.WorkspaceError, match="clients only"):
            eng.require("initiative", capability)


def test_private_rule_and_workspace_notes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("node_modules/\n")
    root = ws.init_workspace(repo, "Synthetic firm")
    assert "/initiatives/" in (root / ".gitignore").read_text().splitlines()
    assert "initiatives/SLUG" in (root / "AGENTS.md").read_text()


def test_existing_workspace_gains_the_private_rule_on_first_initiative(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("/clients/\n/workspace.json\n/profile.md\n/.torque/\n")
    root = ws.init_workspace(repo, "Synthetic firm")
    eng.add_initiative(root, "Plan")
    assert "/initiatives/" in (root / ".gitignore").read_text().splitlines()


def test_initiative_add_respects_maintenance(root):
    (root / ws.MAINTENANCE_FLAG).write_text("x\n")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        eng.add_initiative(root, "Plan")


def test_concurrent_lifecycle_updates_keep_both_history_entries(root, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    folder = eng.add_initiative(root, "Plan")
    path = folder / "state" / "engagement.json"
    writing, release, second_started = Event(), Event(), Event()
    replace = ws._atomic_replace_text

    def hold_first_write(target, text, **kwargs):
        if target == path and json.loads(text)["state"] == "paused":
            writing.set()
            assert release.wait(10), "first writer was not released"
        return replace(target, text, **kwargs)

    def close():
        second_started.set()
        return eng.set_state(root, "Plan", "closed", outcome="complete")

    monkeypatch.setattr(ws, "_atomic_replace_text", hold_first_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(eng.set_state, root, "Plan", "paused", reason="waiting")
        try:
            assert writing.wait(10)
            second = pool.submit(close)
            assert second_started.wait(10)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.2)
        finally:
            release.set()
        assert first.result(timeout=10)["state"] == "paused"
        assert second.result(timeout=10)["state"] == "closed"
    state = eng.load_initiative(root, "Plan")[2]
    assert [entry["state"] for entry in state["history"]] == ["active", "paused", "closed"]


def test_lifecycle_rechecks_maintenance_after_waiting_for_lock(root, monkeypatch):
    from contextlib import contextmanager

    folder = eng.add_initiative(root, "Plan")
    path = folder / "state" / "engagement.json"
    original = path.read_bytes()
    lock = ws._client_creation_lock

    @contextmanager
    def maintenance_while_waiting(workspace):
        (workspace / ws.MAINTENANCE_FLAG).write_text("migration", encoding="utf-8")
        with lock(workspace):
            yield

    monkeypatch.setattr(ws, "_client_creation_lock", maintenance_while_waiting)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        eng.set_state(root, "Plan", "closed", outcome="complete")
    assert path.read_bytes() == original
