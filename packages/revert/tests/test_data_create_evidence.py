"""Offline data-create outcome, diagnostics and persisted capture regressions."""
import json

import pytest
from jsc_revert import manifest, org_detect, org_sequence
from jsc_revert.cli import build_parser
from jsc_revert.wrappers import _common as common
from jsc_revert.wrappers import data_create as wrapper

RECORD_ID = "a00000000000001AAA"
OTHER_ID = "a00000000000002AAA"
PRIVATE_VALUE = "PRIVATE_ROW_OR_STACK_VALUE"


@pytest.fixture
def run_create(tmp_path, monkeypatch):
    client = tmp_path / "client"
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.setattr(org_detect, "resolve_org", lambda *a, **k: org_detect.OrgInfo(
        alias="synthetic-dev", org_id_18="00D000000000001AAA", org_id_short="00D000000000001",
        is_sandbox=True, instance_url="https://example.invalid", login_url="", detected_org_type="developer"))
    monkeypatch.setattr(org_sequence, "acquire_lock", lambda *a, **k: {"owner_token": "synthetic-token", "org_sequence": 1})
    releases = []
    monkeypatch.setattr(org_sequence, "release", lambda *a, **k: releases.append(True))
    monkeypatch.setattr(manifest, "get_sf_cli_version", lambda: "synthetic-offline")
    def run(create_payload, *, create_exit=0, after_payload=None, after_exit=0):
        create_raw = create_payload if isinstance(create_payload, str) else json.dumps(create_payload)
        after_raw = json.dumps(after_payload)
        calls = []
        def execute(cmd, **kwargs):
            calls.append(cmd)
            if cmd[:4] == ["sf", "data", "create", "record"]:
                return create_exit, create_raw, PRIVATE_VALUE
            assert cmd[:4] == ["sf", "data", "get", "record"]
            return after_exit, after_raw, PRIVATE_VALUE
        monkeypatch.setattr(common, "run_sf_subprocess", execute)
        args = build_parser().parse_args(["data", "create", "--target-org", "synthetic-dev",
            "--sobject", "Synthetic__c", "--values", f"Name={PRIVATE_VALUE}"])
        code = wrapper.run(args)
        paths = list(client.rglob("manifest.json"))
        assert len(paths) == 1
        path = paths[0]
        saved = json.loads(path.read_text())
        assert (path.parent / "underlying-result.json").read_text() == create_raw
        assert len([cmd for cmd in calls if cmd[:4] == ["sf", "data", "create", "record"]]) == 1
        assert releases == [True]
        return code, saved, calls, path
    return run


def test_permission_failure_reports_error_and_evidence_without_private_payload(run_create, capsys):
    code, saved, calls, path = run_create({"status": 1, "name": "INVALID_FIELD", "message": PRIVATE_VALUE, "stack": PRIVATE_VALUE}, create_exit=1)
    assert code == common.EXIT_UNDERLYING_FAILED
    assert saved["snapshot_status"] == "failed"
    assert saved["phases"]["underlying_command"]["exit_code"] == 1
    assert len(calls) == 1
    output = capsys.readouterr().err
    assert "INVALID_FIELD" in output and "sf_exit=1" in output and "status=1" in output
    assert str(path) in output
    assert PRIVATE_VALUE not in output


@pytest.mark.parametrize("payload", [
    {"status": 0, "result": {"success": True}},
    {"status": 0, "result": {"success": True, "id": "invalid-id"}},
    {"status": 0, "result": []},
    "not-json",
])
def test_success_without_usable_id_is_partial_not_retried(run_create, capsys, payload):
    code, saved, calls, path = run_create(payload)
    assert code == common.EXIT_POST_FINALIZE_FAILED
    assert saved["snapshot_status"] == "partial"
    assert saved["payload"]["create_command_succeeded"] is True
    assert saved["payload"]["record_id"] is None
    assert saved["revert_capabilities"]["automatic_revertible"] is False
    assert saved["phases"]["underlying_command"]["status"] == "complete"
    assert saved["phases"]["underlying_command"]["exit_code"] == 0
    assert saved["phases"]["post_finalize"]["status"] == "partial"
    assert len(calls) == 1
    output = capsys.readouterr().err
    assert "command succeeded" in output and "Do not retry creation" in output and str(path) in output
    assert PRIVATE_VALUE not in output


@pytest.mark.parametrize("after_payload,after_exit", [
    ({"status": 1, "name": "INSUFFICIENT_ACCESS", "message": PRIVATE_VALUE}, 1),
    ({"status": 0, "result": {}}, 0),
    ({"status": 0, "result": {"Id": OTHER_ID}}, 0),
    ({"status": 0, "result": []}, 0),
    (None, 0),
])
def test_success_with_missing_or_mismatched_after_row_remains_successful_write(run_create, capsys, after_payload, after_exit):
    code, saved, calls, path = run_create({"status": 0, "result": {"success": True, "id": RECORD_ID}}, after_payload=after_payload, after_exit=after_exit)
    assert code == common.EXIT_POST_FINALIZE_FAILED
    assert saved["snapshot_status"] == "partial"
    assert saved["payload"]["record_id"] == RECORD_ID
    assert saved["payload"]["after_row"] is None
    assert saved["phases"]["underlying_command"]["status"] == "complete"
    assert saved["phases"]["post_finalize"]["status"] == "partial"
    assert len(calls) == 2
    assert (path.parent / "post-create-result.json").exists()
    output = capsys.readouterr().err
    assert "after-row capture" in output and "Do not retry creation" in output
    assert str(path) in output and PRIVATE_VALUE not in output


def test_complete_capture_reports_created_id_and_evidence_path(run_create, capsys):
    row = {"Id": RECORD_ID, "Name": PRIVATE_VALUE}
    code, saved, calls, path = run_create({"status": 0, "result": {"success": True, "id": RECORD_ID}}, after_payload={"status": 0, "result": row})
    assert code == common.EXIT_SUCCESS
    assert saved["snapshot_status"] == "complete"
    assert saved["payload"]["after_row"] == row
    assert saved["phases"]["post_finalize"]["status"] == "complete"
    output = capsys.readouterr().err
    assert RECORD_ID in output and str(path) in output
    assert PRIVATE_VALUE not in output


def test_explicit_result_failure_overrides_process_exit_zero(run_create, capsys):
    code, saved, calls, path = run_create({"status": 0, "result": {"success": False, "errors": [{"statusCode": "INVALID_FIELD", "message": PRIVATE_VALUE}]}})
    assert code == common.EXIT_UNDERLYING_FAILED
    assert saved["snapshot_status"] == "failed"
    assert saved["phases"]["underlying_command"]["exit_code"] == 0
    assert len(calls) == 1
    assert "INVALID_FIELD" in capsys.readouterr().err


def test_unstructured_failure_has_bounded_fallback_diagnostic(run_create, capsys):
    code, saved, calls, path = run_create(PRIVATE_VALUE, create_exit=1)
    assert code == common.EXIT_UNDERLYING_FAILED
    output = capsys.readouterr().err
    assert "SF_CLI_ERROR" in output and "sf_exit=1" in output and str(path) in output
    assert PRIVATE_VALUE not in output


@pytest.mark.parametrize("record_id", [None, "", "invalid", 123, [], "a00000000000001AAA\n"])
def test_loaded_create_snapshot_without_valid_id_cannot_inherit_stale_recovery_claim(record_id):
    from jsc_revert.revert_capabilities import effective_capabilities
    result = effective_capabilities({"operation_type": "data_record_create",
        "payload": {"record_id": record_id}, "revert_capabilities": {"automatic_revertible": True}})
    assert result["automatic_revertible"] is False
    assert "No valid created record ID" in result["manual_recovery_path"]
    assert "do not repeat creation" in result["manual_recovery_path"]


@pytest.mark.parametrize("record_id", [RECORD_ID, RECORD_ID[:15]])
def test_valid_create_id_retains_existing_best_effort_recovery(record_id):
    from jsc_revert.revert_capabilities import effective_capabilities
    result = effective_capabilities({"operation_type": "data_record_create", "payload": {"record_id": record_id}})
    assert result["automatic_revertible"] == "best-effort"
    assert any("REFERENCE_FOUND" in item for item in result["side_effects_warning"])
