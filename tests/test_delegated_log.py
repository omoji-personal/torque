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
    assert rows(root, kind="setup", source="workspace setup")
    assert cli.main(["approval", "log", "--workspace", str(root), "--client", "Acme"]) == 0
    assert "(ai" in capsys.readouterr().out
