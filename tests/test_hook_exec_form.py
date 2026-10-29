"""Torque writes its gate hook in Claude Code's exec form: the interpreter as `command` and
its arguments as `args`, started with no shell, with "onFailure": "block". An upgrade
converts a shell-form entry Torque wrote and never an owner's own; doctor reads and probes
both forms, and warns when the installed Claude Code is too old to honour onFailure.
Synthetic workspaces and interpreter paths; no host session runs."""
import contextlib
import io
import json
import os
import subprocess
import sys
import venv

import pytest
from torque import cli, doctor_connected, gate, permissions
from torque import workspace as ws

# The offline test runner starts a program only from a folder holding this file
# (scripts/offline_support.py), unless it is one of its own tools.
FIXTURE_MARKER = ".offline-fixture-bin"
# Interpreter paths with a space and an apostrophe, as Windows and POSIX write them.
ODD_PATHS = [r"C:\Program Files\Owner's Tools\python.exe", "/opt/Owner's Tools/bin/python3"]


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def doctor(root):
    code, raw, _ = invoke("doctor", "--workspace", str(root), "--json")
    return code, json.loads(raw)


def settings_path(root):
    return root / ".claude" / "settings.json"


def settings(root):
    return json.loads(settings_path(root).read_text(encoding="utf-8"))


def write_settings(root, data):
    settings_path(root).parent.mkdir(parents=True, exist_ok=True)
    settings_path(root).write_text(json.dumps(data, indent=2), encoding="utf-8")


def entry(*items, matcher=".*"):
    return {"matcher": matcher, "hooks": list(items)}


def vector(python):
    """The argument vector of Torque's entry for this interpreter."""
    return (python.replace("\\", "/"), "-I", "-c", gate.HOOK_SHIM_CODE)


def fake_probe(command, root, event):
    """A hook that blocks the client-path probe and allows the ordinary one."""
    return (2, "blocked") if "clients/" in event else (0, "")


def no_shell_probe():
    return os.name == "nt" and cli._hook_shell() is None


@pytest.fixture
def firm(tmp_path):
    return ws.init_workspace(tmp_path / "firm", "Example firm")


@pytest.fixture
def odd_python(tmp_path):
    """A virtual environment in a folder named with a space and an apostrophe. Its torque.gate
    stands in for the real one: it records the arguments and interpreter it was started
    with, blocks an event naming clients/ and allows anything else."""
    home = tmp_path / "Owner's Tools"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(home)
    python = home / "Scripts" / "python.exe" if os.name == "nt" else home / "bin" / "python"
    (python.parent / FIXTURE_MARKER).write_text("", encoding="utf-8")
    package = next(home.rglob("site-packages")) / "torque"
    package.mkdir()
    log = tmp_path / "started.jsonl"
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "gate.py").write_text(
        "import json, sys\n"
        "def main():\n"
        "    event = sys.stdin.read()\n"
        f"    with open({str(log)!r}, 'a', encoding='utf-8') as stream:\n"
        "        stream.write(json.dumps({'argv': sys.orig_argv[1:], 'executable': sys.executable}) + '\\n')\n"
        "    if 'clients/' in event:\n"
        "        print('blocked', file=sys.stderr)\n"
        "        return 2\n"
        "    return 0\n", encoding="utf-8")
    return python, log


@pytest.mark.parametrize("python", ODD_PATHS)
def test_the_written_entry_round_trips_its_argument_vector_exactly(firm, monkeypatch, python):
    assert [row["status"] for row in permissions.wire_gate_hooks(firm, python=python)] == ["added"]
    item = settings(firm)["hooks"]["PreToolUse"][0]["hooks"][0]
    # No command line to quote or parse, and no shell to run one.
    assert item == {"type": "command", "command": vector(python)[0], "args": list(vector(python)[1:]),
                    "timeout": cli.HOOK_TIMEOUT, "onFailure": "block"}
    assert permissions.gate_hook_call(item) == vector(python)
    assert permissions.torque_hook_python(item) == vector(python)[0]
    # Doctor's probes hand the operating system that same vector, without a shell.
    started = []

    def run(command, **options):
        started.append((command, bool(options.get("shell"))))
        return subprocess.CompletedProcess(command, 2, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    cli._run_hook_probe(permissions.gate_hook_call(item), firm, "{}")
    doctor_connected._hook_probe(firm, [permissions.gate_hook_call(item)])({"tool_name": "Read"})
    assert started == [(list(vector(python)), False)] * 2


def test_doctor_starts_the_written_interpreter_with_exactly_that_vector(firm, odd_python):
    python, log = odd_python
    ws.set_ai_access(firm, "build-only")
    assert [row["status"] for row in permissions.wire_gate_hooks(firm, python=str(python))] == ["added"]
    hook = cli._gate_hook_report(firm)["hook"]
    assert hook["exec_form"] and hook["isolated"] and hook["fail_closed_shim"] and hook["fails_closed_on_start"]
    assert hook["verified"] is True, hook
    call = permissions.gate_hook_call(settings(firm)["hooks"]["PreToolUse"][0]["hooks"][0])
    probe = doctor_connected._hook_probe(firm, [call])
    assert probe({"tool_name": "Read", "tool_input": {"file_path": "clients/example/notes.md"}})[0] == 2
    started = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    # Doctor's two probes and the connected one, each with the arguments as written.
    assert [run["argv"] for run in started] == [["-I", "-c", gate.HOOK_SHIM_CODE]] * 3
    for run in started:
        assert os.path.normcase(os.path.normpath(run["executable"])) == os.path.normcase(str(python))


@pytest.mark.parametrize("mode", ["full", "build-only"])
def test_upgrade_converts_torques_own_shell_entries_and_never_an_owners(firm, mode):
    if mode == "build-only":
        ws.set_ai_access(firm, "build-only")
    python = "C:/Program Files/Owner's Tools/python.exe"
    other = {"type": "command", "command": "echo firm-hook"}
    # Two entries that run the gate but that Torque did not write: another module form, and
    # Torque's command shape around the owner's own code.
    owner_module = {"type": "command", "command": '"/owner/venv/bin/python" -I -m torque.gate', "timeout": 600}
    owner_code = {"type": "command", "timeout": 600,
                  "command": '"/owner/venv/bin/python" -I -c "from torque.gate import main;main()"'}
    write_settings(firm, {"model": "synthetic-model", "hooks": {
        "PreToolUse": [entry(other, matcher="Bash"),
                       entry({"type": "command", "command": gate.hook_command(python), "timeout": 600}),
                       entry(owner_module, owner_code)],
        # An earlier release's shim, without -I and without a timeout.
        "PostToolUse": [entry({"type": "command", "command": f'"{python}" -c "{permissions._EARLIER_SHIMS[0]}"',
                               "statusMessage": "kept"})]}})
    before = settings_path(firm).read_bytes()
    code, raw, _ = invoke("workspace", "upgrade", str(firm), "--check", "--json")
    rows = [row for row in json.loads(raw)["hooks"] if row["host"] == "Claude Code"]
    assert code == 0 and rows[0]["status"] == "to_convert" and rows[0]["count"] == 2
    assert settings_path(firm).read_bytes() == before
    code, output, _ = invoke("workspace", "upgrade", str(firm))
    assert code == 0 and "Torque's entry in .claude/settings.json now runs in exec form" in output
    data = settings(firm)
    assert data["model"] == "synthetic-model"
    pre = data["hooks"]["PreToolUse"]
    assert pre[0] == entry(other, matcher="Bash") and pre[2] == entry(owner_module, owner_code)
    assert pre[1] == entry({"type": "command", "command": python, "args": ["-I", "-c", gate.HOOK_SHIM_CODE],
                            "timeout": 600, "onFailure": "block"})
    # After a call there is nothing to block, so no onFailure there.
    assert data["hooks"]["PostToolUse"] == [entry({"type": "command", "statusMessage": "kept", "command": python,
                                                   "args": ["-I", "-c", gate.HOOK_SHIM_CODE],
                                                   "timeout": cli.HOOK_TIMEOUT})]
    # Nothing is left to convert, and nothing was added beside the entries.
    after = settings_path(firm).read_bytes()
    assert invoke("workspace", "upgrade", str(firm))[0] == 0
    assert settings_path(firm).read_bytes() == after


def test_upgrade_converts_the_entry_2_0_0a21_wrote(firm):
    # a21's hook_command: the same shell shape, with the shim before its message named the recovery step.
    python = "/venv/bin/python"
    a21 = permissions._EARLIER_SHIMS[-1]
    assert "call blocked)" in a21 and a21 != gate.HOOK_SHIM_CODE
    write_settings(firm, {"hooks": {"PreToolUse": [
        entry({"type": "command", "command": f'"{python}" -I -c "{a21}"', "timeout": 600})]}})
    assert invoke("workspace", "upgrade", str(firm))[0] == 0
    assert settings(firm)["hooks"]["PreToolUse"] == [entry({"type": "command", "command": python, "args": [
        "-I", "-c", gate.HOOK_SHIM_CODE], "timeout": 600, "onFailure": "block"})]


def test_an_exec_entry_with_the_current_shim_is_left_as_it_is(firm):
    item = {**permissions.gate_hook_item("/venv/bin/python"), "timeout": 300}
    write_settings(firm, {"hooks": {"PreToolUse": [entry(item)]}})
    assert [row["status"] for row in permissions.wire_gate_hooks(firm)] == ["present"]
    assert settings(firm)["hooks"]["PreToolUse"] == [entry(item)]


def test_connected_wiring_keeps_an_owners_gate_entry_and_adds_no_second_one():
    owner = {"type": "command", "command": '"/owner/venv/bin/python" -I -m torque.gate', "timeout": 600}
    wired = permissions._with_hooks({"hooks": {"PreToolUse": [entry(owner)]}}, "/agent/venv/bin/python")
    assert wired["hooks"]["PreToolUse"] == [entry(owner)]
    for event in ("PostToolUse", "PostToolUseFailure"):
        assert wired["hooks"][event] == [entry(permissions.gate_hook_item("/agent/venv/bin/python", event))]


@pytest.mark.skipif(no_shell_probe(), reason="no Git Bash to run a shell-form hook the way Claude Code does")
def test_doctor_verifies_both_forms_and_the_upgrade_between_them(firm):
    ws.set_ai_access(firm, "build-only")
    python = sys.executable.replace("\\", "/")
    write_settings(firm, {"hooks": {"PreToolUse": [entry(
        {"type": "command", "command": gate.hook_command(python), "timeout": 600, "onFailure": "block"})]}})
    hook = cli._gate_hook_report(firm)["hook"]
    assert hook["verified"] and hook["convertible"] and not hook["exec_form"], hook
    assert invoke("workspace", "upgrade", str(firm))[0] == 0
    hook = cli._gate_hook_report(firm)["hook"]
    assert hook["verified"] and hook["exec_form"] and not hook["convertible"], hook
    assert hook["commands"] == [vector(python)]


@pytest.mark.parametrize("mode", ["full", "build-only"])
def test_doctor_blocks_on_an_exec_entry_whose_interpreter_is_gone(firm, tmp_path, mode):
    if mode == "build-only":
        ws.set_ai_access(firm, "build-only")
    folder = tmp_path / "moved venv"
    folder.mkdir()
    (folder / FIXTURE_MARKER).write_text("", encoding="utf-8")
    assert permissions.wire_gate_hooks(firm, python=str(folder / "python"))[0]["status"] == "added"
    code, report = doctor(firm)
    hook = report["ai_access"]["hook"]
    assert code == 3 and hook["verified"] is False and hook["blocks_every_call"] is True, hook
    assert any("blocks every tool call" in action for action in report["next_actions"]), report["next_actions"]


def test_doctor_reads_an_owners_exec_entry_without_converting_it(firm, monkeypatch):
    ws.set_ai_access(firm, "build-only")
    monkeypatch.setattr(cli, "_run_hook_probe", fake_probe)
    owner = {"type": "command", "command": sys.executable.replace("\\", "/"), "args": ["-m", "torque.gate"],
             "timeout": 600, "onFailure": "block"}
    write_settings(firm, {"hooks": {"PreToolUse": [entry(owner)]}})
    code, report = doctor(firm)
    hook = report["ai_access"]["hook"]
    assert hook["exec_form"] and hook["verified"] and not hook["isolated"] and not hook["convertible"], hook
    assert code == 3 and any("without -I" in action for action in report["next_actions"])
    assert report["ai_access"]["hook"]["recommended_entry"]["hooks"]["PreToolUse"][0]["hooks"] == [
        permissions.gate_hook_item(sys.executable)]
    assert invoke("workspace", "upgrade", str(firm))[0] == 0
    assert settings(firm)["hooks"]["PreToolUse"] == [entry(owner)]


@pytest.mark.skipif(os.name != "nt", reason="the shell-form advice is for Windows")
def test_doctor_on_windows_advises_converting_torques_shell_entry(firm, monkeypatch):
    ws.set_ai_access(firm, "build-only")
    monkeypatch.setattr(cli, "_run_hook_probe", fake_probe)
    write_settings(firm, {"hooks": {"PreToolUse": [entry({"type": "command", "timeout": 600, "onFailure": "block",
                                                          "command": gate.hook_command("C:/venv/python.exe")})]}})
    _, report = doctor(firm)
    advice = [action for action in report["next_actions"] if "shell form" in action]
    assert len(advice) == 1 and "torque workspace upgrade" in advice[0], report["next_actions"]


def test_doctor_warns_when_claude_code_ignores_on_failure(firm, monkeypatch):
    ws.set_ai_access(firm, "build-only")
    assert permissions.wire_gate_hooks(firm)[0]["status"] == "added"
    monkeypatch.setattr(cli, "_run_hook_probe", fake_probe)
    codes = set()
    for version, warned in (((2, 1, 290), True), ((2, 1, 295), False), ((2, 2, 0), False), (None, False)):
        monkeypatch.setattr(cli, "_claude_code_version", lambda environ=None, found=version: found)
        code, report = doctor(firm)
        codes.add(code)
        assert report["claude_code"] == {"version": ".".join(map(str, version)) if version else None}
        assert any("older than 2.1.295" in action for action in report["next_actions"]) is warned, version
    # Advice only: the version does not change whether the workspace is ready.
    assert len(codes) == 1


def test_doctor_asks_for_no_version_when_no_entry_relies_on_on_failure(firm, monkeypatch):
    ws.set_ai_access(firm, "build-only")
    monkeypatch.setattr(cli, "_run_hook_probe", fake_probe)

    def asked(environ=None):
        raise AssertionError("claude --version was run")
    monkeypatch.setattr(cli, "_claude_code_version", asked)
    write_settings(firm, {"hooks": {"PreToolUse": [entry({"type": "command", "timeout": 600,
                                                          "command": gate.hook_command("/venv/bin/python")})]}})
    _, report = doctor(firm)
    assert "claude_code" not in report
    assert any('"onFailure": "block"' in action for action in report["next_actions"])


def fake_claude(folder, text):
    """A `claude` that prints `text`, in a folder the offline runner accepts."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / FIXTURE_MARKER).write_text("", encoding="utf-8")
    if os.name == "nt":
        (folder / "claude.cmd").write_text(f"@echo {text}\r\n", encoding="utf-8")
    else:
        (folder / "claude").write_text(f"#!/bin/sh\necho '{text}'\n", encoding="utf-8")
        (folder / "claude").chmod(0o755)
    return folder


def test_the_claude_code_version_comes_from_path_and_never_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_CLAUDE_VERSIONS", {})
    found = fake_claude(tmp_path / "tools", "2.1.290 (Claude Code)")
    assert cli._claude_code_version({"PATH": str(found)}) == (2, 1, 290)
    # Asked once per process for each program.
    fake_claude(found, "2.1.312 (Claude Code)")
    assert cli._claude_code_version({"PATH": str(found)}) == (2, 1, 290)
    # One in the current folder (a workspace, say) is not run, nor one a relative PATH entry names.
    here = fake_claude(tmp_path / "workspace", "2.1.312 (Claude Code)")
    monkeypatch.chdir(here)
    assert cli._claude_code_version({"PATH": ""}) is None
    assert cli._claude_code_version({"PATH": "."}) is None
    assert cli._claude_code_version({"PATH": str(fake_claude(tmp_path / "quiet", "no version here"))}) is None
