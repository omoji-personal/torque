import io
import json
import os
import shutil

import pytest

from delegated_helpers import (ACCOUNT, CLEAN, FAKE_OWNER, ME, MODEL, ORGS, YES, base_workspace, control_owner,
                               delegated_grant, delegated_workspace, flow_request, snapshot)
from torque import approval, cli, consent, delegation, presence, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def deny(root, req, reason_class="manifest-deny", **extra):
    # root_owner=FAKE_OWNER (R41) and control_stat=approval._control_stat (R46,
    # read fresh so it picks up whatever delegated_workspace()'s control_owner()
    # most recently configured) stand in for the separate OS account and the
    # genuinely-not-approver-owned control files a single-uid test process
    # cannot have; see delegated_helpers.delegated_grant for the identical need
    # on the grant side.
    kwargs = {"model_id": MODEL, "reason": "not in the case manifest", "root_owner": FAKE_OWNER,
              "control_stat": approval._control_stat, **CLEAN}
    kwargs.update(extra)
    return approval.deny_delegated(root, "Acme", req["id"], reason_class, **kwargs)


def test_denial_file_is_bound_and_writes_nothing_else(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    sha = approval.request_sha256_of(root, "Acme", req["id"])
    before = snapshot(root)
    record = deny(root, req)
    after = snapshot(root)
    # Fix round 1, item 3: the shared decision-<request_id> marker in
    # approvals/granted/ is the one other file a delegated denial now writes,
    # the exclusivity check against a racing grant() / _grant_delegated().
    assert set(after) - set(before) == {f"clients/acme/approvals/denied/{record['id']}.json",
                                        f"clients/acme/approvals/granted/decision-{req['id']}.json"}
    assert all(after[k] == v for k, v in before.items())
    assert (record["request_sha256"], record["reason_class"], record["approver_kind"], record["delegated"]) == \
        (sha, "manifest-deny", "ai", True)
    assert approval.delegated_denials(root, "Acme")[0]["id"] == record["id"]


def test_a_denied_request_cannot_be_granted(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    deny(root, req)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req)
    assert info.value.reason_class == "request-denied"
    with pytest.raises(ws.WorkspaceError, match="denied"):
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                       resolve=ORGS.get)


def test_reason_class_format(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="reason class"):
        deny(root, flow_request(root), reason_class="Bad Class")


def test_denial_refused_for_an_ai_session_and_outside_tier2(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    with pytest.raises(delegation.Refusal) as info:
        deny(root, req, env={"CLAUDECODE": "1"})
    assert info.value.reason_class == "agent-session"
    # F29: the test's own name also promises a tier 1 (outside tier 2) case,
    # which the brief's given test body never asserted. A separate workspace
    # (same construction as test_delegated_grant_refused_in_tier1) never sets
    # approval_verify="owner-uid", so it stays tier 1 (hmac).
    tier1 = base_workspace(tmp_path / "tier1", monkeypatch)
    delegation.set_delegate(tier1, "approver", ACCOUNT, ME, "ai", geteuid=lambda: 0)
    ws.set_ai_access(tier1, "connected", approval="required", presence=YES)
    letter = tmp_path / "tier1-agreement.pdf"
    letter.write_bytes(b"synthetic agreement")
    consent.record_consent(tier1, "Acme", "2026-09-30", letter, ["metadata"], ["acme-dev"], [], presence=YES,
                           resolve=ORGS.get)
    consent.sign_off(tier1, "Acme", "Reviewer", presence=YES)
    with pytest.raises(delegation.Refusal) as info:
        deny(tier1, flow_request(tier1))
    assert info.value.reason_class == "tier-2-required"


def test_a_denial_not_owned_by_the_approver_is_ignored(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    deny(root, flow_request(root))
    config = json.loads((root / "workspace.json").read_text())
    config["approver_uid"] += 1
    config["delegates"]["approver"]["uid"] += 1
    (root / "workspace.json").write_text(json.dumps(config))
    assert approval.delegated_denials(root, "Acme") == []


def test_missing_denied_folder_fails_closed(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    shutil.rmtree(root / "clients/acme/approvals/denied")
    with pytest.raises(ws.WorkspaceError, match="approvals/denied"):
        deny(root, flow_request(root))


# F21: the other half of the grant/denial exclusivity rule (deny refuses when a
# grant already exists; test_a_denied_request_cannot_be_granted above proves the
# reverse: grant refuses when a denial already exists).


def test_deny_refused_when_an_authentic_grant_already_exists(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    delegated_grant(root, req)
    with pytest.raises(delegation.Refusal) as info:
        deny(root, req)
    assert info.value.reason_class == "already-granted"
    assert not list((root / "clients/acme/approvals/denied").glob("dny-*.json"))


def test_deny_ignores_an_unowned_forged_grant_file(tmp_path, monkeypatch):
    """F21 says "verified the same way the gate verifies a grant": a file that
    merely sits in approvals/granted/ naming this request_id, but fails the
    ownership check (here: group/other-writable), is not an authentic grant and
    must not block the denial."""
    root = delegated_workspace(tmp_path, monkeypatch)
    decoy = delegated_grant(root, flow_request(root))
    target = flow_request(root)
    forged_path = root / "clients/acme/approvals/granted" / "apr-0f0f0f0f0f0f.json"
    forged_path.write_text(json.dumps({**decoy, "id": "apr-0f0f0f0f0f0f", "request_id": target["id"]}))
    os.chmod(forged_path, 0o666)
    record = deny(root, target)
    assert record["reason_class"] == "manifest-deny"
    assert approval.delegated_denials(root, "Acme")[0]["request_id"] == target["id"]


def test_a_second_denial_of_the_same_request_is_allowed(tmp_path, monkeypatch):
    """Two denials for one request are not a conflict (nothing here ever grants
    it); each gets its own id, and both are readable."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    first = deny(root, req, reason_class="manifest-deny")
    second = deny(root, req, reason_class="scope-out-of-engagement")
    assert first["id"] != second["id"]
    ids = {d["id"] for d in approval.delegated_denials(root, "Acme") if d["request_id"] == req["id"]}
    assert ids == {first["id"], second["id"]}


# Fix round 1, item 1: delegated_denials must run R46 before trusting any
# denial, and fail closed (refuse) rather than silently reporting no denials.


def test_delegated_denials_fails_closed_on_an_r46_violation(tmp_path, monkeypatch):
    """An approver-owned workspace.json and consent.json make delegated_denials
    refuse instead of returning []; the owner's plain grant() (which has no
    other R46 check of its own) inherits the refusal through _delegated_denial,
    so it cannot silently proceed as though the request was never denied."""
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    req = flow_request(root)
    deny(root, req, model_id=None)  # a human-kind delegate never takes --model-id
    control_owner(monkeypatch, owner=ME)  # workspace.json and consent.json now report as approver-owned
    with pytest.raises(delegation.Refusal) as info:
        approval.delegated_denials(root, "Acme")
    assert info.value.reason_class == "not-delegated"
    fresh = flow_request(root)
    with pytest.raises(delegation.Refusal) as info2:
        approval.grant(root, "Acme", fresh["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                       resolve=ORGS.get)
    assert info2.value.reason_class == "not-delegated"


def test_delegated_denials_returns_empty_when_the_denied_folder_is_missing(tmp_path, monkeypatch):
    """The pre-existing missing-folder behavior is unchanged: no delegated denial
    has ever been written for this client is a provisioning state, not an
    untrustworthy one, and stays silent ([])."""
    root = delegated_workspace(tmp_path, monkeypatch)
    shutil.rmtree(root / "clients/acme/approvals/denied")
    assert approval.delegated_denials(root, "Acme") == []


# Fix round 1, item 2: a dny-*.json file that exists but cannot be read, parsed,
# or lacks a required field makes delegated_denials/_delegated_denial raise a
# refusal, never treated as absent.


def test_a_corrupt_denial_file_blocks_both_grant_paths(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    corrupt = root / "clients/acme/approvals/denied" / "dny-000000000000.json"
    corrupt.write_text("not json")
    corrupt.chmod(0o644)
    req = flow_request(root)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req)
    assert info.value.reason_class == "denial-unreadable"
    with pytest.raises(delegation.Refusal) as info2:
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                       resolve=ORGS.get)
    assert info2.value.reason_class == "denial-unreadable"
    assert not list((root / "clients/acme/approvals/granted").glob("apr-*.json"))


def test_delegated_denials_fails_closed_on_a_denial_missing_a_required_field(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = deny(root, flow_request(root))
    path = root / "clients/acme/approvals/denied" / f"{record['id']}.json"
    broken = json.loads(path.read_text())
    del broken["reason_class"]
    path.write_text(json.dumps(broken))
    with pytest.raises(delegation.Refusal) as info:
        approval.delegated_denials(root, "Acme")
    assert info.value.reason_class == "denial-unreadable"


# Fix round 1, item 3: a per-request exclusive decision marker closes the race
# between {grant(), _grant_delegated()} and deny_delegated() that the earlier,
# purely scan-based checks above (_delegated_denial / _granted_for) cannot: in
# each of these tests, the "losing" side's own scan would see nothing yet (no
# apr-*.json or dny-*.json file exists), so only the marker itself can catch it.


def test_marker_race_deny_wins_blocks_delegated_grant(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    dirs = approval._dirs(root, "Acme", create=False)
    approval._claim_decision(dirs, req["id"], "denied", None)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req)
    assert info.value.reason_class == "request-denied"
    assert not list((root / "clients/acme/approvals/granted").glob("apr-*.json"))


def test_marker_race_deny_wins_blocks_owner_grant(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    req = flow_request(root)
    dirs = approval._dirs(root, "Acme", create=False)
    approval._claim_decision(dirs, req["id"], "denied", None)
    with pytest.raises(ws.WorkspaceError, match="denied"):
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                       resolve=ORGS.get)


def test_marker_race_grant_wins_blocks_delegated_deny(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    dirs = approval._dirs(root, "Acme", create=False)
    approval._claim_decision(dirs, req["id"], "granted", "apr-000000000000")
    with pytest.raises(delegation.Refusal) as info:
        deny(root, req)
    assert info.value.reason_class == "already-granted"
    assert not list((root / "clients/acme/approvals/denied").glob("dny-*.json"))


def _cli_deny(root, req, *extra):
    return cli.main(["approval", "deny", req["id"], "--workspace", str(root), "--client", "Acme", "--delegated",
                     "--reason-class", "manifest-deny", "--model-id", MODEL, "--reason",
                     "not in the case manifest", *extra])


def test_cli_deny_delegated_writes_the_record(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    monkeypatch.setattr(presence, "agent_reason", lambda env=None, ancestors=None: "")
    proof = delegation._delegated_proof
    monkeypatch.setattr(delegation, "_delegated_proof",
                        lambda *a, **k: proof(*a, **{**k, "root_owner": FAKE_OWNER}))
    capsys.readouterr()
    assert _cli_deny(root, req, "--json") == 0
    out, err = capsys.readouterr()
    record = json.loads(out)
    assert (record["approver_kind"], record["delegated"], record["reason_class"]) == ("ai", True, "manifest-deny")
    assert approval.delegated_denials(root, "Acme")[0]["id"] == record["id"]


def test_cli_deny_delegated_refusal_is_exit3_with_json(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    monkeypatch.setenv("CLAUDECODE", "1")
    capsys.readouterr()
    assert _cli_deny(root, req, "--json") == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["refused"] is True and payload["reason_class"] == "agent-session" and payload["message"]
    assert not list((root / "clients/acme/approvals/denied").glob("dny-*.json"))


def test_cli_deny_reason_class_and_model_id_need_delegated(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    code = cli.main(["approval", "deny", req["id"], "--workspace", str(root), "--client", "Acme", "--reason", "no",
                     "--reason-class", "manifest-deny"])
    assert code == 2 and "--delegated" in capsys.readouterr().err


def test_cli_deny_json_needs_delegated(tmp_path, monkeypatch, capsys):
    """Fix round 1, item 4: --json without --delegated is a usage error (exit 2),
    the same as --reason-class and --model-id."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    code = cli.main(["approval", "deny", req["id"], "--workspace", str(root), "--client", "Acme", "--reason", "no",
                     "--json"])
    assert code == 2 and "--delegated" in capsys.readouterr().err
