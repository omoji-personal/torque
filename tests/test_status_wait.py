import io
import json
import os

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, ME, MODEL, ORGS, YES, as_agent, control_owner, delegated_grant,
                               delegated_workspace, flow_request)
from torque import approval, cli, delegation, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def fake_clock(start=None):
    import time
    t = [start if start is not None else time.time()]
    return t, (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))


def status(root, req, wait="0"):
    return cli.main(["approval", "status", req["id"], "--workspace", str(root), "--client", "Acme", "--wait", wait,
                     "--json"])


def deny(root, req, reason_class="manifest-deny", **extra):
    # root_owner=FAKE_OWNER (R41) and control_stat=approval._control_stat (R46,
    # read fresh so it picks up whatever delegated_workspace()'s control_owner()
    # most recently configured) stand in for the separate OS account and the
    # genuinely-not-approver-owned control files a single-uid test process
    # cannot have. This is the same adaptation D9's own test file needed for the
    # identical reason (tests/test_delegated_deny.py's local deny() helper).
    kwargs = {"model_id": MODEL, "reason": "entry says deny", "root_owner": FAKE_OWNER,
              "control_stat": approval._control_stat, **CLEAN}
    kwargs.update(extra)
    return approval.deny_delegated(root, "Acme", req["id"], reason_class, **kwargs)


def test_granted(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    record = delegated_grant(root, req)
    as_agent(monkeypatch)
    state, detail = approval.wait_for_decision(root, "Acme", req["id"], 5)
    assert state == "granted" and detail["approval_id"] == record["id"] and detail["approver_kind"] == "ai"
    assert status(root, req) == 0


def test_denied_with_the_reason(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    deny(root, req, "manifest-deny")
    as_agent(monkeypatch)
    assert status(root, req) == 20
    assert json.loads(capsys.readouterr().out)["detail"]["reason_class"] == "manifest-deny"


def test_owner_denial_event_is_denied(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    approval.deny(root, "Acme", req["id"], "not now", presence=YES, confirm=lambda: True)
    assert approval.decision(root, "Acme", req["id"])[0] == "denied"


def test_expired_request_and_expired_grant(tmp_path, monkeypatch):
    # R47 (fix round 1): a request with no decision yet is expired only past
    # REQUEST_TTL + SKEW, the same tolerance the grant path itself gives a
    # request dated up to SKEW seconds in the future or right at its boundary,
    # so the waiter never abandons a request the grant path could still accept.
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    later = approval._epoch(req["created_at"]) + approval.REQUEST_TTL + approval.SKEW + 1
    assert approval.decision(root, "Acme", req["id"], now=later)[0] == "expired"
    still_pending = approval._epoch(req["created_at"]) + approval.REQUEST_TTL + approval.SKEW - 1
    assert approval.decision(root, "Acme", req["id"], now=still_pending)[0] == "pending"
    other = flow_request(root)
    record = delegated_grant(root, other)
    as_agent(monkeypatch)
    after = approval._epoch(record["expires_at"]) + approval.SKEW + 1
    assert approval.decision(root, "Acme", other["id"], now=after)[0] == "expired"


def test_pending_times_out_and_a_missing_approver_never_reads_as_denied(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    t, clock, sleep = fake_clock()
    state, _ = approval.wait_for_decision(root, "Acme", req["id"], 30, clock=clock, sleep=sleep)
    assert state == "timeout" and t[0] >= 30
    assert status(root, req) == 22


def test_status_without_wait_is_unchanged(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    assert cli.main(["approval", "status", req["id"], "--workspace", str(root), "--client", "Acme"]) == 0
    assert "Not granted yet." in capsys.readouterr().out


# --- Additional coverage beyond the brief's given tests ---


def test_decision_ignores_a_grant_for_a_different_request(tmp_path, monkeypatch):
    """Requirement 21/22: the waiter reads no approval that is not for the given
    request. A grant sitting in approvals/granted/ for a sibling request must
    never leak into this request's decision."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req_a = flow_request(root)
    req_b = flow_request(root)
    delegated_grant(root, req_a)
    as_agent(monkeypatch)
    state, detail = approval.decision(root, "Acme", req_b["id"])
    assert state == "pending" and detail == {}


def test_a_forged_grant_file_is_not_read_as_granted(tmp_path, monkeypatch):
    """"Granted" comes only from a grant that passes the gate's own authenticity
    checks (_problem), never a bare file existing under approvals/granted/. A
    record missing most REQUIRED fields is "malformed approval" to _problem, so
    it must fall through to pending/expired, not granted."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    as_agent(monkeypatch)
    forged = ws.load_client(root, "Acme")[0] / "approvals" / "granted" / "apr-badbadbadbad.json"
    forged.write_text(json.dumps({"schema": approval.SCHEMA, "id": "apr-badbadbadbad",
                                  "request_id": req["id"], "client": "acme"}), encoding="utf-8")
    state, detail = approval.decision(root, "Acme", req["id"])
    assert state == "pending" and detail == {}


def test_r46_violation_is_reported_as_an_error_not_granted(tmp_path, monkeypatch, capsys):
    """Item 5: a denial-unreadable or R46 refusal is an error (exit 2), never
    granted. Poisoning the control files after a genuine grant already exists
    proves the grant is not trusted just because it is on disk."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    delegated_grant(root, req)
    as_agent(monkeypatch)
    control_owner(monkeypatch, owner=ME)
    with pytest.raises(delegation.Refusal) as info:
        approval.decision(root, "Acme", req["id"])
    assert info.value.reason_class == "not-delegated"
    assert status(root, req) == 2
    assert "torque:" in capsys.readouterr().err


def test_corrupt_denial_file_is_reported_as_an_error_not_granted(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    corrupt = ws.load_client(root, "Acme")[0] / "approvals" / "denied" / "dny-000000000000.json"
    corrupt.write_text("not json", encoding="utf-8")
    as_agent(monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        approval.decision(root, "Acme", req["id"])
    assert info.value.reason_class == "denial-unreadable"
    assert status(root, req) == 2
    assert "torque:" in capsys.readouterr().err


def test_wait_seconds_out_of_range(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    with pytest.raises(ws.WorkspaceError, match="0 to"):
        approval.wait_for_decision(root, "Acme", req["id"], -1)
    with pytest.raises(ws.WorkspaceError, match="0 to"):
        approval.wait_for_decision(root, "Acme", req["id"], approval.WAIT_MAX + 1)
    with pytest.raises(ws.WorkspaceError, match="0 to"):
        approval.wait_for_decision(root, "Acme", req["id"], 5.0)
    assert status(root, req, wait="-1") == 2


def test_wait_never_sleeps_once_already_decided(tmp_path, monkeypatch):
    """No busy loop: a state that is already decided returns immediately, with no
    call to sleep at all."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    delegated_grant(root, req)
    as_agent(monkeypatch)
    calls = []
    state, _ = approval.wait_for_decision(root, "Acme", req["id"], 30, sleep=calls.append)
    assert state == "granted" and calls == []


def test_wait_polls_a_bounded_number_of_times_with_positive_durations(tmp_path, monkeypatch):
    """Bounded polling: each sleep between reads is a strictly positive duration
    (never sleep(0), never an unbounded number of iterations) and the wait
    still respects the poll interval given."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    t, clock, advance = fake_clock(start=0.0)
    calls = []

    def sleep(seconds):
        calls.append(seconds)
        advance(seconds)

    state, detail = approval.wait_for_decision(root, "Acme", req["id"], 10, poll=2.0, clock=clock, sleep=sleep)
    assert state == "timeout" and detail == {"waited_seconds": 10}
    assert calls == [2.0, 2.0, 2.0, 2.0, 2.0]


def test_cli_text_lines_for_every_state(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    granted_req = flow_request(root)
    record = delegated_grant(root, granted_req)
    denied_req = flow_request(root)
    deny(root, denied_req, "scope-out-of-engagement")
    pending_req = flow_request(root)
    as_agent(monkeypatch)

    assert cli.main(["approval", "status", granted_req["id"], "--workspace", str(root), "--client", "Acme",
                     "--wait", "0"]) == 0
    assert capsys.readouterr().out.strip() == f"granted {record['id']}"

    assert cli.main(["approval", "status", denied_req["id"], "--workspace", str(root), "--client", "Acme",
                     "--wait", "0"]) == 20
    assert capsys.readouterr().out.strip() == "denied (scope-out-of-engagement): entry says deny"

    assert cli.main(["approval", "status", pending_req["id"], "--workspace", str(root), "--client", "Acme",
                     "--wait", "0"]) == 22
    assert capsys.readouterr().out.strip() == "timeout after 0 s; wait again, do not write"


def test_cli_writes_nothing(tmp_path, monkeypatch):
    """Requirement 22: the waiter must not create any file or folder."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)

    def snapshot():
        return {p for p in root.rglob("*")}

    as_agent(monkeypatch)
    before = snapshot()
    assert status(root, req, wait="0") == 22
    after = snapshot()
    assert before == after
