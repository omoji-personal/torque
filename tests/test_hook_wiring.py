"""The gate hook is wired by the commands that need it, fails closed when it cannot start, and
doctor reads Claude Code's current managed-settings files with the documented precedence.
Synthetic workspaces; the probes run the hook command only, never a host session."""
import contextlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys

import pytest
from torque import cli, gate, permissions
from torque import workspace as ws


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def settings(root):
    return json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))


def gate_items(data, event="PreToolUse"):
    return [item for entry in (data.get("hooks") or {}).get(event, []) for item in entry.get("hooks", [])
            if permissions.gate_hook_call(item) is not None]


def doctor(root):
    code, raw, _ = invoke("doctor", "--workspace", str(root), "--json")
    return code, json.loads(raw)


def no_probe():
    return os.name == "nt" and cli._hook_shell() is None


@pytest.fixture
def firm(tmp_path):
    return ws.init_workspace(tmp_path / "firm", "Example firm")


def test_build_only_writes_its_hook_for_both_hosts(firm):
    code, output, error = invoke("workspace", "ai-access", "build-only", "--path", str(firm))
    assert code == 0 and error == ""
    items = gate_items(settings(firm))
    assert len(items) == 1 and items[0]["onFailure"] == "block" and items[0]["timeout"] == cli.HOOK_TIMEOUT
    assert settings(firm)["hooks"]["PreToolUse"][0]["matcher"] == ".*"
    assert items[0]["command"] == sys.executable.replace("\\", "/")
    assert items[0]["args"] == ["-I", "-c", gate.HOOK_SHIM_CODE]
    agy = json.loads((firm / ".agents" / "hooks.json").read_text(encoding="utf-8"))
    assert agy["torque-gate"]["PreToolUse"][0]["hooks"][0]["command"].endswith("-I -m torque.gate_antigravity")
    assert "build-only" in output and ".claude/settings.json" in output
    # Running it again adds nothing.
    assert invoke("workspace", "ai-access", "build-only", "--path", str(firm))[0] == 0
    assert len(gate_items(settings(firm))) == 1
    assert len(json.loads((firm / ".agents" / "hooks.json").read_text(encoding="utf-8"))) == 1


def test_doctor_is_ready_once_build_only_is_set(firm):
    # The entry is in exec form, so the probe needs no shell, Git Bash included.
    assert invoke("workspace", "ai-access", "build-only", "--path", str(firm))[0] == 0
    code, report = doctor(firm)
    hook = report["ai_access"]["hook"]
    assert hook["verified"] and hook["fails_closed_on_start"], hook
    # The interpreter's installed Torque may predate 1.4.9's tools, which build-only mode blocks as
    # unknown, ordinary reads included; probe this checkout's gate instead.
    working_agy_hook(firm)
    code, report = doctor(firm)
    assert report["ai_access"]["antigravity_hook"]["verified"], report["ai_access"]["antigravity_hook"]
    assert code == 0, report["next_actions"]


def test_wiring_keeps_what_the_settings_already_hold(firm):
    mine = {"type": "command", "command": "echo firm-hook"}
    existing = {"model": "synthetic-model", "permissions": {"allow": ["Bash(npm test)"]},
                "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [mine]}]}}
    (firm / ".claude" / "settings.json").write_text(json.dumps(existing), encoding="utf-8")
    theirs = {"firm-hook": {"PreToolUse": [{"matcher": "*", "hooks": [mine]}]}}
    (firm / ".agents" / "hooks.json").write_text(json.dumps(theirs), encoding="utf-8")
    assert invoke("workspace", "ai-access", "build-only", "--path", str(firm))[0] == 0
    data = settings(firm)
    assert data["model"] == "synthetic-model" and data["permissions"] == existing["permissions"]
    assert data["hooks"]["PreToolUse"][0] == existing["hooks"]["PreToolUse"][0]
    assert len(gate_items(data)) == 1
    agy = json.loads((firm / ".agents" / "hooks.json").read_text(encoding="utf-8"))
    assert agy["firm-hook"] == theirs["firm-hook"] and "torque-gate" in agy


def test_a_settings_file_that_is_not_json_is_left_alone(firm):
    (firm / ".claude" / "settings.json").write_text("{ not json", encoding="utf-8")
    code, output, error = invoke("workspace", "ai-access", "build-only", "--path", str(firm))
    assert code == 3 and (firm / ".claude" / "settings.json").read_text(encoding="utf-8") == "{ not json"
    assert ws.load_workspace(firm)[1]["ai_access"] == "build-only"
    # The entry to add by hand, already escaped for JSON.
    printed = json.loads(output[output.index('{"hooks"'):].splitlines()[0])
    assert gate_items(printed)[0]["onFailure"] == "block"


def test_no_hook_sets_only_the_mode(firm):
    assert invoke("workspace", "ai-access", "build-only", "--path", str(firm), "--no-hook")[0] == 0
    assert not (firm / ".claude" / "settings.json").exists() and not (firm / ".agents" / "hooks.json").exists()
    assert ws.load_workspace(firm)[1]["ai_access"] == "build-only"


def test_init_wires_the_claude_code_hook_for_record_protection(tmp_path):
    code, output, _ = invoke("workspace", "init", str(tmp_path / "firm"), "--name", "Example firm")
    assert code == 0 and output.splitlines()[0] == str((tmp_path / "firm").resolve())
    assert len(gate_items(settings(tmp_path / "firm"))) == 1
    # Antigravity's hook is wired in full mode too: it refuses an ask the gate makes (docs/ai-access.md).
    assert "torque-gate" in agy_hooks(tmp_path / "firm")
    assert invoke("workspace", "init", str(tmp_path / "other"), "--name", "Other", "--no-hook")[0] == 0
    assert not (tmp_path / "other" / ".claude" / "settings.json").exists()
    assert agy_hooks(tmp_path / "other") is None


def test_upgrade_names_missing_record_protection_and_restores_a_build_only_hook(firm):
    # A workspace made before this version has no hook. In full mode the hook is the owner's
    # choice, so upgrade only says how to turn it on; one the owner removed stays removed.
    for options in (("--check",), ()):
        code, output, _ = invoke("workspace", "upgrade", str(firm), *options)
        assert code == 0 and "Record protection: NOT IN FORCE" in output and "ai-access full" in output
        assert not (firm / ".claude" / "settings.json").exists()
    # Build-only mode is enforced by the hook: upgrade adds a missing one, for both hosts.
    assert invoke("workspace", "ai-access", "build-only", "--path", str(firm), "--no-hook")[0] == 0
    code, output, _ = invoke("workspace", "upgrade", str(firm), "--check")
    assert code == 0 and "missing" in output and not (firm / ".claude" / "settings.json").exists()
    assert invoke("workspace", "upgrade", str(firm), "--no-hook")[0] == 0
    assert not (firm / ".claude" / "settings.json").exists()
    code, output, _ = invoke("workspace", "upgrade", str(firm))
    assert code == 0 and len(gate_items(settings(firm))) == 1 and (firm / ".agents" / "hooks.json").is_file()


def test_connected_wiring_rewrites_an_older_gate_entry_to_the_named_interpreter():
    older = {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": gate.hook_command("/owner/venv/bin/python"), "timeout": 600}]}]}}
    wired = permissions._with_hooks(older, "/agent/venv/bin/python")
    for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        items = gate_items(wired, event)
        assert len(items) == 1, event
        assert permissions.gate_hook_call(items[0]) == ("/agent/venv/bin/python", "-I", "-c", gate.HOOK_SHIM_CODE)
    assert gate_items(wired)[0]["onFailure"] == "block"
    assert "onFailure" not in gate_items(wired, "PostToolUse")[0]


def agy_hooks(root):
    path = root / ".agents" / "hooks.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@pytest.fixture
def no_probes(monkeypatch):
    monkeypatch.setattr(cli, "_hook_verification", lambda *args: [])


def test_full_mode_wires_the_antigravity_entry_and_no_hook_leaves_it_out(firm, no_probes):
    assert agy_hooks(firm) is None
    code, output, _ = invoke("workspace", "ai-access", "full", "--path", str(firm), "--no-hook")
    assert code == 0 and agy_hooks(firm) is None and not (firm / ".claude" / "settings.json").exists()
    code, output, _ = invoke("workspace", "ai-access", "full", "--path", str(firm))
    assert code == 0 and "Gate hook for Antigravity: added to .agents/hooks.json" in output
    assert set(agy_hooks(firm)) == {"torque-gate"} and len(gate_items(settings(firm))) == 1
    # Running it again adds nothing.
    assert invoke("workspace", "ai-access", "full", "--path", str(firm))[0] == 0
    assert set(agy_hooks(firm)) == {"torque-gate"}


def test_leaving_build_only_for_full_keeps_the_entry_and_the_owners_hooks(firm, no_probes):
    theirs = {"firm-hook": {"enabled": True, "PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": "echo firm-hook"}]}]}}
    (firm / ".agents" / "hooks.json").write_text(json.dumps(theirs), encoding="utf-8")
    assert invoke("workspace", "ai-access", "build-only", "--path", str(firm))[0] == 0
    assert set(agy_hooks(firm)) == {"firm-hook", "torque-gate"}
    before = agy_hooks(firm)
    assert invoke("workspace", "ai-access", "full", "--path", str(firm))[0] == 0
    assert agy_hooks(firm) == before and not (firm / ".torque" / "hooks.json").exists()


def test_an_antigravity_gate_entry_the_owner_added_stays_in_full_mode(firm, no_probes):
    from torque import gate_antigravity as agy
    owner = agy.hook_entry("/owner/venv/bin/python")
    (firm / ".agents" / "hooks.json").write_text(json.dumps(owner), encoding="utf-8")
    code, output, _ = invoke("workspace", "ai-access", "full", "--path", str(firm))
    assert code == 0 and agy_hooks(firm) == owner and "Antigravity" not in output


def working_agy_hook(root):
    """Antigravity's hook for this checkout's Torque, whatever Torque the interpreter has installed
    (-I keeps PYTHONPATH off, so the source folder is put on the path inside the command)."""
    from torque import gate_antigravity as agy
    src = Path(cli.__file__).resolve().parent.parent.as_posix()
    command = (f'"{sys.executable}" -I -c "import sys; sys.path.insert(0, \'{src}\'); '
               f'from torque import gate_antigravity as g; raise SystemExit(g.main())" # {agy.HOOK_MODULE}')
    entry = {agy.HOOK_NAME: {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]}}
    (root / ".agents" / "hooks.json").write_text(json.dumps(entry), encoding="utf-8")


def set_host(root, key):
    config = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    config["host"] = key
    (root / "workspace.json").write_text(json.dumps(config), encoding="utf-8")


def test_doctor_reports_the_antigravity_hook_in_force_in_full_mode(firm):
    working_agy_hook(firm)
    code, report = doctor(firm)
    agy = report["ai_access"]["antigravity_hook"]
    assert agy["configured"] and agy["verified"] is True, agy
    assert not any("Antigravity" in action for action in report["next_actions"]), report["next_actions"]
    code, output, _ = invoke("doctor", "--workspace", str(firm))
    assert ("Record protection (Antigravity): hook command blocked a standalone record probe; "
            "host enforcement not tested") in output


@pytest.mark.parametrize("host", ["claude", "antigravity"])
def test_a_registered_antigravity_hook_that_cannot_run_makes_full_mode_doctor_unready(firm, tmp_path, host):
    from torque import gate_antigravity as agy
    set_host(firm, host)
    missing = str(tmp_path / "moved-venv" / "python")
    (firm / ".agents" / "hooks.json").write_text(json.dumps(agy.hook_entry(missing)), encoding="utf-8")
    code, report = doctor(firm)
    assert code == 3 and report["ready"] is False
    assert report["ai_access"]["antigravity_hook"]["verified"] is False
    actions = [a for a in report["next_actions"] if "Record protection under Antigravity: NOT IN FORCE" in a]
    assert actions and "blocks every tool call" in actions[0], report["next_actions"]
    code, output, _ = invoke("doctor", "--workspace", str(firm))
    assert code == 3 and "Record protection (Antigravity): NOT IN FORCE" in output


@pytest.mark.parametrize("host", ["claude", "antigravity"])
def test_an_absent_antigravity_hook_leaves_full_mode_doctor_ready(firm, host):
    set_host(firm, host)
    code, report = doctor(firm)
    assert not (firm / ".agents" / "hooks.json").exists()
    assert report["ai_access"]["antigravity_hook"]["configured"] is False
    if host == "antigravity":
        assert any("Record protection under Antigravity: NOT IN FORCE" in a for a in report["next_actions"])
    assert report["ready"] is True and code == 0, report["next_actions"]


def edit_agy_hook(root, change):
    path = root / ".agents" / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["torque-gate"])
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_hook_that_prints_a_decision_but_exits_non_zero_is_not_in_force(firm):
    set_host(firm, "antigravity")
    working_agy_hook(firm)

    def fail(spec):
        item = spec["PreToolUse"][0]["hooks"][0]
        item["command"] = item["command"].replace("raise SystemExit(g.main())", "g.main(); raise SystemExit(1)")
    edit_agy_hook(firm, fail)
    code, report = doctor(firm)
    assert code == 3 and report["ready"] is False
    assert report["ai_access"]["antigravity_hook"]["verified"] is False


def test_a_hook_that_refuses_an_ordinary_read_is_not_in_force(firm):
    set_host(firm, "antigravity")
    working_agy_hook(firm)
    # Denies everything it is asked, with a well-formed answer and exit 0.
    deny = "import sys, json; print(json.dumps({\'decision\': \'deny\', \'reason\': \'x\'}))"

    def refuse(spec):
        item = spec["PreToolUse"][0]["hooks"][0]
        item["command"] = f'"{sys.executable}" -I -c "{deny}" # torque.gate_antigravity'
    edit_agy_hook(firm, refuse)
    code, report = doctor(firm)
    agy = report["ai_access"]["antigravity_hook"]
    assert code == 3 and report["ready"] is False and agy["blocks_every_call"] is True, agy
    assert any("blocks every tool call" in action for action in report["next_actions"])


@pytest.mark.parametrize("fault", ["disabled", "narrow", "not_isolated"])
def test_full_mode_configuration_chosen_on_purpose_is_advice_only(firm, fault):
    set_host(firm, "antigravity")
    working_agy_hook(firm)

    def change(spec):
        if fault == "disabled":
            spec["enabled"] = False
        elif fault == "narrow":
            spec["PreToolUse"][0]["matcher"] = "write_file"
        else:
            item = spec["PreToolUse"][0]["hooks"][0]
            item["command"] = item["command"].replace(" -I ", " ", 1)
    edit_agy_hook(firm, change)
    code, report = doctor(firm)
    assert code == 0 and report["ready"] is True, report["next_actions"]
    assert any("Record protection under Antigravity: NOT IN FORCE" in a for a in report["next_actions"])


def test_a_disabled_hook_is_never_run_and_a_broken_one_is_advice_only(firm, tmp_path):
    from torque import gate_antigravity as agy
    set_host(firm, "antigravity")
    entry = agy.hook_entry(str(tmp_path / "moved-venv" / "python"))
    entry["torque-gate"]["enabled"] = False
    (firm / ".agents" / "hooks.json").write_text(json.dumps(entry), encoding="utf-8")
    code, report = doctor(firm)
    hook = report["ai_access"]["antigravity_hook"]
    assert hook["disabled"] is True and hook["verified"] is None and hook["probe_decision"] is None
    assert code == 0 and report["ready"] is True
    assert not any("blocks every tool call" in a for a in report["next_actions"])


def add_disabled_old_entry(root):
    path = root / ".agents" / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["old-disabled-gate"] = {**json.loads(json.dumps(data["torque-gate"])), "enabled": False}
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_disabled_entry_does_not_hide_a_broken_enabled_hook(firm, tmp_path):
    from torque import gate_antigravity as agy
    set_host(firm, "antigravity")
    working_agy_hook(firm)
    add_disabled_old_entry(firm)
    path = firm / ".agents" / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["torque-gate"] = agy.hook_entry(str(tmp_path / "moved-venv" / "python"))["torque-gate"]
    path.write_text(json.dumps(data), encoding="utf-8")
    code, report = doctor(firm)
    hook = report["ai_access"]["antigravity_hook"]
    assert hook["disabled"] is False and hook["verified"] is False
    assert code == 3 and report["ready"] is False


def test_a_disabled_entry_does_not_make_a_working_hook_disabled(firm):
    working_agy_hook(firm)
    add_disabled_old_entry(firm)
    ws.set_ai_access(firm, "build-only")
    report = cli._antigravity_hook_report(firm, "build-only")
    assert report["disabled"] is False and report["verified"] is True, report
    # With no enabled entry left, the hook is disabled and nothing was run.
    data = json.loads((firm / ".agents" / "hooks.json").read_text(encoding="utf-8"))
    data["torque-gate"]["enabled"] = False
    (firm / ".agents" / "hooks.json").write_text(json.dumps(data), encoding="utf-8")
    report = cli._antigravity_hook_report(firm, "build-only")
    assert report["disabled"] is True and report["verified"] is None


def test_full_mode_hook_that_runs_but_allows_the_protected_write_exits_three(firm):
    set_host(firm, "antigravity")
    working_agy_hook(firm)

    def allow(spec):
        item = spec["PreToolUse"][0]["hooks"][0]
        item["command"] = item["command"].replace(
            "raise SystemExit(g.main())", "import json; print(json.dumps({\'decision\': \'allow\'}))")
    edit_agy_hook(firm, allow)
    code, report = doctor(firm)
    hook = report["ai_access"]["antigravity_hook"]
    assert hook["probe_decision"] == "allow" and not hook["blocks_every_call"], hook
    assert code == 3 and report["ready"] is False


def test_doctor_names_missing_record_protection_in_full_mode(firm):
    code, output, error = invoke("doctor", "--workspace", str(firm))
    assert code == 0 and error == ""
    assert "Record protection: NOT IN FORCE" in output
    assert "torque workspace ai-access full --path" in output


@pytest.mark.skipif(no_probe(), reason="no Git Bash to run the hook the way Claude Code does")
def test_doctor_sees_a_hook_whose_interpreter_cannot_start(firm, tmp_path):
    missing = (tmp_path / "moved-venv" / "python").as_posix()
    data = {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": gate.hook_command(missing), "timeout": 600, "onFailure": "block"}]}]}}
    (firm / ".claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    code, report = doctor(firm)
    assert code == 3 and report["ai_access"]["hook"]["verified"] is False
    assert any("every tool call" in action for action in report["next_actions"]), report["next_actions"]


@pytest.mark.skipif(no_probe(), reason="no Git Bash to run the hook the way Claude Code does")
def test_doctor_advises_failing_closed_when_the_hook_cannot_start(firm):
    ws.set_ai_access(firm, "build-only")
    data = {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": gate.hook_command(sys.executable.replace("\\", "/")), "timeout": 600}]}]}}
    (firm / ".claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    code, report = doctor(firm)
    assert report["ai_access"]["hook"]["fails_closed_on_start"] is False
    assert any('"onFailure": "block"' in action for action in report["next_actions"])


def test_managed_settings_live_where_claude_code_reads_them():
    windows = cli._managed_settings_dir("win32", {"ProgramFiles": r"C:\Program Files", "ProgramData": r"C:\ProgramData"})
    assert PureWindowsPath(windows) == PureWindowsPath(r"C:\Program Files\ClaudeCode")
    assert cli._managed_settings_dir("darwin", {}).as_posix() == "/Library/Application Support/ClaudeCode"
    assert cli._managed_settings_dir("linux", {}).as_posix() == "/etc/claude-code"


@pytest.fixture
def policy(tmp_path, monkeypatch):
    """Point doctor at synthetic managed, legacy and user settings."""
    folder = tmp_path / "managed"
    folder.mkdir()
    legacy = tmp_path / "legacy" / "managed-settings.json"
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(cli, "_managed_settings_dir", lambda *args, **kwargs: folder)
    monkeypatch.setattr(cli, "_legacy_managed_settings", lambda *args, **kwargs: legacy)
    monkeypatch.setattr(cli, "_registry_policy", lambda: None)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    return folder, legacy, home / ".claude" / "settings.json", write


def test_a_drop_in_file_can_restrict_hooks(firm, policy):
    folder, _, _, write = policy
    write(folder / "managed-settings.d" / "20-hooks.json", {"allowManagedHooksOnly": True})
    found = cli._hooks_disabled_by(firm)
    assert len(found) == 1 and "20-hooks.json" in found[0] and "allowManagedHooksOnly" in found[0]


def test_a_later_drop_in_replaces_an_earlier_value(firm, policy):
    folder, _, _, write = policy
    write(folder / "managed-settings.json", {"disableAllHooks": True})
    write(folder / "managed-settings.d" / "50-allow.json", {"disableAllHooks": False})
    write(folder / "managed-settings.d" / ".hidden.json", {"disableAllHooks": True})
    assert cli._hooks_disabled_by(firm) == []


def test_the_legacy_windows_file_is_ignored_and_named(firm, policy):
    _, legacy, _, write = policy
    write(legacy, {"disableAllHooks": True})
    assert cli._hooks_disabled_by(firm) == []
    assert str(legacy) in cli._managed_settings_report(firm)["ignored"]


@pytest.mark.parametrize("layers,disabled", [
    ({"user": True, "project": False}, False),
    ({"managed": False, "local": True}, False),
    ({"user": False, "local": True}, True),
    ({"managed": True, "project": False}, True),
    ({"project": "yes"}, False),
])
def test_disable_all_hooks_follows_settings_precedence(firm, policy, layers, disabled):
    folder, _, user, write = policy
    paths = {"managed": folder / "managed-settings.json", "user": user,
             "project": firm / ".claude" / "settings.json", "local": firm / ".claude" / "settings.local.json"}
    for layer, value in layers.items():
        write(paths[layer], {"disableAllHooks": value})
    assert bool(cli._hooks_disabled_by(firm)) is disabled


@pytest.mark.parametrize("path,provider", [
    (PureWindowsPath(r"C:\Users\user\OneDrive - Example\Work\firm"), "OneDrive"),
    (PurePosixPath("/Users/user/Library/Mobile Documents/com~apple~CloudDocs/firm"), "iCloud Drive"),
    (PurePosixPath("/Users/user/Dropbox/firm"), "Dropbox"),
    (PurePosixPath("/Users/user/Library/CloudStorage/GoogleDrive-user@example.com/My Drive/firm"), "Google Drive"),
    (PurePosixPath("/Users/user/Work/firm"), None),
])
def test_doctor_recognizes_a_synced_folder(path, provider):
    assert cli._synced_folder(path, {}) == provider
