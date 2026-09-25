from collections import namedtuple
import io
import json
import os

import pytest

from torque import approval, before_state, changes, consent, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production")
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False)}
YES = lambda: Presence(True, "")
CMD = ["sf", "project", "deploy", "start", "-m", "Flow:Case_Escalation", "-o", "acme-prod"]
CONFIG = {"ai_access": "connected", "approval": "required", "approval_verify": "hmac"}
posix_only = pytest.mark.skipif(os.name == "nt", reason="file ownership checks are POSIX only")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Synthetic consultants")
    ws.add_client(root, "Acme")
    ws.add_client(root, "Beta")
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-prod", "acme-sbx"],
                           ["Contact"], presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    cid = changes.create_change(root, "Acme", "Flow fix", "Cases escalate", [], "acme-prod")["id"]
    src = tmp_path / "before" / "flows"
    src.mkdir(parents=True)
    (src / "Case_Escalation.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    before = before_state.import_before_state(root, "Acme", cid, tmp_path / "before")
    project = tmp_path / "project"
    flows = project / "force-app" / "main" / "default" / "flows"
    flows.mkdir(parents=True)
    (project / "sfdx-project.json").write_text(json.dumps({"packageDirectories": [{"path": "force-app"}]}),
                                               encoding="utf-8")
    (flows / "Case_Escalation.flow-meta.xml").write_text("<Flow>v2</Flow>", encoding="utf-8")
    (flows / "Other.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    return root, cid, before, project


def request(root, cid, before, project, argv=CMD, **kw):
    if before is not None:
        kw.setdefault("before_state_event", before["event_id"])
    return approval.create_request(root, "Acme", cid, "acme-prod", argv=argv, validated_job="0Af000000000001AAA",
                                   resolve=ORGS.get, cwd=project, **kw)


def grant(root, req, **kw):
    return approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                          resolve=ORGS.get, **kw)


def granted(root, cid, before, project, **kw):
    req = request(root, cid, before, project, **kw)
    return req, grant(root, req)


def use(root, req, client="Acme", org="acme-prod", config=CONFIG, command=None, cwd=None, **kw):
    key = approval.call_key_for_command(command if command is not None else req["command"])
    return approval.consume(root, client, key, org, config=config, cwd=cwd if cwd is not None else req["cwd"], **kw)


def test_grant_then_single_use(setup):
    root, cid, before, project = setup
    req, item = granted(root, cid, before, project)
    assert req["command"] == "sf project deploy start -m Flow:Case_Escalation -o acme-prod"
    assert use(root, req, session_id="s1", tool_use_id="t1") == (True, item["id"])
    ok, why = use(root, req)
    assert not ok and "used" in why
    events = changes.get_change(root, "Acme", cid)["events"]
    assert [e["kind"] for e in events][-3:] == ["approval_request", "approval_grant", "approval_consume"]
    assert events[-1]["session_id"] == "s1" and events[-1]["tool_use_id"] == "t1"
    assert "## Approvals" in changes.render_change(root, "Acme", cid)
    log = approval.approval_log(root, "Acme")
    assert [r["kind"] for r in log][-3:] == ["approval_request", "approval_grant", "approval_consume"]


def test_binding_command_org_client_cwd(setup, tmp_path):
    root, cid, before, project = setup
    req, _ = granted(root, cid, before, project)
    assert not use(root, req, command=req["command"] + " --wait 5")[0]
    assert not use(root, req, org="acme-sbx")[0]
    with pytest.raises(ws.WorkspaceError):
        use(root, req, client="Missing")
    ok, why = use(root, req, client="Beta")
    assert not ok
    ok, why = use(root, req, cwd=tmp_path)
    assert not ok and "run in" in why
    assert use(root, req)[0]


def test_changed_payload_refused(setup):
    root, cid, before, project = setup
    req, _ = granted(root, cid, before, project)
    target = project / "force-app" / "main" / "default" / "flows" / "Case_Escalation.flow-meta.xml"
    target.write_text("<Flow>v3</Flow>", encoding="utf-8")
    ok, why = use(root, req)
    assert not ok and "changed" in why


def test_unrelated_project_file_does_not_matter(setup):
    root, cid, before, project = setup
    req, _ = granted(root, cid, before, project)
    (project / "force-app" / "main" / "default" / "flows" / "Other.flow-meta.xml").write_text("<x/>", encoding="utf-8")
    assert use(root, req)[0]


def test_expiry(setup):
    root, cid, before, project = setup
    req, item = granted(root, cid, before, project)
    later = approval._epoch(item["expires_at"]) + 1
    ok, why = use(root, req, now=later)
    assert not ok and "expired" in why


def test_ttl_caps():
    assert approval.TTL_SECONDS == {"command": 900, "mcp": 900, "browser": 1800} and approval.SKEW == 60


@posix_only
def test_tampered_approval_refused(setup):
    root, cid, before, project = setup
    req, item = granted(root, cid, before, project)
    path = root / "clients" / "acme" / "approvals" / "granted" / f"{item['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["org_alias"] = "acme-sbx"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(path, 0o600)
    ok, why = use(root, req, org="acme-sbx")
    assert not ok and "signature" in why


@posix_only
def test_loose_file_mode_refused(setup):
    root, cid, before, project = setup
    req, item = granted(root, cid, before, project)
    os.chmod(root / "clients" / "acme" / "approvals" / "granted" / f"{item['id']}.json", 0o644)
    ok, why = use(root, req)
    assert not ok and "0600" in why


def test_forged_approval_without_key_refused(setup):
    root, cid, before, project = setup
    req, item = granted(root, cid, before, project)
    approval.key_path().unlink()
    ok, why = use(root, req)
    assert not ok and "signature" in why


def test_tampered_request_is_rederived_at_grant(setup):
    root, cid, before, project = setup
    req = request(root, cid, before, project)
    path = root / "clients" / "acme" / "approvals" / "requests" / f"{req['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["call_key"] = approval.call_key_for_command("sf data delete record -s Account -i 001x -o acme-prod")
    data["command"] = "sf data query -q x -o acme-prod"
    path.write_text(json.dumps(data), encoding="utf-8")
    out = io.StringIO()
    item = approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=out, resolve=ORGS.get)
    assert item["call_key"] == approval.call_key_for_command(req["command"])
    assert "sf project deploy start" in out.getvalue()
    assert not approval.consume(root, "Acme", data["call_key"], "acme-prod", config=CONFIG, cwd=req["cwd"])[0]


def test_production_needs_before_state_or_recovery(setup):
    root, cid, _, project = setup
    req = request(root, cid, None, project)
    with pytest.raises(ws.WorkspaceError, match="before-state"):
        grant(root, req)
    req = request(root, cid, None, project,
                  manual_recovery="Reactivate version 3 of Case_Escalation in Setup, then deactivate version 4.")
    assert grant(root, req)["manual_recovery"].startswith("Reactivate")
    with pytest.raises(ws.WorkspaceError, match="40"):
        request(root, cid, None, project, manual_recovery="roll back")


def test_sandbox_needs_no_before_state(setup):
    root, cid, _, project = setup
    argv = CMD[:-1] + ["acme-sbx"]
    req = approval.create_request(root, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=project)
    assert grant(root, req)["org_kind"] == "sandbox"


def test_uncovered_component_blocks_until_declared_new(setup):
    root, cid, before, project = setup
    classes = project / "force-app" / "main" / "default" / "classes"
    classes.mkdir(parents=True)
    (classes / "NewRouter.cls").write_text("public class NewRouter {}", encoding="utf-8")
    argv = CMD[:-2] + ["-m", "ApexClass:NewRouter", "-o", "acme-prod"]
    req = request(root, cid, before, project, argv=argv)
    with pytest.raises(ws.WorkspaceError, match="ApexClass:NewRouter"):
        grant(root, req)
    assert grant(root, req, new_components=["ApexClass:NewRouter"])["id"].startswith("apr-")


def test_changed_before_state_blocks_grant(setup):
    root, cid, before, project = setup
    req = request(root, cid, before, project)
    stored = root / "clients" / "acme" / "changes" / cid / before["files"][0]["path"]
    stored.write_text("<changed/>", encoding="utf-8")
    with pytest.raises(ws.WorkspaceError, match="changed"):
        grant(root, req)


def test_request_refuses_compound_org_outside_consent_and_reads(setup):
    root, cid, before, project = setup
    with pytest.raises(ws.WorkspaceError, match="one write"):
        approval.create_request(root, "Acme", cid, "acme-prod", argv=["sf", "data", "query", "-q", "x", "-o",
                                                                      "acme-prod"], resolve=ORGS.get, cwd=project)
    with pytest.raises(ws.WorkspaceError, match="consent"):
        approval.create_request(root, "Acme", cid, "other-prod", argv=CMD[:-1] + ["other-prod"], resolve=ORGS.get,
                                cwd=project)
    with pytest.raises(ws.WorkspaceError, match="start with"):
        approval.create_request(root, "Acme", cid, "acme-prod", argv=["bash", "-c", " ".join(CMD)],
                                resolve=ORGS.get, cwd=project)
    with pytest.raises(ws.WorkspaceError, match="targets"):
        approval.create_request(root, "Acme", cid, "acme-prod", argv=CMD[:-1] + ["acme-sbx"], resolve=ORGS.get,
                                cwd=project)


def test_org_identity_changed_since_consent(setup):
    root, cid, before, project = setup
    moved = dict(ORGS, **{"acme-prod": Org("00D000000000009AAA", "production", True)})
    with pytest.raises(ws.WorkspaceError, match="org ID"):
        approval.create_request(root, "Acme", cid, "acme-prod", argv=CMD, before_state_event=before["event_id"],
                                resolve=moved.get, cwd=project)
    req = request(root, cid, before, project)
    with pytest.raises(ws.WorkspaceError, match="org ID"):
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                       resolve=moved.get)


def test_consent_problems_block_requests(setup):
    root, cid, before, project = setup
    consent.suspend(root, "Acme", presence=YES)
    with pytest.raises(ws.WorkspaceError, match="suspended"):
        request(root, cid, before, project)


def test_grant_needs_operator_and_code(setup):
    root, cid, before, project = setup
    req = request(root, cid, before, project)
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        approval.grant(root, "Acme", req["id"], presence=lambda: Presence(False, "needs a real terminal"),
                       confirm=lambda: True, out=io.StringIO(), resolve=ORGS.get)
    with pytest.raises(ws.WorkspaceError, match="code"):
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: False, out=io.StringIO(),
                       resolve=ORGS.get)
    assert approval.list_approvals(root, "Acme") == []


def test_denied_request_cannot_be_granted(setup):
    root, cid, before, project = setup
    req = request(root, cid, before, project)
    approval.deny(root, "Acme", req["id"], "wrong component", presence=YES)
    with pytest.raises(ws.WorkspaceError, match="denied"):
        grant(root, req)


def test_require_for_scripts(setup):
    root, cid, before, project = setup
    granted(root, cid, before, project)
    assert approval.require(root, "Acme", "acme-prod", CMD, config=CONFIG, cwd=project)[0]
    assert not approval.require(root, "Acme", "acme-prod", CMD, config=CONFIG, cwd=project)[0]


@posix_only
def test_owner_uid_tier(setup):
    root, cid, before, project = setup
    req, item = granted(root, cid, before, project)
    other = {**CONFIG, "approval_verify": "owner-uid", "approver_uid": os.getuid() + 1}
    ok, why = use(root, req, config=other)
    assert not ok and "owner" in why
    same = {**CONFIG, "approval_verify": "owner-uid", "approver_uid": os.getuid()}
    ok, why = use(root, req, config=same)
    assert not ok and "separate" in why


@posix_only
def test_owner_uid_grant_refuses_other_account(setup):
    root, cid, before, project = setup
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=os.getuid() + 1,
                     presence=YES)
    req = request(root, cid, before, project)
    with pytest.raises(ws.WorkspaceError, match="approver account"):
        grant(root, req)


def test_browser_window(setup):
    root, cid, _, _ = setup
    req = approval.create_request(root, "Acme", cid, "acme-sbx", browser_minutes=20,
                                  purpose="Adjust the page layout on Case", resolve=ORGS.get)
    item = grant(root, req)
    assert approval._epoch(item["expires_at"]) - approval._epoch(item["granted_at"]) == 1200
    assert approval.active_browser_approval(root, "Acme", "acme-sbx", config=CONFIG)
    assert approval.active_browser_approval(root, "Acme", "acme-sbx", config=CONFIG)
    assert approval.active_browser_approval(root, "Acme", "acme-prod", config=CONFIG) is None
    later = approval._epoch(item["expires_at"]) + 1
    assert approval.active_browser_approval(root, "Acme", "acme-sbx", config=CONFIG, now=later) is None
    with pytest.raises(ws.WorkspaceError, match="purpose"):
        approval.create_request(root, "Acme", cid, "acme-sbx", browser_minutes=20, purpose="x", resolve=ORGS.get)
    with pytest.raises(ws.WorkspaceError):
        approval.create_request(root, "Acme", cid, "acme-sbx", browser_minutes=45, purpose="Adjust layouts now",
                                resolve=ORGS.get)


def test_mcp_call(setup):
    root, cid, _, project = setup
    tool, args = "mcp__salesforce__deploy_metadata", {"usernameOrAlias": "acme-sbx", "sourceDir": "force-app"}
    req = approval.create_request(root, "Acme", cid, "acme-sbx", mcp=(tool, args), resolve=ORGS.get, cwd=project)
    grant(root, req)
    assert not approval.consume(root, "Acme", approval.call_key_for_mcp(tool, {**args, "x": 1}), "acme-sbx",
                                config=CONFIG, cwd=project)[0]
    assert approval.consume(root, "Acme", approval.call_key_for_mcp(tool, args), "acme-sbx", config=CONFIG,
                            cwd=project)[0]


def test_wrapper_lookup(setup):
    root, cid, before, project = setup
    argv = ["torque", "deploy", "-o", "acme-sbx", "--metadata", "Flow:Case_Escalation", "--workspace", str(root),
            "--client", "acme"]
    req = approval.create_request(root, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=project)
    grant(root, req)
    assert approval.consumed_for_wrapper(root, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=project) is None
    assert use(root, req, org="acme-sbx")[0]
    assert approval.consumed_for_wrapper(root, "Acme", ("torque", argv[1:] + ["--wait", "5"]), "acme-sbx", cwd=project) is None
    assert approval.consumed_for_wrapper(root, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=project)["id"]
    assert approval.consumed_for_wrapper(root, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=project) is None


def test_command_words():
    assert approval.command_words("torque deploy -o x") == ("torque", ["deploy", "-o", "x"])
    assert approval.command_words("python3 -m torque deploy -o x") == ("torque", ["deploy", "-o", "x"])
    assert approval.command_words("jsc deploy -o x") == ("jsc", ["deploy", "-o", "x"])
    assert approval.command_words("sf project deploy start -o x") is None


def test_approved_parent_for_revert_children(setup, tmp_path, monkeypatch):
    root, cid, before, project = setup
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(approval, "_recovery_snapshot", lambda *a: (snap, ["deploy", "-o", "acme-sbx"]))
    argv = ["torque", "recover", "run", "snap-1", "--org", "acme-sbx", "--workspace", str(root), "--client", "acme"]
    req = approval.create_request(root, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=project)
    item = grant(root, req)
    assert approval.approved_parent(root, "Acme", item["id"], "acme-sbx", cwd=project) is None
    assert use(root, req, org="acme-sbx")[0]
    assert approval.approved_parent(root, "Acme", item["id"], "acme-sbx", cwd=project) is None
    assert approval.consumed_for_wrapper(root, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=project)
    child = ("jsc", ["deploy", "-o", "acme-sbx"])
    assert approval.approved_parent(root, "Acme", item["id"], "acme-sbx", child, cwd=project) is None
    approval.authorize_child(root, "Acme", item["id"], child[1])
    assert approval.approved_parent(root, "Acme", item["id"], "acme-prod", child, cwd=project) is None
    assert approval.approved_parent(root, "Acme", "../../x", "acme-sbx", child, cwd=project) is None
    import time
    assert approval.approved_parent(root, "Acme", item["id"], "acme-sbx", child, now=time.time() + 3600, cwd=project) is None
    assert approval.approved_parent(root, "Acme", item["id"], "acme-sbx", child, cwd=project)["id"] == item["id"]
