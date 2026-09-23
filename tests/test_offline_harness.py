"""The release runner must distinguish completed fixtures from declined suites."""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

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
