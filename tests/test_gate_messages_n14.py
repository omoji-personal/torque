"""N14 (this group's part): a bad workspace.json names itself in the block message,
fail-closed messages carry a next step, and a scope block invites the retry the rule
now allows while a boundary block keeps the stop-and-hand-off language. Synthetic only."""
import io
import json
import os
from pathlib import Path

import pytest

from torque import gate


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("TORQUE_CLIENT", raising=False)


def make(tmp_path, workspace_json):
    base = tmp_path / "firm"
    (base / "clients" / "acme").mkdir(parents=True)
    (base / "project").mkdir()
    (base / ".torque").mkdir()
    (base / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    if workspace_json is not None:
        (base / "workspace.json").write_text(workspace_json, encoding="utf-8")
    return Path(os.path.realpath(base))


def hook(monkeypatch, capsys, root, tool, tool_input, cwd=None):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"cwd": str(cwd or root), "tool_name": tool, "tool_input": tool_input})))
    code = gate.main()
    return code, capsys.readouterr().err


# claude-A2a#8: a workspace that is build-only only because its config is wrong says so.
CONFIG_PROBLEMS = [
    ('{"schema": "torque.workspace/1", "name": "X", "ai_access": "Full"}', "not a known mode"),
    ('{"schema": "torque.workspace/1", "name": "X", "ai_access": "full-access"}', "not a known mode"),
    ('{"schema": "torque.workspace/1", "name": "X", "ai_access": "full",}', "could not be read"),
    ('{"schema": "torque.workspace/1", "name": "X", "ai_access": "connected"}', "approval"),
    (None, "workspace marker"),
]


@pytest.mark.parametrize("config,expected", CONFIG_PROBLEMS)
def test_a_wrongly_build_only_workspace_names_itself(tmp_path, monkeypatch, capsys, home, config, expected):
    root = make(tmp_path, config)
    code, err = hook(monkeypatch, capsys, root, "Bash", {"command": "sf org list"}, cwd=root / "project")
    assert code == 2, err
    assert "Note:" in err and expected in err, err
    assert "workspace.json" in err or "workspace marker" in err
    assert "ai-access full" in err


def test_a_deliberate_build_only_workspace_adds_no_config_note(tmp_path, monkeypatch, capsys, home):
    root = make(tmp_path, '{"schema": "torque.workspace/1", "name": "X", "ai_access": "build-only"}')
    code, err = hook(monkeypatch, capsys, root, "Bash", {"command": "sf org list"}, cwd=root / "project")
    assert code == 2 and "Note:" not in err, err


# claude-A2a#14: git missing/erroring gives a different fix than untracking.
def test_git_unavailable_does_not_prescribe_untracking(tmp_path, monkeypatch, home):
    root = make(tmp_path, '{"schema": "torque.workspace/1", "name": "X", "ai_access": "build-only"}')

    def no_git(workspace):
        return None  # git could not answer
    monkeypatch.setattr(gate, "clients_index_count", no_git)
    allowed, reason = gate.decide("Bash", {"command": "git log"}, root, "build-only", root)
    assert not allowed
    assert "git is on PATH" in reason and "git rm -r --cached" not in reason, reason


def test_tracked_clients_still_prescribes_untracking(tmp_path, monkeypatch, home):
    root = make(tmp_path, '{"schema": "torque.workspace/1", "name": "X", "ai_access": "build-only"}')
    monkeypatch.setattr(gate, "clients_index_count", lambda workspace: 3)
    allowed, reason = gate.decide("Bash", {"command": "git log"}, root, "build-only", root)
    assert not allowed and "git rm -r --cached clients" in reason, reason


# claude-A2a#14: an unreadable .claude/worktrees/ names the folder and a next step.
def test_unreadable_worktrees_folder_names_itself(tmp_path, monkeypatch, home):
    root = make(tmp_path, '{"schema": "torque.workspace/1", "name": "X", "ai_access": "build-only"}')
    real = Path.iterdir

    def denied(self):
        if self.name == "worktrees":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)
    monkeypatch.setattr(Path, "iterdir", denied)
    allowed, reason = gate.decide("Read", {"file_path": str(root / "project" / "a.md")}, root, "build-only", root)
    assert not allowed
    assert "worktrees" in reason and "fail closed" in reason and "permissions" in reason, reason


# Round 2 (claude-A2a#14): when the gate cannot load, the shim's message says how to
# recover outside the blocked session. It stays shell-safe (no double quote, no percent
# sign, for the shell-form hook command) and works as one argv element (the exec-form
# entry: command = interpreter, args = ["-I", "-c", shim]), and names no mode.
def test_the_load_failure_message_names_the_recovery_step():
    shim = gate.HOOK_SHIM_CODE
    assert "torque doctor" in shim and "own terminal" in shim
    assert '"' not in shim and "%" not in shim
    assert not any(word in shim.casefold() for word in ("build-only", "connected", "de-identified"))


def test_a_shim_that_cannot_import_the_gate_blocks_with_the_recovery_step():
    import subprocess
    import sys
    broken = gate.HOOK_SHIM_CODE.replace("from torque.gate import main", "from torque.gate_missing import main")
    assert broken != gate.HOOK_SHIM_CODE
    done = subprocess.run([sys.executable, "-I", "-c", broken], input="{}", capture_output=True, text=True,
                          timeout=60)
    assert done.returncode == 2, done.stderr
    assert "could not load" in done.stderr and "torque doctor" in done.stderr, done.stderr


# claude-A2a#6: a scope block invites a retry; a boundary block says to stop.
def test_a_scope_block_does_not_tell_the_assistant_to_stop(tmp_path, monkeypatch, home):
    root = make(tmp_path, '{"schema": "torque.workspace/1", "name": "X", "ai_access": "build-only"}')
    # A root-level recursive search is narrowable: it names project/ and does not append
    # the run-it-yourself handoff, and the period is not doubled.
    allowed, reason = gate.decide("Bash", {"command": "grep -r secret"}, root, "build-only", root)
    assert not allowed
    assert "project/" in reason and "Run it yourself outside the AI session" not in reason, reason
    assert ".." not in reason.replace("...", ""), reason  # no doubled period


def test_a_boundary_block_keeps_the_handoff(tmp_path, monkeypatch, home):
    root = make(tmp_path, '{"schema": "torque.workspace/1", "name": "X", "ai_access": "build-only"}')
    allowed, reason = gate.decide("Bash", {"command": "sf org list --target-org prod"}, root, "build-only", root)
    assert not allowed and "Run it yourself outside the AI session" in reason, reason
