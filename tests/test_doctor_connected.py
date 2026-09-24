import json
import os
import sys
from collections import namedtuple

import pytest

from torque import cli, consent, doctor_connected as dc, gate, permissions, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    # Doctor reads the user's own settings file; keep the real one out of these tests.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


def make(tmp_path, settings, hook=True):
    root = ws.init_workspace(tmp_path / "w", "Firm")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    data = dict(settings)
    if hook:
        command = gate.hook_command(sys.executable.replace("\\", "/"))
        data["hooks"] = {"PreToolUse": [{"matcher": ".*", "hooks": [{"type": "command", "command": command,
                                                                      "timeout": 600}]}]}
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    return root


def fake_probe(event):
    text = json.dumps(event)
    if "python3" in text and "bypassPermissions" not in text:
        return 0, '{"hookSpecificOutput": {"permissionDecision": "ask"}}'
    if event.get("tool_use_id") == "doctor-bound_read":
        return 0, ""
    return 2, ""


def test_missing_rules_not_ready(tmp_path):
    report = dc.report(make(tmp_path, {}), None, probe=fake_probe)
    assert not report["ready"] and any("permission" in p for p in report["problems"])


def test_missing_hook_not_ready(tmp_path):
    report = dc.report(make(tmp_path, {"permissions": permissions.generate()}, hook=False), None, probe=fake_probe)
    assert any("hook" in p for p in report["problems"])


def test_probe_expectations(tmp_path):
    root = make(tmp_path, {"permissions": permissions.generate()})
    report = dc.report(root, None, probe=fake_probe)
    kinds = {p["route"]: p["got"] for p in report["probes"]}
    assert kinds == {"org_write": "deny", "read_unbound": "deny", "check_only_unbound": "deny",
                     "unverifiable": "ask", "admin": "deny", "browser_write": "deny"}
    assert report["ready"], report["problems"]
    assert any("tier 1" in a for a in report["advice"])


def test_probe_mismatch_not_ready(tmp_path):
    root = make(tmp_path, {"permissions": permissions.generate()})
    report = dc.report(root, None, probe=lambda event: (0, ""))
    assert not report["ready"] and any("probe org_write" in p for p in report["problems"])


def test_real_hook_probes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = make(tmp_path, {"permissions": permissions.generate()})
    report = dc.report(root, None)
    assert {p["route"]: p["got"] for p in report["probes"]} == {p["route"]: p["expected"] for p in report["probes"]}


def test_client_consent_and_live_identity(tmp_path, monkeypatch):
    Org = namedtuple("Org", "org_id_18 detected_org_type")
    root = make(tmp_path, {"permissions": permissions.generate()})
    ws.add_client(root, "Acme")
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"x")
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    report = dc.report(root, "Acme", probe=fake_probe)
    assert any("no consent record" in p for p in report["problems"])
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-sbx"], ["C"], presence=YES,
                           resolve={"acme-sbx": Org("00D000000000001AAA", "sandbox")}.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    assert dc.report(root, "Acme", probe=fake_probe)["ready"]
    moved = dc.report(root, "Acme", live=True, probe=fake_probe,
                      resolve={"acme-sbx": Org("00D000000000009AAA", "sandbox")}.get)
    assert any("resolves to 00D000000000009AAA" in p for p in moved["problems"])


@pytest.mark.skipif(os.name == "nt", reason="uid tier is POSIX only")
def test_owner_uid_needs_a_separate_account(tmp_path):
    root = make(tmp_path, {"permissions": permissions.generate()})
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=os.getuid(),
                     presence=YES)
    report = dc.report(root, None, probe=fake_probe)
    assert any("separate OS account" in p for p in report["problems"])


def test_doctor_cli_reports_connected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = make(tmp_path, {})
    code = cli.main(["doctor", "--workspace", str(root)])
    out = capsys.readouterr().out
    assert code == 3 and "AI access: connected" in out and "Probe org_write" in out


def test_real_hook_bound_probes(tmp_path, monkeypatch):
    Org = namedtuple("Org", "org_id_18 detected_org_type")
    root = make(tmp_path, {"permissions": permissions.generate()})
    ws.add_client(root, "Acme")
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"x")
    monkeypatch.setattr("jsc_revert.intent_marker._current_user_name", lambda: "consultant")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-sbx"], ["C"], presence=YES,
                           resolve={"acme-sbx": Org("00D000000000001AAA", "sandbox")}.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    report = dc.report(root, "Acme")
    got = {p["route"]: (p["expected"], p["got"]) for p in report["probes"]}
    assert "bound_read" in got and all(expected == seen for expected, seen in got.values()), got
