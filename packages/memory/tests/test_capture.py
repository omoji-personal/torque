"""Capture tests — F-then-S detection, normalized retry signature, transient filter."""
import json
import time

from jsc_memory import capture, spool, storage


def _spool_events(session_id, events):
    """Helper: write events to spool."""
    storage.ensure_dirs()
    sp = spool.spool_path_for_session(session_id)
    with open(sp, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_simple_failure_captured(isolated_memory_dir):
    _spool_events("s1", [
        {"ts": 1000.0, "tool": "Bash", "event_type": "tool_failure", "exit": 1,
         "input_hash": "x", "input": "git push", "stderr_first_line": "error: nothing"},
    ])
    written, drop = capture.synthesize_session("s1")
    assert written == 1
    assert drop is None
    pending = storage.list_review_pending()
    assert len(pending) == 1
    assert pending[0].trigger in ("tool_error", "failure_then_success")


def test_failure_then_success_with_edited_input(isolated_memory_dir):
    """Operator fixes the command between F and S — should capture as F-then-S."""
    _spool_events("s2", [
        {"ts": 1000.0, "tool": "Bash", "event_type": "tool_failure", "exit": 1,
         "input_hash": "fail", "input": "sf project deploy --metadata Flow:EXAMPLE",
         "stderr_first_line": "INVALID_FIELD"},
        {"ts": 1010.0, "tool": "Read", "event_type": "tool_call", "exit": 0,
         "input_hash": "r1", "input": ""},
        {"ts": 1020.0, "tool": "Edit", "event_type": "tool_call", "exit": 0,
         "input_hash": "e1", "input": ""},
        {"ts": 1030.0, "tool": "Bash", "event_type": "tool_call", "exit": 0,
         "input_hash": "fixed",  # different input → operator edited
         "input": "sf project deploy --metadata Flow:EXAMPLE"},
    ])
    written, _ = capture.synthesize_session("s2")
    pending = storage.list_review_pending()
    triggers = [l.trigger for l in pending]
    # Should detect F-then-S OR sf_metadata_error (both valid HIGH-confidence outcomes)
    assert any(t in ("failure_then_success", "sf_metadata_error") for t in triggers), f"Got triggers: {triggers}"


def test_sf_metadata_error_captured(isolated_memory_dir):
    _spool_events("s3", [
        {"ts": 1000.0, "tool": "Bash", "event_type": "tool_failure", "exit": 1,
         "input_hash": "x", "input": "sf project deploy start --metadata Flow:Foo",
         "stderr_first_line": "INVALID_FIELD: No such column 'Bar__c'"},
    ])
    written, _ = capture.synthesize_session("s3")
    pending = storage.list_review_pending()
    assert any(l.trigger == "sf_metadata_error" for l in pending)
    assert any(l.confidence == "HIGH" for l in pending)


def test_python_traceback_captured(isolated_memory_dir):
    _spool_events("s4", [
        {"ts": 1000.0, "tool": "Bash", "event_type": "tool_failure", "exit": 1,
         "input_hash": "x", "input": "python3 script.py",
         "stderr_first_line": "Traceback (most recent call last):"},
    ])
    written, _ = capture.synthesize_session("s4")
    pending = storage.list_review_pending()
    assert any(l.trigger == "python_traceback" for l in pending)


def test_operator_correction_captured(isolated_memory_dir):
    _spool_events("s5", [
        {"ts": 1000.0, "tool": "UserPrompt", "event_type": "operator_correction",
         "input_hash": "msg-hash", "stderr_first_line": "no use after-save flow"},
    ])
    written, _ = capture.synthesize_session("s5")
    pending = storage.list_review_pending()
    assert any(l.trigger == "operator_correction" for l in pending)
    assert any(l.confidence == "HIGH" for l in pending)


def test_dedup_same_signature(isolated_memory_dir):
    """50 same-root failures should dedupe to 1 candidate."""
    events = []
    for i in range(50):
        events.append({
            "ts": 1000.0 + i,
            "tool": "Bash",
            "event_type": "tool_failure",
            "exit": 1,
            "input_hash": f"x{i}",  # different hash but same root
            "input": "sf project deploy start --target-org sf-test",
            "stderr_first_line": "INVALID_FIELD: same error",
        })
    _spool_events("s6", events)
    written, _ = capture.synthesize_session("s6")
    # Should dedupe by signature → 1 sf_metadata_error candidate, not 50
    pending = storage.list_review_pending()
    sf_meta_count = sum(1 for l in pending if l.trigger == "sf_metadata_error")
    assert sf_meta_count <= 2, f"Expected ≤2 sf_metadata_error after dedup; got {sf_meta_count}"


def test_empty_spool_no_capture(isolated_memory_dir):
    written, drop = capture.synthesize_session("nonexistent")
    assert written == 0
    assert drop is None


def test_synthesize_orphan(isolated_memory_dir):
    """Orphaned spool can be recovered."""
    _spool_events("orphan-1", [
        {"ts": 1000.0, "tool": "Bash", "event_type": "tool_failure", "exit": 1,
         "input_hash": "x", "input": "test cmd", "stderr_first_line": "error"},
    ])
    spool_path = spool.spool_path_for_session("orphan-1")
    written, _ = capture.synthesize_orphan(spool_path)
    assert written == 1
