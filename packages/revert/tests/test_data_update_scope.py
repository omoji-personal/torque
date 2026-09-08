"""Offline restoration regressions with synthetic Salesforce records only."""
from argparse import Namespace
import json
from pathlib import Path

import pytest

from jsc_revert import revert_capabilities as rc, revert_planner as rp
from jsc_revert.update_fields import parse_update_fields


def snapshot(*, fields=("Name",), legacy=False):
    payload = {
        "object_api_name": "Account", "record_id": "001000000000001AAA",
        "before_row": {
            "Id": "001000000000001AAA", "attributes": {"type": "Account"},
            "Name": "Original Name", "IsDeleted": False, "CreatedDate": "2026-01-01",
            "BillingAddress": {"city": "Synthetic"}, "Formula__c": 23,
            "Phone": "unrelated original", "Nullable__c": None,
        },
        "after_row": {"Name": "New Name", "Phone": "automation changed it", "Formula__c": 24},
    }
    if not legacy:
        payload["fields_updated"] = list(fields) if fields is not None else None
    return {"operation_type": "data_record_update", "snapshot_id": "synthetic",
            "org": {"alias": "explicit-target"}, "payload": payload,
            "wrapper_command": "jsc data update -o explicit-target --sobject Account --record-id 001000000000001AAA --values 'Name=New'"}


@pytest.mark.parametrize("values, expected", [
    ("Name='A name' Description=\"two = pairs Phone=embedded\"", ["Name", "Description"]),
    ("Name=O'Brien Phone=123", ["Name", "Phone"]),
    ("Name=first Name=last", ["Name"]),
    ("Name=first nAmE=last", ["Name"]),
    ("Description='line one\nline two'", ["Description"]),
    ("Description=\"{'key': 'Name=fake'}\" Phone=123", ["Description", "Phone"]),
    ("Name=bad unpaired", None),
    ("Name='bad' Other='unclosed", None),
])
def test_update_field_parser_respects_value_boundaries(values, expected):
    assert parse_update_fields(values) == expected


def test_full_salesforce_capture_restores_only_explicitly_updated_fields():
    command = rp.build_revert_command(snapshot(), None)
    assert command[command.index("--values") + 1] == "Name='Original Name'"
    assert command[command.index("-o") + 1] == "explicit-target"
    assert rc.effective_capabilities(snapshot())["automatic_revertible"] is True


def test_exact_legacy_wrapper_log_recovers_scope_without_using_row_differences():
    manifest = snapshot(legacy=True)
    command = rp.build_revert_command(manifest, None)
    assert command[command.index("--values") + 1] == "Name='Original Name'"
    assert rc.effective_capabilities(manifest)["automatic_revertible"] is True


@pytest.mark.parametrize("fields", [None, [], ["Missing__c"], ["Name", "Missing__c"], ["BillingAddress"], ["Id"]])
def test_uncaptured_or_ambiguous_update_scope_has_no_automatic_plan(fields):
    manifest = snapshot(fields=fields)
    assert rp.build_revert_command(manifest, None) is None
    assert rc.effective_capabilities(manifest)["automatic_revertible"] is False


def test_legacy_missing_command_never_falls_back_to_entire_before_row():
    manifest = snapshot(legacy=True)
    manifest.pop("wrapper_command")
    assert rp.build_revert_command(manifest, None) is None
    assert rc.effective_capabilities(manifest)["automatic_revertible"] is False


def test_field_names_match_capture_case_insensitively():
    command = rp.build_revert_command(snapshot(fields=["name"]), None)
    assert command[command.index("--values") + 1] == "Name='Original Name'"


def test_only_requested_null_values_affect_capability_and_partial_restore_is_explicit():
    assert rc.effective_capabilities(snapshot())["automatic_revertible"] is True
    partial = snapshot(fields=["Name", "Nullable__c"])
    assert rc.effective_capabilities(partial)["automatic_revertible"] == "best-effort"
    assert rc.effective_capabilities(snapshot(fields=["Nullable__c"]))["automatic_revertible"] is False


def test_wrapper_persists_requested_fields_but_preserves_full_forensic_capture(tmp_path, monkeypatch):
    from jsc_revert.wrappers import data_update as update
    before = snapshot()["payload"]["before_row"]
    after = {**before, "Name": "New Name", "Phone": "automation changed it"}
    values = "Name='New Name'"
    seen = []
    class Context:
        def __init__(self, **kwargs):
            self.org = Namespace(is_sandbox=True)
            self.snap_dir = tmp_path
            self.manifest = {"operation_type": kwargs["operation_type"], "snapshot_id": "synthetic",
                             "wrapper_command": kwargs["wrapper_command"], "org": {"alias": kwargs["target_org"]}}
        def resolve_org(self): return 0
        def acquire_org_lock(self): return 0
        def init_snapshot_dir(self): pass
        def set_revert_capabilities(self):
            self.manifest["revert_capabilities"] = rc.compute_revertibility(self.manifest["operation_type"], self.manifest["payload"])
        def update_phase(self, *args, **kwargs): pass
        def save(self): (tmp_path / "manifest.json").write_text(json.dumps(self.manifest))
        def release_lock(self): seen.append("released")
    reads = iter([before, after])
    monkeypatch.setattr(update.c, "WrapperContext", Context)
    monkeypatch.setattr(update, "_query_record", lambda *args: next(reads))
    def fake_sf(command):
        assert command[command.index("--values") + 1] == values
        seen.append(command)
        return 0, '{"result":{"success":true}}', ''
    monkeypatch.setattr(update.c, "run_sf_subprocess", fake_sf)
    args = Namespace(target_org="explicit-target", sobject="Account", record_id="001000000000001AAA", values=values)
    assert update.run(args) == 0
    persisted = json.loads((tmp_path / "manifest.json").read_text())
    assert persisted["payload"]["fields_updated"] == ["Name"]
    assert persisted["payload"]["before_row"] == before
    assert persisted["payload"]["after_row"] == after
    command = rp.build_revert_command(persisted, tmp_path)
    assert command[command.index("--values") + 1] == "Name='Original Name'"
    assert len(seen) == 2 and seen[-1] == "released"


def test_permission_assignment_without_ids_never_claims_exact_recovery():
    capability = rc.compute_revertibility("org_assign_permset", {"created_psa_ids": [], "psa_id_capture_status": "not_returned"})
    assert capability["automatic_revertible"] is False
    assert "did not capture" in capability["manual_recovery_path"]
