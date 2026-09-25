"""Permission profiles (interactive vs unattended), the setup sidecar and gate
hook wiring (task D3). F48: pytestmark sits at the top of the module, before
any test, so the POSIX-only skip applies to every test below, not only the
ones after it in file order."""
import json
import os
from pathlib import Path
import shutil

import pytest

from delegated_helpers import (CLEAN, FAKE_OWNER, ME, MODEL, YES, base_workspace, changed, delegated_workspace,
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


def test_cli_permissions_write_unattended_delegated_refuses_an_agent_session(capsys, tmp_path, monkeypatch):
    """The CLI wires --unattended, --with-hooks, --delegated, --model-id and
    --hook-python through to permissions.write_settings. This real pytest
    process genuinely has CLAUDECODE set (an actual Claude Code session), so a
    --delegated call here hits the agent-session refusal; fix round 1 (item 1)
    now guarantees that refusal fires before any tier-2 decision, even though
    this workspace is not tier 2 either, so this is a live, end-to-end
    confirmation of that ordering through the real CLI (the tier-2-required
    class itself is covered directly, without needing a real terminal, by
    test_unattended_needs_a_delegated_tier2_workspace above, on the owner
    path with an injected presence). No CLI flag can fake presence or a
    delegate's caller proof (Claude never approves its own writes, common.md),
    so the delegated *success* path stays a Python-API-level test
    (test_delegated_unattended_write_with_hooks above), matching
    task-D2-report.md's precedent that the CLI's own delegated write is
    verified by hand, not by an in-session automated test."""
    from torque import cli
    root = base_workspace(tmp_path, monkeypatch)
    code = cli.main(["approval", "permissions", "--workspace", str(root), "--write", "--unattended",
                     "--with-hooks", "--delegated", "--model-id", MODEL, "--hook-python", "/usr/bin/python3"])
    assert code == 3
    assert "agent session" in capsys.readouterr().err


# --- Fix round 1 (coordinator review): items 1, 2, 3, 5, 6, 7. Item 4 is a
# docs-only change (docs/connected-approval.md); item 8 is a docstring fix
# (load_profile), neither needs a test. ---


def test_delegated_write_does_not_use_ws_load_workspace(tmp_path, monkeypatch):
    """Important 1: on the delegated path, write_settings must decide from
    delegation._delegated_actor_and_config's own protected read, not a second,
    separately timed ws.load_workspace call, and use its (actor, config, root)
    for both the tier-2 check and the settings path containment check. This
    also puts the agent-session refusal before any tier-2 decision."""
    root = delegated_workspace(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise AssertionError("ws.load_workspace must not be called on the delegated path")

    monkeypatch.setattr(ws, "load_workspace", boom)
    # The delegated write still succeeds: it never needed ws.load_workspace.
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    assert permissions.load_profile(root) == "unattended"
    # An AI session still refuses with agent-session (reached without ever
    # calling ws.load_workspace, and before any tier-2 decision).
    with pytest.raises(delegation.Refusal) as info:
        permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL,
                                   root_owner=FAKE_OWNER, env={"CLAUDECODE": "1"}, ancestors=lambda: [])
    assert info.value.reason_class == "agent-session"


def test_drift_and_merge_flag_variant_spellings_of_a_gated_route(tmp_path, monkeypatch):
    """Important 2: drift (and the unattended merge filter) must treat as a
    problem any ask rule whose globs intersect a GATED_ASK body, not only a
    string-identical rule."""
    variants = ("Bash(torque deploy *)", "Bash(sf project deploy start --target-org:*)",
                "Bash(sf project deploy:*)")
    for variant in variants:
        assert variant not in permissions.GATED_ASK, f"{variant} is not actually a variant spelling"
        settings = {"permissions": {**permissions.generate("unattended")}}
        settings["permissions"]["ask"] = [*settings["permissions"]["ask"], variant]
        problems = permissions.drift(settings, permissions.generate("unattended"), "unattended")
        assert any(variant in p and "gate decides" in p for p in problems), variant

    root = delegated_workspace(tmp_path, monkeypatch)
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"permissions": {"ask": ["Bash(git push:*)", *variants]}}))
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    ask = json.loads(path.read_text())["permissions"]["ask"]
    assert "Bash(git push:*)" in ask
    assert not any(variant in ask for variant in variants)


def test_hook_python_is_explicit_and_recorded_in_the_sidecar(tmp_path, monkeypatch):
    """Controller ruling R43: the hooks command uses the given interpreter, not
    the calling process's own sys.executable, and the resolved path is
    recorded in the sidecar's "hook_python" key."""
    root = delegated_workspace(tmp_path, monkeypatch)
    given = "/opt/agent-venv/bin/python3"
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, hook_python=given, **CLEAN)
    settings = json.loads((root / ".claude" / "settings.json").read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command.startswith(f'"{given}" -I -c')
    sidecar = permissions.load_sidecar(root)
    assert sidecar["hook_python"] == given


def test_hook_python_defaults_to_sys_executable(tmp_path, monkeypatch):
    """The owner path, and any call with no --hook-python, is unchanged: the
    calling process's own sys.executable, exactly as before this ruling."""
    import sys
    root = base_workspace(tmp_path, monkeypatch)
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    permissions.write_settings(root, presence=YES, confirm=lambda: True, with_hooks=True)
    settings = json.loads((root / ".claude" / "settings.json").read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert sys.executable.replace("\\", "/") in command


def test_settings_dir_relaxes_only_when_this_call_created_it(tmp_path, monkeypatch):
    """R55, extended to write_settings (spec requirement 22): .claude relaxes
    to 0755 only when this call is the one that created it (existence read
    before mkdir), exactly like workspace.py's _connected_rule/.claude/rules.
    Ownership no longer decides this (superseding Minor 4): a preexisting
    .claude is never re-moded as a side effect of this call, whoever owns
    it. .claude already exists from delegated_workspace()'s init, so it is
    removed first to prove the "this call created it" half."""
    root = delegated_workspace(tmp_path, monkeypatch)
    shutil.rmtree(root / ".claude")
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    assert oct((root / ".claude").stat().st_mode & 0o777) == oct(0o755)


def test_settings_dir_leaves_a_preexisting_dir_alone_when_owned_by_the_real_caller(tmp_path, monkeypatch):
    """Counterpart: a preexisting .claude (from init) keeps its mode even
    though this call's real OS caller (os.getuid()) owns it; only whether
    this call created the folder decides, never who owns it. The directory
    is forced to a known, restrictive 0700 first: init_workspace's own
    mkdir(parents=True, ...) quirk already leaves .claude at 0755 in a
    typical sandbox (pathlib applies `mode` only to a mkdir call's own final
    target, not to parents it creates along the way), so without forcing a
    different starting mode this test cannot tell "the chmod ran and
    produced 0755 anyway" apart from "the chmod was correctly skipped"."""
    root = delegated_workspace(tmp_path, monkeypatch)
    (root / ".claude").chmod(0o700)
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    assert (root / ".claude").stat().st_mode & 0o777 == 0o700
    # the settings file itself, which this call just created, is still relaxed
    assert oct((root / ".claude" / "settings.json").stat().st_mode & 0o777) == oct(0o644)


def test_settings_dir_leaves_a_preexisting_dir_alone_when_not_owned_by_the_real_caller(tmp_path, monkeypatch):
    """Same, for a preexisting .claude this call's real OS caller does not
    own: the settings file still relaxes, the folder still does not (this
    is no longer an ownership check, since ownership is irrelevant now; it
    only proves the getuid mismatch this test injects has no bearing on the
    outcome either way)."""
    root = delegated_workspace(tmp_path, monkeypatch)
    (root / ".claude").chmod(0o700)
    monkeypatch.setattr(os, "getuid", lambda: ME + 5)
    permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               getuid=lambda: ME, **CLEAN)
    assert (root / ".claude").stat().st_mode & 0o777 == 0o700
    assert oct((root / ".claude" / "settings.json").stat().st_mode & 0o777) == oct(0o644)


def test_sidecar_written_before_any_step_that_can_fail_after_settings(tmp_path, monkeypatch):
    """Minor 4 (sequencing): the sidecar reflects the new profile even when a
    later step (here, the settings file's own chmod) raises, so settings.json
    is never left rewritten to a new profile while the sidecar still names
    the old one."""
    root = delegated_workspace(tmp_path, monkeypatch)
    real_chmod = Path.chmod

    def failing_chmod(self, mode):
        if self.name == "settings.json":
            raise OSError("simulated chmod failure")
        return real_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", failing_chmod)
    with pytest.raises(OSError):
        permissions.write_settings(root, profile="unattended", delegated=True, model_id=MODEL,
                                   root_owner=FAKE_OWNER, **CLEAN)
    assert permissions.load_profile(root) == "unattended"


def test_hooks_timeout_is_idempotent_and_keeps_existing_hooks(tmp_path, monkeypatch):
    """Minor 5: the hooks entry's timeout is cli.HOOK_TIMEOUT; a second
    --with-hooks write is a no-op (no duplicate torque.gate entry); a user's
    existing hook for the same event is kept alongside it."""
    from torque import cli
    root = delegated_workspace(tmp_path, monkeypatch)
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Read", "hooks": [{"type": "command", "command": "my-own-hook"}]}]}}))
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN)
    settings = json.loads(path.read_text())
    pre = settings["hooks"]["PreToolUse"]
    assert any(e.get("matcher") == "Read" and e["hooks"][0]["command"] == "my-own-hook" for e in pre)
    gate_entries = [e for e in pre if e.get("matcher") == ".*"]
    assert len(gate_entries) == 1
    assert gate_entries[0]["hooks"][0]["timeout"] == cli.HOOK_TIMEOUT
    permissions.write_settings(root, profile="unattended", with_hooks=True, delegated=True, model_id=MODEL,
                               root_owner=FAKE_OWNER, **CLEAN)
    settings_again = json.loads(path.read_text())
    pre_again = settings_again["hooks"]["PreToolUse"]
    assert [e for e in pre_again if e.get("matcher") == ".*"] == gate_entries
    assert any(e.get("matcher") == "Read" and e["hooks"][0]["command"] == "my-own-hook" for e in pre_again)


def test_cli_permissions_flags_without_write_are_a_usage_error(tmp_path, monkeypatch):
    """Minor 6: --with-hooks, --delegated, --model-id and --hook-python
    without --write are a usage error (exit 2)."""
    from torque import cli
    root = base_workspace(tmp_path, monkeypatch)
    for flag in (["--with-hooks"], ["--delegated"], ["--model-id", MODEL], ["--hook-python", "/usr/bin/python3"]):
        code = cli.main(["approval", "permissions", "--workspace", str(root), *flag])
        assert code == 2, flag
