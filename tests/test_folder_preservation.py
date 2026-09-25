"""D20 (spec requirement 22): existing workspace folders are never deleted,
recreated or re-moded by any a16 operation, so an operator's provisioned
ownership and modes survive every delegated and owner operation."""
import io
import os
import shlex
import stat

import pytest

from delegated_helpers import (ACCOUNT, CLEAN, FAKE_OWNER, ME, MODEL, ORGS, WRITE, YES, as_agent, delegated_grant,
                               delegated_workspace, flow_request, launched)
from torque import approval, changes, consent, delegation, doctor_connected, execution, launch, permissions
from torque import workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
FOLDERS = ("clients/acme/approvals", "clients/acme/approvals/requests", "clients/acme/approvals/granted",
           "clients/acme/approvals/denied", "clients/acme/approvals/consumed", "clients/acme/changes",
           "clients/acme/cases", "clients/acme/cases/cx-04", ".claude", ".claude/rules")


def identity(root):
    out = {}
    for name in FOLDERS:
        st = os.stat(root / name)
        out[name] = (st.st_ino, stat.S_IMODE(st.st_mode), st.st_gid)
    return out


def test_no_flow_deletes_recreates_or_re_modes_a_folder(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    (root / "clients/acme/cases/cx-04").mkdir(parents=True)
    # F5: add_client does not create clients/<slug>/changes (only flow_request's
    # first changes.create_change call would, and that happens after the
    # snapshot below), so it is created here, beside the brief's L3787
    # equivalent, before every folder in FOLDERS is given its provisioned mode.
    (root / "clients/acme/changes").mkdir(parents=True, exist_ok=True)
    for name in FOLDERS:
        os.chmod(root / name, 0o750 if name.endswith(("granted", "denied")) else 0o1770)
    before = identity(root)

    req = flow_request(root, cwd=root / "clients/acme/cases/cx-04")
    delegated_grant(root, req)
    other = flow_request(root, cwd=root / "clients/acme/cases/cx-04")
    # root_owner=FAKE_OWNER (R41) and control_stat=approval._control_stat (R46)
    # stand in for the separate OS account and the genuinely-not-approver-owned
    # control files a single-uid test process cannot have (see
    # delegated_helpers.delegated_grant, which already threads these through
    # for the grant side).
    approval.deny_delegated(root, "Acme", other["id"], "manifest-deny", model_id=MODEL, reason="deny",
                            root_owner=FAKE_OWNER, control_stat=approval._control_stat, **CLEAN)
    binding = launch.create_binding(root, "Acme", model_id=MODEL, root_owner=FAKE_OWNER, **CLEAN)

    # claim_binding and every later call in this test are the agent's side (a
    # separate OS account from the approver's), the same as approval.consume
    # below; grant/deny/create_binding above are the delegated approver's own
    # writes and must run before this uid switch, not after.
    as_agent(monkeypatch)
    launch.claim_binding(root, "Acme", binding["id"], **CLEAN)
    # F1: write_launch_record creates the approvals folders only when absent;
    # launched() also binds TORQUE_CLIENT/TORQUE_LAUNCH for the execution
    # record below, the same way a real delegated or presence launch would.
    launched(monkeypatch, root)
    ok, approval_id = approval.consume(root, "Acme", approval.call_key_for_command(shlex.join(WRITE)), "acme-dev",
                                       config=ws.load_workspace(root)[1], session_id="s-d20", tool_use_id="t-d20",
                                       cwd=root / "clients/acme/cases/cx-04")
    assert ok, approval_id
    # F35: an execution record (execution.handle is exactly what the gate's own
    # PostToolUse dispatch calls: gate._post_event).
    execution.handle({"hook_event_name": "PostToolUse", "session_id": "s-d20", "tool_use_id": "t-d20",
                      "cwd": str(root / "clients/acme/cases/cx-04"), "tool_name": "Bash",
                      "tool_input": {"command": shlex.join(WRITE)},
                      "tool_response": {"interrupted": False}}, os.environ)
    executed = [e for e in changes.get_change(root, "Acme", req["change"])["events"]
               if e["kind"] == "approval_executed" and e.get("tool_use_id") == "t-d20"]
    assert executed and executed[0]["outcome"] == "succeeded"
    approval.decision(root, "Acme", req["id"])
    approval.approval_log(root, "Acme")
    doctor_connected.report(root, "Acme", live=True, resolve=ORGS.get)

    # R55 (spec requirement 22), part (c): the delegated setup steps
    # themselves (ai-access, permissions write with hooks, consent record,
    # sign-off), the ones that write into .claude, .claude/rules and
    # clients/acme/consent.json. getuid=lambda: ME keeps proving this account
    # is the workspace's named setup delegate even though as_agent above
    # mocked the real os.getuid() to ME + 1; that mismatch (ME + 1 != the
    # real, on-disk owner of .claude, which is ME, this same process, from
    # delegated_workspace()'s init) is deliberate here, the same way
    # test_settings_dir_chmod_skipped_when_not_owned_by_the_real_caller
    # proves it: it is what makes write_settings' own ownership-gated
    # .claude relax (permissions.py, "Fix round 1, minor 4", a separate,
    # already-reviewed rule from R55's) correctly skip a folder this
    # delegate does not really own, matching a real deployment where
    # .claude was provisioned by a different account than the one now
    # running this step.
    setup_kwargs = {"model_id": MODEL, "root_owner": FAKE_OWNER, "getuid": lambda: ME, **CLEAN}
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME, delegated=True,
                     **setup_kwargs)
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, **setup_kwargs)
    letter2 = tmp_path / "agreement2.pdf"
    letter2.write_bytes(b"synthetic agreement 2")
    consent.record_consent(root, "Acme", "2026-09-30", letter2, ["metadata", "records"], ["acme-dev"], ["Contact"],
                           resolve=ORGS.get, delegated=True, **setup_kwargs)
    consent.sign_off(root, "Acme", "Second Model Reviewer", delegated=True, **setup_kwargs)
    assert not consent.consent_problems(consent.load_consent(root, "Acme"))

    # F35: also exercise the owner grant and a consent sign-off. An owner
    # (presence) grant is refused outright while the approver delegate is an
    # AI (R45), so the approver switches to a human delegate first; the a15
    # owner path itself grants only from the approver account (tier 2), which
    # this single-account test process already is, once as_agent's uid
    # override is undone.
    monkeypatch.setattr(os, "getuid", lambda: ME)
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "human", geteuid=lambda: 0)
    owner_req = flow_request(root, cwd=root / "clients/acme/cases/cx-04")
    record = approval.grant(root, "Acme", owner_req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                            resolve=ORGS.get)
    assert record["delegated"] is False and record["approver_kind"] == "human"
    consent.sign_off(root, "Acme", "Second Reviewer", presence=YES)

    assert identity(root) == before
