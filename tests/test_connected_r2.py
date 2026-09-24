"""R2 external review findings for connected mode (fix round 2). Written failing first."""
from collections import namedtuple
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from torque import (approval, before_state, changes, cli, cli_approval, consent, gate, gate_connected as gc,
                    namespaces, permissions, presence, workspace as ws)
from torque.connected_routes import classify
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
CONFIG = {"ai_access": "connected", "approval": "required", "approval_verify": "hmac"}
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX file ownership")


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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
    return approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=kw.pop("out", io.StringIO()),
                          resolve=ORGS.get, **kw)


def project(tmp_path):
    folder = tmp_path / "project"
    (folder / "force-app" / "main" / "default" / "classes").mkdir(parents=True)
    (folder / "sfdx-project.json").write_text(json.dumps({"packageDirectories": [{"path": "force-app"}]}),
                                              encoding="utf-8")
    for name in ("A", "B"):
        (folder / "force-app" / "main" / "default" / "classes" / f"{name}.cls").write_text("class", encoding="utf-8")
    return folder


# D1: the payload covers every file that decides the write

def test_d1_tree_import_plan_dependencies_are_bound(w, tmp_path):
    folder = project(tmp_path)
    (folder / "data").mkdir()
    (folder / "data" / "Account.json").write_text('{"records": []}', encoding="utf-8")
    (folder / "data" / "plan.json").write_text(json.dumps([{"sobject": "Account", "files": ["Account.json"]}]),
                                               encoding="utf-8")
    argv = ["sf", "data", "import", "tree", "--plan", "data/plan.json", "-o", "acme-sbx"]
    before, _ = approval.payload_digest(argv, folder)
    (folder / "data" / "Account.json").write_text('{"records": [{"Name": "X"}]}', encoding="utf-8")
    assert approval.payload_digest(argv, folder)[0] != before


def test_d1_every_value_of_a_multi_value_flag_is_bound(w, tmp_path):
    folder = project(tmp_path)
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "ApexClass:B", "-o", "acme-sbx"]
    before, count = approval.payload_digest(argv, folder)
    (folder / "force-app" / "main" / "default" / "classes" / "B.cls").write_text("changed", encoding="utf-8")
    assert approval.payload_digest(argv, folder)[0] != before
    assert before_state.deploy_components(argv, folder) == ["ApexClass:A", "ApexClass:B"]


def test_d1_legacy_source_path_is_bound(w, tmp_path):
    folder = project(tmp_path)
    argv = ["sfdx", "force:source:deploy", "--sourcepath", "force-app", "-u", "acme-sbx"]
    before, count = approval.payload_digest(argv, folder)
    assert count >= 2
    (folder / "force-app" / "main" / "default" / "classes" / "A.cls").write_text("changed", encoding="utf-8")
    assert approval.payload_digest(argv, folder)[0] != before


def test_d1_missing_or_linked_payload_is_refused(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    with pytest.raises(ws.WorkspaceError, match="does not exist"):
        approval.create_request(w, "Acme", cid, "acme-sbx", argv=["sf", "apex", "run", "-f", "missing.apex", "-o",
                                                                  "acme-sbx"], resolve=ORGS.get, cwd=folder)
    if os.name != "nt":
        (folder / "real.apex").write_text("System.debug(1);", encoding="utf-8")
        (folder / "link.apex").symlink_to(folder / "real.apex")
        with pytest.raises(ws.WorkspaceError, match="link"):
            approval.create_request(w, "Acme", cid, "acme-sbx", argv=["sf", "apex", "run", "-f", "link.apex", "-o",
                                                                      "acme-sbx"], resolve=ORGS.get, cwd=folder)


def test_d1_mcp_file_inputs_are_bound(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    tool, args = "mcp__salesforce__deploy_metadata", {"usernameOrAlias": "acme-sbx", "sourceDir": "force-app"}
    req = approval.create_request(w, "Acme", cid, "acme-sbx", mcp=(tool, args), resolve=ORGS.get, cwd=folder)
    assert req["payload_digest"]
    grant(w, req)
    (folder / "force-app" / "main" / "default" / "classes" / "A.cls").write_text("changed", encoding="utf-8")
    ok, why = approval.consume(w, "Acme", approval.call_key_for_mcp(tool, args), "acme-sbx", config=CONFIG,
                               cwd=folder)
    assert not ok and "changed" in why


# D2: a browser window is bound to the org the browser is in

def browser_window(w, org="acme-sbx"):
    cid = change(w, org)
    kw = {"manual_recovery": "Undo the layout change in Setup, restoring the saved layout."} if org == "acme-prod" else {}
    req = approval.create_request(w, "Acme", cid, org, browser_minutes=10, purpose="Add Tier to the Case layout",
                                  resolve=ORGS.get, **kw)
    return grant(w, req)


def test_d2_click_needs_navigation_to_the_window_org(w):
    browser_window(w, "acme-sbx")
    click = ("mcp__claude-in-chrome__computer", {"action": "left_click"})
    assert run(w, *click).action == "deny"
    nav = {"url": "https://acme--sbx.sandbox.lightning.force.com/lightning/setup/ObjectManager/home"}
    assert run(w, "mcp__claude-in-chrome__navigate", nav).action == "allow"
    assert run(w, *click).action == "allow"
    assert run(w, *click, session="other").action == "deny"


def test_d2_sandbox_window_does_not_cover_production(w):
    browser_window(w, "acme-sbx")
    assert run(w, "mcp__claude-in-chrome__navigate", {"url": "https://acme.lightning.force.com/"}).action == "allow"
    assert run(w, "mcp__claude-in-chrome__computer", {"action": "left_click"}).action == "deny"


def test_d2_navigation_to_an_org_outside_consent_is_refused(w):
    assert run(w, "mcp__claude-in-chrome__navigate",
               {"url": "https://beta.my.salesforce.com/"}).action == "deny"
    assert run(w, "mcp__claude-in-chrome__navigate", {"url": "https://example.com/"}).action == "allow"


def test_d2_switching_tabs_forgets_the_org(w):
    browser_window(w, "acme-sbx")
    run(w, "mcp__claude-in-chrome__navigate", {"url": "https://acme--sbx.sandbox.my.salesforce.com/"})
    assert run(w, "mcp__chrome-devtools__select_page", {"pageIdx": 2}).action == "allow"
    assert run(w, "mcp__chrome-devtools__click", {"uid": "x"}).action == "deny"


# D3: equivalent reads honor the data classes

@pytest.mark.parametrize("tool,inp", [
    ("Bash", {"command": "sfdx force:data:soql:query -q 'SELECT Id FROM Account' -u acme-prod"}),
    ("Bash", {"command": "sfdx force:apex:log:get -i 07L -u acme-prod"}),
    ("Bash", {"command": "sf api request rest /services/data/v60.0/sobjects/Account/001000000000001 -o acme-prod"}),
    ("Bash", {"command": "sf api request rest /services/data/v60.0/query?q=SELECT+Id+FROM+Account -o acme-prod"}),
    ("mcp__salesforce__get_record", {"usernameOrAlias": "acme-prod", "id": "001"}),
    ("mcp__salesforce__get_apex_log", {"usernameOrAlias": "acme-prod", "id": "07L"}),
])
def test_d3_record_and_log_reads_need_their_class(w, tool, inp):
    assert run(w, tool, inp).action == "deny"


def test_d3_metadata_reads_still_pass(w):
    assert run(w, "Bash", {"command": "sf api request rest /services/data/v60.0/sobjects/Account/describe "
                                      "-o acme-prod"}).action == "allow"
    assert run(w, "mcp__salesforce__describe_object", {"usernameOrAlias": "acme-prod", "object": "Account"}
               ).action == "allow"


# D4: script approvals check suspended consent; sign-off is validated

def test_d4_require_refuses_after_suspension(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "acme-sbx"]
    grant(w, approval.create_request(w, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder))
    consent.suspend(w, "Acme", presence=YES)
    ok, why = approval.require(w, "Acme", "acme-sbx", argv, config=CONFIG, cwd=folder)
    assert not ok and "suspended" in why


def test_d4_sign_off_needs_a_time_and_the_record_its_client():
    base = {"schema": consent.SCHEMA, "client": "acme", "status": "active", "data_allowed": ["metadata"],
            "approved_orgs": [{"alias": "a", "org_id_18": "00D000000000001AAA", "kind": "sandbox"}],
            "reviewer": {"name": "R", "signed_off_at": "2026-09-30T10:00:00+00:00"}}
    assert consent.consent_problems(base) == []
    assert consent.consent_problems({**base, "reviewer": {"name": "R"}})
    assert consent.consent_problems({**base, "reviewer": {"name": "R", "signed_off_at": "yesterday"}})
    assert consent.consent_problems(base, client="beta")


# D5: enumeration and explicit orgs respect scoping

def test_d5_client_list_is_scoped(w):
    assert run(w, "Bash", {"command": "torque client list --workspace ."}).action == "deny"
    assert run(w, "Bash", {"command": "torque client list --workspace ."}, client=None).action == "deny"


def test_d5_unverifiable_call_naming_an_org_outside_consent_is_refused(w):
    assert run(w, "Bash", {"command": "HOME=/tmp/x sf data query -q x -o beta-prod"}).action == "deny"
    assert run(w, "Bash", {"command": "HOME=/tmp/x sf project retrieve start -o acme-prod"}).action == "ask"


# D6: skipped prompts refuse writes and browser actions too; shell -c asks

def test_d6_approved_write_refused_in_bypass_mode_and_not_used(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "acme-sbx"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)
    grant(w, req)
    assert run(w, "Bash", {"command": req["command"]}, mode="bypassPermissions", cwd=folder).action == "deny"
    assert run(w, "Bash", {"command": req["command"]}, cwd=folder).action == "allow"


def test_d6_browser_action_refused_in_bypass_mode(w):
    browser_window(w, "acme-sbx")
    run(w, "mcp__claude-in-chrome__navigate", {"url": "https://acme--sbx.sandbox.my.salesforce.com/"})
    assert run(w, "mcp__claude-in-chrome__computer", {"action": "left_click"}, mode="auto").action == "deny"


def test_d6_shell_c_asks(w):
    assert run(w, "Bash", {"command": "sh -c 'echo reviewed'"}).action == "ask"


# D7: production before-state checks coverage and provenance

def test_d7_source_dir_deploy_lists_its_components(tmp_path):
    folder = project(tmp_path)
    argv = ["sf", "project", "deploy", "start", "-d", "force-app", "-o", "x"]
    assert sorted(before_state.deploy_components(argv, folder)) == ["ApexClass:A", "ApexClass:B"]


def test_d7_meta_file_alone_does_not_cover_apex_source():
    before = {"files": [{"path": "evidence/before-1/classes/Example.cls-meta.xml"}]}
    assert before_state.coverage(["ApexClass:Example"], before) == ["ApexClass:Example"]
    before["files"].append({"path": "evidence/before-1/classes/Example.cls"})
    assert before_state.coverage(["ApexClass:Example"], before) == []


def test_d7_production_write_without_listable_components_needs_recovery(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")
    src = tmp_path / "unrelated"
    src.mkdir()
    (src / "unrelated.txt").write_text("x", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, src)
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=["sf", "apex", "run", "-f", "force-app/main/"
                                  "default/classes/A.cls", "-o", "acme-prod"], resolve=ORGS.get, cwd=folder,
                                  before_state_event=before["event_id"])
    with pytest.raises(ws.WorkspaceError, match="recovery"):
        grant(w, req)


def test_d7_capture_records_org_identity_and_grant_checks_it(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")

    def fake(cmd, **kw):
        out = cmd[cmd.index("--target-metadata-dir") + 1]
        os.makedirs(os.path.join(out, "unpackaged", "classes"))
        Path(out, "unpackaged", "classes", "A.cls").write_text("class", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": 0, "result": {"id": "09S1"}}), "")

    before = before_state.capture_metadata(w, "Acme", cid, "acme-prod", ["ApexClass:A"], run=fake,
                                           org_id_18="00D000000000009AAA")
    assert before["org_id_18"] == "00D000000000009AAA"
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=["sf", "project", "deploy", "start", "-m",
                                  "ApexClass:A", "-o", "acme-prod"], resolve=ORGS.get, cwd=folder,
                                  before_state_event=before["event_id"])
    with pytest.raises(ws.WorkspaceError, match="another org"):
        grant(w, req)


def test_d7_capture_checks_identity_before_reading(w, tmp_path, monkeypatch):
    cid = change(w, "acme-prod")
    moved = {**ORGS, "acme-prod": Org("00D000000000009AAA", "production", True, "https://x.my.salesforce.com")}
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: moved.get(alias))
    monkeypatch.setattr(before_state, "capture_metadata", lambda *a, **k: pytest.fail("read before the check"))
    code = cli.main(["approval", "request", "--workspace", str(w), "--client", "Acme", "--change", cid, "--org",
                     "acme-prod", "--capture-before-metadata", "ApexClass:A", "--", "sf", "project", "deploy",
                     "start", "-m", "ApexClass:A", "-o", "acme-prod"])
    assert code == 2


# D8: wrapper verification fails closed and authenticates the approval

def test_d8_unreadable_workspace_config_fails_closed(tmp_path, monkeypatch):
    from jsc_revert.wrappers import _common as c
    client = tmp_path / "firm" / "clients" / "acme"
    client.mkdir(parents=True)
    (client / "client.json").write_text("{}", encoding="utf-8")
    (tmp_path / "firm" / "workspace.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    assert c.connected_approval("acme-sbx", "00D000000000001AAA")[0] == c.EXIT_NOT_APPROVED


def test_d8_connected_workspace_found_from_the_working_folder(w, monkeypatch):
    from jsc_revert.wrappers import _common as c
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    monkeypatch.setenv("TORQUE_CLIENT", "acme")
    monkeypatch.chdir(w)
    monkeypatch.setattr(c, "_consumed_approval", lambda *a: None)
    assert c.connected_approval("acme-sbx", "00D000000000001AAA")[0] == c.EXIT_NOT_APPROVED


def wrapper_setup(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    argv = ["torque", "deploy", "-o", "acme-sbx", "--metadata", "ApexClass:A", "--workspace", str(w),
            "--client", "acme"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)
    item = grant(w, req)
    assert approval.consume(w, "Acme", approval.call_key_for_command(req["command"]), "acme-sbx", config=CONFIG,
                            cwd=folder)[0]
    return argv, item, folder


def test_d8_wrapper_lookup_authenticates_the_approval(w, tmp_path):
    argv, item, _ = wrapper_setup(w, tmp_path)
    path = w / "clients" / "acme" / "approvals" / "granted" / f"{item['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["org_id_18"] = "00D000000000009AAA"
    path.write_text(json.dumps(data), encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)
    assert approval.consumed_for_wrapper(w, "Acme", ("torque", argv[1:]), "acme-sbx") is None


def test_d8_revert_child_must_match_the_authorized_command(w, tmp_path):
    argv, item, _ = wrapper_setup(w, tmp_path)
    assert approval.consumed_for_wrapper(w, "Acme", ("torque", argv[1:]), "acme-sbx")
    child = ["deploy", "-o", "acme-sbx", "--metadata", "ApexClass:A"]
    approval.authorize_child(w, "Acme", item["id"], child)
    assert approval.approved_parent(w, "Acme", item["id"], "acme-sbx", ("jsc", ["deploy", "-o", "acme-sbx"])) is None
    assert approval.approved_parent(w, "Acme", item["id"], "acme-sbx", ("jsc", child))["id"] == item["id"]
    assert approval.approved_parent(w, "Acme", item["id"], "acme-sbx", ("jsc", child)) is None


# D9: a transient org-resolution failure in the wrapper does not use up the approval

def test_d9_released_approval_can_be_used_again(w, tmp_path):
    argv, item, folder = wrapper_setup(w, tmp_path)
    key = approval.call_key_for_command(item["command"])
    assert not approval.consume(w, "Acme", key, "acme-sbx", config=CONFIG, cwd=folder)[0]
    assert approval.release_for_retry(w, "Acme", ("torque", argv[1:]), "acme-sbx")
    assert approval.consume(w, "Acme", key, "acme-sbx", config=CONFIG, cwd=folder)[0]


# D10: the audit record is mandatory

@posix_only
def test_d10_consume_refused_when_the_change_record_cannot_be_written(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "acme-sbx"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)
    grant(w, req)
    events = w / "clients" / "acme" / "changes" / cid / "events"
    os.chmod(events, 0o500)
    try:
        ok, why = approval.consume(w, "Acme", approval.call_key_for_command(req["command"]), "acme-sbx",
                                   config=CONFIG, cwd=folder)
    finally:
        os.chmod(events, 0o700)
    assert not ok and "record" in why
    assert approval.consume(w, "Acme", approval.call_key_for_command(req["command"]), "acme-sbx", config=CONFIG,
                            cwd=folder)[0]


@posix_only
def test_d10_check_only_refused_when_it_cannot_be_logged(w):
    base = w / "clients" / "acme" / "approvals"
    base.mkdir(exist_ok=True)
    os.chmod(base, 0o500)
    try:
        decision = run(w, "Bash", {"command": "sf project deploy validate -m ApexClass:A -o acme-prod"})
    finally:
        os.chmod(base, 0o700)
    assert decision.action == "deny"


# D11: approval records and events carry the audit fields

def test_d11_audit_fields_and_recovery_decision(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "acme-prod"]
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=argv, resolve=ORGS.get, cwd=folder,
                                  manual_recovery="Redeploy the previous version of class A from the release tag.")
    item = grant(w, req)
    assert item["command_sha256"] == approval.call_key_for_command(req["command"])
    assert approval.consume(w, "Acme", item["command_sha256"], "acme-prod", config=CONFIG, cwd=folder)[0]
    events = changes.get_change(w, "Acme", cid)["events"]
    assert any(e["kind"] == "decision" and "Redeploy" in e["summary"] for e in events)
    used = [e for e in events if e["kind"] == "approval_consume"][-1]
    for key in ("request_id", "approval_id", "command", "command_sha256", "org_alias", "org_id_18", "org_kind",
                "approver", "manual_recovery", "granted_at", "expires_at", "session_id", "tool_use_id"):
        assert key in used, key


def test_d11_log_links_observations_by_job(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "acme-sbx"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder,
                                  validated_job="0Af000000000001AAA")
    grant(w, req)
    assert approval.consume(w, "Acme", approval.call_key_for_command(req["command"]), "acme-sbx", config=CONFIG,
                            cwd=folder)[0]
    root, record = changes.load_change(w, "Acme", cid)
    for job in ("0Af000000000001AAA", "0Af000000000002AAA"):
        changes._append(root, record, {"kind": "metadata_observation", "summary": "deploy", "basis":
                                       "salesforce_metadata_api", "target_org": "acme-sbx", "job_id": job,
                                       "result": "pass", "observation": {}, "business_acceptance_proven": False})
    row = [r for r in approval.approval_log(w, "Acme") if r["kind"] == "approval_consume"][0]
    linked = {o["job_id"]: o["linked"] for o in row["later_observations"]}
    assert linked == {"0Af000000000001AAA": True, "0Af000000000002AAA": False}


# D12: permission rules and doctor readiness

def test_d12_generated_rules_cover_more_routes():
    gen = permissions.generate()
    for rule in ("Bash(torque ai-regression:*)", "Bash(python*)", "Bash(node*)", "Bash(pip*)"):
        assert rule in gen["ask"], rule


def test_d12_wildcard_allows_are_overlaps():
    problems = permissions.drift({"permissions": {**permissions.generate(), "allow": ["Bash(*python*)",
                                                                                    "Bash(sf *)"]}},
                                 permissions.generate())
    assert sum("allows" in p for p in problems) == 2


def make_doctor_workspace(tmp_path, command=None):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    command = command or gate.hook_command(sys.executable.replace("\\", "/"))
    settings = {"permissions": permissions.generate(), "hooks": {"PreToolUse": [
        {"matcher": ".*", "hooks": [{"type": "command", "command": command, "timeout": 600}]}]}}
    (root / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return root


def ok_probe(event):
    return (0, '{"hookSpecificOutput": {"permissionDecision": "ask"}}') if "doctor_probe.py" in json.dumps(event) \
        else (2, "")


def test_d12_doctor_requires_the_fail_closed_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    from torque import doctor_connected as dc
    root = make_doctor_workspace(tmp_path, command=f'"{sys.executable}" -I -m torque.gate')
    assert any("fail-closed" in p for p in dc.report(root, None, probe=ok_probe)["problems"])


def test_d12_doctor_reads_user_and_local_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Path.home() on Windows
    from torque import doctor_connected as dc
    root = make_doctor_workspace(tmp_path)
    assert dc.report(root, None, probe=ok_probe)["ready"]
    (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}),
                                                    encoding="utf-8")
    assert not dc.report(root, None, probe=ok_probe)["ready"]
    (home / ".claude" / "settings.json").unlink()
    (root / ".claude" / "settings.local.json").write_text(json.dumps({"permissions": {"defaultMode": "auto"}}),
                                                          encoding="utf-8")
    assert not dc.report(root, None, probe=ok_probe)["ready"]


# D13: recovery inputs and grant-screen evidence

def test_d13_before_state_from_a_single_export_file(w, tmp_path):
    cid = change(w, "acme-prod")
    export = tmp_path / "accounts.csv"
    export.write_text("Id,Name\n001,A\n", encoding="utf-8")
    item = before_state.import_before_state(w, "Acme", cid, export)
    assert item["files"][0]["path"].endswith("accounts.csv")


def test_d13_capture_before_with_metadata_option_parses(w):
    parsed = cli.build_parser().parse_args(["approval", "request", "--workspace", ".", "--client", "Acme", "--change",
                                            "chg-000000000000", "--org", "acme-prod", "--capture-before",
                                            "--metadata", "Flow:X", "--record", "Account:001"])
    assert parsed.capture_before and parsed.metadata == ["Flow:X"] and parsed.record == ["Account:001"]


def test_d13_grant_shows_validation_result_and_audit_trail(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")
    src = tmp_path / "b" / "classes"
    src.mkdir(parents=True)
    (src / "A.cls").write_text("class", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, tmp_path / "b")
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "acme-prod"]
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=argv, resolve=ORGS.get, cwd=folder,
                                  before_state_event=before["event_id"], validated_job="0Af000000000001AAA")
    trail = tmp_path / "trail.json"
    trail.write_text(json.dumps({"result": {"records": [
        {"CreatedDate": "2999-01-01T00:00:00.000+0000", "Display": "Changed Apex Class code: A", "Section": "Apex"}]}}),
        encoding="utf-8")
    out = io.StringIO()
    grant(w, req, out=out, report=lambda job, org: {"status": "Succeeded", "checkOnly": True}, audit_trail=trail)
    text = out.getvalue()
    assert "Succeeded" in text and "changed after the before-state" in text


# D14: payloads above the cap go through the Torque route

def test_d14_large_payload_uses_the_wrapper_route(w, tmp_path, monkeypatch):
    folder = project(tmp_path)
    cid = change(w)
    monkeypatch.setattr(approval, "PAYLOAD_FILE_CAP", 1)
    raw = ["sf", "project", "deploy", "start", "-d", "force-app", "-o", "acme-sbx"]
    with pytest.raises(ws.WorkspaceError, match="torque deploy"):
        approval.create_request(w, "Acme", cid, "acme-sbx", argv=raw, resolve=ORGS.get, cwd=folder)
    argv = ["torque", "deploy", "-o", "acme-sbx", "--source-dir", "force-app", "--workspace", str(w),
            "--client", "acme"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)
    assert req["payload_check"] == "wrapper"
    grant(w, req)
    assert approval.consume(w, "Acme", approval.call_key_for_command(req["command"]), "acme-sbx", config=CONFIG,
                            cwd=folder)[0]
    (folder / "force-app" / "main" / "default" / "classes" / "A.cls").write_text("changed", encoding="utf-8")
    assert approval.consumed_for_wrapper(w, "Acme", ("torque", argv[1:]), "acme-sbx") is None


# D15: consent, evidence and approval files guarded in full mode too

def test_d15_full_mode_guards_approval_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.add_client(root, "Acme")

    def hook(tool, inp):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "tool_name": tool,
                                                                 "tool_input": inp})))
        code = gate.main()
        capsys.readouterr()
        return code
    assert hook("Write", {"file_path": str(root / "clients" / "acme" / "consent.json"), "content": "{}"}) == 2
    assert hook("Bash", {"command": "rm -rf clients/acme/approvals"}) == 2
    assert hook("Write", {"file_path": str(root / "clients" / "acme" / "notes.md"), "content": "x"}) == 0
    assert hook("Bash", {"command": "sf data query -q x -o prod"}) == 0


# D16: key protection and tier validation

def test_d16_reading_the_key_is_refused(w):
    assert run(w, "Read", {"file_path": str(approval.key_path())}).action == "deny"


def test_d16_generated_rules_name_the_platform_key_path():
    gen = permissions.generate()
    assert any("approval.key" in r and r.startswith("Read(") for r in gen["deny"])


@posix_only
def test_d16_owner_uid_must_be_another_account(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w)
    argv = ["sf", "project", "deploy", "start", "-m", "ApexClass:A", "-o", "acme-sbx"]
    req = approval.create_request(w, "Acme", cid, "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)
    grant(w, req)
    same = {**CONFIG, "approval_verify": "owner-uid", "approver_uid": os.getuid()}
    ok, why = approval.consume(w, "Acme", approval.call_key_for_command(req["command"]), "acme-sbx", config=same,
                               cwd=folder)
    assert not ok and "separate" in why


def test_d16_owner_uid_refused_where_unsupported(tmp_path, monkeypatch):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    monkeypatch.setattr(ws, "_owner_uid_supported", lambda: False)
    with pytest.raises(ws.WorkspaceError, match="not supported"):
        ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=502, presence=YES)


# D17: empty agent markers count

def test_d17_empty_agent_variable_counts():
    class Tty(io.StringIO):
        def isatty(self):
            return True
    assert not presence.operator_present(env={"CLAUDECODE": ""}, stdin=Tty(), stdout=Tty(),
                                         ancestors=lambda: []).ok


# D18: attached manifest flag

def test_d18_attached_manifest_flag(tmp_path):
    (tmp_path / "package.xml").write_text("<Package><types><members>npsp__X__c</members><name>CustomObject</name>"
                                          "</types></Package>", encoding="utf-8")
    assert namespaces.find_namespaces(["--manifest=package.xml"], tmp_path) == ["npsp"]
