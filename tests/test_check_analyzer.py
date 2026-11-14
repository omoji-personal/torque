"""Code Analyzer inside `torque check metadata`: present (stubbed), absent, failing; one report per problem."""
import os
import subprocess
from types import SimpleNamespace

import pytest

from check_fixtures import e10_project, Tree, start, create_task
from jsc_loganalyzer import code_analyzer as adapter


def flow_report(t, extra=()):
    rel = os.path.join("force-app", "main", "default", "flows", "Gift_Thank_You_Task.flow-meta.xml")
    def v(engine, rule, line, sev=2):
        return {"engine": engine, "rule": rule, "severity": sev, "message": f"{rule} message.", "resources": [],
                "primaryLocationIndex": 0, "locations": [{"file": rel, "startLine": line}]}
    return {"runDir": str(t.root) + os.sep, "versions": {"code-analyzer": "0.52.0"},
            "violations": [v("regex", "AvoidOldSalesforceApiVersions", 3), v("flow", "MissingDescription", 2, 4),
                           v("flow", "MissingDescription", 11, 4), v("flow", "HardcodedId", 30, 3),
                           v("flow", "MissingFaultHandler", 11, 2), v("flow", "UnusedResource", 99, 4),
                           {**v("flow", "HardcodedId", 1), "locations": [{"file": "elsewhere/Other.flow-meta.xml",
                                                                         "startLine": 1}]}, *extra]}


def lines_of(t):
    """Line numbers of the planted flow's description position, element and owner value."""
    text = (t.root / "force-app/main/default/flows/Gift_Thank_You_Task.flow-meta.xml").read_text().splitlines()
    element = next(i for i, x in enumerate(text, 1) if "<recordCreates>" in x)
    owner = next(i for i, x in enumerate(text, 1) if "OwnerId" in x)
    return element, owner


def test_analyzer_findings_appear_once(tmp_path):
    t = e10_project(tmp_path)
    element, owner = lines_of(t)
    calls = []

    def runner(workspace, output, **kw):
        calls.append((workspace, kw))
        report = flow_report(t)
        for violation in report["violations"]:
            loc = violation["locations"][0]
            if violation["rule"] in ("MissingFaultHandler",) or (violation["rule"] == "MissingDescription"
                                                                 and loc["startLine"] == 11):
                loc["startLine"] = element
            if violation["rule"] == "HardcodedId" and loc["file"].startswith("force-app"):
                loc["startLine"] = owner
        return report

    r = t.check(analyzer_mode="auto", runner=runner)
    workspace, kw = calls[0]
    assert workspace == str(t.root) and kw["rule_selectors"] == ("Recommended", "flow") and kw["cwd"] == str(t.root)
    assert [p.endswith("Gift_Thank_You_Task.flow-meta.xml") for p in kw["targets"]] == [True]
    assert kw["env"]["SF_DISABLE_TELEMETRY"]
    a = r["analyzer"]
    assert a["status"] == "ran" and a["findings"] == 6 and a["reported_once_with_torque"] == 5
    by_rule = {}
    for f in r["findings"]:
        by_rule.setdefault(f["rule"], []).append(f)
    assert by_rule["flow.no-fault-path"][0]["confirmed_by"] == ["code-analyzer flow:MissingFaultHandler"]
    assert by_rule["flow.hard-coded-id"][0]["confirmed_by"] == ["code-analyzer flow:HardcodedId"]
    assert by_rule["flow.api-version-behind"][0]["confirmed_by"] == ["code-analyzer regex:AvoidOldSalesforceApiVersions"]
    assert not any(r_.startswith("code-analyzer:flow:Missing") or r_.endswith("HardcodedId") for r_ in by_rule)
    unused = by_rule["code-analyzer:flow:UnusedResource"]
    assert len(unused) == 1 and unused[0]["source"] == "code-analyzer" and unused[0]["severity"] == "note"
    assert "ran with rule selectors Recommended + flow on 1 file(s)" in a["summary"]


def test_analyzer_absent_is_one_line_and_no_claim(tmp_path):
    t = e10_project(tmp_path)

    def runner(*a, **k):
        raise adapter.NotInstalled("the code-analyzer plugin is not installed; " + adapter.INSTALL_HINT)

    r = t.check(analyzer_mode="auto", runner=runner)
    assert r["analyzer"]["status"] == "not-installed" and r["analyzer"]["rule_selectors"] == []
    assert "sf plugins install code-analyzer" in r["analyzer"]["summary"]
    assert "Code Analyzer's rules" in r["not_checked"]
    assert all(f["source"] == "torque" and not f["confirmed_by"] for f in r["findings"])


def test_analyzer_failure_is_reported_not_hidden(tmp_path):
    t = e10_project(tmp_path)

    def runner(*a, **k):
        raise RuntimeError("sf code-analyzer run did not write a report (exit 1). stderr: boom")

    r = t.check(analyzer_mode="auto", runner=runner)
    assert r["analyzer"]["status"] == "failed" and "boom" in r["analyzer"]["detail"]
    assert "its rules are not covered" in r["analyzer"]["summary"]


def test_nothing_for_the_analyzer(tmp_path):
    t = Tree(tmp_path)
    t.field("Shift__c", "Hours__c")
    r = t.check(analyzer_mode="auto", runner=lambda *a, **k: pytest.fail("must not run"))
    assert r["analyzer"]["status"] == "not-applicable"


def test_ignored_rule_also_drops_its_analyzer_twin(tmp_path):
    t = e10_project(tmp_path)
    element, _ = lines_of(t)

    def runner(workspace, output, **kw):
        return {"runDir": str(t.root), "violations": [{"engine": "flow", "rule": "MissingFaultHandler", "severity": 2,
                "message": "m", "locations": [{"file": str(t.root / "force-app/main/default/flows/"
                                                           "Gift_Thank_You_Task.flow-meta.xml"),
                                               "startLine": element}]}]}

    r = t.check(analyzer_mode="auto", runner=runner, ignore=["flow.no-fault-path"])
    assert not [f for f in r["findings"] if "fault" in f["rule"].lower()]


# The adapter itself (packages/debug_log_analyzer/jsc_loganalyzer/code_analyzer.py), as changed for this command.

def test_adapter_passes_selectors_targets_and_working_folder(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        out = cmd[cmd.index("--output-file") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            fh.write('{"violations": []}')
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(adapter, "run_tool", fake_run)
    report = adapter.run_code_analyzer(str(tmp_path), str(tmp_path / "o.json"), targets=["a.flow-meta.xml"],
                                       rule_selectors=("Recommended", "flow"), cwd=str(tmp_path), env={"X": "1"})
    assert report == {"violations": []}
    cmd = seen["cmd"]
    assert cmd[:3] == ["sf", "code-analyzer", "run"] and "--target-org" not in cmd
    assert cmd.count("--rule-selector") == 2 and cmd[cmd.index("--target") + 1] == "a.flow-meta.xml"
    assert seen["kw"]["cwd"] == str(tmp_path) and seen["kw"]["env"] == {"X": "1"}


@pytest.mark.parametrize("problem", ["no-sf", "no-plugin"])
def test_adapter_says_not_installed(tmp_path, monkeypatch, problem):
    def fake_run(cmd, **kw):
        if problem == "no-sf":
            raise FileNotFoundError("sf")
        return SimpleNamespace(returncode=127, stdout="",
                               stderr=" ›   Warning: code-analyzer is not a sf command.")

    monkeypatch.setattr(adapter, "run_tool", fake_run)
    with pytest.raises(adapter.NotInstalled, match="sf plugins install code-analyzer"):
        adapter.run_code_analyzer(str(tmp_path), str(tmp_path / "o.json"))


def test_adapter_other_failure_stays_runtime_error(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "run_tool", lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="",
                                                                                stderr="engine crashed"))
    with pytest.raises(RuntimeError) as info:
        adapter.run_code_analyzer(str(tmp_path), str(tmp_path / "o.json"))
    assert not isinstance(info.value, adapter.NotInstalled)


def test_adapter_timeout(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(adapter, "run_tool", fake_run)
    with pytest.raises(RuntimeError, match="timeout"):
        adapter.run_code_analyzer(str(tmp_path), str(tmp_path / "o.json"))
