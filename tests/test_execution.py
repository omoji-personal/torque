import io
import json
import os
import shlex
import sys
import time

import pytest

from delegated_helpers import (CLEAN, MODEL, ORGS, WRITE, as_agent, delegated_grant, delegated_workspace,
                               flow_request, launched)
from torque import approval, changes, execution, gate
from torque import workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


def send(monkeypatch, capsys, event):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    return gate.main(), capsys.readouterr()


def approved_call(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    delegated_grant(root, req)
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    base = {"cwd": str(root), "session_id": "s1", "tool_use_id": "t-exec-1", "tool_name": "Bash",
            "tool_input": {"command": shlex.join(WRITE)}}
    assert send(monkeypatch, capsys, {**base, "hook_event_name": "PreToolUse", "permission_mode": "default"})[0] == 0
    return root, req, base


def events(root, req, kind):
    return [e for e in changes.get_change(root, "Acme", req["change"])["events"] if e["kind"] == kind]


def executed(root, req):
    return events(root, req, "approval_executed")


FOREGROUND = {"stdout": "Deploy ID: 0Af000000000009AAA", "stderr": "", "interrupted": False, "isImage": False,
              "noOutputExpected": False}


def test_post_tool_use_records_execution_linked_to_the_consumption(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)
    event = {**base, "hook_event_name": "PostToolUse", "tool_response": FOREGROUND}
    assert send(monkeypatch, capsys, event)[0] == 0
    [row] = executed(root, req)
    [consume] = events(root, req, "approval_consume")
    assert (row["tool_use_id"], row["outcome"], row["exit_status"], row["consume_event_id"], row["approver_kind"]) == \
        ("t-exec-1", "succeeded", 0, consume["id"], "ai")
    assert (row["approval_id"], row["request_id"], row["session_id"]) == \
        (consume["approval_id"], req["id"], "s1")


def test_failed_call_records_failed(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)
    send(monkeypatch, capsys, {**base, "hook_event_name": "PostToolUseFailure", "error": "Exit code 1\nDeploy failed",
                               "is_interrupt": False})
    assert [(r["outcome"], r["exit_status"]) for r in executed(root, req)] == [("failed", 1)]


def test_calls_without_an_approval_record_nothing_and_never_block(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)
    code, _ = send(monkeypatch, capsys, {**base, "tool_use_id": "t-other", "hook_event_name": "PostToolUse",
                                         "tool_response": {}})
    assert code == 0 and executed(root, req) == []
    # A parsed post event that is otherwise malformed never blocks and records nothing.
    for bad in ({"hook_event_name": "PostToolUse"},
                {**base, "hook_event_name": "PostToolUse", "tool_input": "not an object"},
                {**base, "hook_event_name": "PostToolUseFailure", "tool_use_id": 7}):
        assert send(monkeypatch, capsys, bad)[0] == 0
    assert executed(root, req) == []


def test_unparseable_input_still_fails_closed_even_when_it_mentions_post_tool_use(tmp_path, monkeypatch, capsys):
    # F2: dispatch reads only the parsed hook_event_name; unparseable input is a15's exit 2.
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name": "PostToolUse"'))
    assert gate.main() == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO('["PostToolUse"]'))
    assert gate.main() == 2


def test_outcome_parsing():
    assert execution.outcome({"hook_event_name": "PostToolUse", "tool_response": {"interrupted": True}}) == \
        ("interrupted", None)
    assert execution.outcome({"hook_event_name": "PostToolUse", "tool_response": {"exitCode": 3}}) == ("failed", 3)
    assert execution.outcome({"hook_event_name": "PostToolUseFailure", "error": "no code"}) == ("failed", None)


@pytest.mark.parametrize("event, expected", [
    ({"tool_name": "Bash", "tool_response": FOREGROUND}, ("succeeded", 0)),
    ({"tool_name": "Bash", "tool_response": {"exitCode": 0, "interrupted": False}}, ("succeeded", 0)),
    ({"tool_name": "Bash", "tool_response": {"exit_code": 4}}, ("failed", 4)),
    # D0: a Bash call past its own timeout moves to a background task and still logs PostToolUse.
    ({"tool_name": "Bash", "tool_response": {**FOREGROUND, "backgroundTaskId": "b1", "timedOutAfterMs": 2000}},
     ("unknown", None)),
    ({"tool_name": "Bash", "tool_response": {**FOREGROUND, "timedOutAfterMs": 2000}}, ("unknown", None)),
    ({"tool_name": "Bash", "tool_response": {**FOREGROUND, "backgroundTaskId": "b1", "exitCode": 0}},
     ("unknown", None)),
    ({"tool_name": "Bash", "tool_input": {"command": "x", "run_in_background": True}, "tool_response": FOREGROUND},
     ("unknown", None)),
    # No clear completed-in-foreground signal: unknown, never succeeded.
    ({"tool_name": "Bash", "tool_response": {"stdout": ""}}, ("unknown", None)),
    ({"tool_name": "Bash", "tool_response": {}}, ("unknown", None)),
    ({"tool_name": "Bash"}, ("unknown", None)),
    ({"tool_name": "Bash", "tool_response": "text"}, ("unknown", None)),
    ({"tool_name": "Bash", "tool_response": {"exitCode": True, "interrupted": False}}, ("unknown", None)),
    # A non-Bash tool (MCP, browser) has no exit status; its PostToolUse means it returned.
    ({"tool_name": "mcp__sf__deploy", "tool_response": [{"type": "text", "text": "ok"}]}, ("succeeded", None)),
    ({"tool_name": "mcp__sf__deploy", "tool_response": {"backgroundTaskId": "b1"}}, ("unknown", None)),
])
def test_post_tool_use_outcomes(event, expected):
    assert execution.outcome({"hook_event_name": "PostToolUse", **event}) == expected


@pytest.mark.parametrize("event, expected", [
    ({"error": "Exit code 1\nDeploy failed"}, ("failed", 1)),
    ({"error": "Exit code 127"}, ("failed", 127)),
    ({"error": "Deploy failed\nExit code 5"}, ("failed", None)),
    ({"error": "stderr mentions exit code 9"}, ("failed", None)),
    ({"error": None}, ("failed", None)),
    ({"error": "Exit code 130", "is_interrupt": True}, ("interrupted", None)),
])
def test_post_tool_use_failure_outcomes(event, expected):
    assert execution.outcome({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", **event}) == expected


def test_a_backgrounded_approved_call_records_unknown(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)
    send(monkeypatch, capsys, {**base, "hook_event_name": "PostToolUse",
                               "tool_response": {**FOREGROUND, "backgroundTaskId": "b7", "timedOutAfterMs": 2000}})
    assert [(r["outcome"], r["exit_status"]) for r in executed(root, req)] == [("unknown", None)]


def test_a_repeated_post_event_records_once(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)
    event = {**base, "hook_event_name": "PostToolUse", "tool_response": FOREGROUND}
    send(monkeypatch, capsys, event)
    send(monkeypatch, capsys, event)
    assert len(executed(root, req)) == 1


def test_a_post_event_from_another_session_records_nothing(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)
    send(monkeypatch, capsys, {**base, "session_id": "s2", "hook_event_name": "PostToolUse",
                               "tool_response": FOREGROUND})
    assert executed(root, req) == []


def test_a_marker_without_its_consume_event_marks_nothing(tmp_path, monkeypatch, capsys):
    """A consumed marker naming this tool_use_id is not enough: the change record must
    also hold the gate's approval_consume event for the same approval and call."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    record = delegated_grant(root, req)
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    dirs = approval._dirs(root, "Acme")
    (dirs["consumed"] / record["id"]).write_text(json.dumps({"at": "2026-09-25T00:00:00Z", "session_id": "s1",
                                                             "tool_use_id": "t-forged"}))
    code, _ = send(monkeypatch, capsys, {"cwd": str(root), "session_id": "s1", "tool_use_id": "t-forged",
                                         "tool_name": "Bash", "tool_input": {"command": shlex.join(WRITE)},
                                         "hook_event_name": "PostToolUse", "tool_response": FOREGROUND})
    assert code == 0 and executed(root, req) == []


def test_a_denied_call_records_nothing(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    base = {"cwd": str(root), "session_id": "s1", "tool_use_id": "t-deny", "tool_name": "Bash",
            "tool_input": {"command": shlex.join(WRITE)}}
    send(monkeypatch, capsys, {**base, "hook_event_name": "PreToolUse", "permission_mode": "default"})
    assert events(root, req, "approval_consume") == []
    assert send(monkeypatch, capsys, {**base, "hook_event_name": "PostToolUse", "tool_response": FOREGROUND})[0] == 0
    assert executed(root, req) == []


def test_an_unbound_session_records_nothing(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)
    monkeypatch.delenv("TORQUE_LAUNCH")
    send(monkeypatch, capsys, {**base, "hook_event_name": "PostToolUse", "tool_response": FOREGROUND})
    assert executed(root, req) == []


def test_a_failure_to_record_is_reported_and_never_blocks(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)

    def boom(*a, **k):
        raise ws.WorkspaceError("disk full")
    monkeypatch.setattr(changes, "append_approval_event", boom)
    code, out = send(monkeypatch, capsys, {**base, "hook_event_name": "PostToolUse", "tool_response": FOREGROUND})
    assert code == 0 and "execution record could not be written" in out.err and "disk full" in out.err


def test_the_record_step_runs_under_the_gate_budget(tmp_path, monkeypatch, capsys):
    root, req, base = approved_call(tmp_path, monkeypatch, capsys)

    seen = []

    def slow(*a, **k):
        seen.append(gate._deadline)  # set only while a call budget is active
        gate._deadline = time.monotonic() - 1  # the budget has run out
        gate._spend()
    monkeypatch.setattr(execution, "_record", slow)
    code, out = send(monkeypatch, capsys, {**base, "hook_event_name": "PostToolUse", "tool_response": FOREGROUND})
    assert seen and seen[0] is not None and gate._deadline is None
    assert code == 0 and "time budget" in out.err and executed(root, req) == []


def browser_window(root):
    cid = changes.create_change(root, "Acme", "Layout change", "Add Tier to the Case layout", [], "acme-dev")["id"]
    req = approval.create_request(root, "Acme", cid, "acme-dev", browser_minutes=10,
                                  purpose="Add Tier to the Case layout", resolve=ORGS.get)
    return req, delegated_grant(root, req)


def test_each_browser_action_in_a_window_records_its_own_execution(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req, window = browser_window(root)
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    base = {"cwd": str(root), "session_id": "s1", "tool_name": "Bash",
            "tool_input": {"command": "torque browser run --target-org acme-dev"}}
    for tid in ("t-b1", "t-b2"):
        assert send(monkeypatch, capsys, {**base, "tool_use_id": tid, "hook_event_name": "PreToolUse",
                                          "permission_mode": "default"})[0] == 0
    send(monkeypatch, capsys, {**base, "tool_use_id": "t-b2", "hook_event_name": "PostToolUseFailure",
                               "error": "Exit code 2"})
    send(monkeypatch, capsys, {**base, "tool_use_id": "t-b1", "hook_event_name": "PostToolUse",
                               "tool_response": FOREGROUND})
    [consume] = events(root, req, "approval_consume")
    rows = [(r["tool_use_id"], r["approval_id"], r["outcome"], r["exit_status"], r["consume_event_id"])
            for r in executed(root, req)]
    assert rows == [("t-b2", window["id"], "failed", 2, consume["id"]),
                    ("t-b1", window["id"], "succeeded", 0, consume["id"])]
    send(monkeypatch, capsys, {**base, "tool_use_id": "t-b9", "hook_event_name": "PostToolUse",
                               "tool_response": FOREGROUND})
    assert len(executed(root, req)) == 2


# F2: a PreToolUse event is decided as PreToolUse whatever text its command holds.

POST_TEXT = 'echo "PostToolUse" && '


def mode_folder(tmp_path, mode):
    folder = tmp_path / "w"
    folder.mkdir()
    if mode is not None:
        (folder / "workspace.json").write_text(json.dumps(
            {"schema": "torque.workspace/1", "name": "Example", "profile": "generic", "ai_access": mode}),
            encoding="utf-8")
    return folder


@pytest.mark.parametrize("mode, org_code", [("build-only", 2), ("full", 0), (None, 0)])
def test_pre_tool_use_mentioning_post_tool_use_is_decided_as_pre_tool_use(tmp_path, monkeypatch, capsys, mode,
                                                                          org_code):
    folder = mode_folder(tmp_path, mode)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    base = {"hook_event_name": "PreToolUse", "cwd": str(folder), "session_id": "s1", "tool_use_id": "t1",
            "tool_name": "Bash"}
    code, out = send(monkeypatch, capsys, {**base, "tool_input": {"command": POST_TEXT + "sf org display --target-org prod"}})
    assert code == org_code and (out.err.strip() if org_code else out.out == "")
    code, out = send(monkeypatch, capsys, {**base, "tool_input": {"command": POST_TEXT + "git status"}})
    assert code == 0 and out.out == ""


@pytest.mark.parametrize("mode", ["build-only", "full", None])
def test_post_events_outside_connected_mode_never_block_and_record_nothing(tmp_path, monkeypatch, capsys, mode):
    folder = mode_folder(tmp_path, mode)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    for name in execution.POST_EVENTS:
        code, out = send(monkeypatch, capsys, {"hook_event_name": name, "cwd": str(folder), "session_id": "s1",
                                               "tool_use_id": "t1", "tool_name": "Bash",
                                               "tool_input": {"command": "sf org display --target-org prod"},
                                               "tool_response": FOREGROUND, "error": "Exit code 1"})
        assert code == 0 and out.out == "" and out.err == ""


def test_pre_tool_use_mentioning_post_tool_use_is_still_decided_in_connected_mode(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    launched(monkeypatch, root)
    as_agent(monkeypatch)
    event = {"hook_event_name": "PreToolUse", "cwd": str(root), "session_id": "s1", "tool_use_id": "t1",
             "permission_mode": "default", "tool_name": "Bash",
             "tool_input": {"command": "echo PostToolUse; " + shlex.join(WRITE)}}
    code, _ = send(monkeypatch, capsys, event)
    assert code == 2 and executed(root, req) == []


def test_malformed_pre_tool_use_mentioning_post_tool_use_fails_closed(tmp_path, monkeypatch, capsys):
    for event in ({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": "PostToolUse"},
                  {"hook_event_name": "PreToolUse", "note": "PostToolUse"},
                  {"note": '"PostToolUse'}):
        assert send(monkeypatch, capsys, event)[0] == 2


def test_the_gate_and_execution_agree_on_the_post_events():
    assert gate.POST_EVENTS == execution.POST_EVENTS == ("PostToolUse", "PostToolUseFailure")
    assert "approval_executed" in changes.APPROVAL_KINDS
    assert changes.APPROVAL_EVENT_FIELDS["approval_executed"] == ("approval_id", "tool_use_id", "outcome")
