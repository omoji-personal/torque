import json
import os
import shlex

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, MODEL, WRITE, as_agent, delegated_grant, delegated_workspace,
                               flow_request)
from torque import approval, cli, launch, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def rows(root, **match):
    return [r for r in approval.approval_log(root, "Acme") if all(r.get(k) == v for k, v in match.items())]


def test_unused_delegated_grant_and_denial_appear_from_decision_files(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted = delegated_grant(root, flow_request(root))
    # root_owner=FAKE_OWNER (R41) and control_stat=approval._control_stat (R46) stand
    # in for the separate OS account and the genuinely-not-approver-owned control
    # files a single-uid test process cannot have; the brief's given call predates
    # both rulings, same as every other task's own delegated-approver tests (see
    # delegated_helpers.delegated_grant and tests/test_delegated_deny.py's deny()).
    denied = approval.deny_delegated(root, "Acme", flow_request(root)["id"], "manifest-deny", model_id=MODEL,
                                     reason="entry says deny", root_owner=FAKE_OWNER,
                                     control_stat=approval._control_stat, **CLEAN)
    [g] = rows(root, kind="approval_grant", approval_id=granted["id"])
    [d] = rows(root, kind="approval_deny", request_id=denied["request_id"])
    assert (g["source"], g["approver_kind"], g["approver_model"]) == ("decision file", "ai", MODEL)
    assert (d["source"], d["reason_class"], d["approver_kind"]) == ("decision file", "manifest-deny", "ai")


def test_used_grant_is_logged_once_from_the_change_record(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted = delegated_grant(root, flow_request(root))
    as_agent(monkeypatch)
    approval.consume(root, "Acme", approval.call_key_for_command(shlex.join(WRITE)), "acme-dev",
                     config=ws.load_workspace(root)[1], cwd=root)
    grants = rows(root, kind="approval_grant", approval_id=granted["id"])
    assert len(grants) == 1 and grants[0]["source"] == "change record" and grants[0]["approver_kind"] == "ai"


def test_launches_and_setup_steps_appear(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    # root_owner=FAKE_OWNER (R41) on create_binding, as_agent + CLEAN env on
    # claim_binding (agent-session, and the launching account must differ from
    # the approver's): the brief's given calls predate both rulings, the same
    # adaptation D11/D12's own tests needed for create_binding/claim_binding.
    binding = launch.create_binding(root, "Acme", model_id=MODEL, root_owner=FAKE_OWNER, **CLEAN)
    as_agent(monkeypatch)
    launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    [row] = rows(root, kind="launch")
    assert (row["source"], row["approver_kind"], row["approval_id"]) == ("launch record", "ai", binding["id"])
    # R53: a genuine, still-checking-out binding claim verifies.
    assert (row["via"], row["delegated"], row["verified"]) == ("binding", True, True)
    assert rows(root, kind="setup", source="workspace setup")
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme"]) == 0
    assert "(ai" in capsys.readouterr().out


def test_human_delegate_binding_launch_is_delegated_via_binding_not_kind(tmp_path, monkeypatch):
    # F8: delegated comes from via == "binding", not from kind. A human
    # delegate's binding-based launch (kind "human") must still read as
    # delegated True, via "binding" -- unlike a presence launch (kind
    # "human", via "presence"), which is the a15 self-account, non-delegated
    # case and stays delegated False.
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    binding = launch.create_binding(root, "Acme", model_id=None, root_owner=FAKE_OWNER, **CLEAN)
    as_agent(monkeypatch)
    launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    [row] = rows(root, kind="launch")
    assert (row["approver_kind"], row["via"], row["delegated"]) == ("human", "binding", True)
    assert row["verified"] is True


def test_presence_launch_is_not_delegated_and_not_verified(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = launch.write_launch_record(root, "Acme", "human")
    [row] = rows(root, kind="launch", approval_id=record["id"])
    assert (row["via"], row["delegated"], row["verified"]) == ("presence", False, False)


def _write_launch_file(root, content, name="launch-aaaaaaaaaaaa.launch"):
    consumed = root / "clients" / "acme" / "approvals" / "consumed"
    consumed.mkdir(parents=True, exist_ok=True)
    path = consumed / name
    path.write_text(content, encoding="utf-8")
    return path


def test_launch_file_with_a_non_string_created_at_is_a_problem_row(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted = delegated_grant(root, flow_request(root))
    path = _write_launch_file(root, json.dumps({
        "schema": "torque.launch/1", "id": "launch-aaaaaaaaaaaa", "kind": "human", "via": "presence",
        "client": "acme", "workspace": str(root), "pid": 1, "pid_started": "x", "created_at": 12345}))
    entries = approval.approval_log(root, "Acme")  # must not raise
    [problem] = [r for r in entries if r["source"] == "launch record" and r.get("problem")]
    assert problem["kind"] == "launch" and path.name in problem["problem"] and problem["verified"] is False
    # the rest of the log still renders
    assert any(r.get("kind") == "approval_grant" and r["approval_id"] == granted["id"] for r in entries)
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme"]) == 0
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme", "--json"]) == 0


def test_launch_file_with_a_missing_created_at_is_a_problem_row(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted = delegated_grant(root, flow_request(root))
    path = _write_launch_file(root, json.dumps({
        "schema": "torque.launch/1", "id": "launch-aaaaaaaaaaaa", "kind": "ai", "via": "presence",
        "client": "acme", "workspace": str(root), "pid": 1, "pid_started": "x"}))
    entries = approval.approval_log(root, "Acme")  # must not raise
    [problem] = [r for r in entries if r["source"] == "launch record" and r.get("problem")]
    assert path.name in problem["problem"]
    assert any(r.get("kind") == "approval_grant" and r["approval_id"] == granted["id"] for r in entries)
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme"]) == 0
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme", "--json"]) == 0


def test_garbage_json_launch_file_is_a_problem_row(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted = delegated_grant(root, flow_request(root))
    path = _write_launch_file(root, "not json at all {{{")
    entries = approval.approval_log(root, "Acme")  # must not raise
    [problem] = [r for r in entries if r["source"] == "launch record" and r.get("problem")]
    assert path.name in problem["problem"]
    assert any(r.get("kind") == "approval_grant" and r["approval_id"] == granted["id"] for r in entries)
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme"]) == 0
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme", "--json"]) == 0


def test_non_object_json_launch_file_is_a_problem_row(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted = delegated_grant(root, flow_request(root))
    path = _write_launch_file(root, json.dumps([1, 2, 3]))
    entries = approval.approval_log(root, "Acme")  # must not raise
    [problem] = [r for r in entries if r["source"] == "launch record" and r.get("problem")]
    assert path.name in problem["problem"]
    assert any(r.get("kind") == "approval_grant" and r["approval_id"] == granted["id"] for r in entries)
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme"]) == 0
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme", "--json"]) == 0


def test_forged_launch_file_naming_the_delegated_approver_shows_verified_false(tmp_path, monkeypatch):
    # A well-formed .launch file (the agent can write anything here), naming
    # the workspace's real delegated approver but no real binding it ever
    # issued. It is not a problem row (every required field is well-typed)
    # but it must not verify: nothing here proves the approver actually
    # issued this launch.
    root = delegated_workspace(tmp_path, monkeypatch)
    path = _write_launch_file(root, json.dumps({
        "schema": "torque.launch/1", "id": "lnk-bbbbbbbbbbbb", "kind": "ai", "via": "binding",
        "client": "acme", "workspace": str(root), "pid": os.getpid(), "pid_started": "x",
        "created_at": "2026-09-25T00:00:00+00:00", "binding_id": "lnk-bbbbbbbbbbbb", "nonce": "0" * 32,
        "binding_created_at": "2026-09-25T00:00:00+00:00", "binding_expires_at": "2026-09-25T00:10:00+00:00",
        "approver": {"approver": "someone", "approver_uid": 0, "approver_kind": "ai",
                    "approver_model": MODEL}}), name="lnk-bbbbbbbbbbbb.launch")
    entries = approval.approval_log(root, "Acme")  # must not raise
    [row] = [r for r in entries if r["source"] == "launch record" and r["approval_id"] == "lnk-bbbbbbbbbbbb"]
    assert row.get("problem") is None
    assert (row["via"], row["verified"]) == ("binding", False)
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme"]) == 0
    out = cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme", "--json"])
    assert out == 0
