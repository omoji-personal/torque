"""Fix round 3: the R2 recheck (R2c) findings. Written failing first."""
from collections import namedtuple
import io
import json
import os
import threading
from pathlib import Path

import pytest

from torque import (approval, argv_flags, before_state, changes, cli, cli_approval, consent, gate,
                    gate_connected as gc, permissions, workspace as ws)
from torque.connected_routes import classify
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
CONFIG = {"ai_access": "connected", "approval": "required", "approval_verify": "hmac"}
SBX = "https://acme--sbx.sandbox.lightning.force.com/lightning/setup/home"
PROD = "https://acme.lightning.force.com/lightning/page/home"
CLICK = "mcp__claude-in-chrome__computer"
NAV = "mcp__claude-in-chrome__navigate"


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.add_client(root, "Beta")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-prod", "acme-sbx"],
                           ["Contact"], presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def run(root, tool, inp, mode=None, session="s1", cwd=None, client="acme"):
    return gc.decide_connected(tool, inp, root, cwd or root, env={"TORQUE_CLIENT": client} if client else {},
                               permission_mode=mode, session_id=session, tool_use_id="t1")


def change(root, org="acme-sbx"):
    return changes.create_change(root, "Acme", "Change", "Works", [], org)["id"]


def grant(root, req, **kw):
    return approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True,
                          out=kw.pop("out", io.StringIO()), resolve=ORGS.get, **kw)


def project(tmp_path):
    folder = tmp_path / "project"
    base = folder / "force-app" / "main" / "default"
    (base / "classes").mkdir(parents=True)
    (base / "labels").mkdir(parents=True)
    (folder / "sfdx-project.json").write_text(json.dumps({"packageDirectories": [{"path": "force-app"}]}),
                                              encoding="utf-8")
    for name in ("A", "B"):
        (base / "classes" / f"{name}.cls").write_text("class", encoding="utf-8")
    (base / "labels" / "CustomLabels.labels-meta.xml").write_text("<CustomLabels/>", encoding="utf-8")
    return folder


# D1

def test_d1_selectorless_deploy_binds_the_whole_project(tmp_path):
    folder = project(tmp_path)
    argv = ["sf", "project", "deploy", "start", "-o", "acme-sbx"]
    before, count = approval.payload_digest(argv, folder)
    assert count >= 3
    (folder / "force-app" / "main" / "default" / "classes" / "B.cls").write_text("changed", encoding="utf-8")
    assert approval.payload_digest(argv, folder)[0] != before


def test_d1_shared_metadata_files_are_bound(tmp_path):
    folder = project(tmp_path)
    argv = ["sf", "project", "deploy", "start", "-m", "CustomLabel:Welcome_Text", "-o", "acme-sbx"]
    before, _ = approval.payload_digest(argv, folder)
    (folder / "force-app" / "main" / "default" / "labels" / "CustomLabels.labels-meta.xml").write_text(
        "<CustomLabels><x/></CustomLabels>", encoding="utf-8")
    assert approval.payload_digest(argv, folder)[0] != before


def test_d1_attached_multi_value_flag():
    assert argv_flags.values(["--source-dir=one", "two", "-o", "x"], ["--source-dir"]) == ["one", "two"]


def test_d1_component_with_no_local_files_is_refused(w, tmp_path):
    folder = project(tmp_path)
    with pytest.raises(ws.WorkspaceError, match="Flow:Missing"):
        approval.create_request(w, "Acme", change(w), "acme-sbx", argv=["sf", "project", "deploy", "start", "-m",
                                "Flow:Missing", "-o", "acme-sbx"], resolve=ORGS.get, cwd=folder)


# D2

def window(w, org="acme-sbx"):
    kw = {"manual_recovery": "Undo the layout change in Setup, restoring the saved layout."} if org == "acme-prod" else {}
    req = approval.create_request(w, "Acme", change(w, org), org, browser_minutes=10,
                                  purpose="Add Tier to the Case layout", resolve=ORGS.get, **kw)
    return grant(w, req)


def test_d2_click_is_bound_to_the_navigated_tab(w):
    window(w)
    assert run(w, NAV, {"url": SBX, "tabId": 1}).action == "allow"
    assert run(w, CLICK, {"action": "left_click", "tabId": 1}).action == "allow"
    assert run(w, CLICK, {"action": "left_click", "tabId": 2}).action == "deny"
    assert run(w, CLICK, {"action": "left_click"}).action == "deny"
    assert run(w, "mcp__chrome-devtools__click", {"uid": "x"}).action == "deny"


def test_d2_click_naming_another_org_is_refused(w):
    window(w)
    run(w, NAV, {"url": SBX, "tabId": 1})
    assert run(w, CLICK, {"action": "left_click", "tabId": 1, "url": PROD}).action == "deny"
    assert run(w, CLICK, {"action": "left_click", "tabId": 1, "usernameOrAlias": "acme-prod"}).action == "deny"


def test_d2_a_tab_that_has_shown_another_org_stays_refused(w):
    window(w)
    run(w, NAV, {"url": PROD, "tabId": 1})
    run(w, NAV, {"url": SBX, "tabId": 1})
    assert run(w, CLICK, {"action": "left_click", "tabId": 1}).action == "deny"


# D3

def test_d3_encoded_rest_paths_are_decoded(w):
    cmd = ("sf api request rest '/services/data/v60.0/tooling/query?q=SELECT+Id+FROM+Apex%4Cog' -o acme-prod")
    assert run(w, "Bash", {"command": cmd}).action == "deny"
    cmd = "sf api request rest /services/data/v60.0/sobjects/Account/%30%30%31 -o acme-prod"
    assert run(w, "Bash", {"command": cmd}).action == "deny"


def test_d3_data_resume_needs_records(w):
    assert run(w, "Bash", {"command": "sf data resume -i 750x -o acme-prod"}).action == "deny"


# D7

def test_d7_destructive_changes_are_in_scope(tmp_path):
    folder = project(tmp_path)
    (folder / "destructive.xml").write_text("<Package><types><members>Old</members><name>ApexClass</name></types>"
                                            "</Package>", encoding="utf-8")
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "--pre-destructive-changes", "destructive.xml",
            "-o", "x"]
    assert set(before_state.deploy_components(argv, folder)) == {"ApexClass:A", "ApexClass:Old"}


def test_d7_object_needs_its_definition_file():
    before = {"files": [{"path": "evidence/b/objects/Widget__c/fields/Size__c.field-meta.xml"}]}
    assert before_state.coverage(["CustomObject:Widget__c"], before) == ["CustomObject:Widget__c"]
    before["files"].append({"path": "evidence/b/objects/Widget__c/Widget__c.object-meta.xml"})
    assert before_state.coverage(["CustomObject:Widget__c"], before) == []


# D8

def test_d8_indeterminate_working_folder_fails_closed(tmp_path, monkeypatch):
    from jsc_revert.wrappers import _common as c
    folder = tmp_path / "ws"
    (folder / "clients").mkdir(parents=True)
    (folder / ".torque").mkdir()
    (folder / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(folder)
    assert c.connected_approval("acme-sbx", "00D000000000001AAA")[0] == c.EXIT_NOT_APPROVED


def test_d8_selected_client_without_workspace_config_fails_closed(tmp_path, monkeypatch):
    from jsc_revert.wrappers import _common as c
    client = tmp_path / "firm" / "clients" / "acme"
    client.mkdir(parents=True)
    (client / "client.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    assert c.connected_approval("acme-sbx", "00D000000000001AAA")[0] == c.EXIT_NOT_APPROVED


def consumed_route(w, tmp_path):
    folder = project(tmp_path)
    argv = ["torque", "deploy", "-o", "acme-sbx", "--metadata", "ApexClass:A", "--workspace", str(w),
            "--client", "acme"]
    req = approval.create_request(w, "Acme", change(w), "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)
    item = grant(w, req)
    assert approval.consume(w, "Acme", item["command_sha256"], "acme-sbx", config=CONFIG, cwd=folder)[0]
    return argv, item, folder


def test_d8_wrapper_must_run_in_the_approved_folder(w, tmp_path):
    argv, item, folder = consumed_route(w, tmp_path)
    assert approval.consumed_for_wrapper(w, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=tmp_path) is None
    assert approval.consumed_for_wrapper(w, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=folder)


# D9

def test_d9_revert_child_resolution_failure_returns_the_parent_approval(w, tmp_path):
    argv, item, folder = consumed_route(w, tmp_path)
    assert approval.consumed_for_wrapper(w, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=folder)
    child = ["deploy", "-o", "acme-sbx", "--metadata", "ApexClass:A"]
    approval.authorize_child(w, "Acme", item["id"], child)
    assert approval.release_child(w, "Acme", item["id"], "acme-sbx", ("jsc", child), cwd=folder)
    assert approval.consume(w, "Acme", item["command_sha256"], "acme-sbx", config=CONFIG, cwd=folder)[0]


# D11

def test_d11_deny_keeps_recovery_and_validation_references(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=["sf", "project", "deploy", "start", "-m",
                                  "ApexClass:A", "-o", "acme-prod"], resolve=ORGS.get, cwd=folder,
                                  manual_recovery="Redeploy the previous version of class A from the release tag.",
                                  validated_job="0Af000000000001AAA")
    approval.deny(w, "Acme", req["id"], "not today", presence=YES)
    event = [e for e in changes.get_change(w, "Acme", cid)["events"] if e["kind"] == "approval_deny"][0]
    for key in ("manual_recovery", "validated_job", "before_state_event", "command_sha256", "org_id_18"):
        assert key in event


def test_d11_event_schemas_are_enforced(w):
    cid = change(w)
    with pytest.raises(ws.WorkspaceError, match="missing"):
        changes.append_approval_event(w, "Acme", cid, "approval_consume", {"approval_id": "apr-000000000001"})


def test_d11_cli_log_marks_unlinked_observations(w, tmp_path, capsys):
    folder = project(tmp_path)
    cid = change(w)
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv=["sf", "project", "deploy", "start", "-m",
                                  "ApexClass:A", "-o", "acme-sbx"], resolve=ORGS.get, cwd=folder,
                                  validated_job="0Af000000000001AAA")
    item = grant(w, req)
    assert approval.consume(w, "Acme", item["command_sha256"], "acme-sbx", config=CONFIG, cwd=folder)[0]
    root, record = changes.load_change(w, "Acme", cid)
    changes._append(root, record, {"kind": "metadata_observation", "summary": "deploy", "basis":
                                   "salesforce_metadata_api", "target_org": "acme-sbx", "job_id": "0AfUNRELATED",
                                   "result": "pass", "observation": {}, "business_acceptance_proven": False})
    capsys.readouterr()
    assert cli.main(["approval", "log", "--workspace", str(w), "--client", "Acme"]) == 0
    assert "not linked" in capsys.readouterr().out


# D12

def test_d12_wildcard_intersection_is_exact():
    problems = permissions.drift({"permissions": {**permissions.generate(), "allow": ["Bash(p*on -c *)"]}},
                                 permissions.generate())
    assert any("p*on -c *" in p for p in problems)
    ok = permissions.drift({"permissions": {**permissions.generate(), "allow": ["Bash(git status)",
                                                                                "Bash(ls *)"]}},
                           permissions.generate())
    assert not any("allows" in p for p in ok)


# D13

def test_d13_json_export_serves_a_production_record_grant(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")
    export = tmp_path / "accounts.json"
    export.write_text(json.dumps({"status": 0, "result": {"records": [
        {"attributes": {"type": "Account"}, "Id": "001000000000001AAA", "Name": "A"}]}}), encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, export)
    argv = ["sf", "data", "update", "record", "-s", "Account", "-i", "001000000000001AAA", "-v", "Name=B",
            "-o", "acme-prod"]
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=argv, resolve=ORGS.get, cwd=folder,
                                  before_state_event=before["event_id"])
    assert grant(w, req)["id"]


def test_d13_csv_export_with_object_serves_a_production_record_grant(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")
    export = tmp_path / "accounts.csv"
    export.write_text("Id,Name\n001000000000001AAA,A\n", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, export, sobject="Account")
    argv = ["sf", "data", "update", "record", "-s", "Account", "-i", "001000000000001AAA", "-v", "Name=B",
            "-o", "acme-prod"]
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=argv, resolve=ORGS.get, cwd=folder,
                                  before_state_event=before["event_id"])
    assert grant(w, req)["id"]


# D15

def full_hook(root, monkeypatch, capsys, tool, inp, cwd):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(cwd), "tool_name": tool, "tool_input": inp})))
    code = gate.main()
    capsys.readouterr()
    return code


def test_d15_full_mode_guard_resolves_relative_and_ancestor_targets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.add_client(root, "Acme")
    granted = root / "clients" / "acme" / "approvals" / "granted"
    granted.mkdir(parents=True)
    assert full_hook(root, monkeypatch, capsys, "Write", {"file_path": "apr-000000000001.json", "content": "{}"},
                     granted) == 2
    assert full_hook(root, monkeypatch, capsys, "Bash", {"command": "rm -rf clients/acme"}, root) == 2
    assert full_hook(root, monkeypatch, capsys, "Bash", {"command": "rm -rf clients/acme/notes"}, root) == 0


# Doctor check-only probe

def test_doctor_probes_check_only_unbound(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from torque import doctor_connected as dc
    routes = [p[0] for p in dc.PROBES]
    assert "check_only_unbound" in routes


# N1

def test_n1_mcp_record_write_without_files(w):
    tool, args = "mcp__salesforce__update_record", {"usernameOrAlias": "acme-sbx", "id": "001", "Name": "B"}
    req = approval.create_request(w, "Acme", change(w), "acme-sbx", mcp=(tool, args), resolve=ORGS.get)
    grant(w, req)
    assert approval.consume(w, "Acme", approval.call_key_for_mcp(tool, args), "acme-sbx", config=CONFIG)[0]


# N2

def test_n2_new_capture_spelling_is_an_org_read(w):
    assert run(w, "Bash", {"command": "torque approval request --workspace . --client acme --change c --org beta-prod "
                                      "--capture-before --metadata Flow:X -- sf x"}).action == "deny"


def test_n2_usable_consent_checks_the_client(w):
    path = w / "clients" / "acme" / "consent.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["client"] = "beta"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError, match="another client"):
        approval._usable_consent(w, "Acme")


# N3

def test_n3_wrapper_claim_is_single_use_under_concurrency(w, tmp_path):
    argv, item, folder = consumed_route(w, tmp_path)
    results = []
    barrier = threading.Barrier(4)

    def claim():
        barrier.wait()
        results.append(approval.consumed_for_wrapper(w, "Acme", ("torque", argv[1:]), "acme-sbx", cwd=folder))
    threads = [threading.Thread(target=claim) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(r is not None for r in results) == 1
