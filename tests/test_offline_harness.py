"""The release runner must distinguish completed fixtures from declined suites."""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import shlex

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def harness():
    return _module(ROOT / "scripts/test-offline.py", "torque_offline_harness_under_test")


def _sentinel(directory, name, marker):
    directory.mkdir(exist_ok=True)
    if os.name == "nt":
        # A script, not a fresh unsigned launcher, which Smart App Control may block.
        path = directory / (name + ".cmd")
        path.write_bytes(f'@>"{marker}" echo reached'.encode("utf-8") + b"\r\n")
        return path
    path = directory / name
    path.write_text("#!/bin/sh\nprintf reached > " + shlex.quote(str(marker)) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_bare_resolved_and_absolute_live_tools_cannot_reach_sentinels(harness, monkeypatch, tmp_path):
    marker = tmp_path / "real-tool-ran"
    ambient = tmp_path / "ambient"
    cwd = tmp_path / "working"
    sentinel = _sentinel(ambient, "sf", marker)
    _sentinel(cwd, "sf", marker)
    monkeypatch.setenv("PATH", str(ambient) + os.pathsep + os.environ["PATH"])
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = (
        "import json, shutil, subprocess\n"
        f"commands = ['sf', shutil.which('sf'), {str(sentinel)!r}]\n"
        + ("commands.append('sf.exe')\n" if os.name == "nt" else "")
        + "for command in commands:\n"
        "    run = subprocess.run([command, 'org', 'display'], capture_output=True, text=True)\n"
        "    assert run.returncode == 1, (command, run.stdout, run.stderr)\n"
        "    assert json.loads(run.stdout)['name'] == 'OfflineBackendUnavailable'\n"
    )
    run = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not marker.exists()
    assert Path(env["TORQUE_TEST_LIVE_SENTINEL"]).read_text().splitlines() == ["sf"] * (4 if os.name == "nt" else 3)
    if os.name == "nt":
        assert (scratch / "tools/bin/sf.cmd").is_file()


TOOLS = ("sf", "sfdx", "gemini", "claude", "codex", "curl", "wget", "ssh")
TRICKY = ["two words", 'say "hi"', "100% %PATH%", "a^b", "x&y|z", "trailing\\", "two words\\", "", "-c", "!x!"]
STUB_SUFFIX = ".cmd" if os.name == "nt" else ""


def test_stub_folder_holds_no_unsigned_executable(harness, tmp_path):
    # Smart App Control blocks freshly written unsigned launchers at random (WinError 4551);
    # scripts for the trusted interpreter are not executables to it.
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    harness.offline_environment(ROOT, scratch)
    names = {path.name for path in (scratch / "tools/bin").iterdir()}
    if os.name == "nt":
        assert not [name for name in names if name.lower().endswith((".exe", ".com", ".dll"))]
    assert {name + STUB_SUFFIX for name in (*TOOLS, "python", "torque")} <= names


def test_every_stub_resolves_by_path_lookup_and_explicit_path(harness, tmp_path):
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = f"""
import json, shutil, subprocess
from pathlib import Path
bins = Path({str(scratch / 'tools/bin')!r})
for name in {TOOLS!r}:
    found = shutil.which(name)
    explicit = bins / (name + {STUB_SUFFIX!r})
    assert found and Path(found).resolve() == explicit.resolve(), (name, found)
    for command in (name, found, str(explicit)):
        run = subprocess.run([command, 'org', 'display'], capture_output=True, text=True)
        assert run.returncode == 1 and json.loads(run.stdout)['name'] == 'OfflineBackendUnavailable', (command, run)
"""
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert Path(env["TORQUE_TEST_LIVE_SENTINEL"]).read_text().splitlines() == [name for name in TOOLS for _ in range(3)]


def test_arguments_reach_a_stubbed_program_unchanged(harness, tmp_path):
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = f"""
import json, shutil, subprocess
explicit = {str(scratch / 'tools/bin' / ('python' + STUB_SUFFIX))!r}
arguments = {TRICKY!r}
show = ['-c', 'import json, sys; print(json.dumps(sys.argv[1:]))']
for command in ('python', 'python3', shutil.which('python'), explicit):
    run = subprocess.run([command, *show, *arguments], capture_output=True, text=True)
    assert run.returncode == 0, (command, run.stderr)
    assert json.loads(run.stdout) == arguments, (command, run.stdout)
"""
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.skipif(os.name != "nt", reason="the .cmd shims are the Windows stubs")
def test_a_shim_started_by_cmd_exe_still_reaches_the_stub(harness, tmp_path):
    # A caller that is not Python (a batch file, another shell) starts the shim itself.
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    command = f'"{env["COMSPEC"]}" /d /c ""{scratch / "tools/bin/sf.cmd"}" org display"'
    code = (f"import subprocess; run = subprocess.run({command!r}, executable={env['COMSPEC']!r}, "
            "capture_output=True, text=True); print(run.returncode, run.stdout)")
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.startswith("1 ") and "OfflineBackendUnavailable" in run.stdout
    assert Path(env["TORQUE_TEST_LIVE_SENTINEL"]).read_text().splitlines() == ["sf"]


def test_auth_environment_and_directories_are_isolated(harness, monkeypatch, tmp_path):
    original = tmp_path / "original-home"
    original.mkdir()
    (original / ".sf").mkdir()
    (original / ".sf/auth.json").write_text("synthetic auth marker", encoding="utf-8")
    for name in ("HOME", "USERPROFILE", "APPDATA", "SF_CONFIG_DIR", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(name, str(original))
    for name in ("SF_ACCESS_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SSH_AUTH_SOCK",
                 "AWS_SHARED_CREDENTIALS_FILE", "GOOGLE_APPLICATION_CREDENTIALS", "TORQUE_CLIENT"):
        monkeypatch.setenv(name, "synthetic-sensitive-setting")
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    run = subprocess.run([sys.executable, "-c", "import os; from pathlib import Path; "
                          "assert not (Path.home()/'.sf/auth.json').exists(); "
                          "assert 'synthetic-sensitive-setting' not in os.environ.values(); "
                          "assert 'SF_CONFIG_DIR' not in os.environ; "
                          "assert 'CLAUDE_CONFIG_DIR' not in os.environ; "
                          "print(Path.home())"], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert Path(run.stdout.strip()).is_relative_to(scratch)
    assert env["PATH"] == str(scratch / "tools/bin")


def test_unapproved_executable_cannot_run(harness, tmp_path):
    marker = tmp_path / "unexpected-program-ran"
    executable = _sentinel(tmp_path / "ambient", "unexpected-program", marker)
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = ("import subprocess\ntry:\n"
            f"    subprocess.run([{str(executable)!r}], check=True)\n"
            "except PermissionError as exc:\n"
            "    assert 'unapproved executable' in str(exc)\n"
            "else:\n    raise AssertionError('unapproved executable ran')\n")
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not marker.exists()


def test_network_guard_covers_dns_tcp_udp_and_fresh_children(harness, tmp_path):
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    # Emitting the real audit events exercises the guard without risking even a
    # DNS query if it is absent. Numeric loopback audit events remain allowed.
    code = """
import socket, sys
sock = socket.socket()
for event, args in (
    ('socket.getaddrinfo', ('example.org', 443, 0, 0, 0)),
    ('socket.connect', (sock, ('192.0.2.1', 443))),
    ('socket.sendto', (sock, b'fixture', ('192.0.2.1', 53))),
):
    try:
        sys.audit(event, *args)
    except PermissionError as exc:
        assert 'external network' in str(exc)
    else:
        raise AssertionError('external network was not blocked')
sys.audit('socket.bind', sock, ('127.0.0.1', 0))
sys.audit('socket.connect', sock, ('127.0.0.1', 80))
sock.close()
"""
    parent = "import os, subprocess, sys; subprocess.run([sys.executable, '-c', " + repr(code) + "], check=True, env={'PYTHONPATH': ''})"
    # Retain SystemRoot on Windows; this test deliberately replaces PYTHONPATH.
    parent = parent.replace("env={'PYTHONPATH': ''}", "env=dict(os.environ, PYTHONPATH='')")
    run = subprocess.run([sys.executable, "-c", parent], env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr


def test_only_a_marked_fixture_folder_replaces_the_live_tool_stub(harness, tmp_path):
    marked, plain = tmp_path / "marked", tmp_path / "plain"
    fake = _sentinel(marked, "sf", tmp_path / "fake-ran")
    (marked / ".offline-fixture-bin").touch()
    _sentinel(plain, "sf", tmp_path / "plain-ran")
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = ("import os, subprocess, sys\n"
            "for folder in sys.argv[1:]:\n"
            "    subprocess.run(['sf', 'org', 'display'], capture_output=True,\n"
            "                   env=dict(os.environ, PATH=folder + os.pathsep + os.environ['PATH']))\n")
    run = subprocess.run([sys.executable, "-c", code, str(marked), str(plain)], env=env,
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert fake.exists() and (tmp_path / "fake-ran").exists()
    assert not (tmp_path / "plain-ran").exists()
    assert Path(env["TORQUE_TEST_LIVE_SENTINEL"]).read_text().splitlines() == ["sf"]


def test_checkout_scripts_on_child_path_cannot_shadow_the_bootstrap_guard(harness, tmp_path):
    # A test may hand its own sys.path to a child as PYTHONPATH; the checkout's
    # scripts/ then precedes the bootstrap copy, which alone has tools.json.
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    child = "import offline_support; print(offline_support.BASE)"
    parent = ("import os, subprocess, sys\n"
              f"path = os.pathsep.join([{str(ROOT / 'scripts')!r}, *sys.path])\n"
              f"out = subprocess.run([sys.executable, '-c', {child!r}], check=True, capture_output=True, text=True,\n"
              "                     env=dict(os.environ, PYTHONPATH=path)).stdout\n"
              "print(out.strip())\n")
    run = subprocess.run([sys.executable, "-c", parent], env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    # Under the offline runner the outer bootstrap wins; either copy has tools.json.
    loaded = Path(run.stdout.strip())
    assert loaded != (ROOT / "scripts").resolve() and (loaded / "tools.json").is_file()


@pytest.mark.parametrize("found", [False, True])
def test_launcher_preserves_and_reports_synthetic_private_scan(tmp_path, found):
    root = tmp_path / "repository"
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir()
    for name in ("test-offline.py", "offline_support.py", "public_hygiene.py"):
        (root / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    (root / "tests/test_public_hygiene.py").write_bytes((ROOT / "tests/test_public_hygiene.py").read_bytes())
    term = "synthetic-private-scan-canary"
    denylist = tmp_path / "private-terms.txt"
    denylist.write_text(term + "\n", encoding="utf-8")
    (root / "example.txt").write_text(term if found else "public fixture", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "example.txt"], check=True)
    run = subprocess.run([sys.executable, str(root / "scripts/test-offline.py"), "--pytest-only", "-q",
                          "tests/test_public_hygiene.py::test_private_denylist"], cwd=tmp_path,
                         env=dict(os.environ, TORQUE_PRIVATE_DENYLIST=str(denylist)),
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == (1 if found else 0), run.stdout + run.stderr
    assert "Private denylist scan: " + ("FAILED" if found else "PASSED") in run.stdout
    assert term not in run.stdout + run.stderr


@pytest.mark.parametrize("output,complete", [
    ("", False),
    ("SKIP: whole suite declined\n0/0 fixtures measured (declined under load)\n", False),
    ("example self-test PASSED (0 fixtures)\n", False),
    ("example self-test PASSED (12 fixtures)\n", True),
    ("SKIP: optional environment-specific fixture\nexample self-test PASSED (12 fixtures)\n", True),
    ("meeting_processor self-test PASSED: 12/12 fixtures\n", True),
    ("meeting_processor self-test PASSED: 0/0 fixtures\n", False),
    ("meeting_processor self-test PASSED: 11/12 fixtures\n", False),
    ("mcp_capture self-test PASSED\n", False),
    ("PASS: actual fixture\nmcp_capture self-test PASSED\n", True),
    ("FAIL: actual fixture\nexample self-test PASSED (12 fixtures)\n", False),
    ("example self-test PASSED (12 fixtures)\nINCOMPLETE: later failure\n", False),
])
def test_fixture_completion_contract(harness, output, complete):
    assert harness.standalone_summary(output)[0] is complete


def _fake_repository(harness, monkeypatch, tmp_path, names):
    root = tmp_path / "source"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/offline_support.py").write_bytes((ROOT / "scripts/offline_support.py").read_bytes())
    for name in names:
        path = root / "packages/example/tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def main():\n    return 0\n", encoding="utf-8")
    monkeypatch.setattr(harness, "__file__", str(root / "scripts/test-offline.py"))
    monkeypatch.setattr(sys, "argv", ["test-offline.py", "-q"])


def test_zero_exit_declined_suite_fails_aggregate(harness, monkeypatch, tmp_path, capsys):
    _fake_repository(harness, monkeypatch, tmp_path, ["test_declined.py"])
    calls = []

    def child(command, **kwargs):
        calls.append(command)
        output = "SKIP: suite timed out\n0/0 fixtures measured (declined under load)\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", child)
    assert harness.main() == 1
    output = capsys.readouterr()
    assert "INCOMPLETE packages/example/tests/test_declined.py" in output.err
    assert "PASS packages/example/tests/test_declined.py" not in output.out
    assert len(calls) == 2  # pytest and the executable harness were both requested


def test_outer_timeout_fails_but_runs_remaining_suites(harness, monkeypatch, tmp_path, capsys):
    _fake_repository(harness, monkeypatch, tmp_path, ["test_a_timeout.py", "test_b_measured.py"])
    measured = []

    def child(command, **kwargs):
        if command[-1].endswith("test_a_timeout.py"):
            raise subprocess.TimeoutExpired(command, 180)
        if command[-1].endswith("test_b_measured.py"):
            measured.append(command[-1])
        return subprocess.CompletedProcess(command, 0, stdout="example self-test PASSED (12 fixtures)\n", stderr="")

    monkeypatch.setattr(subprocess, "run", child)
    assert harness.main() == 1
    output = capsys.readouterr()
    assert "INCOMPLETE packages/example/tests/test_a_timeout.py" in output.err
    assert "PASS packages/example/tests/test_b_measured.py" in output.out
    assert len(measured) == 1


def test_nonzero_child_cannot_be_overridden_by_success_banner(harness, monkeypatch, tmp_path, capsys):
    _fake_repository(harness, monkeypatch, tmp_path, ["test_failed.py"])

    def child(command, **kwargs):
        return subprocess.CompletedProcess(command, 1 if command[-1].endswith(".py") else 0,
                                           stdout="example self-test PASSED (12 fixtures)\n", stderr="")

    monkeypatch.setattr(subprocess, "run", child)
    assert harness.main() == 1
    assert "FAIL packages/example/tests/test_failed.py" in capsys.readouterr().err


@pytest.mark.parametrize("arguments,standalone", [
    (["-q", "--maxfail", "1"], True),
    (["-q", "-k", "example"], True),
    (["-q", "tests"], True),
    (["--pytest-only", "-q", "tests"], False),
    (["--collect-only", "-q"], False),
    (["--help"], False),
])
def test_only_explicit_selection_skips_executable_suites(harness, monkeypatch, tmp_path, arguments, standalone):
    _fake_repository(harness, monkeypatch, tmp_path, ["test_measured.py"])
    monkeypatch.setattr(sys, "argv", ["test-offline.py", *arguments])
    calls = []
    def child(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="example self-test PASSED (1 fixtures)\n", stderr="")
    monkeypatch.setattr(subprocess, "run", child)
    assert harness.main() == 0
    assert len(calls) == (2 if standalone else 1)
    assert "--pytest-only" not in calls[0]


def test_runner_and_fresh_children_import_current_source_despite_stale_pythonpath(tmp_path):
    root = tmp_path / "source"
    script = root / "scripts/test-offline.py"
    script.parent.mkdir(parents=True)
    script.write_bytes((ROOT / "scripts/test-offline.py").read_bytes())
    script.with_name("offline_support.py").write_bytes((ROOT / "scripts/offline_support.py").read_bytes())
    for base in (root / "src", root / "packages/example", tmp_path / "stale"):
        for module in ("torque", "jsc_example"):
            if base.name != "stale" and ((base.name == "src") != (module == "torque")):
                continue
            package = base / module
            package.mkdir(parents=True)
            (package / "__init__.py").write_text(f'ORIGIN = {str(base)!r}\n', encoding="utf-8")
    test = root / "tests/test_source.py"
    test.parent.mkdir()
    assertions = (
        "import torque, jsc_example\n"
        f"assert torque.ORIGIN == {str(root / 'src')!r}\n"
        f"assert jsc_example.ORIGIN == {str(root / 'packages/example')!r}\n"
    )
    test.write_text("def test_actual_source():\n" + "\n".join("    " + line for line in assertions.splitlines()) + "\n", encoding="utf-8")
    executable = root / "packages/example/tests/test_executable.py"
    executable.parent.mkdir()
    executable.write_text(assertions + "print('source self-test PASSED (2 fixtures)')\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "stale"))
    run = subprocess.run([sys.executable, str(script), "-q", "--maxfail", "1"],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout
    assert "PASS packages/example/tests/test_executable.py" in run.stdout


def test_qa_load_timeout_entry_point_exits_incomplete(monkeypatch, capsys):
    path = ROOT / "packages/qa_orchestrator/tests/test_orchestrator.py"
    qa = _module(path, "torque_qa_harness_under_test")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(subprocess, "run", timed_out)
    # os.getloadavg does not exist on Windows at all; raising=False lets the
    # monkeypatch add it for the duration of this test regardless of platform.
    monkeypatch.setattr(qa.os, "getloadavg", lambda: (20, 20, 20), raising=False)
    with pytest.raises(qa.CliTooSlow):
        qa._run_cli(["--help"])

    def decline():
        raise qa.CliTooSlow("synthetic timeout after some fixtures")

    # Execute the actual checked-in __main__ exception handling, without running
    # the large legacy suite or a real subprocess just to simulate machine load.
    tree = ast.parse(path.read_text(encoding="utf-8"))
    entry = next(node for node in reversed(tree.body)
                 if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test))
    namespace = {**vars(qa), "__name__": "__main__", "main": decline}
    with pytest.raises(SystemExit) as exit_info:
        exec(compile(ast.Module(body=[entry], type_ignores=[]), str(path), "exec"), namespace)
    assert exit_info.value.code == 2
    output = capsys.readouterr()
    assert "INCOMPLETE" in output.err
    assert "PASSED" not in output.out + output.err
    assert "0/0" not in output.out + output.err


@pytest.mark.parametrize("arguments,expected", [
    (["-q"], []),
    (["-q", "--workers", "1"], []),
    (["-q", "--workers", "4"], ["-p", "xdist", "-n", "4"]),
])
def test_workers_spread_pytest_across_processes_and_keep_executable_suites_serial(
        harness, monkeypatch, tmp_path, arguments, expected):
    _fake_repository(harness, monkeypatch, tmp_path, ["test_measured.py"])
    monkeypatch.setattr(sys, "argv", ["test-offline.py", *arguments])
    monkeypatch.setattr(harness.importlib.util, "find_spec", lambda name: object() if name == "xdist" else None)
    calls = []

    def child(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="example self-test PASSED (1 fixtures)\n", stderr="")

    monkeypatch.setattr(subprocess, "run", child)
    assert harness.main() == 0
    pytest_call, suite_call = calls
    assert pytest_call[1:3] == ["-m", "pytest"] and "--workers" not in pytest_call
    assert [arg for arg in pytest_call if arg in ("-p", "xdist", "-n", "4")] == expected
    assert suite_call[-1].endswith("test_measured.py") and "-n" not in suite_call


def test_workers_without_pytest_xdist_say_how_to_install_it(harness, monkeypatch, tmp_path, capsys):
    _fake_repository(harness, monkeypatch, tmp_path, ["test_measured.py"])
    monkeypatch.setattr(sys, "argv", ["test-offline.py", "-q", "--workers", "4"])
    monkeypatch.setattr(harness.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    assert harness.main() == 2
    assert "pip install '.[dev]'" in capsys.readouterr().err


@pytest.mark.parametrize("arguments,given", [(["-q"], None), (["-q", "--basetemp", "chosen"], "chosen"),
                                             (["-q", "--basetemp=chosen"], "chosen")])
def test_tmp_paths_start_from_a_short_base_in_the_scratch_folder_unless_one_is_given(
        harness, monkeypatch, tmp_path, arguments, given):
    _fake_repository(harness, monkeypatch, tmp_path, [])
    monkeypatch.setattr(sys, "argv", ["test-offline.py", "--pytest-only", *arguments])
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs["env"]))
                        or subprocess.CompletedProcess(command, 0))
    assert harness.main() == 0
    [(command, env)] = calls
    options = [arg for arg in command if arg.startswith("--basetemp")]
    if given:
        assert options == [arguments[1]]  # only the caller's own
    else:
        [option] = options
        base = Path(option.split("=", 1)[1])
        assert base.parent == Path(env["HOME"]).parent and len(base.name) == 1


@pytest.mark.parametrize("scratch,limited,noted", [
    ("C:/tq/qa/torque-offline-abcdefgh", True, False),
    ("C:/" + "deep-temporary-folder/" * 3 + "torque-offline-abcdefgh", True, True),
    ("C:/" + "deep-temporary-folder/" * 3 + "torque-offline-abcdefgh", False, False),
])
def test_a_deep_temporary_folder_under_the_windows_path_limit_is_named_with_the_fix(harness, scratch, limited, noted):
    note = harness.path_limit_note(Path(scratch), limited)
    assert bool(note) is noted
    if noted:
        assert "Set TMP and TEMP to a short folder" in note and "LongPathsEnabled" in note


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs a Bash")
def test_bash_finds_and_runs_every_stub_on_the_isolated_path(harness, tmp_path):
    # Git Bash resolves a bare name only to a file without an extension.
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = f"""
import json, subprocess
for name in {(*TOOLS, "python", "git")!r}:
    found = subprocess.run(['bash', '--noprofile', '--norc', '-c', 'command -v ' + name],
                           capture_output=True, text=True)
    assert found.returncode == 0 and '/tools/bin/' in found.stdout.replace(chr(92), '/'), (name, found)
live = subprocess.run(['bash', '--noprofile', '--norc', '-c', 'sf org list'], capture_output=True, text=True)
assert live.returncode == 1 and json.loads(live.stdout)['name'] == 'OfflineBackendUnavailable', live
local = subprocess.run(['bash', '--noprofile', '--norc', '-c', 'git --version'], capture_output=True, text=True)
assert local.returncode == 0 and local.stdout.startswith('git version'), local
shown = subprocess.run(['bash', '--noprofile', '--norc', '-c',
                        'python -c "import json, sys; print(json.dumps(sys.argv[1:]))" "$@"', '_',
                        'two words', 'say "hi"', 'x&y', 'a^b', '100%'], capture_output=True, text=True)
assert json.loads(shown.stdout) == ['two words', 'say "hi"', 'x&y', 'a^b', '100%'], shown
"""
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert Path(env["TORQUE_TEST_LIVE_SENTINEL"]).read_text().splitlines() == ["sf"]


@pytest.mark.skipif(os.name != "nt", reason="Windows names are case-insensitive and carry .exe")
def test_windows_spellings_of_a_stub_name_reach_the_stub_with_arguments_unchanged(harness, tmp_path):
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = f"""
import json, subprocess
from jsc_common.tools import run
arguments = ['line' + chr(10) + 'break', '100% %PATH%', 'x&y', 'say "hi"', 'end' + chr(92)]
show = ['-c', 'import json, sys; print(json.dumps(sys.argv[1:]))']
for launch in (subprocess.run, run):
    for name in ('python.exe', 'python3.exe', 'Python', 'PYTHON', 'PYTHON.EXE'):
        done = launch([name, *show, *arguments], capture_output=True, text=True)
        assert done.returncode == 0 and json.loads(done.stdout) == arguments, (name, done)
    git = launch(['git.exe', '--version'], capture_output=True, text=True)
    assert git.returncode == 0 and git.stdout.startswith('git version'), git
    for name in ('SF', 'Sf.exe'):
        sf = launch([name, 'data', 'query', '--query', 'SELECT Id' + chr(10) + 'FROM Account'],
                    capture_output=True, text=True)
        assert sf.returncode == 1 and json.loads(sf.stdout)['name'] == 'OfflineBackendUnavailable', (name, sf)
"""
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr


def test_the_launch_helper_reaches_a_stub_with_every_argument_unchanged(harness, tmp_path):
    # jsc_common.tools.run is how Torque starts sf; on Windows it quotes a .cmd file for
    # cmd.exe and cannot carry a line break, so the stub must be dispatched before that.
    scratch = tmp_path / "isolated"
    scratch.mkdir()
    env = harness.offline_environment(ROOT, scratch)
    code = f"""
import json
from jsc_common.tools import run
query = 'SELECT Id' + chr(10) + 'FROM Account'
live = run(['sf', 'data', 'query', '--query', query], capture_output=True, text=True)
assert live.returncode == 1 and json.loads(live.stdout)['name'] == 'OfflineBackendUnavailable', live
arguments = {TRICKY!r} + [query]
show = ['-c', 'import json, sys; print(json.dumps(sys.argv[1:]))']
shown = run(['python', *show, *arguments], capture_output=True, text=True)
assert shown.returncode == 0 and json.loads(shown.stdout) == arguments, shown
"""
    run = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert Path(env["TORQUE_TEST_LIVE_SENTINEL"]).read_text().splitlines() == ["sf"]
