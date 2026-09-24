from collections import namedtuple
import io
import json
import os
from pathlib import Path

import pytest

from torque import approval, before_state, changes, consent, gate, gate_connected as gc, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production")
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False)}
YES = lambda: Presence(True, "")
WRITE = "sf project deploy start -m Flow:Case_Escalation -o acme-prod"


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.add_client(root, "Beta")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-prod", "acme-sbx"],
                           ["Contact"], presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))



def run(root, tool, inp, env=None, mode=None, cwd=None):
    return gc.decide_connected(tool, inp, root, cwd or root, env={"TORQUE_CLIENT": "acme"} if env is None else env,
                               permission_mode=mode, session_id="s1", tool_use_id="t1")


def grant_write(w, tmp_path, command=WRITE, org="acme-prod"):
    cid = changes.create_change(w, "Acme", "Flow fix", "Cases escalate", [], org)["id"]
    src = tmp_path / "b" / "flows"
    src.mkdir(parents=True, exist_ok=True)
    (src / "Case_Escalation.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, tmp_path / "b")
    req = approval.create_request(w, "Acme", cid, org, argv=command.split(), resolve=ORGS.get,
                                  before_state_event=before["event_id"], cwd=w)
    approval.grant(w, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(), resolve=ORGS.get)
    return cid


def test_read_in_scope_allowed_out_of_scope_denied(w):
    assert run(w, "Bash", {"command": "sf data query -q 'SELECT Id FROM Case' -o acme-prod"}).action == "allow"
    assert run(w, "Bash", {"command": "sf data query -q 'x' -o beta-prod"}).action == "deny"
    assert run(w, "Bash", {"command": "sf data query -q 'x'"}).action == "deny"
    assert run(w, "mcp__salesforce__run_soql_query", {"usernameOrAlias": "acme-sbx", "query": "x"}).action == "allow"


def test_unbound_session_denies_org_routes(w):
    assert run(w, "Bash", {"command": "sf data query -q x -o acme-prod"}, env={}).action == "deny"
    assert run(w, "Bash", {"command": "git status"}, env={}).action == "allow"
    assert run(w, "Bash", {"command": "python3 x.py"}, env={}).action == "ask"
    assert run(w, "Bash", {"command": "torque approval grant req-000000000001"}, env={}).action == "deny"
    assert run(w, "Bash", {"command": "torque context --workspace . --client acme"}, env={}).action == "deny"
    assert run(w, "Read", {"file_path": str(w / "clients" / "acme" / "context.md")}, env={}).action == "deny"
    assert run(w, "Bash", {"command": "sf data query -q x -o acme-prod"},
               env={"TORQUE_CLIENT": "missing"}).action == "deny"


def test_write_needs_approval_then_single_use(w, tmp_path):
    assert run(w, "Bash", {"command": WRITE}).action == "deny"
    grant_write(w, tmp_path)
    assert run(w, "Bash", {"command": "  " + WRITE + "  "}).action == "allow"
    decision = run(w, "Bash", {"command": WRITE})
    assert decision.action == "deny" and "used" in decision.reason


def test_approval_is_bound_to_the_working_folder(w, tmp_path):
    grant_write(w, tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert run(w, "Bash", {"command": WRITE}, cwd=other).action == "deny"
    assert run(w, "Bash", {"command": WRITE}).action == "allow"


def test_compound_write_denied_even_when_approved(w, tmp_path):
    grant_write(w, tmp_path)
    assert run(w, "Bash", {"command": f"echo go && {WRITE}"}).action == "deny"
    assert run(w, "Bash", {"command": f"{WRITE} > out.txt"}).action == "deny"
    assert run(w, "Bash", {"command": WRITE}).action == "allow"


def test_check_only_allowed_and_logged(w):
    assert run(w, "Bash", {"command": WRITE + " --dry-run"}).action == "allow"
    assert run(w, "Bash", {"command": "sf apex run test -o acme-prod"}).action == "allow"
    log = (w / "clients" / "acme" / "approvals" / "activity.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["action"] for line in log] == ["check-only", "check-only"]


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto", "dontAsk"])
def test_unverifiable_asks_and_skipped_prompts_deny(w, mode):
    assert run(w, "Bash", {"command": "python3 tools/fix.py"}).action == "ask"
    assert run(w, "Bash", {"command": "python3 tools/fix.py"}, mode="default").action == "ask"
    assert run(w, "Bash", {"command": "python3 tools/fix.py"}, mode=mode).action == "deny"


def test_admin_and_other_client_denied(w):
    for command in ("torque approval grant req-000000000001 --workspace . --client acme",
                    "torque client consent sign-off --workspace . --client acme --reviewer me",
                    "torque launch --workspace . --client acme", "torque workspace ai-access full",
                    "sf alias set acme-prod=someone@example.com"):
        assert run(w, "Bash", {"command": command}).action == "deny", command
    assert run(w, "Bash", {"command": "torque context --workspace . --client beta"}).action == "deny"
    assert run(w, "Bash", {"command": "torque context --workspace . --client acme"}).action == "allow"
    assert run(w, "Read", {"file_path": str(w / "clients" / "beta" / "context.md")}).action == "deny"
    assert run(w, "Read", {"file_path": str(w / "clients" / "acme" / "context.md")}).action == "allow"
    assert run(w, "Grep", {"pattern": "x", "path": str(w / "clients")}).action == "deny"


def test_consent_and_approval_files_cannot_be_written(w):
    assert run(w, "Write", {"file_path": str(w / "clients" / "acme" / "consent.json"), "content": "{}"}).action == "deny"
    assert run(w, "Edit", {"file_path": str(w / "clients" / "acme" / "approvals" / "granted" / "apr-000000000001.json"),
                           "old_string": "a", "new_string": "b"}).action == "deny"
    assert run(w, "Write", {"file_path": str(w / "workspace.json"), "content": "{}"}).action == "deny"


def test_consent_suspended_denies_reads(w):
    consent.suspend(w, "Acme", presence=YES)
    assert run(w, "Bash", {"command": "sf data query -q x -o acme-prod"}).action == "deny"


def test_records_not_allowed_denies_data_query(w, tmp_path):
    letter = tmp_path / "b.pdf"
    letter.write_bytes(b"x")
    consent.record_consent(w, "Acme", "2026-09-30", letter, ["metadata"], ["acme-prod"], ["C"], presence=YES,
                           resolve=ORGS.get)
    consent.sign_off(w, "Acme", "Reviewer", presence=YES)
    assert run(w, "Bash", {"command": "sf data query -q x -o acme-prod"}).action == "deny"
    assert run(w, "Bash", {"command": "sf apex get log -i 07L -o acme-prod"}).action == "deny"
    assert run(w, "Bash", {"command": "sf project retrieve start -m Flow:X -o acme-prod"}).action == "allow"


def test_browser_interaction_needs_window(w, tmp_path):
    assert run(w, "mcp__claude-in-chrome__computer", {"action": "left_click"}).action == "deny"
    assert run(w, "mcp__claude-in-chrome__read_page", {}).action == "allow"
    cid = changes.create_change(w, "Acme", "Layout", "Cases show tier", [], "acme-sbx")["id"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", browser_minutes=10,
                                  purpose="Add the Tier field to the Case layout", resolve=ORGS.get)
    approval.grant(w, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(), resolve=ORGS.get)
    assert run(w, "mcp__claude-in-chrome__computer", {"action": "left_click"}).action == "allow"
    assert run(w, "mcp__claude-in-chrome__form_input", {}).action == "allow"
    assert run(w, "mcp__computer-use__type", {"text": "x"}).action == "deny"


def test_ask_json_shape():
    data = json.loads(gc.ask_json("reason"))
    assert data["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert data["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert data["hookSpecificOutput"]["permissionDecisionReason"] == "reason"


def _hook(monkeypatch, capsys, event):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = gate.main()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_hook_end_to_end(w, monkeypatch, capsys):
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    base = {"hook_event_name": "PreToolUse", "cwd": str(w), "session_id": "s1", "tool_use_id": "t1",
            "permission_mode": "default", "tool_name": "Bash"}
    code, out, _ = _hook(monkeypatch, capsys, {**base, "tool_input": {"command": "python3 x.py"}})
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    code, _, err = _hook(monkeypatch, capsys, {**base, "tool_input": {"command": WRITE}})
    assert code == 2 and "Connected mode" in err
    code, out, _ = _hook(monkeypatch, capsys, {**base, "tool_input": {"command": "sf data query -q x -o acme-sbx"}})
    assert code == 0 and out == ""
    code, _, err = _hook(monkeypatch, capsys, {**base, "permission_mode": "bypassPermissions",
                                               "tool_input": {"command": "python3 x.py"}})
    assert code == 2


def test_hook_without_binding_denies(w, monkeypatch, capsys):
    monkeypatch.delenv("TORQUE_CLIENT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    event = {"cwd": str(w), "tool_name": "Bash", "tool_input": {"command": "sf data query -q x -o acme-sbx"}}
    code, _, err = _hook(monkeypatch, capsys, event)
    assert code == 2 and "no client is bound" in err


def test_build_only_above_connected_still_blocks(w, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    event = {"cwd": str(w), "tool_name": "Bash", "tool_input": {"command": "sf data query -q x -o acme-sbx"}}
    (tmp_path / "workspace.json").write_text(json.dumps({"ai_access": "build-only"}), encoding="utf-8")
    code, _, err = _hook(monkeypatch, capsys, event)
    assert code == 2 and "Build-only" in err


def test_salesforce_cli_state_and_path_folders_are_protected(w, tmp_path):
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = {"TORQUE_CLIENT": "acme", "HOME": str(home), "PATH": str(bin_dir)}
    assert run(w, "Edit", {"file_path": str(home / ".sfdx" / "alias.json"), "old_string": "a", "new_string": "b"},
               env=env).action == "deny"
    assert run(w, "Read", {"file_path": str(home / ".sf" / "config.json")}, env=env).action == "deny"
    assert run(w, "Bash", {"command": "cat ~/.sfdx/alias.json"}, env=env).action == "deny"
    assert run(w, "Bash", {"command": f"cp x {home.as_posix()}/.local/share/sf/node_modules/p/index.js"},
               env=env).action == "deny"
    assert run(w, "Write", {"file_path": str(bin_dir / "sf"), "content": "#!/bin/sh"}, env=env).action == "deny"
    assert run(w, "Read", {"file_path": str(bin_dir / "sf")}, env=env).action == "allow"
    assert run(w, "Write", {"file_path": str(w / "project" / "notes.md"), "content": "x"}, env=env).action == "allow"
