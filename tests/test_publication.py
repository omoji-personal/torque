"""Publication durability: directory fsync and mode-preserving replace."""
import os
import stat
from pathlib import Path

import pytest

from torque import workspace as ws


def test_create_only_syncs_the_directory(tmp_path, monkeypatch):
    synced = []
    monkeypatch.setattr(ws, "_fsync_dir", lambda d: synced.append(Path(d)))
    ws.atomic_write_new(tmp_path / "a.txt", "one\n")
    assert synced == [tmp_path]


def test_replace_syncs_the_directory(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    target.write_text("one\n")
    synced = []
    monkeypatch.setattr(ws, "_fsync_dir", lambda d: synced.append(Path(d)))
    ws._atomic_replace_text(target, "two\n")
    assert target.read_text() == "two\n" and synced == [tmp_path]


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_replace_keeps_the_existing_mode(tmp_path):
    target = tmp_path / "shared.json"
    target.write_text("{}\n")
    target.chmod(0o660)
    ws._atomic_replace_text(target, '{"a": 1}\n', keep_mode=True)
    assert stat.S_IMODE(target.stat().st_mode) == 0o660


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_replace_without_keep_mode_resets_to_a_private_file(tmp_path):
    """Fix round 1 finding 1: preservation must be opt-in. A caller that manages
    its own mode afterward (set_ai_access, _connected_rule) must not have an
    existing 0644 file's mode silently carried forward into the replacement;
    the default replace is a fresh, private (mkstemp 0600) file, exactly as
    before durable-publication work touched this function."""
    target = tmp_path / "workspace.json"
    target.write_text("{}\n")
    target.chmod(0o644)
    ws._atomic_replace_text(target, '{"a": 1}\n')
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_create_only_still_refuses_an_existing_file(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("keep\n")
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        ws.atomic_write_new(target, "replace\n")
    assert target.read_text() == "keep\n"
