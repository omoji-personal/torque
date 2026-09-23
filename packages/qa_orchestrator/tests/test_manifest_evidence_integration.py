"""Offline regressions for browser manifests and complete exact-deploy evidence."""
from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from jsc_qa import dispatcher
from jsc_browser_tests import cli as browser_cli, suite, vision
from jsc_browser_tests.diagnostics import artifact_child
from jsc_browser_tests.flow_spec import FlowSpec
from jsc_browser_tests.runner import FlowResult, StepResult, Variation


TARGET = "synthetic-org"
JOB = "0AfVs000001ojZFKAY"


@pytest.fixture(autouse=True)
def offline_scope(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("External process/network I/O is forbidden in these tests")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path / "clients" / "selected"))
    monkeypatch.delenv("JSC_ROOT", raising=False)
    monkeypatch.setattr(dispatcher, "_is_production_alias", lambda target: False)
    monkeypatch.setattr(vision, "gemini_available", lambda: True)
    monkeypatch.setattr(vision, "analyze_screenshot", forbidden)


def run_artifacts(*, run="new-run", target=TARGET, scope=None, legacy=False,
                  cells=True, screenshot=True, profile="case_worker", flow="urgent_service"):
    base = (scope or Path(os.environ["TORQUE_WORKSPACE"])) / "state" / "qa-tests"
    target_dir = base / target if legacy else artifact_child(base, target)
    run_dir = artifact_child(target_dir, run)
    nested = artifact_child(run_dir, "inner-run")
    nested.mkdir(parents=True)
    shot = nested / "unhelpful_filename.png"
    if screenshot:
        shot.write_bytes(b"synthetic PNG content; analyzer is mocked")
    step = {"step_name": "after urgent update", "screenshot_path": str(shot)} if screenshot else {}
    payload = {"target_org": target, "runid": run, "cells": [
        {"flow": flow, "profile": profile, "status": "PASS", "steps": [step]}
    ] if cells else []}
    manifest = run_dir / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest, shot, payload


def analyze_stub(monkeypatch, *, status="OK", findings=None):
    calls = []
    def analyze(path, **context):
        calls.append((Path(path), context))
        return vision.VisionAnalysisResult(str(path), status=status, findings=findings or [], model="offline-fake")
    monkeypatch.setattr(vision, "analyze_screenshot", analyze)
    return calls


def test_nested_encoded_manifest_uses_recorded_identity_and_only_listed_screenshots(monkeypatch):
    manifest, shot, _ = run_artifacts()
    (shot.parent / "unlisted.png").write_bytes(b"not evidence")
    calls = analyze_stub(monkeypatch)
    result = dispatcher.dispatch_vision(TARGET, "synthetic change")
    assert result.status == "PASS"
    assert calls == [(shot.resolve(), {"flow_name": "urgent_service", "profile": "case_worker",
                                      "step_name": "after urgent update"})]
    assert result.metadata["manifest_path"] == str(manifest.resolve())
    assert result.metadata["screenshot_count"] == 1
    assert len(result.metadata["manifest_sha256"]) == 64
    assert "does not establish browser execution" in result.detail


def test_no_fallback_to_sibling_client(tmp_path, monkeypatch):
    run_artifacts(scope=tmp_path / "clients" / "other")
    calls = analyze_stub(monkeypatch)
    result = dispatcher.dispatch_vision(TARGET, "synthetic change")
    assert result.status == "MANUAL_REQUIRED"
    assert not calls


def test_new_empty_run_does_not_fall_back_to_old_screenshots(monkeypatch):
    old, _, _ = run_artifacts(run="old")
    latest, _, _ = run_artifacts(run="latest", cells=False, screenshot=False)
    os.utime(old, (10, 10))
    os.utime(latest, (20, 20))
    calls = analyze_stub(monkeypatch)
    result = dispatcher.dispatch_vision(TARGET, "synthetic change")
    assert result.status == "MANUAL_REQUIRED"
    assert result.metadata["manifest_path"] == str(latest)
    assert result.metadata["screenshot_count"] == 0
    assert not calls


@pytest.mark.parametrize("corruption", ["json", "target", "missing_target", "cell_target", "cells", "profile", "missing_file"])
def test_new_invalid_run_does_not_fall_back_or_invoke_provider(corruption, monkeypatch):
    old, _, _ = run_artifacts(run="old")
    manifest, shot, payload = run_artifacts(run="latest")
    if corruption == "target":
        payload["target_org"] = "other-org"
    elif corruption == "missing_target":
        del payload["target_org"]
    elif corruption == "cell_target":
        payload["cells"][0]["target_org"] = "other-org"
    elif corruption == "cells":
        payload["cells"] = "not cells"
    elif corruption == "profile":
        del payload["cells"][0]["profile"]
    elif corruption == "missing_file":
        shot.unlink()
    manifest.write_text("invalid JSON" if corruption == "json" else json.dumps(payload), encoding="utf-8")
    os.utime(old, (10, 10))
    os.utime(manifest, (20, 20))
    calls = analyze_stub(monkeypatch)
    assert dispatcher.dispatch_vision(TARGET, "synthetic change").status == "ERROR"
    assert not calls


@pytest.mark.parametrize("outside", ["sibling-client", "sibling-run", "symlink-file", "symlink-run", "symlink-target"])
def test_artifact_scope_escape_is_rejected(tmp_path, monkeypatch, outside):
    manifest, shot, payload = run_artifacts()
    other_manifest, other_shot, _ = run_artifacts(run="other-run")
    if outside == "sibling-client":
        _, other_shot, _ = run_artifacts(scope=tmp_path / "clients" / "other")
        payload["cells"][0]["steps"][0]["screenshot_path"] = str(other_shot)
    elif outside == "sibling-run":
        payload["cells"][0]["steps"][0]["screenshot_path"] = str(other_shot)
    elif outside == "symlink-file":
        shot.unlink()
        shot.symlink_to(other_shot)
    elif outside == "symlink-run":
        (manifest.parent / "linked-run").symlink_to(other_manifest.parent, target_is_directory=True)
    else:
        base = Path(os.environ["TORQUE_WORKSPACE"]) / "state" / "qa-tests"
        (base / TARGET).symlink_to(other_manifest.parent, target_is_directory=True)
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(other_manifest, (10, 10))
    os.utime(manifest, (20, 20))
    calls = analyze_stub(monkeypatch)
    assert dispatcher.dispatch_vision(TARGET, "synthetic change").status == "ERROR"
    assert not calls


def test_safe_legacy_manifest_with_explicit_target_and_context(monkeypatch):
    manifest, shot, payload = run_artifacts(legacy=True)
    cell = payload.pop("cells")[0]
    cell["steps"] = [{"name": "legacy step", "screenshot": str(shot.relative_to(manifest.parent))}]
    payload["results"] = [cell]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    calls = analyze_stub(monkeypatch)
    assert dispatcher.dispatch_vision(TARGET, "legacy synthetic change").status == "PASS"
    assert calls[0][0] == shot.resolve()
    assert calls[0][1]["profile"] == "case_worker"
    assert calls[0][1]["step_name"] == "legacy step"


def test_missing_analyzer_includes_actual_inventory(monkeypatch):
    _, shot, _ = run_artifacts()
    monkeypatch.setattr(vision, "gemini_available", lambda: False)
    result = dispatcher.dispatch_vision(TARGET, "synthetic change")
    assert result.status == "MANUAL_REQUIRED"
    assert result.metadata["screenshots"][0]["path"] == str(shot)


@pytest.mark.parametrize("analyzer_status,expected", [
    ("GEMINI_UNAVAILABLE", "MANUAL_REQUIRED"), ("unknown", "MANUAL_REQUIRED"),
    ("ERROR", "ERROR"), ("WARN", "PASS"),
])
def test_analyzer_status_cannot_turn_missing_execution_into_pass(monkeypatch, analyzer_status, expected):
    run_artifacts()
    analyze_stub(monkeypatch, status=analyzer_status)
    assert dispatcher.dispatch_vision(TARGET, "synthetic change").status == expected


def test_p1_visual_finding_fails(monkeypatch):
    run_artifacts()
    finding = vision.VisionFinding("P1", "error_toast", "synthetic error", "Visible error")
    analyze_stub(monkeypatch, findings=[finding])
    assert dispatcher.dispatch_vision(TARGET, "synthetic change").status == "FAIL"


@pytest.mark.parametrize("route", ["suite-run", "browser"])
def test_actual_browser_cli_manifest_is_consumed_without_layout_guessing(monkeypatch, route):
    from jsc_browser_tests.provisioning import fixtures, object_registry
    flow = SimpleNamespace(name="synthetic_readonly", spec=FlowSpec(
        name="synthetic_readonly", workflow="E1", writes=False, profiles=["admin"],
        variations=[Variation("happy")]))
    monkeypatch.setattr(fixtures, "new_runid", lambda: "stable-run")
    monkeypatch.setattr(object_registry, "load_registry", lambda *args: {})
    monkeypatch.setattr(browser_cli, "_load_seed", lambda target: {})

    async def preflight(config):
        return {}

    async def cell_executor(cell, config):
        shot = Path(config["run_dir"]) / "actual.png"
        shot.write_bytes(b"synthetic PNG")
        return FlowResult(flow_name=flow.spec.name, profile=cell.profile, target_org=TARGET,
                          overall_status="PASS", steps=[StepResult("actual capture", "PASS", "USER_FIDELITY",
                                                                    screenshot_path=str(shot))])

    async def offline_suite(config):
        return await suite.run_suite({**config, "flows": [flow], "preflight": preflight,
                                      "cell_executor": cell_executor})
    monkeypatch.setattr(browser_cli, "run_suite", offline_suite)
    if route == "suite-run":
        code = browser_cli.cmd_suite_run(SimpleNamespace(target_org=TARGET, profiles="admin", run_dir=None,
                                                        manifest=None, report=None, headed=False,
                                                        allow_production_writes=False))
    else:
        code = browser_cli._run_flow_with_profiles(flow, TARGET, ["admin"])
    assert code == 0
    calls = analyze_stub(monkeypatch)
    result = dispatcher.dispatch_vision(TARGET, "synthetic change")
    assert result.status == "PASS"
    assert result.metadata["runid"] == "stable-run"
    assert calls[0][1] == {"flow_name": "synthetic_readonly", "profile": "admin", "step_name": "actual capture"}


@pytest.mark.parametrize("status", ["Succeeded", "Failed"])
def test_exact_job_retains_complete_json_and_expected_scope(monkeypatch, status, capsys):
    components = [{"componentType": "CustomField", "fullName": f"Account.F{index}__c", "success": True}
                  for index in range(40)]
    payload = {"status": 0, "result": {"id": JOB, "status": status, "done": True,
               "success": status == "Succeeded", "checkOnly": False,
               "details": {"componentSuccesses": components}}}
    raw = json.dumps(payload)
    assert len(raw) > 1500
    transport = json.dumps({"status": 0, "result": {"statusCode": 200,
                           "headers": {"set-cookie": "synthetic-secret"},
                           "body": {"deployResult": payload["result"]}}})
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout=transport, stderr=""))
    result = dispatcher.dispatch_meta_api(TARGET, "synthetic change", deploy_job_id=JOB,
                                         deploy_components=["CustomField:Account.F39__c"])
    assert result.status == ("PASS" if status == "Succeeded" else "FAIL")
    assert result.raw_output == raw
    assert json.loads(result.raw_output) == payload
    assert result.metadata["expected_components"] == ["CustomField:Account.F39__c"]
    assert capsys.readouterr().out == ""


def test_failed_component_scope_is_explicit(monkeypatch):
    payload = {"status": 0, "result": {"id": JOB, "status": "Succeeded", "done": True,
               "success": True, "checkOnly": False, "details": {"componentSuccesses": [
                   {"componentType": "Flow", "fullName": "Unrelated", "success": True}]}}}
    transport = {"status": 0, "result": {"statusCode": 200,
                 "headers": {}, "body": {"deployResult": payload["result"]}}}
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout=json.dumps(transport), stderr=""))
    result = dispatcher.dispatch_meta_api(TARGET, "synthetic change", deploy_job_id=JOB,
                                         deploy_components=["Flow:Expected"])
    assert result.status == "FAIL"
    assert result.metadata["expected_components"] == ["Flow:Expected"]
    assert result.metadata["missing_components"] == ["Flow:Expected"]
    assert result.metadata["reported_components"] == ["Flow:Unrelated"]
