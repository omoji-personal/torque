"""Permission profiles (interactive vs unattended), the setup sidecar and gate
hook wiring (task D3). F48: pytestmark sits at the top of the module, before
any test, so the POSIX-only skip applies to every test below, not only the
ones after it in file order."""
import json
import os

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, MODEL, YES, base_workspace, changed, delegated_workspace,
                               snapshot, within)
from torque import delegation, permissions, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")

GATED = ("Bash(sf project deploy start:*)", "Bash(torque deploy:*)", "Bash(torque browser:*)",
         "Bash(sf api request:*)", "mcp__playwright")
BACKSTOP = ("Bash(python:*)", "Bash(node:*)", "Bash(bash:*)", "Bash(curl:*)")
# Every delegated write_settings(...) call below passes root_owner=FAKE_OWNER:
# delegation.delegated_actor's R41 check (added in D1's fix round 1, after this
# task's brief was written; see task-D1-report.md and task-D2-report.md's
# identically named parameter) refuses a delegate that is the same account as
# the workspace directory's owner. In this single-uid test environment the
# fixture's real directory owner is ME, the same uid delegated_workspace()
# registers as the delegate, so every call needs the override to reach the
# behavior it actually targets. write_settings itself gained a root_owner
# keyword-only parameter for exactly this, threaded straight to
# delegation.delegated_actor.


def test_unattended_profile_asks_only_as_a_backstop():
    interactive, unattended = permissions.generate("interactive"), permissions.generate("unattended")
    for rule in GATED:
        assert rule in interactive["ask"] and rule not in unattended["ask"]
    for rule in BACKSTOP:
        assert rule in unattended["ask"]
    assert unattended["deny"] == interactive["deny"]
    assert unattended["disableBypassPermissionsMode"] == "disable" and "allow" not in unattended
    assert permissions.generate() == interactive


def test_unattended_needs_a_delegated_tier2_workspace(tmp_path, monkeypatch):
    root = base_workspace(tmp_path, monkeypatch)
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    with pytest.raises(delegation.Refusal) as info:
        permissions.write_settings(root, presence=YES, confirm=lambda: True, profile="unattended")
    assert info.value.reason_class == "tier-2-required"


def test_delegated_unattended_write_with_hooks(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    before = snapshot(root)
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN)
    assert within(changed(before, snapshot(root)), delegation.SETUP_WRITES["permissions"])
    settings = json.loads((root / ".claude" / "settings.json").read_text())
    assert not set(GATED) & set(settings["permissions"]["ask"])
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        entry = settings["hooks"][event][0]
        assert entry["matcher"] == ".*" and "torque.gate" in entry["hooks"][0]["command"]
    sidecar = permissions.load_sidecar(root)
    assert sidecar["profile"] == "unattended" and sidecar["written_by"]["kind"] == "ai"
    assert permissions.load_profile(root) == "unattended"


def test_switching_to_unattended_removes_gated_asks_and_keeps_the_users_rules(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"permissions": {"ask": ["Bash(git push:*)", *GATED[:2]]}}))
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    ask = json.loads(path.read_text())["permissions"]["ask"]
    assert "Bash(git push:*)" in ask and not set(GATED) & set(ask)


def test_drift_flags_a_gated_ask_under_the_unattended_profile():
    settings = {"permissions": {**permissions.generate("unattended")}}
    settings["permissions"]["ask"] = [*settings["permissions"]["ask"], "Bash(torque deploy:*)"]
    problems = permissions.drift(settings, permissions.generate("unattended"), "unattended")
    assert any("torque deploy" in p and "gate decides" in p for p in problems)


def test_owner_interactive_write_is_unchanged(tmp_path, monkeypatch):
    root = base_workspace(tmp_path, monkeypatch)
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    permissions.write_settings(root, presence=YES, confirm=lambda: True)
    assert not (root / permissions.PROFILE_FILE).exists()
    assert permissions.load_profile(root) == "interactive"


def test_malformed_sidecar_is_invalid(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    (root / ".claude").mkdir(exist_ok=True)
    (root / permissions.PROFILE_FILE).write_text("{not json")
    assert permissions.load_profile(root) == "invalid"


# --- Additional tests for the preflight-scan.md rulings named in this task's
# dispatch (F12, F13; F3 is covered inline above by extending the given test's
# event tuple to include PostToolUseFailure). ---


def test_delegated_write_leaves_settings_and_sidecar_agent_readable(tmp_path, monkeypatch):
    """F12: files the setup delegate writes are 0644 (dirs 0755) so a
    different agent account can read them; mkstemp otherwise leaves 0600."""
    root = delegated_workspace(tmp_path, monkeypatch)
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN)
    settings_path = root / ".claude" / "settings.json"
    sidecar_path = root / permissions.PROFILE_FILE
    assert oct(settings_path.stat().st_mode & 0o777) == oct(0o644)
    assert oct(sidecar_path.stat().st_mode & 0o777) == oct(0o644)
    assert oct((root / ".claude").stat().st_mode & 0o777) == oct(0o755)


def test_owner_write_keeps_a15_modes(tmp_path, monkeypatch):
    """F12: the owner path is unchanged (still mkstemp's 0600 for the file it
    writes; the containing .claude directory, whatever mode init_workspace gave
    it, is left exactly as it was, not further relaxed)."""
    root = base_workspace(tmp_path, monkeypatch)
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    settings_path = root / ".claude" / "settings.json"
    dir_mode_before = settings_path.parent.stat().st_mode & 0o777
    permissions.write_settings(root, presence=YES, confirm=lambda: True)
    assert oct(settings_path.stat().st_mode & 0o777) == oct(0o600)
    assert settings_path.parent.stat().st_mode & 0o777 == dir_mode_before


def test_owner_rewrite_to_interactive_updates_a_stale_unattended_sidecar(tmp_path, monkeypatch):
    """F13: an owner rewrite to interactive must not leave a stale "unattended"
    sidecar behind (D13 would keep sending allow; D15 would flag drift)."""
    root = delegated_workspace(tmp_path, monkeypatch)
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    assert permissions.load_profile(root) == "unattended"
    permissions.write_settings(root, presence=YES, confirm=lambda: True)
    assert permissions.load_profile(root) == "interactive"
    sidecar = permissions.load_sidecar(root)
    assert sidecar["profile"] == "interactive" and sidecar["written_by"]["kind"] == "human"


def test_cli_permissions_unattended_print_without_write(capsys, tmp_path, monkeypatch):
    """The CLI's --unattended flag, without --write, prints generate("unattended")."""
    from torque import cli
    root = base_workspace(tmp_path, monkeypatch)
    code = cli.main(["approval", "permissions", "--workspace", str(root), "--unattended"])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"permissions": permissions.generate("unattended")}


def test_cli_permissions_write_unattended_refuses_a_non_tier2_workspace(capsys, tmp_path, monkeypatch):
    """The CLI wires --unattended (and --with-hooks/--delegated/--model-id, not
    separately reachable here) through to permissions.write_settings. No CLI flag
    can fake presence or a delegate's caller proof (Claude never approves its own
    writes, common.md), so the delegated *success* path is not automatable from
    inside this real Claude Code session; this refusal fires from write_settings's
    own upfront tier-2 check, before any delegated-actor / presence check runs,
    so it is reachable and gives an automated, exit-code-level check of the CLI
    wiring. The delegated success path is exercised at the Python-API level by
    test_delegated_unattended_write_with_hooks above (matching task-D2-report.md's
    precedent: the CLI's delegated write itself was verified by hand there)."""
    from torque import cli
    root = base_workspace(tmp_path, monkeypatch)
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    code = cli.main(["approval", "permissions", "--workspace", str(root), "--write", "--unattended",
                     "--with-hooks", "--delegated", "--model-id", MODEL])
    assert code == 3
    assert "tier 2" in capsys.readouterr().err
