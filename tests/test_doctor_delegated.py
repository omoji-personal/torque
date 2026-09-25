"""Doctor for the unattended profile, with delegates, setup steps and approvals by
kind (task D15, spec requirements 11 and 19). Every delegated write_settings call
passes root_owner=FAKE_OWNER (R41: the delegate must not own the workspace
directory, which a single-uid test cannot arrange otherwise; see
tests/test_permission_profiles.py)."""
import io
import json
import os
import sys

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, MODEL, ORGS, as_agent, delegated_grant, delegated_workspace,
                               flow_request)
from torque import cli, doctor_connected, gate, permissions

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def ready_root(tmp_path, monkeypatch, **extra):
    root = delegated_workspace(tmp_path, monkeypatch)
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN, **extra)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    as_agent(monkeypatch)   # doctor refuses an approver_uid equal to the session's own account
    return root


def edit_settings(root, change):
    path = root / ".claude" / "settings.json"
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_unattended_workspace_is_ready_with_every_probe_documented(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    result = doctor_connected.report(root, "Acme", live=True, resolve=ORGS.get)
    assert result["ready"], result["problems"]
    assert result["profile"] == "unattended" and result["delegates"]["approver"]["kind"] == "ai"
    assert [p["route"] for p in result["probes"]] == list(doctor_connected.PROBE_ROUTES)
    assert set(doctor_connected.UNDER_P) == set(doctor_connected.PROBE_ROUTES)
    assert all(p["under_p"] for p in result["probes"])
    steps = {s["step"]: s for s in result["setup_steps"]}
    # F4: the fixture sets the mode on the owner path, so there is no ai-access step here (D2 proves it).
    assert {"delegates", "permissions"} <= set(steps) and steps["permissions"]["kind"] == "ai"
    assert not list((root / "clients/acme/approvals/consumed").glob("probe-*.launch"))


def test_gated_ask_under_unattended_is_drift(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    edit_settings(root, lambda data: data["permissions"]["ask"].append("Bash(torque deploy:*)"))
    result = doctor_connected.report(root, "Acme")
    assert not result["ready"] and any("gate decides" in p for p in result["problems"])


def test_unattended_needs_the_post_hook(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    edit_settings(root, lambda data: data["hooks"].pop("PostToolUse"))
    assert any("PostToolUse" in p for p in doctor_connected.report(root, "Acme")["problems"])


def test_unattended_without_delegation_is_not_ready(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    config = json.loads((root / "workspace.json").read_text())
    config["delegates"].pop("approver")
    (root / "workspace.json").write_text(json.dumps(config))
    assert any("unattended profile" in p for p in doctor_connected.report(root, "Acme")["problems"])


def test_user_settings_come_from_claude_config_dir(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash(torque deploy:*)"]}}))
    assert any("torque deploy" in p for p in doctor_connected.report(root, "Acme")["problems"])


# Carry-forwards and rulings beyond the brief's tests.

def test_probe_launch_record_binds_the_bound_probes(tmp_path, monkeypatch):
    """F49: bound probes run with TORQUE_LAUNCH naming a probe record written for the
    doctor process; the record is gone afterwards (the file only, F1)."""
    root = ready_root(tmp_path, monkeypatch)
    seen = []
    real = doctor_connected._hook_probe

    def spy(root_, commands):
        run = real(root_, commands)

        def wrapped(event):
            launch_id = event.get("_torque_launch")
            if launch_id:
                assert (root / "clients/acme/approvals/consumed" / f"{launch_id}.launch").is_file()
                seen.append(launch_id)
            return run(event)
        return wrapped
    monkeypatch.setattr(doctor_connected, "_hook_probe", spy)
    result = doctor_connected.report(root, "Acme")
    assert result["ready"], result["problems"]
    launches = set(seen)
    assert len(launches) == 1 and next(iter(launches)).startswith("probe-")
    assert (root / "clients/acme/approvals/consumed").is_dir()
    assert not list((root / "clients/acme/approvals/consumed").glob("*.launch"))


def test_gated_ask_in_the_local_settings_is_drift(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    (root / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"ask": ["Bash(sf project deploy:*)"]}}))
    assert any("gate decides" in p for p in doctor_connected.report(root, "Acme")["problems"])


def test_unattended_needs_the_post_failure_hook(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    edit_settings(root, lambda data: data["hooks"].pop("PostToolUseFailure"))
    problems = doctor_connected.report(root, "Acme")["problems"]
    assert any("PostToolUseFailure" in p for p in problems)


def test_post_hook_must_match_every_tool_and_run_the_same_command(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)

    def narrow(data):
        data["hooks"]["PostToolUse"][0]["matcher"] = "Bash"
        data["hooks"]["PostToolUseFailure"][0]["hooks"][0]["command"] = gate.hook_command("/usr/bin/python3")
    edit_settings(root, narrow)
    problems = doctor_connected.report(root, "Acme")["problems"]
    assert any("PostToolUse" in p and ".*" in p for p in problems)
    assert any("PostToolUseFailure" in p and "same command" in p for p in problems)


def test_post_hook_probes_run_for_mcp_and_failed_bash(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    result = doctor_connected.report(root, "Acme")
    checks = {c["check"]: c for c in result["checks"]}
    for name in ("post_hook_mcp", "post_hook_failure"):
        assert checks[name]["ok"], checks[name]
    assert checks["post_hook_mcp"]["event"]["tool_name"].startswith("mcp__")
    assert checks["post_hook_failure"]["event"]["hook_event_name"] == "PostToolUseFailure"


def test_missing_hook_interpreter_fails_open_and_is_reported(tmp_path, monkeypatch):
    missing = str(tmp_path / "gone" / "python3")
    root = ready_root(tmp_path, monkeypatch, hook_python=missing)
    result = doctor_connected.report(root, "Acme")
    assert not result["ready"]
    assert any(missing in p and "fails open" in p for p in result["problems"])


def test_hook_not_running_the_recorded_interpreter_is_drift(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    sidecar_path = root / permissions.PROFILE_FILE
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["hook_python"] = "/opt/agent-venv/bin/python3"
    sidecar_path.write_text(json.dumps(sidecar))
    problems = doctor_connected.report(root, "Acme")["problems"]
    assert any("/opt/agent-venv/bin/python3" in p for p in problems)


def test_changed_sidecar_is_drift(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    sidecar_path = root / permissions.PROFILE_FILE
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["settings_sha256"] = "0" * 64
    sidecar_path.write_text(json.dumps(sidecar))
    problems = doctor_connected.report(root, "Acme")["problems"]
    assert any("torque-permissions.json" in p and "settings_sha256" in p for p in problems)


def test_settings_changed_after_the_rules_were_written_is_drift(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    edit_settings(root, lambda data: data.setdefault("env", {}).update({"X": "1"}))
    problems = doctor_connected.report(root, "Acme")["problems"]
    assert any("settings_sha256" in p for p in problems)


def test_unreadable_sidecar_is_not_ready(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    (root / permissions.PROFILE_FILE).write_text("{not json")
    result = doctor_connected.report(root, "Acme")
    assert result["profile"] == "invalid"
    assert any("unreadable" in p and "torque-permissions.json" in p for p in result["problems"])


def test_agent_cannot_write_the_sidecar(tmp_path, monkeypatch):
    """R51: the gate denies a file-tool Write and a Bash redirect to the sidecar."""
    root = ready_root(tmp_path, monkeypatch)
    result = doctor_connected.report(root, "Acme")
    checks = {c["check"]: c for c in result["checks"]}
    for name in ("sidecar_write", "sidecar_bash_write"):
        assert checks[name]["expected"] == "deny" and checks[name]["got"] == "deny", checks[name]
    assert checks["sidecar_write"]["event"]["tool_input"]["file_path"].endswith("torque-permissions.json")


def test_sidecar_write_allowed_is_not_ready(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)

    def probe(event):
        # Everything is denied except a Write, as if R51 were missing.
        return (0, "") if event["tool_name"] == "Write" else (2, "")
    result = doctor_connected.report(root, "Acme", probe=probe)
    assert any("sidecar_write" in p for p in result["problems"])


def test_approvals_by_kind_counts_log_rows(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN)
    req = flow_request(root)
    record = delegated_grant(root, req)
    from torque import approval
    approval._log_grant(root, "Acme", record)   # what the gate writes at first use
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    as_agent(monkeypatch)
    result = doctor_connected.report(root, "Acme")
    assert result["approvals_by_kind"] == {"human": 0, "ai": 1}
    out = io.StringIO()
    doctor_connected.print_report(result, out)
    assert "Approvals by kind: human 0, ai 1" in out.getvalue()


def test_interactive_profile_has_no_under_p(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    permissions.write_settings(root, profile="interactive", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    as_agent(monkeypatch)
    result = doctor_connected.report(root, "Acme")
    assert result["profile"] == "interactive"
    assert all(p["under_p"] is None for p in result["probes"])


def test_print_report_shows_delegates_steps_and_under_p(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)
    out = io.StringIO()
    doctor_connected.print_report(doctor_connected.report(root, "Acme"), out)
    text = out.getvalue()
    assert "Permission profile: unattended" in text
    assert "Approver: " in text and "(uid " in text and ", ai)" in text
    assert "Setup delegate: " in text
    assert "Setup step permissions:" in text
    assert "under claude -p" in text and doctor_connected.REFUSED in text


def test_doctor_cli_json_carries_the_unattended_report(tmp_path, monkeypatch, capsys):
    root = ready_root(tmp_path, monkeypatch)
    cli.main(["doctor", "--workspace", str(root), "--client", "Acme", "--json"])
    data = json.loads(capsys.readouterr().out)
    connected = data["ai_access"]["connected"]
    assert connected["profile"] == "unattended" and connected["setup_steps"]
    assert "approvals_by_kind" in connected


# Fix round 1: every rewrite instruction gives the exact command for the current profile,
# so following it never silently downgrades an unattended workspace to interactive.

def _rewrite_lines(problems):
    return [p for p in problems if "torque approval permissions" in p]


def test_unattended_drift_gives_the_unattended_rewrite_command(tmp_path, monkeypatch):
    python = sys.executable.replace("\\", "/")
    root = ready_root(tmp_path, monkeypatch, hook_python=python)
    edit_settings(root, lambda data: data.setdefault("env", {}).update({"X": "1"}))
    problems = doctor_connected.report(root, "Acme")["problems"]
    drift = [p for p in problems if "settings_sha256" in p]
    assert drift and all("rewrite the rules" not in p or "torque approval permissions" in p for p in problems)
    command = (f"torque approval permissions --workspace {root} --write --unattended --with-hooks "
               f"--hook-python {python}")
    assert command in drift[0]


def test_every_rewrite_instruction_names_the_command(tmp_path, monkeypatch):
    root = ready_root(tmp_path, monkeypatch)

    def damage(data):
        data["permissions"]["ask"].append("Bash(torque deploy:*)")
        data["permissions"]["deny"] = []
    edit_settings(root, damage)
    sidecar_path = root / permissions.PROFILE_FILE
    sidecar = json.loads(sidecar_path.read_text())
    sidecar["hook_python"] = "/opt/agent-venv/bin/python3"
    sidecar_path.write_text(json.dumps(sidecar))
    problems = doctor_connected.report(root, "Acme")["problems"]
    asking = [p for p in problems if "rewrite" in p or "--write" in p]
    assert len(asking) >= 3
    for p in asking:
        assert f"torque approval permissions --workspace {root} --write --unattended --with-hooks" in p, p
        assert "--hook-python /opt/agent-venv/bin/python3" in p, p


def test_missing_interpreter_rewrite_command_keeps_the_profile(tmp_path, monkeypatch):
    missing = str(tmp_path / "gone" / "python3")
    root = ready_root(tmp_path, monkeypatch, hook_python=missing)
    problems = doctor_connected.report(root, "Acme")["problems"]
    line = next(p for p in problems if "fails open" in p)
    assert f"--workspace {root} --write --unattended --with-hooks --hook-python" in line


def test_interactive_drift_command_has_no_unattended_flag(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    permissions.write_settings(root, profile="interactive", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    as_agent(monkeypatch)
    edit_settings(root, lambda data: data["permissions"].update({"deny": []}))
    problems = _rewrite_lines(doctor_connected.report(root, "Acme")["problems"])
    assert problems and all(f"--workspace {root} --write --with-hooks" in p for p in problems)
    assert not any("--unattended" in p for p in problems)
