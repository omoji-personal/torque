"""Offline assignment response regressions using real snapshot persistence.

The installed plugin-user 3.6.38 assign() returns {successes:[{name,value}],
where value is a permission-set NAME. It supplies no PSA IDs. Other versions
or adapters can supply result lists, id fields or nested value.id fields.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jsc_revert import manifest, org_detect, org_sequence
from jsc_revert.cli import build_parser
from jsc_revert.wrappers import _common as common
from jsc_revert.wrappers import org_assign_permset as wrapper

PSA = "0Pa000000000001AAA"
OTHER_PSA = "0Pa000000000002AAA"


@pytest.mark.parametrize("result,exit_code,expected_ids,capture,status,expected_exit", [
    ({"successes": [{"name": "synthetic-user", "value": "Synthetic_Access"}], "failures": []}, 0, [], "not_returned", "complete", 0),
    ([{"name": "synthetic-user", "value": "Synthetic_Access"}], 0, [], "not_returned", "complete", 0),
    ({"successes": [{"value": {"id": PSA}}]}, 0, [PSA], "captured", "complete", 0),
    ([{"id": PSA}, {"id": PSA}, {"value": {"id": OTHER_PSA}}], 0, [PSA, OTHER_PSA], "captured", "complete", 0),
    ({"successes": [{"id": PSA}, {"value": "Synthetic_Access"}]}, 0, [PSA], "partial", "complete", 0),
    ({"successes": [{"id": "005000000000001AAA"}, {"value": None}, "unexpected-row"]}, 0, [], "not_returned", "complete", 0),
    ({"successes": [{"id": PSA}], "failures": [{"message": "synthetic denial"}]}, 68, [PSA], "captured", "applied_partial", 21),
    ({"successes": [], "failures": [{"message": "synthetic denial"}]}, 1, [], "not_returned", "failed", 20),
    ([{"id": PSA, "success": False, "errors": ["synthetic denial"]}], 1, [], "not_returned", "failed", 20),
    (None, 0, [], "unrecognized_response", "complete", 0),
    ("unexpected-result", 1, [], "unrecognized_response", "failed", 20),
])
def test_assignment_response_saved_without_shape_crash(tmp_path, monkeypatch, capsys,
        result, exit_code, expected_ids, capture, status, expected_exit):
    client = tmp_path / "client"
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.setattr(org_detect, "resolve_org", lambda *a, **k: org_detect.OrgInfo(
        alias="synthetic-dev", org_id_18="00D000000000001AAA", org_id_short="00D000000000001",
        is_sandbox=True, instance_url="https://example.invalid", login_url="", detected_org_type="developer"))
    released = []
    monkeypatch.setattr(org_sequence, "acquire_lock", lambda *a, **k: {"owner_token": "synthetic-token", "org_sequence": 1})
    monkeypatch.setattr(org_sequence, "release", lambda *a, **k: released.append(True))
    monkeypatch.setattr(manifest, "get_sf_cli_version", lambda: "synthetic-offline")
    raw = json.dumps({"status": exit_code, "result": result})
    commands = []
    def execute(command, **kwargs):
        commands.append(command)
        return exit_code, raw, ""
    monkeypatch.setattr(common, "run_sf_subprocess", execute)
    args = build_parser().parse_args(["org", "assign", "permset", "--target-org", "synthetic-dev",
        "--name", "Synthetic_Access", "--on-behalf-of", "synthetic-user"])
    assert wrapper.run(args) == expected_exit
    paths = list(client.rglob("manifest.json"))
    assert len(paths) == 1
    saved = json.loads(paths[0].read_text())
    assert saved["payload"]["created_psa_ids"] == expected_ids
    assert saved["payload"]["psa_id_capture_status"] == capture
    assert saved["snapshot_status"] == status
    assert saved["phases"]["underlying_command"]["exit_code"] == exit_code
    assert saved["phases"]["post_finalize"]["status"] == ("complete" if capture == "captured" else "partial")
    assert saved["revert_capabilities"]["automatic_revertible"] is False
    assert (paths[0].parent / "underlying-result.json").read_text() == raw
    assert len(commands) == 1 and commands[0][-2:] == ["--on-behalf-of", "synthetic-user"]
    assert released == [True]
    assert ("capture is incomplete" in capsys.readouterr().err) == (capture != "captured")


@pytest.mark.parametrize("stdout", ["not-json", "null", "[]", '{"result":{"successes":{}}}', '{"result":{"successes":[],"failures":null}}'])
def test_unrecognized_output_never_invents_recovery_ids(stdout):
    result = wrapper._assignment_result(stdout)
    assert result["created_psa_ids"] == []
    assert result["psa_id_capture_status"] == "unrecognized_response"
    assert result["assignment_success_count"] is None
