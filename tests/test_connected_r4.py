"""Fix round 4: the R2d recheck findings and the browser ruling. Written failing first."""
from collections import namedtuple
import io
import json
import os
from pathlib import Path

import pytest

from torque import approval, before_state, changes, consent, gate, gate_connected as gc, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
CONFIG = {"ai_access": "connected", "approval": "required", "approval_verify": "hmac"}
SBX = "https://acme--sbx.sandbox.lightning.force.com/lightning/setup/home"


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-prod", "acme-sbx"],
                           ["Contact"], presence=YES, resolve=ORGS.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def run(root, tool, inp, mode=None, session="s1"):
    return gc.decide_connected(tool, inp, root, root, env={"TORQUE_CLIENT": "acme"}, permission_mode=mode,
                               session_id=session, tool_use_id="t1")


def change(root, org="acme-sbx"):
    return changes.create_change(root, "Acme", "Change", "Works", [], org)["id"]


def grant(root, req, **kw):
    return approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                          resolve=ORGS.get, **kw)


def project(tmp_path):
    folder = tmp_path / "project"
    base = folder / "force-app" / "main" / "default"
    (base / "aura" / "Widget").mkdir(parents=True)
    (base / "lwc" / "card").mkdir(parents=True)
    (base / "classes").mkdir(parents=True)
    (folder / "sfdx-project.json").write_text(json.dumps({"packageDirectories": [{"path": "force-app"}]}),
                                              encoding="utf-8")
    for name in ("Widget.cmp", "WidgetController.js", "WidgetHelper.js", "WidgetRenderer.js"):
        (base / "aura" / "Widget" / name).write_text("x", encoding="utf-8")
    for name in ("card.js", "card.html", "card.js-meta.xml"):
        (base / "lwc" / "card" / name).write_text("x", encoding="utf-8")
    (base / "classes" / "Keep.cls").write_text("class", encoding="utf-8")
    (base / "classes" / "Keep.cls-meta.xml").write_text("<ApexClass/>", encoding="utf-8")
    return folder


# D1: every file of a bundle is bound

@pytest.mark.parametrize("component,member", [
    ("AuraDefinitionBundle:Widget", "aura/Widget/WidgetController.js"),
    ("AuraDefinitionBundle:Widget", "aura/Widget/WidgetRenderer.js"),
    ("LightningComponentBundle:card", "lwc/card/card.html"),
])
def test_d1_bundle_members_are_bound(tmp_path, component, member):
    folder = project(tmp_path)
    argv = ["sf", "project", "deploy", "start", "-m", component, "-o", "acme-sbx"]
    before, _ = approval.payload_digest(argv, folder)
    (folder / "force-app" / "main" / "default" / member).write_text("changed", encoding="utf-8")
    assert approval.payload_digest(argv, folder)[0] != before


# D7: an incomplete bundle or Apex capture does not cover recovery

def test_d7_lwc_needs_its_source_and_metadata():
    base = "evidence/b/lwc/card/"
    assert before_state.coverage(["LightningComponentBundle:card"], {"files": [{"path": base + "card.js-meta.xml"}]})
    assert not before_state.coverage(["LightningComponentBundle:card"],
                                     {"files": [{"path": base + "card.js-meta.xml"}, {"path": base + "card.js"}]})


def test_d7_apex_needs_source_and_metadata():
    assert before_state.coverage(["ApexClass:A"], {"files": [{"path": "evidence/b/classes/A.cls"}]})


def test_d7_production_lwc_grant_refused_with_metadata_only(w, tmp_path):
    folder = project(tmp_path)
    cid = change(w, "acme-prod")
    src = tmp_path / "before" / "lwc" / "card"
    src.mkdir(parents=True)
    (src / "card.js-meta.xml").write_text("<x/>", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, tmp_path / "before")
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=["sf", "project", "deploy", "start", "-m",
                                  "LightningComponentBundle:card", "-o", "acme-prod"], resolve=ORGS.get,
                                  cwd=folder, before_state_event=before["event_id"])
    with pytest.raises(ws.WorkspaceError, match="does not cover"):
        grant(w, req)


# D2 and N4 (ruling): browser changes only through Torque's own browser

def window(w, org="acme-sbx"):
    req = approval.create_request(w, "Acme", change(w, org), org, browser_minutes=10,
                                  purpose="Add Tier to the Case layout", resolve=ORGS.get)
    return grant(w, req)


def test_d2_mcp_browser_changes_are_refused_even_with_a_window(w):
    window(w)
    assert run(w, "mcp__claude-in-chrome__navigate", {"url": SBX, "tabId": 1}).action == "allow"
    assert run(w, "mcp__claude-in-chrome__computer", {"action": "left_click", "tabId": 1}).action == "deny"
    assert run(w, "mcp__chrome-devtools__click", {"uid": "x"}).action == "deny"
    assert run(w, "mcp__playwright__browser_click", {"element": "x"}).action == "deny"


def test_n4_a_search_string_cannot_authorize(w):
    window(w)
    assert run(w, "mcp__claude-in-chrome__find", {"query": SBX, "tabId": 2}).action == "allow"
    assert run(w, "mcp__claude-in-chrome__computer", {"action": "left_click", "tabId": 2}).action == "deny"


def test_d2_torque_browser_with_a_window(w):
    assert run(w, "Bash", {"command": "torque browser run --target-org acme-sbx"}).action == "deny"
    window(w)
    assert run(w, "Bash", {"command": "torque browser run --target-org acme-sbx"}).action == "allow"


def test_d2_torque_browser_guard_checks_every_request():
    from torque import browser_guard as bg
    guard = bg.Guard(org_alias="acme-sbx", org_id_18="00D000000000001AAA",
                     host_key=gc.org_key("https://acme--sbx.sandbox.my.salesforce.com"))
    assert bg.request_allowed(guard, "https://acme--sbx.sandbox.lightning.force.com/aura", "POST")
    assert bg.request_allowed(guard, "https://acme--sbx--c.sandbox.vf.force.com/apex/x", "GET")
    assert not bg.request_allowed(guard, "https://acme.lightning.force.com/aura", "POST")
    assert not bg.request_allowed(guard, "https://acme.my.salesforce.com/", "GET")
    assert not bg.request_allowed(guard, "https://login.salesforce.com/", "POST")
    assert bg.request_allowed(guard, "https://static.example.com/x.js", "GET")


def test_d2_torque_browser_refuses_without_a_window(w, monkeypatch):
    from torque import browser_guard as bg
    monkeypatch.setenv("TORQUE_WORKSPACE", str(w / "clients" / "acme"))
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: ORGS.get(alias))
    with pytest.raises(bg.GuardRefused, match="window"):
        bg.connected_guard("acme-sbx")
    window(w)
    guard = bg.connected_guard("acme-sbx")
    assert guard.org_id_18 == "00D000000000001AAA"
    moved = {**ORGS, "acme-sbx": Org("00D000000000009AAA", "sandbox", False, "https://x.sandbox.my.salesforce.com")}
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: moved.get(alias))
    with pytest.raises(bg.GuardRefused, match="org ID"):
        bg.connected_guard("acme-sbx")


def test_d2_not_connected_means_no_guard(tmp_path, monkeypatch):
    from torque import browser_guard as bg
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    assert bg.connected_guard("acme-sbx") is None


# D15: directory changes inside a command

def full_hook(root, monkeypatch, capsys, command):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(root), "tool_name": "Bash",
                                                             "tool_input": {"command": command}})))
    code = gate.main()
    capsys.readouterr()
    return code


@pytest.fixture
def full_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    root = ws.init_workspace(tmp_path / "full", "Firm")
    ws.add_client(root, "Acme")
    (root / "clients" / "acme" / "approvals" / "granted").mkdir(parents=True)
    (root / "build").mkdir()
    return root


@pytest.mark.parametrize("command,code", [
    ("cd clients/acme && rm -rf approvals", 2),
    ("cd clients/acme && printf '{}' > consent.json", 2),
    ("cd clients && cd acme/approvals && rm granted/x", 2),
    ('cd "$X" && rm -rf approvals', 2),
    ("cd build && rm -rf out", 0),
    ("cd clients/acme && cat context.md", 0),
])
def test_d15_directory_changes_are_followed(full_ws, monkeypatch, capsys, command, code):
    assert full_hook(full_ws, monkeypatch, capsys, command) == code


# N5: a recovery approval binds its snapshot

def test_n5_recovery_approval_binds_the_snapshot(w, tmp_path, monkeypatch):
    folder = project(tmp_path)
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "manifest.json").write_text('{"payload": {"record_id": "001A"}}', encoding="utf-8")
    monkeypatch.setattr(approval, "_recovery_snapshot",
                        lambda workspace, client, words, org_alias, org_id: (snap, ["deploy", "-o", org_alias]))
    argv = ["torque", "recover", "exec", "snap-1", "--org", "acme-sbx", "--workspace", str(w), "--client", "acme"]
    req = approval.create_request(w, "Acme", change(w), "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)
    assert req["recovery_plan"] == ["deploy", "-o", "acme-sbx"] and req["payload_files"] == 1
    item = grant(w, req)
    (snap / "manifest.json").write_text('{"payload": {"record_id": "001B"}}', encoding="utf-8")
    ok, why = approval.consume(w, "Acme", item["command_sha256"], "acme-sbx", config=CONFIG, cwd=folder)
    assert not ok and "changed" in why


def test_n5_missing_snapshot_is_refused(w, tmp_path, monkeypatch):
    folder = project(tmp_path)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(w / "clients" / "acme"))
    argv = ["torque", "recover", "exec", "0123456789abcdef", "--org", "acme-sbx", "--workspace", str(w),
            "--client", "acme"]
    with pytest.raises(ws.WorkspaceError, match="snapshot"):
        approval.create_request(w, "Acme", change(w), "acme-sbx", argv=argv, resolve=ORGS.get, cwd=folder)


# N6: destructive deploys

def destructive(folder):
    (folder / "destructive.xml").write_text("<Package><types><members>Old</members><name>ApexClass</name></types>"
                                            "</Package>", encoding="utf-8")
    return ["sf", "project", "deploy", "start", "-m", "ApexClass:Keep", "--post-destructive-changes",
            "destructive.xml", "-o", "acme-prod"]


def test_n6_deleted_component_needs_no_local_file_but_needs_recovery_evidence(w, tmp_path):
    folder = project(tmp_path)
    argv = destructive(folder)
    cid = change(w, "acme-prod")
    src = tmp_path / "before" / "classes"
    src.mkdir(parents=True)
    for name in ("Keep.cls", "Keep.cls-meta.xml"):
        (src / name).write_text("x", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, tmp_path / "before")
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=argv, resolve=ORGS.get, cwd=folder,
                                  before_state_event=before["event_id"])
    with pytest.raises(ws.WorkspaceError, match="ApexClass:Old"):
        grant(w, req)
    for name in ("Old.cls", "Old.cls-meta.xml"):
        (src / name).write_text("x", encoding="utf-8")
    before = before_state.import_before_state(w, "Acme", cid, tmp_path / "before")
    req = approval.create_request(w, "Acme", cid, "acme-prod", argv=argv, resolve=ORGS.get, cwd=folder,
                                  before_state_event=before["event_id"])
    assert grant(w, req)["id"]


def test_n6_destructive_manifest_is_bound(tmp_path):
    folder = project(tmp_path)
    argv = destructive(folder)
    before, _ = approval.payload_digest(argv, folder)
    (folder / "destructive.xml").write_text("<Package><types><members>Keep</members><name>ApexClass</name></types>"
                                            "</Package>", encoding="utf-8")
    assert approval.payload_digest(argv, folder)[0] != before
