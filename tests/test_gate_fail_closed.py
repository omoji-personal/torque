"""The protected-record check and a failure inside it. In full mode the check is
best effort: an error lets the call through, as documented. In build-only and
connected mode an error blocks the call with a reason (fail closed)."""
import io
import json
import os
from pathlib import Path

import pytest

from torque import gate, gate_connected as gc, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")
CALLS = [("Bash", {"command": "ls project"}), ("PowerShell", {"command": "Get-ChildItem project"}),
         ("Write", {"file_path": "project/notes.md", "content": "x"}), ("mcp__shell__run", {"command": "ls project"})]


def boom(*_args, **_kwargs):
    raise RuntimeError("synthetic failure")


def make(tmp_path, mode):
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    (Path(root) / "project").mkdir(exist_ok=True)
    if mode == "connected":
        ws.set_ai_access(root, "connected", approval="required", presence=YES)
    elif mode == "build-only":
        ws.set_ai_access(root, "build-only")
    return Path(os.path.realpath(root))


def hook(monkeypatch, capsys, root, tool, tool_input):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("TORQUE_CLIENT", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "tool_name": tool,
                                                             "tool_input": tool_input})))
    code = gate.main()
    return code, capsys.readouterr().err


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


@pytest.mark.parametrize("tool,tool_input", CALLS)
def test_build_only_blocks_when_the_record_check_fails(tmp_path, monkeypatch, home, tool, tool_input):
    root = make(tmp_path, "build-only")
    assert gate.decide(tool, tool_input, root, "build-only", root) == (True, "")
    monkeypatch.setattr(gate, "_approval_file_targets_reason", boom)
    allowed, reason = gate.decide(tool, tool_input, root, "build-only", root)
    assert not allowed
    assert "RuntimeError: synthetic failure" in reason and "blocked to fail closed" in reason
    assert "consent, approval and control records" in reason and "outside the AI session" in reason


@pytest.mark.parametrize("tool,tool_input", CALLS)
def test_connected_mode_blocks_when_the_record_check_fails(tmp_path, monkeypatch, home, tool, tool_input):
    root = make(tmp_path, "connected")
    env = {"TORQUE_CLIENT": "acme"}
    assert gc.decide_connected(tool, tool_input, root, root, env=env).action != "deny"
    monkeypatch.setattr(gate, "_approval_file_targets_reason", boom)
    decision = gc.decide_connected(tool, tool_input, root, root, env=env)
    assert decision.action == "deny"
    assert decision.reason.startswith("Connected mode: ") and "blocked to fail closed" in decision.reason


@pytest.mark.parametrize("mode", ["build-only", "connected"])
def test_the_hook_exits_2_with_the_reason(tmp_path, monkeypatch, capsys, home, mode):
    root = make(tmp_path, mode)
    monkeypatch.setattr(gate, "_approval_file_targets_reason", boom)
    code, err = hook(monkeypatch, capsys, root, "Bash", {"command": "ls project"})
    assert code == 2 and "synthetic failure" in err and "fail closed" in err


def test_full_mode_still_lets_the_call_through(tmp_path, monkeypatch, capsys, home):
    # Documented in docs/ai-access.md: the full-mode check never blocks by failing.
    root = make(tmp_path, "full")
    code, err = hook(monkeypatch, capsys, root, "Bash", {"command": "rm clients/acme/consent.json"})
    assert code == 2 and "consent" in err
    monkeypatch.setattr(gate, "_approval_file_targets_reason", boom)
    for tool, tool_input in [*CALLS, ("Bash", {"command": "rm clients/acme/consent.json"})]:
        assert hook(monkeypatch, capsys, root, tool, tool_input) == (0, ""), tool
    assert gate._approval_file_reason("Bash", {"command": "ls"}, root) == ""
    assert "fail closed" in gate._approval_file_reason("Bash", {"command": "ls"}, root, strict=True)


def test_a_spent_budget_inside_the_check_blocks_as_a_budget_overrun(tmp_path, monkeypatch, home):
    root = make(tmp_path, "build-only")

    def spent(*_args, **_kwargs):
        raise gate.BudgetExceeded("globs in this call match more than 10000 paths")
    monkeypatch.setattr(gate, "_approval_file_targets_reason", spent)
    allowed, reason = gate.decide("Bash", {"command": "ls project"}, root, "build-only", root)
    assert not allowed and "could not be checked in time" in reason
    # Full mode: best effort, as before.
    assert gate._approval_file_reason("Bash", {"command": "ls project"}, root) == ""
    with pytest.raises(gate.BudgetExceeded):
        gate._approval_file_reason("Bash", {"command": "ls project"}, root, strict=True)


def test_workspaces_that_cannot_be_read_block_a_write_after_an_unknown_cd(tmp_path, monkeypatch, home):
    root = make(tmp_path, "build-only")

    def unreadable(_start):
        raise PermissionError(13, "Permission denied")
    command = {"command": 'cd "$SOMEWHERE" && rm -rf notes'}
    monkeypatch.setattr(gate, "_workspace_chain", unreadable)
    # Full mode keeps its answer: no workspace known, nothing to protect.
    assert gate._workspace_roots(root) == []
    assert gate._approval_file_reason("Bash", command, root) == ""
    reason = gate._approval_file_reason("Bash", command, root, strict=True)
    assert "PermissionError" in reason and "blocked to fail closed" in reason
    with pytest.raises(PermissionError):
        gate._workspace_roots(root, strict=True)


def test_worktree_copies_that_cannot_be_listed_block_the_call(tmp_path, monkeypatch, home):
    root = make(tmp_path, "build-only")
    # No .claude/worktrees folder means no copies, and so does a file by that name.
    assert gate._worktree_copies(root) == []
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "worktrees").write_text("not a folder", encoding="utf-8")
    assert gate._worktree_copies(root) == []
    assert gate.decide("Read", {"file_path": str(root / "project" / "a.md")}, root, "build-only", root) == (True, "")

    real = Path.iterdir

    def denied(self):
        if self.name == "worktrees":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)
    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(PermissionError):
        gate._worktree_copies(root)


def test_the_hook_blocks_when_worktree_copies_cannot_be_listed(tmp_path, monkeypatch, capsys, home):
    root = make(tmp_path, "build-only")
    real = Path.iterdir

    def denied(self):
        if self.name == "worktrees":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self)
    monkeypatch.setattr(Path, "iterdir", denied)
    code, err = hook(monkeypatch, capsys, root, "Read", {"file_path": str(root / "project" / "a.md")})
    assert code == 2 and "fail closed" in err
