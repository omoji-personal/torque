"""Side-Eff and the report verdict: an org with no debug logs is missing evidence, a
check that could not run is not a failure, and neither reads as FAILURES."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from jsc_loganalyzer import cli as analyzer
from jsc_qa.dispatcher import DispatchResult, dispatch_side_eff
from jsc_qa.report import format_report, result_exit_code

# A Salesforce CLI for an org with no debug logs: the ApexLog query succeeds with
# no rows, and every other command fails as an unreachable org would.
FAKE_SF = (
    "import json, sys\n"
    "if sys.argv[1:3] == ['data', 'query']:\n"
    "    print(json.dumps({'status': 0, 'result': {'records': [], 'totalSize': 0, 'done': True}}))\n"
    "    sys.exit(0)\n"
    "print(json.dumps({'status': 1, 'name': 'SyntheticOrg', 'message': 'synthetic org'}))\n"
    "sys.exit(1)\n"
)


def _fake_sf(folder):
    """Put FAKE_SF on a folder the offline guard lets replace its unavailable sf stub."""
    folder.mkdir()
    (folder / ".offline-fixture-bin").touch()
    if os.name == "nt":  # Windows starts an .exe, not a script
        from distlib.scripts import ScriptMaker
        maker = ScriptMaker(None, str(folder))
        maker.executable = sys.executable
        maker.variants = {""}
        maker.script_template = FAKE_SF
        maker.make("sf = builtins:print")
    else:
        (folder / "fake_sf.py").write_text(FAKE_SF, encoding="utf-8")
        (folder / "sf").write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
                                   + shlex.quote(str(folder / "fake_sf.py")) + ' "$@"\n', encoding="utf-8")
        (folder / "sf").chmod(0o755)
    return folder


def _analyzer_against(monkeypatch, sf):
    """Run the real analyzer in-process for Side-Eff, with the Salesforce CLI replaced by `sf`."""
    monkeypatch.setattr(analyzer, "run_tool", sf)

    def run(cmd, **kwargs):
        assert cmd[1:3] == ["-m", "jsc_loganalyzer.cli"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = analyzer.main(cmd[3:])
        return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr="")

    monkeypatch.setattr("jsc_qa.dispatcher.subprocess.run", run)


def _query(returncode, records=()):
    """An sf CLI whose ApexLog query exits `returncode` with these records."""
    def sf(argv, **kwargs):
        assert argv[:3] == ["sf", "data", "query"], argv
        body = {"status": returncode, "result": {"records": list(records), "totalSize": len(records), "done": True}}
        return SimpleNamespace(returncode=returncode, stdout=json.dumps(body), stderr="")
    return sf


def test_an_org_with_no_debug_logs_is_manual_required_with_the_next_step(monkeypatch):
    _analyzer_against(monkeypatch, _query(0))
    result = dispatch_side_eff("synthetic-dev", "A2 validation rule change")
    assert result.status == "MANUAL_REQUIRED"
    assert "No debug log was found in synthetic-dev" in result.detail
    assert "trace flag" in result.detail and "--since" in result.detail
    assert result.metadata["logs_analyzed"] == 0
    assert result.metadata["change_execution_proven"] is False
    others = [DispatchResult("MetaAPI", "MANUAL_REQUIRED", "Synthetic fixture")]
    assert result_exit_code([*others, result]) == 3
    text = format_report([], "synthetic-dev", False, [*others, result])
    assert "VERDICT: INCOMPLETE." in text and "FAILURES" not in text


def test_a_qa_run_on_an_org_without_debug_logs_is_incomplete_not_failed(tmp_path):
    # The audit's reproduction, end to end: real processes, an org with no ApexLog rows.
    folder = _fake_sf(tmp_path / "bin")
    env = dict(os.environ, PATH=str(folder) + os.pathsep + os.environ["PATH"])
    run = subprocess.run([sys.executable, "-m", "jsc_qa.cli", "run", "A2 validation rule change",
                          "--org", "synthetic-dev"], cwd=tmp_path, env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=300)
    side_eff = [line for line in run.stdout.splitlines() if "Side-Eff" in line]
    assert run.returncode == 3, run.stdout + run.stderr
    assert side_eff and "MANUAL_REQUIRED" in side_eff[0], run.stdout
    assert "No debug log was found in synthetic-dev" in run.stdout
    assert "VERDICT: INCOMPLETE." in run.stdout and "FAILURES" not in run.stdout


def _one_log(body):
    """An sf CLI whose ApexLog query lists one log with this body."""
    def sf(argv, **kwargs):
        if argv[:3] == ["sf", "data", "query"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"status": 0, "result": {"records": [{"Id": "07L000000000001AAA"}]}}), stderr="")
        return SimpleNamespace(returncode=0, stdout=body, stderr="")
    return sf


def _lines(*events):
    return "\n".join(f"09:00:00.{n:03d} ({n})|{event}" for n, event in enumerate(events, start=1))


UNIT = "SampleTrigger on Account trigger event AfterUpdate|__sfdc_trigger/SampleTrigger"


@pytest.mark.parametrize("events,expected", [
    # Codex review, round 2: debug text is not an event, in either direction.
    (["EXECUTION_STARTED", "USER_DEBUG|[1]|DEBUG|diagnostic marker |FATAL_ERROR|synthetic", "EXECUTION_FINISHED"],
     "PASS"),
    (["EXECUTION_STARTED",
      *[event for _ in range(8) for event in ("CODE_UNIT_STARTED|[EXTERNAL]|01q000000000001|" + UNIT,
                                               "USER_DEBUG|[5]|DEBUG|example |CODE_UNIT_FINISHED|" + UNIT)],
      *["CODE_UNIT_FINISHED|" + UNIT] * 8, "EXECUTION_FINISHED"],
     "FAIL"),
])
def test_debug_message_text_cannot_change_the_side_effects_verdict(monkeypatch, events, expected):
    _analyzer_against(monkeypatch, _one_log(_lines(*events)))
    assert dispatch_side_eff("synthetic-dev", "synthetic change").status == expected


def test_a_failed_log_query_is_an_error_with_its_reason(monkeypatch):
    _analyzer_against(monkeypatch, _query(1))
    result = dispatch_side_eff("synthetic-dev", "synthetic change")
    assert result.status == "ERROR"
    assert "the ApexLog query failed" in result.detail


def test_zero_coverage_without_the_analyzers_no_logs_result_stays_an_error(monkeypatch):
    evidence = {"score": None, "findings": [], "summary": {"p0_count": 0, "p1_count": 0, "p2_count": 0, "total": 0},
                "logs_analyzed": 0, "analysis_scope": "latest_available_log"}
    monkeypatch.setattr("jsc_qa.dispatcher.subprocess.run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(evidence), stderr=""))
    assert dispatch_side_eff("synthetic-dev", "synthetic").status == "ERROR"


def test_a_run_that_cannot_start_is_not_reported_as_a_failed_check(capsys):
    from jsc_qa import cli as qa
    assert qa.main(["run", "xyzzy nothing recognizable", "--org", "synthetic-dev"]) == 2
    assert "no change-types matched" in capsys.readouterr().err


@pytest.mark.parametrize("statuses,code,failures,not_run", [
    (["ERROR"], 2, False, "1 check could not run and none failed"),
    (["PASS", "ERROR", "ERROR"], 2, False, "2 checks could not run and none failed"),
    (["MANUAL_REQUIRED", "ERROR"], 2, False, "1 check could not run and none failed"),
    (["FAIL", "ERROR"], 1, True, "In addition, 1 check could not run"),
    (["FAIL"], 1, True, None),
])
def test_checks_that_could_not_run_are_not_reported_as_failures(statuses, code, failures, not_run):
    results = [DispatchResult("Example", status, "Synthetic fixture") for status in statuses]
    assert result_exit_code(results) == code
    text = format_report([], "synthetic-dev", False, results)
    assert ("VERDICT: FAILURES detected." in text) is failures
    if not_run:
        assert not_run in text
    else:
        assert "could not run" not in text
