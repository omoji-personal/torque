"""Debug logs in the format the Apex Developer Guide documents (Debug Log Details,
Debug Log Levels), written by hand with synthetic IDs, names and values.

A line is `HH:mm:ss.SSS (nanoseconds since the request started)|EVENT|fields`. A
trigger's code unit is named `<Trigger> on <Object> trigger event <Event>` and ends
with its typeRef, `__sfdc_trigger/<Trigger>` or `__sfdc_trigger/<namespace>/<Trigger>`.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
from types import SimpleNamespace

import pytest

from jsc_loganalyzer import cli
from jsc_loganalyzer.parsers import run_all_parsers

HEADER = ("64.0 APEX_CODE,FINEST;APEX_PROFILING,INFO;CALLOUT,INFO;DB,INFO;SYSTEM,DEBUG;"
          "VALIDATION,INFO;VISUALFORCE,INFO;WORKFLOW,INFO")
EVENT = re.compile(r"[A-Z][A-Z0-9_]*(?:\||$)")


def log(*lines: str) -> str:
    """A log body: the header, then each event with its timestamp. Other lines
    (blank lines, stack traces, limit details) are kept as they are."""
    body = [HEADER]
    for n, text in enumerate(lines, start=1):
        body.append(f"09:00:00.{n % 1000:03d} ({n * 1000000})|{text}" if EVENT.match(text) else text)
    return "\n".join(body)


def unit(edge: str, trigger: str, event: str, obj: str = "Account", ref: str | None = None) -> str:
    """A trigger code-unit line in the documented shape."""
    name = f"{trigger} on {obj} trigger event {event}"
    ref = f"__sfdc_trigger/{trigger}" if ref is None else ref
    head = "CODE_UNIT_STARTED|[EXTERNAL]|01q000000000001|" if edge == "STARTED" else "CODE_UNIT_FINISHED|"
    return head + name + (f"|{ref}" if ref else "")


def recursive(depth: int, trigger: str = "SampleTrigger", ref: str | None = None) -> list[str]:
    """An after-update trigger that updates its own records, unguarded, `depth` levels deep."""
    events = ["EXECUTION_STARTED", "CODE_UNIT_STARTED|[EXTERNAL]|execute_anonymous_apex",
              "DML_BEGIN|[1]|Op:Update|Type:Account|Rows:1"]
    for _ in range(depth):
        events += [unit("STARTED", trigger, "AfterUpdate", ref=ref), "DML_BEGIN|[4]|Op:Update|Type:Account|Rows:1"]
    for _ in range(depth):
        events += ["DML_END|[4]", unit("FINISHED", trigger, "AfterUpdate", ref=ref)]
    return events + ["DML_END|[1]", "CODE_UNIT_FINISHED|execute_anonymous_apex", "EXECUTION_FINISHED"]


def categories(text: str) -> list[str]:
    return [finding.category for finding in run_all_parsers(text)]


def test_the_documented_example_line_is_read_as_a_trigger_unit():
    text = log(*recursive(8))
    assert "|SampleTrigger on Account trigger event AfterUpdate|__sfdc_trigger/SampleTrigger" in text
    [finding] = [f for f in run_all_parsers(text) if f.category == "trigger-recursion-deep"]
    assert finding.severity == 1
    assert "SampleTrigger (AfterUpdate)" in finding.message and "8 levels deep" in finding.message


def test_recursion_at_the_boundary_is_reported_as_p1():
    [finding] = [f for f in run_all_parsers(log(*recursive(6))) if f.category == "trigger-recursion-deep"]
    assert finding.severity == 2 and "6 levels deep" in finding.message


def test_a_namespaced_trigger_is_named_by_its_type_ref():
    text = log(*recursive(8, "PkgTrigger", ref="__sfdc_trigger/samplens/PkgTrigger"))
    [finding] = [f for f in run_all_parsers(text) if f.category == "trigger-recursion-deep"]
    assert "samplens/PkgTrigger" in finding.message


@pytest.mark.parametrize("name", [
    "SampleTrigger on Account trigger event BeforeUpdate for [001000000000001AAA]",  # older, no typeRef
    "SampleTrigger on Account trigger event BeforeUpdate for [new]|__sfdc_trigger/SampleTrigger",
])
def test_unit_names_with_record_ids_are_read(name):
    events = [f"CODE_UNIT_STARTED|[EXTERNAL]|01q000000000001|{name}" for _ in range(8)]
    [finding] = [f for f in run_all_parsers(log(*events)) if f.category == "trigger-recursion-deep"]
    assert "SampleTrigger (BeforeUpdate)" in finding.message


def test_chunks_of_200_records_are_not_recursion():
    # A 2,000-row insert runs each trigger ten times in a row, each run finished
    # before the next starts.
    events = ["EXECUTION_STARTED", "DML_BEGIN|[1]|Op:Insert|Type:Account|Rows:2000"]
    for _ in range(10):
        events += [unit("STARTED", "SampleTrigger", "BeforeInsert"), unit("FINISHED", "SampleTrigger", "BeforeInsert")]
    for _ in range(10):
        events += [unit("STARTED", "SampleTrigger", "AfterInsert"), unit("FINISHED", "SampleTrigger", "AfterInsert")]
    events += ["DML_END|[1]", "EXECUTION_FINISHED"]
    assert "trigger-recursion-deep" not in categories(log(*events))


def test_before_and_after_events_of_one_trigger_are_counted_apart():
    events = []
    for _ in range(4):
        events += [unit("STARTED", "SampleTrigger", "BeforeUpdate"), unit("STARTED", "SampleTrigger", "AfterUpdate")]
    assert "trigger-recursion-deep" not in categories(log(*events))


def _trigger_inserting_a_contact(outcome: str) -> list[str]:
    return [
        "EXECUTION_STARTED",
        "DML_BEGIN|[1]|Op:Insert|Type:Account|Rows:1",
        unit("STARTED", "SampleTrigger", "AfterInsert"),
        "DML_BEGIN|[7]|Op:Insert|Type:Contact|Rows:1",
        "CODE_UNIT_STARTED|[EXTERNAL]|Validation:Contact:new",
        "VALIDATION_RULE|03d000000000001|Require_Sample_Phone",
        "VALIDATION_FORMULA|ISBLANK(Phone)|Phone=null",
        outcome,
        "CODE_UNIT_FINISHED|Validation:Contact:new",
        "DML_END|[7]",
        unit("FINISHED", "SampleTrigger", "AfterInsert"),
        "DML_END|[1]",
        "EXECUTION_FINISHED",
    ]


def test_a_validation_rule_rejecting_a_triggers_dml_is_reported():
    [finding] = [f for f in run_all_parsers(log(*_trigger_inserting_a_contact("VALIDATION_FAIL")))
                 if f.category == "flow-trigger-vr-chain"]
    assert finding.severity == 2
    assert "Require_Sample_Phone" in finding.message and "SampleTrigger (AfterInsert)" in finding.message


def test_validation_rules_that_pass_inside_a_trigger_are_normal():
    assert "flow-trigger-vr-chain" not in categories(log(*_trigger_inserting_a_contact("VALIDATION_PASS")))


def test_a_validation_failure_on_the_users_own_save_is_not_a_trigger_problem():
    text = log("EXECUTION_STARTED", "DML_BEGIN|[1]|Op:Insert|Type:Contact|Rows:1",
               "VALIDATION_RULE|03d000000000001|Require_Sample_Phone", "VALIDATION_FAIL",
               "DML_END|[1]", "EXECUTION_FINISHED")
    assert "flow-trigger-vr-chain" not in categories(text)


def test_a_validation_failure_after_a_nested_trigger_finished_still_names_the_outer_one():
    events = _trigger_inserting_a_contact("VALIDATION_FAIL")
    nested = [unit("STARTED", "ContactTrigger", "BeforeInsert", obj="Contact"),
              unit("FINISHED", "ContactTrigger", "BeforeInsert", obj="Contact")]
    at = events.index("CODE_UNIT_STARTED|[EXTERNAL]|Validation:Contact:new")
    [finding] = [f for f in run_all_parsers(log(*events[:at], *nested, *events[at:]))
                 if f.category == "flow-trigger-vr-chain"]
    assert "SampleTrigger (AfterInsert)" in finding.message


def _unhandled() -> list[str]:
    # The order the guide documents: the exception, the fatal error with its stack
    # trace, then the units finish as the transaction ends.
    error = "System.NullPointerException: Attempt to de-reference a null object"
    return [
        "EXECUTION_STARTED",
        unit("STARTED", "SampleTrigger", "BeforeUpdate"),
        f"EXCEPTION_THROWN|[5]|{error}",
        f"FATAL_ERROR|{error}",
        "",
        "Trigger.SampleTrigger: line 5, column 1",
        f"FATAL_ERROR|{error}",
        "",
        "Trigger.SampleTrigger: line 5, column 1",
        unit("FINISHED", "SampleTrigger", "BeforeUpdate"),
        "EXECUTION_FINISHED",
    ]


def test_an_uncaught_exception_is_reported_once_as_unhandled():
    [finding] = [f for f in run_all_parsers(log(*_unhandled())) if f.category == "unhandled-exception"]
    assert finding.severity == 1
    assert "NullPointerException" in finding.message
    assert "swallow" not in finding.message.lower() and "caught" not in finding.message.lower()
    assert "fatal-swallowed" not in categories(log(*_unhandled()))


def test_an_unhandled_exception_with_a_long_limit_summary_is_still_unhandled():
    # The earlier lookahead read this shape, where the finish markers are more than
    # ten lines away, as "caught and suppressed".
    events = _unhandled()
    limits = ["CUMULATIVE_LIMIT_USAGE", "LIMIT_USAGE_FOR_NS|(default)|",
              *[f"  Number of synthetic resource {n}: 0 out of 100" for n in range(12)], "",
              "CUMULATIVE_LIMIT_USAGE_END"]
    at = events.index(unit("FINISHED", "SampleTrigger", "BeforeUpdate"))
    assert categories(log(*events[:at], *limits, *events[at:])).count("unhandled-exception") == 1


def test_a_caught_exception_is_not_reported_as_unhandled():
    text = log("EXECUTION_STARTED", unit("STARTED", "SampleTrigger", "BeforeUpdate"),
               "EXCEPTION_THROWN|[5]|System.DmlException: Synthetic failure",
               "USER_DEBUG|[7]|DEBUG|handled synthetic failure",
               unit("FINISHED", "SampleTrigger", "BeforeUpdate"), "EXECUTION_FINISHED")
    assert "unhandled-exception" not in categories(text)


def test_no_finding_depends_on_one_orgs_trigger_bypass_setting():
    # The earlier parser flagged DML in any namespaced trigger that did not first
    # query one org's custom setting; that convention is not Salesforce's.
    invented = "\n".join(["|CODE_UNIT_STARTED|[EXTERNAL]|Trigger:samplens__PkgTrigger",
                          "|DML_BEGIN|[10]|Op:Update|Type:Account|Rows:1"])
    documented = log(unit("STARTED", "PkgTrigger", "AfterUpdate", ref="__sfdc_trigger/samplens/PkgTrigger"),
                     "DML_BEGIN|[10]|Op:Update|Type:Account|Rows:1",
                     unit("FINISHED", "PkgTrigger", "AfterUpdate", ref="__sfdc_trigger/samplens/PkgTrigger"))
    for text in (invented, documented):
        assert not [f for f in run_all_parsers(text) if "bypass" in (f.category + f.message).lower()]


def test_a_debug_message_that_contains_a_fatal_error_token_is_not_a_fatal_error():
    # Codex review, round 2: USER_DEBUG carries a user-supplied string.
    text = log("EXECUTION_STARTED", "USER_DEBUG|[1]|DEBUG|diagnostic marker |FATAL_ERROR|synthetic",
               "EXECUTION_FINISHED")
    assert run_all_parsers(text) == []


def test_debug_messages_cannot_close_real_trigger_units():
    # Codex review, round 2: each debug message quotes a unit's finish line.
    events = ["EXECUTION_STARTED"]
    for _ in range(8):
        events += [unit("STARTED", "SampleTrigger", "AfterUpdate"),
                   "USER_DEBUG|[5]|DEBUG|example |" + unit("FINISHED", "SampleTrigger", "AfterUpdate")]
    events += [unit("FINISHED", "SampleTrigger", "AfterUpdate")] * 8 + ["EXECUTION_FINISHED"]
    [finding] = [f for f in run_all_parsers(log(*events)) if f.category == "trigger-recursion-deep"]
    assert finding.severity == 1 and "8 levels deep" in finding.message


@pytest.mark.parametrize("message", [
    "|CALLOUT_REQUEST|GET https://example.invalid/api",
    "|SOQL_EXECUTE_BEGIN|[1]|Aggregations:0|SELECT Id FROM Account",
    "Number of SOQL queries: 99 out of 100",
])
def test_event_text_inside_a_debug_message_is_only_text(message):
    text = log("EXECUTION_STARTED", f"USER_DEBUG|[1]|DEBUG|quoted {message}", "EXECUTION_FINISHED")
    assert run_all_parsers(text) == []


def test_debug_messages_cannot_open_trigger_units():
    events = []
    for _ in range(5):
        events += [unit("STARTED", "SampleTrigger", "AfterUpdate"),
                   "USER_DEBUG|[5]|DEBUG|example |" + unit("STARTED", "SampleTrigger", "AfterUpdate")]
    assert "trigger-recursion-deep" not in categories(log(*events))  # 5 levels, not 10


def test_a_validation_failure_quoted_in_a_debug_message_inside_a_trigger_is_only_text():
    events = _trigger_inserting_a_contact("VALIDATION_PASS")
    events.insert(events.index("VALIDATION_PASS"), "USER_DEBUG|[9]|DEBUG|expected |VALIDATION_FAIL")
    assert "flow-trigger-vr-chain" not in categories(log(*events))


def test_limits_are_read_under_their_limit_event():
    text = log("EXECUTION_STARTED", "CUMULATIVE_LIMIT_USAGE", "LIMIT_USAGE_FOR_NS|(default)|",
               "  Number of SOQL queries: 95 out of 100", "CUMULATIVE_LIMIT_USAGE_END", "EXECUTION_FINISHED")
    [finding] = run_all_parsers(text)
    assert finding.category == "governor-asymptotic" and finding.severity == 1


def _main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_text_output_labels_each_finding_with_the_priority_its_totals_use(tmp_path):
    path = tmp_path / "synthetic.log"
    path.write_text(log("EXECUTION_STARTED", "CUMULATIVE_LIMIT_USAGE", "LIMIT_USAGE_FOR_NS|(default)|",
                        "  Maximum CPU time: 9500 out of 10000",
                        "  Number of SOQL queries: 80 out of 100", "CUMULATIVE_LIMIT_USAGE_END",
                        "EXECUTION_FINISHED"), encoding="utf-8")
    code, output, _ = _main(["--log-file", str(path)])
    assert code == 0
    assert "  P0: 1" in output and "  P1: 1" in output
    assert "[P0] governor-asymptotic: cpu" in output
    assert "[P1] governor-asymptotic: soql" in output


def _sf(query_result=None, query_exit=0, body_exit=0, body="", raises=None):
    """A stand-in for the sf CLI: the ApexLog query, then `sf apex log get` for each log."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if raises is not None:
            raise raises
        if argv[:3] == ["sf", "data", "query"]:
            stdout = query_result if isinstance(query_result, str) else json.dumps(query_result)
            return SimpleNamespace(returncode=query_exit, stdout=stdout, stderr="")
        return SimpleNamespace(returncode=body_exit, stdout=body, stderr="")

    run.calls = calls
    return run


EMPTY = {"status": 0, "result": {"records": [], "totalSize": 0, "done": True}}
ONE_LOG = {"status": 0, "result": {"records": [{"Id": "07L000000000001AAA"}], "totalSize": 1, "done": True}}


@pytest.mark.parametrize("since", [[], ["--since", "2026-06-13T10:00:00Z"]])
def test_an_org_with_no_logs_reports_nothing_analyzed_and_exits_zero(monkeypatch, since):
    monkeypatch.setattr(cli, "run_tool", _sf(EMPTY))
    code, output, _ = _main(["--target-org", "synthetic-dev", *since, "--json"])
    assert code == 0
    data = json.loads(output)
    assert data["logs_analyzed"] == 0 and data["analysis_scope"] == "no_logs_found"
    assert data["score"] is None and data["findings"] == [] and data["summary"]["total"] == 0
    assert "trace flag" in data["next_step"] and "error" not in data


def test_an_org_with_no_logs_says_so_in_text(monkeypatch):
    monkeypatch.setattr(cli, "run_tool", _sf(EMPTY))
    code, output, _ = _main(["--target-org", "synthetic-dev"])
    assert code == 0
    assert "no debug log found in synthetic-dev" in output and "trace flag" in output
    assert "health score" not in output


@pytest.mark.parametrize("sf,reason", [
    (_sf(EMPTY, query_exit=1), "query failed"),
    (_sf("not json"), "could not be read"),
    (_sf({"status": 0, "result": {}}), "could not be read"),
    (_sf(EMPTY, raises=FileNotFoundError("sf")), "could not run"),
])
def test_a_failed_query_is_an_error_not_an_empty_org(monkeypatch, sf, reason):
    monkeypatch.setattr(cli, "run_tool", sf)
    code, output, _ = _main(["--target-org", "synthetic-dev", "--json"])
    assert code == 2
    data = json.loads(output)
    assert reason in data["error"] and "logs_analyzed" not in data


@pytest.mark.parametrize("sf,reason", [
    (_sf(ONE_LOG, body_exit=1), "1 of 1 log(s) could not be fetched (sf exit 1)"),
    (_sf(ONE_LOG, body="   "), "1 of 1 log(s) could not be fetched (empty body)"),
])
def test_a_log_that_cannot_be_fetched_is_an_error_not_an_empty_org(monkeypatch, sf, reason):
    monkeypatch.setattr(cli, "run_tool", sf)
    code, output, _ = _main(["--target-org", "synthetic-dev", "--json"])
    assert code == 2
    data = json.loads(output)
    assert reason in data["error"]
    assert data["logs_analyzed"] == 0 and data["logs_unread"] == 1 and data["score"] is None


def _bodies(*replies):
    """sf for a --since window: the query lists one log per reply, then each `log get`
    returns its reply (a body, or a non-zero exit) or raises it."""
    listed = {"status": 0, "result": {"records": [{"Id": f"07L00000000000{n}AAA"} for n in range(len(replies))]}}
    queue = iter(replies)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["sf", "data", "query"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(listed), stderr="")
        reply = next(queue)
        if isinstance(reply, BaseException):
            raise reply
        return SimpleNamespace(returncode=0, stdout=reply, stderr="") if isinstance(reply, str) \
            else SimpleNamespace(returncode=reply, stdout="", stderr="synthetic failure")

    run.calls = calls
    return run


SINCE = ["--target-org", "synthetic-dev", "--since", "2026-06-13T10:00:00Z", "--json"]


def test_a_partly_unreadable_window_keeps_its_findings_and_is_incomplete(monkeypatch):
    # Codex review, round 2: one log read, one not.
    monkeypatch.setattr(cli, "run_tool", _bodies(log(*_unhandled()), 1))
    code, output, _ = _main(SINCE)
    assert code == 2
    data = json.loads(output)
    assert data["logs_analyzed"] == 1 and data["logs_unread"] == 1
    assert "1 of 2 log(s) could not be fetched (sf exit 1)" in data["error"]
    assert [f["category"] for f in data["findings"]] == ["unhandled-exception"]


def test_a_clean_log_does_not_make_a_partly_unreadable_window_pass(monkeypatch):
    monkeypatch.setattr(cli, "run_tool", _bodies(log("EXECUTION_STARTED", "EXECUTION_FINISHED"), 1))
    code, output, _ = _main(SINCE)
    data = json.loads(output)
    assert code == 2 and data["findings"] == [] and data["logs_unread"] == 1 and "error" in data


@pytest.mark.parametrize("failure,why", [
    (subprocess.TimeoutExpired(["sf"], 60), "TimeoutExpired, 1 not attempted"),
    (OSError("synthetic"), "OSError, 1 not attempted"),
])
def test_a_fetch_that_times_out_stops_and_counts_the_rest_as_unread(monkeypatch, failure, why):
    sf = _bodies(log("EXECUTION_STARTED", "EXECUTION_FINISHED"), failure, log(*_unhandled()))
    monkeypatch.setattr(cli, "run_tool", sf)
    code, output, _ = _main(SINCE)
    data = json.loads(output)
    assert code == 2 and data["logs_analyzed"] == 1 and data["logs_unread"] == 2
    assert f"2 of 3 log(s) could not be fetched ({why})" in data["error"]
    assert len(sf.calls) == 3  # the query and two fetches; the third log was not attempted


def test_a_partly_unreadable_window_in_text_shows_the_analysis_and_the_error(monkeypatch):
    monkeypatch.setattr(cli, "run_tool", _bodies(log(*_unhandled()), 1))
    code, output, error = _main(["--target-org", "synthetic-dev", "--since", "2026-06-13T10:00:00Z"])
    assert code == 2
    assert "[P0] unhandled-exception" in output
    assert "1 of 2 log(s) could not be fetched" in error


def test_a_malformed_since_is_refused_before_any_query(monkeypatch):
    sf = _sf(EMPTY)
    monkeypatch.setattr(cli, "run_tool", sf)
    code, _, error = _main(["--target-org", "synthetic-dev", "--since", "yesterday"])
    assert code == 2 and "ISO-8601" in error and sf.calls == []


def test_logs_that_were_found_are_fetched_and_analyzed(monkeypatch):
    monkeypatch.setattr(cli, "run_tool", _sf(ONE_LOG, body=log(*_unhandled())))
    code, output, _ = _main(["--target-org", "synthetic-dev", "--json"])
    assert code == 0
    data = json.loads(output)
    assert data["logs_analyzed"] == 1 and data["analysis_scope"] == "latest_available_log"
    assert [f["category"] for f in data["findings"]] == ["unhandled-exception"]
