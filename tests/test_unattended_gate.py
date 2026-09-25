import io
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, MODEL, ORGS, WRITE, as_agent, delegated_grant, delegated_workspace,
                               flow_request, launched)
from torque import approval, changes, gate, gate_connected, permissions

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def hook(monkeypatch, capsys, root, command):
    event = {"hook_event_name": "PreToolUse", "cwd": str(root), "session_id": "s1", "tool_use_id": "t-u1",
             "permission_mode": "default", "tool_name": "Bash", "tool_input": {"command": command}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    return gate.main(), capsys.readouterr()


def unattended_root(tmp_path, monkeypatch):
    # write_settings' delegated path needs root_owner=FAKE_OWNER in a single-uid test
    # environment (D3's fix round 1, controller ruling R41): not in this task's brief
    # Interfaces text, landed after it was written, same gap D3/D5 fixed the same way.
    root = delegated_workspace(tmp_path, monkeypatch)
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    return root


def test_approved_write_gets_an_explicit_allow(tmp_path, monkeypatch, capsys):
    root = unattended_root(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    code, out = hook(monkeypatch, capsys, root, shlex.join(WRITE))
    decision = json.loads(out.out)["hookSpecificOutput"]
    assert code == 0 and decision["permissionDecision"] == "allow" and record["id"] in decision["permissionDecisionReason"]


def test_unapproved_write_is_denied_without_a_prompt(tmp_path, monkeypatch, capsys):
    root = unattended_root(tmp_path, monkeypatch)
    flow_request(root)
    launched(monkeypatch, root)
    code, out = hook(monkeypatch, capsys, root, shlex.join(WRITE))
    assert code == 2 and out.out == ""


def test_unverifiable_still_asks_as_a_backstop(tmp_path, monkeypatch, capsys):
    root = unattended_root(tmp_path, monkeypatch)
    launched(monkeypatch, root)
    code, out = hook(monkeypatch, capsys, root, "python3 x.py")
    assert code == 0 and json.loads(out.out)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_interactive_profile_output_is_unchanged(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    delegated_grant(root, flow_request(root))
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    code, out = hook(monkeypatch, capsys, root, shlex.join(WRITE))
    assert code == 0 and out.out == ""


def test_a_sidecar_without_delegation_is_ignored(tmp_path, monkeypatch, capsys):
    root = unattended_root(tmp_path, monkeypatch)
    config = json.loads((root / "workspace.json").read_text())
    config["delegates"].pop("approver")
    (root / "workspace.json").write_text(json.dumps(config))
    assert gate_connected.unattended(root) is False


# Fix round 1 (controller review): gate-level coverage for the explicit-allow guard
# itself, so that deleting `and worst.approved` in gate.py, or weakening the
# allow-only condition in decide_connected, is caught by a test.

def test_a_read_or_local_call_gets_no_explicit_allow(tmp_path, monkeypatch, capsys):
    root = unattended_root(tmp_path, monkeypatch)
    launched(monkeypatch, root)
    code, out = hook(monkeypatch, capsys, root, "ls")
    assert code == 0 and out.out == ""


def test_a_reset_sidecar_gets_no_explicit_allow_even_for_an_approved_write(tmp_path, monkeypatch, capsys):
    root = unattended_root(tmp_path, monkeypatch)
    delegated_grant(root, flow_request(root))
    (root / permissions.PROFILE_FILE).write_text("{}", encoding="utf-8")
    assert gate_connected.unattended(root) is False
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    code, out = hook(monkeypatch, capsys, root, shlex.join(WRITE))
    assert code == 0 and out.out == ""


def browser_grant(root, org="acme-dev"):
    cid = changes.create_change(root, "Acme", "Layout change", "Add Tier to the Case layout", [], org)["id"]
    req = approval.create_request(root, "Acme", cid, org, browser_minutes=10,
                                  purpose="Add Tier to the Case layout", resolve=ORGS.get)
    return delegated_grant(root, req)


def test_a_granted_browser_window_gets_an_explicit_allow_naming_the_window(tmp_path, monkeypatch, capsys):
    root = unattended_root(tmp_path, monkeypatch)
    window = browser_grant(root)
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    code, out = hook(monkeypatch, capsys, root, "torque browser run --target-org acme-dev")
    decision = json.loads(out.out)["hookSpecificOutput"]
    assert (code == 0 and decision["permissionDecision"] == "allow"
            and window["id"] in decision["permissionDecisionReason"])


# Minor 1 (controller review): the explicit allow must be tied to the root(s) whose
# own decision actually carries the approval id, not to "any connected root is
# unattended". Real stacked workspaces are not used here: a second, genuinely
# unrelated connected root would simply deny the call as unbound (its own
# TORQUE_CLIENT/TORQUE_LAUNCH never verify for a different workspace root), which
# would mask the very bug under test. `decide_connected` is faked per root instead,
# so the two roots' Decision objects are controlled directly: one root actually
# approved this call (and is not unattended), the other is unattended but did not
# approve anything.

def test_only_an_approving_root_being_unattended_triggers_the_explicit_allow(tmp_path, monkeypatch, capsys):
    attended = delegated_workspace(tmp_path / "attended", monkeypatch)  # no sidecar written: stays interactive
    unattended = unattended_root(tmp_path / "unattended", monkeypatch)
    assert gate_connected.unattended(attended) is False and gate_connected.unattended(unattended) is True
    decisions = {attended: gate_connected.Decision("allow", "", "apr-from-attended"),
                 unattended: gate_connected.Decision("allow", "", None)}
    monkeypatch.setattr(gate, "_connected_workspaces", lambda cwd, tool_input: [attended, unattended])
    monkeypatch.setattr(gate_connected, "decide_connected",
                        lambda tool_name, tool_input, workspace, cwd, **kw: decisions[workspace])
    code, out = hook(monkeypatch, capsys, attended, "ls")
    assert code == 0 and out.out == ""


def test_a_non_approving_tie_break_winner_never_borrows_another_roots_approval(tmp_path, monkeypatch, capsys):
    """`worst` is picked by `max(results, ...)`, which keeps the first "allow" seen on
    a tie. When that first (`worst`) decision itself carries no approval, the call must
    stay silent even though a later root in the same list did use one and is unattended:
    printing would have to fall back to `worst.approved` (None) for the reason text,
    an "approval None was used" message that names no real approval. This is the case
    `and worst.approved` (kept as the outer guard, alongside the `approving`/`all(...)`
    scoping added above) protects against."""
    non_approving = delegated_workspace(tmp_path / "non-approving", monkeypatch)  # not unattended; irrelevant here
    approving_unattended = unattended_root(tmp_path / "approving-unattended", monkeypatch)
    decisions = {non_approving: gate_connected.Decision("allow", "", None),
                 approving_unattended: gate_connected.Decision("allow", "", "apr-from-the-other-root")}
    monkeypatch.setattr(gate, "_connected_workspaces",
                        lambda cwd, tool_input: [non_approving, approving_unattended])
    monkeypatch.setattr(gate_connected, "decide_connected",
                        lambda tool_name, tool_input, workspace, cwd, **kw: decisions[workspace])
    code, out = hook(monkeypatch, capsys, non_approving, "ls")
    assert code == 0 and out.out == ""


# Fix round 2 (controller ruling R51): in connected mode, a file-write tool
# (Write/Edit/MultiEdit/NotebookEdit -- every PATH_TOOLS tool) targeting
# `.claude/torque-permissions.json` must be denied exactly as a Bash write to it
# already is. `decide_connected` calls `gate._decide(...)` before any of its own
# org-specific routing, so these go straight through that shared path.

def test_write_to_the_sidecar_is_denied_through_decide_connected(tmp_path, monkeypatch):
    root = unattended_root(tmp_path, monkeypatch)
    target = str(root / permissions.PROFILE_FILE)
    decision = gate_connected.decide_connected("Write", {"file_path": target, "content": "{}"}, root, root,
                                               env={"TORQUE_CLIENT": "acme"})
    assert decision.action == "deny"


def test_edit_to_the_sidecar_is_denied_through_decide_connected(tmp_path, monkeypatch):
    root = unattended_root(tmp_path, monkeypatch)
    target = str(root / permissions.PROFILE_FILE)
    decision = gate_connected.decide_connected(
        "Edit", {"file_path": target, "old_string": "a", "new_string": "b"}, root, root,
        env={"TORQUE_CLIENT": "acme"})
    assert decision.action == "deny"


def test_a_relative_traversal_path_to_the_sidecar_is_denied_through_decide_connected(tmp_path, monkeypatch):
    root = unattended_root(tmp_path, monkeypatch)
    decision = gate_connected.decide_connected(
        "Write", {"file_path": "./.claude/../.claude/torque-permissions.json", "content": "{}"}, root, root,
        env={"TORQUE_CLIENT": "acme"})
    assert decision.action == "deny"


# Fix round 3 (controller review of fix round 2): 1 (Important) the new PATH_TOOLS
# `.claude/` check must not reach into a worktree copy under `.claude/worktrees/`,
# since each copy is decided by its own `_decide_root` pass (copy=True); 2 (scope)
# the check must apply to connected-mode roots only, matching common.md's "Default
# (full) and build-only behavior do not change" (it currently also fires for
# `decide(mode="build-only", ...)`, which shares `_decide_root_for` with connected
# mode's `decide_connected`).

def _git(root, *args):
    import subprocess
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


def unattended_root_with_a_worktree(tmp_path, monkeypatch):
    """An unattended_root workspace, tracked by git, with a real `git worktree add`
    copy at .claude/worktrees/feat (test_gate_alpha12.py's own fixture pattern:
    real git, not a hand-built directory)."""
    root = unattended_root(tmp_path, monkeypatch)
    (root / "project").mkdir(parents=True, exist_ok=True)
    (root / "project" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("/clients/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "worktree", "add", "-q", str(root / ".claude" / "worktrees" / "feat"))
    feat = root / ".claude" / "worktrees" / "feat"
    assert (feat / "workspace.json").is_file()  # a real git worktree carries its own tracked copy
    return root, feat


def test_write_and_edit_inside_a_worktree_copy_are_allowed_from_the_workspace_root(tmp_path, monkeypatch):
    root, feat = unattended_root_with_a_worktree(tmp_path, monkeypatch)
    target = str(feat / "project" / "a.py")
    write = gate_connected.decide_connected("Write", {"file_path": target, "content": "y = 2\n"}, root, root,
                                            env={"TORQUE_CLIENT": "acme"})
    edit = gate_connected.decide_connected(
        "Edit", {"file_path": target, "old_string": "x = 1", "new_string": "y = 2"}, root, root,
        env={"TORQUE_CLIENT": "acme"})
    assert write.action == "allow" and edit.action == "allow"


def test_write_and_edit_inside_a_worktree_copy_are_allowed_from_inside_the_worktree(tmp_path, monkeypatch):
    root, feat = unattended_root_with_a_worktree(tmp_path, monkeypatch)
    target = str(feat / "project" / "a.py")
    # cwd is the worktree itself; workspace is still the outer root, matching how
    # `_main` would evaluate the outer root's own connected-mode decision for a
    # call made while "inside" the worktree.
    write = gate_connected.decide_connected("Write", {"file_path": target, "content": "y = 2\n"}, root, feat,
                                            env={"TORQUE_CLIENT": "acme"})
    edit = gate_connected.decide_connected(
        "Edit", {"file_path": target, "old_string": "x = 1", "new_string": "y = 2"}, root, feat,
        env={"TORQUE_CLIENT": "acme"})
    assert write.action == "allow" and edit.action == "allow"


def test_the_main_workspaces_sidecar_and_settings_stay_denied_alongside_the_worktree_exemption(tmp_path, monkeypatch):
    root, feat = unattended_root_with_a_worktree(tmp_path, monkeypatch)
    sidecar = gate_connected.decide_connected(
        "Write", {"file_path": str(root / permissions.PROFILE_FILE), "content": "{}"}, root, root,
        env={"TORQUE_CLIENT": "acme"})
    settings = gate_connected.decide_connected(
        "Write", {"file_path": str(root / ".claude" / "settings.json"), "content": "{}"}, root, root,
        env={"TORQUE_CLIENT": "acme"})
    assert sidecar.action == "deny" and settings.action == "deny"


def test_a_traversal_path_out_of_worktrees_back_to_the_sidecar_is_still_denied(tmp_path, monkeypatch):
    root, feat = unattended_root_with_a_worktree(tmp_path, monkeypatch)
    decision = gate_connected.decide_connected(
        "Write", {"file_path": ".claude/worktrees/../torque-permissions.json", "content": "{}"}, root, root,
        env={"TORQUE_CLIENT": "acme"})
    assert decision.action == "deny"


def test_build_only_write_to_a_claude_rules_file_is_unaffected(tmp_path, monkeypatch):
    """dea2041 (a15) parity: build-only mode's file-tool checks never gained the
    `.claude/` restriction the round-2 fix added for connected mode."""
    from torque import workspace as ws
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    root = Path(os.path.realpath(root))
    ws.set_ai_access(root, "build-only")
    (root / ".claude" / "rules").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "rules" / "x.md").write_text("old", encoding="utf-8")
    allowed, reason = gate.decide("Write", {"file_path": str(root / ".claude" / "rules" / "x.md")}, root,
                                  "build-only", root)
    assert (allowed, reason) == (True, "")
