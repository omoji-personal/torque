"""Temporary files for atomic writes: a folder that refuses new files must fail at once, never spin.

Older Python versions on Windows let tempfile.mkstemp take a permission error for a name clash and retry it up to
TMP_MAX times (about two billion there); a CI job on Python 3.10 hung for two hours that way inside
`torque workspace init`. Torque's own helpers retry only a real name clash."""
import os
from pathlib import Path

import pytest

from jsc_memory import storage
from torque import workspace as ws


def refusing_open(calls):
    real = os.open

    def fake(name, flags, mode=0o777, *args, **kwargs):
        if Path(str(name)).name.startswith((".torque-", ".lesson.md.")):
            calls.append(name)
            raise PermissionError(13, "Access is denied", str(name))
        return real(name, flags, mode, *args, **kwargs)
    return fake


def test_a_folder_that_refuses_new_files_fails_at_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(os, "open", refusing_open(calls))
    with pytest.raises(PermissionError):
        ws.exclusive_temporary(tmp_path, ".torque-")
    assert len(calls) == 1
    with pytest.raises(PermissionError):
        storage._atomic_write(tmp_path / "lesson.md", "text")
    assert len(calls) == 2


def test_a_name_clash_is_retried_and_the_file_is_new_and_exclusive(tmp_path, monkeypatch):
    names = iter(["a" * 32, "a" * 32, "b" * 32])

    class FixedUuid:
        def __init__(self):
            self.hex = next(names)
    monkeypatch.setattr(ws, "uuid4", FixedUuid)
    first, path_one = ws.exclusive_temporary(tmp_path, ".torque-")
    os.close(first)
    second, path_two = ws.exclusive_temporary(tmp_path, ".torque-")     # the first name is taken: retried
    os.close(second)
    assert Path(path_one).name == ".torque-" + "a" * 32 and Path(path_two).name == ".torque-" + "b" * 32
    if os.name != "nt":
        assert Path(path_two).stat().st_mode & 0o777 == 0o600


def test_an_atomic_write_still_replaces_the_file_and_leaves_no_temporary(tmp_path):
    target = tmp_path / "lesson.md"
    storage._atomic_write(target, "one")
    storage._atomic_write(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [p.name for p in tmp_path.iterdir()] == ["lesson.md"]
    root = ws.init_workspace(tmp_path / "w", "Synthetic")
    assert (root / "AGENTS.md").is_file()
    assert not [p for p in root.iterdir() if p.name.startswith(".torque-")]
