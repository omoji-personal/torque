"""Plain pytest: a session started outside scripts/test-offline.py gives itself the runner's
offline environment, so a focused run tests this checkout without reaching real tools."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = "packages/meeting_processor/tests/test_meeting_processor.py"

# Run as `python -I`: no inherited PYTHONPATH, so none of this suite's guard either. That is
# a developer's own interpreter typing `pytest`, in a shell that still has the real tools.
PROBE = textwrap.dedent("""\
    import json, os, socket, subprocess, sys
    import pytest

    facts_path, *arguments = sys.argv[1:]

    class Probe:
        def pytest_sessionstart(self, session):
            import torque
            facts = {"sentinel": bool(os.environ.get("TORQUE_TEST_LIVE_SENTINEL")),
                     "guard": "offline_support" in sys.modules, "torque": torque.__file__,
                     "home": os.environ.get("HOME", "")}
            facts["sf"] = subprocess.run(["sf", "org", "list", "--json"], capture_output=True, text=True).stdout
            try:
                socket.create_connection(("192.0.2.1", 9), timeout=1).close()
                facts["network"] = "reached"
            except OSError as exc:
                facts["network"] = str(exc)
            with open(facts_path, "w", encoding="utf-8") as stream:
                json.dump(facts, stream)

    sys.exit(pytest.main(["-p", "no:cacheprovider", *arguments], plugins=[Probe()]))
""")


def _plain(tmp_path, *arguments):
    script = tmp_path / "probe.py"
    script.write_text(PROBE, encoding="utf-8")
    facts = tmp_path / "facts.json"
    run = subprocess.run([sys.executable, "-I", str(script), str(facts), *arguments], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    return run, json.loads(facts.read_text(encoding="utf-8")) if facts.exists() else None


def test_plain_pytest_runs_a_focused_test_inside_the_offline_environment(tmp_path):
    run, facts = _plain(tmp_path, "-q", "tests/test_offline_harness.py::test_fixture_completion_contract")
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "12 passed" in run.stdout
    assert "standalone fixture suites run only through that runner" in run.stdout
    assert facts["sentinel"] and facts["guard"], facts
    assert Path(facts["torque"]).resolve().is_relative_to(ROOT / "src"), facts["torque"]
    assert "OfflineBackendUnavailable" in facts["sf"]
    assert "blocked external network access" in facts["network"]
    assert not Path(facts["home"]).parent.exists()  # its scratch folder is removed afterwards


def test_plain_collection_of_the_packages_does_not_crash(tmp_path):
    run, _ = _plain(tmp_path, "--collect-only", "-q", "-rs", "packages")
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "INTERNALERROR" not in output and "SystemExit" not in output
    assert "standalone fixture suite; scripts/test-offline.py runs it" in output
    assert "packages/debug_log_analyzer/tests/test_parsers.py::" in output


def test_an_installed_torque_imported_first_stops_the_session(tmp_path):
    # Fail closed: tests would otherwise exercise that copy instead of this checkout.
    script = tmp_path / "first.py"
    script.write_text("import sys, torque, pytest\n"
                      "sys.exit(pytest.main(['-p', 'no:cacheprovider', '-q', sys.argv[1]]))\n", encoding="utf-8")
    run = subprocess.run([sys.executable, "-I", "-c", "import torque"], cwd=tmp_path, capture_output=True)
    if run.returncode:
        pytest.skip("no installed Torque outside the checkout to import first")
    run = subprocess.run([sys.executable, "-I", str(script), "tests/test_offline_harness.py::test_fixture_completion_contract"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert run.returncode == 4, run.stdout + run.stderr
    assert "already imported from outside this checkout: torque" in run.stderr
    assert "python scripts/test-offline.py" in run.stderr


def test_the_standalone_meeting_suite_still_runs_and_measures_as_a_script():
    run = subprocess.run([sys.executable, HARNESS], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", timeout=180)
    assert run.returncode == 0, run.stdout + run.stderr
    summary = re.search(r"meeting_processor self-test PASSED: (\d+)/(\d+) fixtures", run.stdout)
    assert summary and int(summary[1]) > 0 and summary[1] == summary[2], run.stdout
