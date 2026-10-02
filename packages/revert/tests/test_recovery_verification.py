import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from jsc_revert import (bundle, manifest as mf, metadata_scope, org_detect, org_sequence,
                        revert_executor, snapshot_pre, stale_detector)
from jsc_revert.wrappers import _common as common, deploy
from jsc_revert.cli import build_parser


def org():
    short = "00D000000000001"
    return org_detect.OrgInfo("synthetic", org_detect._pad_to_18(short), short, True,
                              "https://example.org", "https://example.org", "sandbox")


def captured(root, content, *, companion=None):
    root.mkdir(parents=True, exist_ok=True)
    kind, name, relative = ("CustomField", "Account.Note__c", "objects/Account/fields/Note__c.field-meta.xml")
    if companion is not None:
        kind, name, relative = "ApexClass", "Example", "classes/Example.cls"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<CustomField><label>{content}</label></CustomField>" if companion is None else content, encoding="utf-8")
    if companion is not None:
        Path(str(path) + "-meta.xml").write_text(companion, encoding="utf-8")
    metadata_scope.write_capture_inventory(root)
    return snapshot_pre.RetrieveResult("Succeeded", [snapshot_pre.FileClassification(
        kind, name, "present", hashlib.sha256(path.read_bytes()).hexdigest(), str(path), None)], root / "raw.json")


def manifest(tmp_path):
    before = captured(tmp_path / "metadata-before", "before").files[0]
    return {"snapshot_id": "synthetic-snapshot", "operation_type": "deploy_metadata", "org": org()._asdict(),
            "payload": {"selectors": {"metadata_args": ["CustomField:Account.Note__c"]}, "files": [
                {"type": before.type, "fullName": before.fullName, "before_state": "present",
                 "before_checksum": before.checksum, "filePath": before.file_path}]}}


def backend(monkeypatch, content="deployed"):
    monkeypatch.setattr(snapshot_pre, "run_pre_snapshot_retrieve",
                        lambda selectors, target, output, **kw: captured(output, content))


def test_successful_deployment_result_is_the_drift_baseline(tmp_path, monkeypatch):
    snap = manifest(tmp_path)
    backend(monkeypatch)
    stale_detector.capture_deployment_state(tmp_path, snap, "synthetic")
    assert stale_detector.classify_metadata_drift(tmp_path, snap, "synthetic")[0].state == stale_detector.DriftState.PRESENT_SAME
    backend(monkeypatch, "subsequent edit")
    drift = stale_detector.classify_metadata_drift(tmp_path, snap, "synthetic")
    assert drift[0].state == stale_detector.DriftState.PRESENT_CHANGED
    assert stale_detector.is_blocking(drift)


def test_companion_file_edits_are_detected(tmp_path, monkeypatch):
    snap = manifest(tmp_path)
    before = captured(tmp_path / "metadata-before", "class body", companion="before metadata").files[0]
    snap["payload"] = {"selectors": {"metadata_args": ["ApexClass:Example"]}, "files": [
        {"type": before.type, "fullName": before.fullName, "before_state": "present",
         "before_checksum": before.checksum, "filePath": before.file_path}]}
    def retrieve(selectors, target, output, **kwargs):
        return captured(output, "class body", companion=state[0])
    state = ["deployed metadata"]
    monkeypatch.setattr(snapshot_pre, "run_pre_snapshot_retrieve", retrieve)
    stale_detector.capture_deployment_state(tmp_path, snap, "synthetic")
    state[0] = "later companion edit"
    assert stale_detector.classify_metadata_drift(tmp_path, snap, "synthetic")[0].state == stale_detector.DriftState.PRESENT_CHANGED


@pytest.mark.parametrize("unknown", ["legacy", "exception", "empty", "failed", "no_checksum"])
@pytest.mark.parametrize("force", [False, True])
def test_unknown_state_blocks_recovery_even_with_force(tmp_path, monkeypatch, unknown, force):
    snap = manifest(tmp_path)
    backend(monkeypatch)
    if unknown != "legacy":
        stale_detector.capture_deployment_state(tmp_path, snap, "synthetic")
        def retrieve(*args, **kwargs):
            if unknown == "exception":
                raise ValueError("retrieve unavailable")
            files = [] if unknown == "empty" else [snapshot_pre.FileClassification(
                "CustomField", "Account.Note__c", "retrieve_failed" if unknown == "failed" else "present", None, None, None)]
            return snapshot_pre.RetrieveResult("Succeeded", files, tmp_path / "raw.json")
        monkeypatch.setattr(snapshot_pre, "run_pre_snapshot_retrieve", retrieve)
    monkeypatch.setattr(org_detect, "resolve_org", lambda target: org())
    monkeypatch.setattr(mf, "load_by_id", lambda *a: (tmp_path, snap))
    monkeypatch.setattr(common, "connected_approval", lambda *a, **k: (0, None))
    monkeypatch.setattr(revert_executor.subprocess, "run", lambda *a, **k: pytest.fail("unverified recovery executed"))
    assert revert_executor.execute_revert(snap["snapshot_id"], "synthetic", force_ack=force) == 50
    assert not org_sequence.org_lock_path(org().org_id_short).exists()


@pytest.mark.parametrize("force,expected", [(False, 50), (True, 0)])
def test_verified_drift_requires_override_and_lease_covers_child(tmp_path, monkeypatch, force, expected):
    snap = manifest(tmp_path)
    backend(monkeypatch)
    stale_detector.capture_deployment_state(tmp_path, snap, "synthetic")
    def retrieve(selectors, target, output, **kw):
        with pytest.raises(org_sequence.LockConflictError):
            org_sequence.acquire_lock(org().org_id_short, "second-alias", "other", "deploy_metadata")
        return captured(output, "later edit")
    monkeypatch.setattr(snapshot_pre, "run_pre_snapshot_retrieve", retrieve)
    monkeypatch.setattr(org_detect, "resolve_org", lambda target: org())
    monkeypatch.setattr(mf, "load_by_id", lambda *a: (tmp_path, snap))
    monkeypatch.setattr(common, "connected_approval", lambda *a, **k: (0, None))
    original = subprocess.run
    calls = []
    def child(command, **kwargs):
        calls.append(command)
        with pytest.raises(org_sequence.LockConflictError):
            org_sequence.acquire_lock(org().org_id_short, "second-alias", "other", "deploy_metadata")
        script = '''
import json, os
from jsc_revert.wrappers import _common as c
from jsc_revert import org_detect, org_sequence
p=json.loads(os.environ[c.REVERT_LEASE_ENV])
x=c.WrapperContext("revert", "synthetic", "synthetic", parent_snapshot_id=p["snapshot_id"])
x.org=org_detect.OrgInfo("synthetic", org_detect._pad_to_18(p["org_id"]),p["org_id"],True,"https://example.org","https://example.org","sandbox")
assert x.acquire_org_lock()==0
x.ensure_ownership()
x.release_lock()
assert org_sequence.org_lock_path(p["org_id"]).exists()
'''
        return original([sys.executable, "-c", script], **kwargs)
    monkeypatch.setattr(revert_executor.subprocess, "run", child)
    assert revert_executor.execute_revert(snap["snapshot_id"], "synthetic", force_ack=force) == expected
    assert len(calls) == int(force)
    assert not org_sequence.org_lock_path(org().org_id_short).exists()


@pytest.mark.parametrize("capture_fails", [False, True])
def test_deploy_captures_result_under_lease_or_reports_partial(tmp_path, monkeypatch, capture_fails):
    monkeypatch.setenv("JSC_REVERT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    monkeypatch.setattr(org_detect, "resolve_org", lambda target: org())
    monkeypatch.setattr(common, "connected_approval", lambda *a, **k: (0, None))
    monkeypatch.setattr(mf, "get_sf_cli_version", lambda: "synthetic")
    calls = []
    def retrieve(selectors, target, output, **kwargs):
        calls.append(output)
        with pytest.raises(org_sequence.LockConflictError):
            org_sequence.acquire_lock(org().org_id_short, "other-alias", "other", "deploy_metadata")
        if len(calls) == 2 and capture_fails:
            raise ValueError("post-capture unavailable")
        return captured(output, "before" if len(calls) == 1 else "deployed")
    monkeypatch.setattr(snapshot_pre, "run_pre_snapshot_retrieve", retrieve)
    monkeypatch.setattr(common, "run_sf_subprocess", lambda *a, **k: (0, json.dumps({"status": 0, "result": {
        "id": "0Af000000000001AAA", "status": "Succeeded", "done": True, "success": True}}), ""))
    args = build_parser().parse_args(["deploy", "-o", "synthetic", "--metadata", "CustomField:Account.Note__c"])
    assert deploy.run(args) == (30 if capture_fails else 0)
    saved = json.loads(next((tmp_path / "snapshots").rglob("manifest.json")).read_text(encoding="utf-8"))
    assert saved["phases"]["underlying_command"]["status"] == "complete"
    assert saved["snapshot_status"] == ("partial" if capture_fails else "complete")
    assert bool(saved["payload"].get("after_state")) is not capture_fails
