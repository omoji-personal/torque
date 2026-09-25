import io
import json
import os
import shlex
import sys

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, MODEL, WRITE, as_agent, delegated_grant, delegated_workspace,
                               flow_request, launched)
from torque import gate, permissions

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
    from torque import gate_connected
    assert gate_connected.unattended(root) is False
