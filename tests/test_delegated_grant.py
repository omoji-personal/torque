import io
import json
import os
import shlex

import pytest

from delegated_helpers import (ACCOUNT, CLEAN, FAKE_OWNER, ME, MODEL, ORGS, WRITE, YES, as_agent, base_workspace,
                               control_owner, delegated_grant, delegated_workspace, flow_request)
from torque import approval, changes, cli, consent, delegation, presence, workspace as ws
from torque.presence import Presence

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def consume(root, command=None):
    config = ws.load_workspace(root)[1]
    return approval.consume(root, "Acme", approval.call_key_for_command(command or shlex.join(WRITE)), "acme-dev",
                            config=config, session_id="s1", tool_use_id="t1", cwd=root)


def granted_path(root, record):
    return root / "clients/acme/approvals/granted" / f"{record['id']}.json"


def test_delegated_grant_records_kind_and_identity(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    assert (record["approver_kind"], record["approver"], record["approver_uid"], record["approver_model"],
            record["delegated"]) == ("ai", ACCOUNT, ME, MODEL, True)
    stored = json.loads(granted_path(root, record).read_text())
    assert stored["approver_kind"] == "ai" and stored["delegated"] is True


def test_delegated_grant_needs_no_terminal_and_no_code(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)

    def boom(*a, **k):
        raise AssertionError("the delegated path must not ask for a terminal or a code")
    monkeypatch.setattr(presence, "operator_present", boom)
    monkeypatch.setattr(presence, "confirm_code", boom)
    assert delegated_grant(root, req)["delegated"] is True


def test_delegated_grant_writes_nothing_into_the_change_record(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    before = [e["kind"] for e in changes.get_change(root, "Acme", req["change"])["events"]]
    delegated_grant(root, req)
    assert [e["kind"] for e in changes.get_change(root, "Acme", req["change"])["events"]] == before
    assert not (root / "clients/acme/approvals/activity.jsonl").exists()


def test_delegated_grant_refused_in_tier1(tmp_path, monkeypatch):
    root = base_workspace(tmp_path, monkeypatch)
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=lambda: 0)
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"x")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-dev"], [], presence=YES,
                           resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, flow_request(root))
    assert info.value.reason_class == "tier-2-required"


def test_delegated_grant_refused_inside_an_ai_session(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, flow_request(root), env={"CLAUDECODE": "1"})
    assert info.value.reason_class == "agent-session"


def test_delegated_grant_refused_under_a_claude_ancestor(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, flow_request(root), ancestors=lambda: [(1, "zsh"), (2, "/usr/local/bin/claude")])
    assert info.value.reason_class == "agent-session"


@pytest.mark.parametrize("org,live", [
    ("acme-prod", ORGS),
    ("acme-dev", {**ORGS, "acme-dev": ORGS["acme-dev"]._replace(detected_org_type="unknown")}),
    ("acme-dev", {"acme-prod": ORGS["acme-prod"]}),
], ids=["production", "unknown-type", "unresolvable"])
def test_delegated_grant_refused_for_production_and_unknown(tmp_path, monkeypatch, org, live):
    root = delegated_workspace(tmp_path, monkeypatch, orgs=("acme-dev", "acme-prod"))
    argv = [*WRITE[:-1], org]
    req = flow_request(root, argv, org=org)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    with pytest.raises(delegation.Refusal) as info:
        approval.grant(root, "Acme", req["id"], delegated=True, model_id=MODEL,
                       request_sha256=view["request_sha256"], payload_digest=view["payload"]["digest"],
                       out=io.StringIO(), resolve=live.get, root_owner=FAKE_OWNER, **CLEAN)
    assert info.value.reason_class == "org-production-or-unknown"
    assert not list((root / "clients/acme/approvals/granted").glob("apr-*.json"))


def test_delegated_grant_refused_when_the_consent_records_the_org_as_production(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    path = root / "clients/acme/consent.json"
    item = json.loads(path.read_text())
    item["approved_orgs"][0]["kind"] = "production"
    path.write_text(json.dumps(item))
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req)
    assert info.value.reason_class == "org-production-or-unknown"


def test_delegated_grant_refuses_an_expired_request(tmp_path, monkeypatch):
    """F11: a delegated grant refuses a request older than the request TTL (plus skew)."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    late = approval._epoch(req["created_at"]) + approval.REQUEST_TTL + approval.SKEW + 1
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, now=late)
    assert info.value.reason_class == "request-expired"
    assert not list((root / "clients/acme/approvals/granted").glob("apr-*.json"))
    in_time = approval._epoch(req["created_at"]) + approval.REQUEST_TTL
    assert delegated_grant(root, req, now=in_time)["delegated"] is True


def test_delegated_grant_needs_the_reviewed_hashes(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    for missing in ({"request_sha256": None}, {"payload_digest": None}):
        with pytest.raises(delegation.Refusal) as info:
            delegated_grant(root, req, **missing)
        assert info.value.reason_class == "request-changed"


def test_delegated_grant_refuses_a_denied_request(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    approval.deny(root, "Acme", req["id"], "not this one", presence=YES, confirm=lambda: True)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req)
    assert info.value.reason_class == "request-denied"


def test_delegated_grant_refuses_unusable_consent(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    consent.suspend(root, "Acme", presence=YES)
    with pytest.raises(delegation.Refusal) as info:
        approval.grant(root, "Acme", req["id"], delegated=True, model_id=MODEL,
                       request_sha256=view["request_sha256"], payload_digest=view["payload"]["digest"],
                       out=io.StringIO(), resolve=ORGS.get, root_owner=FAKE_OWNER, **CLEAN)
    assert info.value.reason_class == "consent-unusable"


def test_delegated_grant_refuses_the_workspace_owner_account(tmp_path, monkeypatch):
    """R41: the delegate may not be the account that owns the workspace directory."""
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, flow_request(root), root_owner=lambda p: ME)
    assert info.value.reason_class == "not-delegated"


def test_delegated_grant_decides_from_the_protected_read(tmp_path, monkeypatch):
    """Tier, delegate and namespaces come from the one protected read of workspace.json;
    a by-path load (used only to locate the client folder) decides nothing."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    real = ws.load_workspace

    def tampered(*a, **k):
        found, config = real(*a, **k)
        return found, {**config, "approval_verify": "hmac", "delegates": {}, "managed_namespaces": ["zz"]}
    monkeypatch.setattr(ws, "load_workspace", tampered)
    record = approval.grant(root, "Acme", req["id"], delegated=True, model_id=MODEL,
                            request_sha256=view["request_sha256"], payload_digest=view["payload"]["digest"],
                            out=io.StringIO(), resolve=ORGS.get, root_owner=FAKE_OWNER, **CLEAN)
    assert record["delegated"] is True and record["namespaces"] == view_namespaces(root, req)


def view_namespaces(root, req):
    return approval._derive(approval.load_request(root, "Acme", req["id"]))["namespaces"]


def test_gate_uses_a_delegated_approval_once_and_logs_its_kind(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    delegated_grant(root, req)
    as_agent(monkeypatch)
    assert consume(root)[0]
    assert not consume(root)[0]
    events = changes.get_change(root, "Acme", req["change"])["events"]
    grant = [e for e in events if e["kind"] == "approval_grant"][0]
    used = [e for e in events if e["kind"] == "approval_consume"][0]
    assert grant["approver_kind"] == used["approver_kind"] == "ai" and used["approver_model"] == MODEL
    assert grant["delegated"] is used["delegated"] is True and used["approver_uid"] == ME


def test_gate_refuses_an_ai_approval_the_workspace_did_not_delegate(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    delegated_grant(root, flow_request(root))
    config = json.loads((root / "workspace.json").read_text())
    config["delegates"].pop("approver")
    (root / "workspace.json").write_text(json.dumps(config))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "delegated approval" in why


def test_gate_refuses_an_ai_approval_marked_production(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    granted_path(root, record).write_text(json.dumps({**record, "org_kind": "production"}))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "production" in why


def test_gate_refuses_an_ai_approval_whose_consented_org_is_production(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    delegated_grant(root, flow_request(root))
    path = root / "clients/acme/consent.json"
    item = json.loads(path.read_text())
    item["approved_orgs"][0]["kind"] = "production"
    path.write_text(json.dumps(item))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "production" in why


def test_gate_refuses_a_record_without_a_kind(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    granted_path(root, record).write_text(json.dumps({k: v for k, v in record.items() if k != "approver_kind"}))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "malformed" in why


@pytest.mark.parametrize("field", ["approver", "approver_uid", "approver_kind", "approver_model", "delegated"])
def test_gate_refuses_a_human_record_missing_an_identity_field(tmp_path, monkeypatch, field):
    """F10: the gate refuses a record missing any identity field, approver_model included."""
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    record = approval.grant(root, "Acme", flow_request(root)["id"], presence=YES, confirm=lambda: True,
                            out=io.StringIO(), resolve=ORGS.get)
    assert "approver_model" in approval.REQUIRED
    granted_path(root, record).write_text(json.dumps({k: v for k, v in record.items() if k != field}))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "malformed" in why


@pytest.mark.parametrize("change,expected", [
    ({"delegated": False}, "delegated approver"),
    ({"approver_kind": "human", "approver_model": None}, "delegated approval"),
    ({"approver_uid": ME + 7}, "delegated approval"),
    ({"approver": ACCOUNT + "-other"}, "delegated approval"),
    ({"approver_model": None}, "malformed"),
    ({"approver_kind": "robot"}, "malformed"),
    ({"delegated": "yes"}, "malformed"),
])
def test_gate_refuses_an_inconsistent_delegated_record(tmp_path, monkeypatch, change, expected):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    granted_path(root, record).write_text(json.dumps({**record, **change}))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and expected in why


def test_gate_refuses_a_human_record_naming_a_model(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    record = approval.grant(root, "Acme", flow_request(root)["id"], presence=YES, confirm=lambda: True,
                            out=io.StringIO(), resolve=ORGS.get)
    granted_path(root, record).write_text(json.dumps({**record, "approver_model": MODEL}))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "malformed" in why


def test_owner_grant_keeps_presence_code_and_kind_human(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    req = flow_request(root)
    with pytest.raises(ws.WorkspaceError, match="real terminal"):
        approval.grant(root, "Acme", req["id"], presence=lambda: Presence(False, "no terminal"),
                       confirm=lambda: True, out=io.StringIO(), resolve=ORGS.get)
    with pytest.raises(ws.WorkspaceError, match="code did not match"):
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: False, out=io.StringIO(),
                       resolve=ORGS.get)
    record = approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                            resolve=ORGS.get)
    assert (record["approver_kind"], record["delegated"], record["approver_model"]) == ("human", False, None)
    as_agent(monkeypatch)
    assert consume(root)[0]


def test_owner_grant_refuses_a_model_id(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="--model-id"):
        approval.grant(root, "Acme", flow_request(root)["id"], presence=YES, confirm=lambda: True,
                       out=io.StringIO(), resolve=ORGS.get, model_id=MODEL)


def _cli_grant(root, req, view, *extra):
    return cli.main(["approval", "grant", req["id"], "--workspace", str(root), "--client", "Acme", "--delegated",
                     "--model-id", MODEL, "--request-sha256", view["request_sha256"], "--payload-digest",
                     view["payload"]["digest"], *extra])


def test_cli_delegated_grant_json_keeps_stdout_to_the_record(tmp_path, monkeypatch, capsys):
    """F6: with --json the review screen goes to stderr; stdout parses as the record."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    monkeypatch.setattr(presence, "agent_reason", lambda env=None, ancestors=None: "")
    proof = delegation._delegated_proof
    monkeypatch.setattr(delegation, "_delegated_proof",
                        lambda *a, **k: proof(*a, **{**k, "root_owner": FAKE_OWNER}))
    monkeypatch.setattr(approval, "_resolver", lambda resolve: resolve or ORGS.get)
    capsys.readouterr()
    assert _cli_grant(root, req, view, "--json") == 0
    out, err = capsys.readouterr()
    record = json.loads(out)
    assert (record["approver_kind"], record["delegated"], record["approver_model"]) == ("ai", True, MODEL)
    assert "Client:" in err and "Client:" not in out


def test_cli_delegated_refusal_is_exit3_with_json(tmp_path, monkeypatch, capsys):
    """F37: a refused delegated grant exits 3 and, with --json, names its reason class."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    monkeypatch.setenv("CLAUDECODE", "1")
    capsys.readouterr()
    assert _cli_grant(root, req, view, "--json") == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["refused"] is True and payload["reason_class"] == "agent-session" and payload["message"]
    assert _cli_grant(root, req, view) == 3
    assert "refused (agent-session)" in capsys.readouterr().err
    assert not list((root / "clients/acme/approvals/granted").glob("apr-*.json"))


def test_cli_review_flags_need_delegated(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    code = cli.main(["approval", "grant", req["id"], "--workspace", str(root), "--client", "Acme",
                     "--model-id", MODEL])
    assert code == 2 and "--delegated" in capsys.readouterr().err


# Controller ruling R45: with an AI approver delegate, the approver account is the
# AI's, so no human-kind (owner) approval is accepted from it, at grant or in the gate.


def owner_grant(root, req):
    return approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                          resolve=ORGS.get)


def test_r45_owner_grant_refused_in_an_ai_approver_workspace(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(delegation.Refusal) as info:
        owner_grant(root, flow_request(root))
    assert info.value.reason_class == "human-grant-needs-human-approver"
    assert "approver is a person" in str(info.value)
    assert not list((root / "clients/acme/approvals/granted").glob("apr-*.json"))


def test_r45_gate_refuses_a_hand_written_human_record_in_an_ai_approver_workspace(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    forged = {**record, "approver_kind": "human", "approver_model": None, "delegated": False}
    forged.pop("reviewed_request_sha256")
    forged.pop("idempotency_key")
    granted_path(root, record).write_text(json.dumps(forged))
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "approver is a person" in why


@pytest.mark.parametrize("delegate", [None, "human"])
def test_r45_human_path_unchanged_without_an_ai_approver(tmp_path, monkeypatch, delegate):
    if delegate is None:
        root = delegated_workspace(tmp_path, monkeypatch)
        config = json.loads((root / "workspace.json").read_text())
        config["delegates"].pop("approver")
        (root / "workspace.json").write_text(json.dumps(config))
    else:
        root = delegated_workspace(tmp_path, monkeypatch, kind=delegate)
    record = owner_grant(root, flow_request(root))
    assert (record["approver_kind"], record["delegated"]) == ("human", False)
    as_agent(monkeypatch)
    assert consume(root)[0]


def test_r45_ai_delegated_grant_still_accepted_for_non_production(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    assert (record["approver_kind"], record["org_kind"]) == ("ai", "developer")
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert ok and why == record["id"]


@pytest.mark.parametrize("delegates", [{"approver": {"kind": "ai"}}, {"approver": "ai"}, ["approver"]])
def test_r45_malformed_approver_delegate_fails_closed(tmp_path, monkeypatch, delegates):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    req = flow_request(root)
    config = json.loads((root / "workspace.json").read_text())
    config["delegates"] = delegates
    (root / "workspace.json").write_text(json.dumps(config))
    with pytest.raises(delegation.Refusal) as info:
        owner_grant(root, req)
    assert info.value.reason_class == "human-grant-needs-human-approver"


# Fix round 1, ruling R46: in a tier 2 workspace the control files (workspace.json,
# the client's consent.json) must not be owned or writable by the approver account.


@pytest.mark.parametrize("name", ["workspace.json", "consent.json"])
def test_r46_gate_refuses_when_the_approver_owns_a_control_file(tmp_path, monkeypatch, name):
    root = delegated_workspace(tmp_path, monkeypatch)
    delegated_grant(root, flow_request(root))
    control_owner(monkeypatch, owner=ME, only=name)
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and name in why and "approver account" in why


def test_r46_gate_refuses_an_owner_grant_when_the_approver_owns_workspace_json(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    approval.grant(root, "Acme", flow_request(root)["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                   resolve=ORGS.get)
    control_owner(monkeypatch, owner=ME, only="workspace.json")
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "workspace.json" in why


@pytest.mark.parametrize("name", ["workspace.json", "consent.json"])
def test_r46_grant_refuses_when_the_approver_owns_a_control_file(tmp_path, monkeypatch, name):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    control_owner(monkeypatch, owner=ME, only=name)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req)
    assert info.value.reason_class == "not-delegated" and name in str(info.value)
    assert not list((root / "clients/acme/approvals/granted").glob("apr-*.json"))


def test_r46_group_writable_control_file_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    delegated_grant(root, req)
    consent_file = root / "clients/acme/consent.json"
    consent_file.chmod(0o664)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req)
    assert info.value.reason_class == "not-delegated" and "consent.json" in str(info.value)
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "consent.json" in why


def test_r46_symlinked_consent_refused_by_the_gate(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    delegated_grant(root, flow_request(root))
    consent_file = root / "clients/acme/consent.json"
    real = tmp_path / "consent-real.json"
    real.write_bytes(consent_file.read_bytes())
    consent_file.unlink()
    consent_file.symlink_to(real)
    as_agent(monkeypatch)
    ok, why = consume(root)
    assert not ok and "consent.json" in why


@pytest.mark.parametrize("owner", [ME + 1, 0], ids=["consultant", "root"])
def test_r46_consultant_or_root_owned_control_files_accepted(tmp_path, monkeypatch, owner):
    root = delegated_workspace(tmp_path, monkeypatch)
    control_owner(monkeypatch, owner=owner)
    delegated_grant(root, flow_request(root))
    as_agent(monkeypatch)
    assert consume(root)[0]


# Fix round 1, minor 1: every failure to read what is being granted is a Refusal.


def test_delegated_grant_refuses_a_future_dated_request(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    early = approval._epoch(req["created_at"]) - approval.SKEW - 5
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, now=early)
    assert info.value.reason_class == "request-changed"


def test_delegated_grant_refuses_an_unreadable_request_or_change(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    kwargs = {"delegated": True, "model_id": MODEL, "request_sha256": view["request_sha256"],
              "payload_digest": view["payload"]["digest"], "out": io.StringIO(), "resolve": ORGS.get,
              "root_owner": FAKE_OWNER, **CLEAN}
    change_files = list((root / "clients/acme/changes").rglob(f"{req['change']}*"))
    assert change_files
    for path in change_files:
        path.rename(path.with_name(path.name + ".moved"))
    with pytest.raises(delegation.Refusal) as info:
        approval.grant(root, "Acme", req["id"], **kwargs)
    assert info.value.reason_class == "request-changed"
    (root / "clients/acme/approvals/requests" / f"{req['id']}.json").write_text("{not json")
    with pytest.raises(delegation.Refusal) as info:
        approval.grant(root, "Acme", req["id"], **kwargs)
    assert info.value.reason_class == "request-changed"
