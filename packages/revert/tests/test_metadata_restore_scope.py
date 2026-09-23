"""Offline scope regression: an adjacent capture must never become a deploy input."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from jsc_revert import metadata_scope as scope, revert_planner as planner
from jsc_revert import revert_capabilities, stale_detector, snapshot_pre
from jsc_revert.wrappers import _common, deploy


def capture(root, relative, kind, name, content=None):
    path = root / "metadata-before" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or f'<{kind} xmlns="http://soap.sforce.com/2006/04/metadata"><label>Before</label></{kind}>', encoding="utf-8")
    return {"type": kind, "fullName": name, "before_state": "present",
            "filePath": str(path), "before_checksum": hashlib.sha256(path.read_bytes()).hexdigest()}


def snapshot(records, selectors=None):
    return {"operation_type": "deploy_metadata", "snapshot_id": "scope-test", "org": {"alias": "synthetic"},
            "payload": {"selectors": {"metadata_args": selectors or ["CustomField:Request__c.Note__c"]}, "files": records}}


def field_fixture(root):
    field = capture(root, "objects/Request__c/fields/Note__c.field-meta.xml", "CustomField", "Request__c.Note__c")
    parent = capture(root, "objects/Request__c/Request__c.object-meta.xml", "CustomObject", "Request__c")
    sibling = capture(root, "objects/Request__c/fields/Status__c.field-meta.xml", "CustomField", "Request__c.Status__c")
    return snapshot([field, parent, sibling])


def selectors(command):
    return [command[i+1] for i, arg in enumerate(command[:-1]) if arg == "--source-dir"]


def test_exact_field_plan_excludes_parent_and_sibling_and_stages_only_field(tmp_path):
    manifest = field_fixture(tmp_path)
    command = planner.build_revert_command(manifest, tmp_path)
    selected = selectors(command)
    assert selected == [manifest["payload"]["files"][0]["filePath"]]
    before = {p: p.read_bytes() for p in (tmp_path / "metadata-before").rglob("*") if p.is_file()}
    stage, args = _common.stage_source_project(selected)
    try:
        assert args == ["force-app"]
        assert sorted(str(p.relative_to(Path(stage)/"force-app")) for p in (Path(stage)/"force-app").rglob("*") if p.is_file()) == ["objects/Request__c/fields/Note__c.field-meta.xml"]
        assert {p: p.read_bytes() for p in before} == before
    finally:
        shutil.rmtree(stage)


def test_explicit_object_scope_preserves_captured_children(tmp_path):
    manifest = field_fixture(tmp_path)
    manifest["payload"]["files"].append(capture(tmp_path, "customMetadata/Request__c.Unrelated.md-meta.xml", "CustomMetadata", "Request__c.Unrelated"))
    manifest["payload"]["selectors"]["metadata_args"] = ["CustomObject:Request__c"]
    assert len(selectors(planner.build_revert_command(manifest, tmp_path))) == 3


@pytest.mark.parametrize("selector,expected", [("CustomField:Request__c.*", 2), ("CustomField", 2), ("CustomField:Request__c.Note*", 1)])
def test_original_wildcard_scopes_are_retained(tmp_path, selector, expected):
    manifest = field_fixture(tmp_path)
    manifest["payload"]["selectors"]["metadata_args"] = [selector]
    assert len(scope.recovery_files(manifest["payload"], tmp_path)) == expected


def test_apex_body_and_meta_companion_survive_but_neighbor_does_not(tmp_path):
    record = capture(tmp_path, "classes/Example.cls", "ApexClass", "Example", "public class Example {}")
    companion = Path(record["filePath"] + "-meta.xml")
    companion.write_text('<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata"><apiVersion>67.0</apiVersion></ApexClass>', encoding="utf-8")
    capture(tmp_path, "classes/Neighbor.cls", "ApexClass", "Neighbor", "public class Neighbor {}")
    scope.write_capture_inventory(tmp_path / "metadata-before")
    files = scope.recovery_files(snapshot([record], ["ApexClass:Example"])["payload"], tmp_path)
    assert set(files) == {Path(record["filePath"]), companion}


def test_selected_lwc_bundle_preserves_html_js_css_without_neighbor(tmp_path):
    record = capture(tmp_path, "lwc/example/example.js-meta.xml", "LightningComponentBundle", "example")
    for name in ["example.html", "example.js", "example.css"]:
        Path(record["filePath"]).with_name(name).write_text("synthetic", encoding="utf-8")
    capture(tmp_path, "lwc/neighbor/neighbor.js-meta.xml", "LightningComponentBundle", "neighbor")
    scope.write_capture_inventory(tmp_path / "metadata-before")
    files = scope.recovery_files(snapshot([record], ["LightningComponentBundle:example"])["payload"], tmp_path)
    assert {p.name for p in files} == {"example.js-meta.xml", "example.html", "example.js", "example.css"}


def test_expanded_static_resource_keeps_only_selected_resource_contents(tmp_path):
    record = capture(tmp_path, "staticresources/example.resource-meta.xml", "StaticResource", "example")
    folder = Path(record["filePath"]).parent / "example" / "nested"
    folder.mkdir(parents=True)
    (folder / "style.css").write_text("body{}", encoding="utf-8")
    capture(tmp_path, "staticresources/neighbor.resource-meta.xml", "StaticResource", "neighbor")
    scope.write_capture_inventory(tmp_path / "metadata-before")
    files = scope.recovery_files(snapshot([record], ["StaticResource:example"])["payload"], tmp_path)
    assert set(files) == {Path(record["filePath"]), folder / "style.css"}


@pytest.mark.parametrize("corruption", ["missing", "changed", "outside", "symlink", "missing-selector", "extra-selector", "absent", "mixed-selector"])
def test_incomplete_or_unsafe_capture_never_becomes_broad_restore(tmp_path, corruption):
    manifest = field_fixture(tmp_path)
    record = manifest["payload"]["files"][0]
    path = Path(record["filePath"])
    if corruption == "missing": path.unlink()
    elif corruption == "changed": path.write_text("changed", encoding="utf-8")
    elif corruption == "outside":
        outside = tmp_path / "outside.field-meta.xml"
        outside.write_bytes(path.read_bytes())
        record["filePath"] = str(outside)
    elif corruption == "symlink":
        body = path.read_bytes(); path.unlink()
        outside = tmp_path / "outside.field-meta.xml"; outside.write_bytes(body); path.symlink_to(outside)
    elif corruption == "missing-selector": manifest["payload"]["selectors"] = {}
    elif corruption == "extra-selector": manifest["payload"]["selectors"]["metadata_args"].append("CustomField:Request__c.Missing__c")
    elif corruption == "absent": record["before_state"] = "absent"
    elif corruption == "mixed-selector": manifest["payload"]["selectors"]["source_dirs"] = ["force-app"]
    assert planner.build_revert_command(manifest, tmp_path) is None


def test_child_label_does_not_restore_whole_aggregate(tmp_path):
    record = capture(tmp_path, "labels/CustomLabels.labels-meta.xml", "CustomLabel", "Example", "<CustomLabels><labels><fullName>Example</fullName></labels><labels><fullName>Neighbor</fullName></labels></CustomLabels>")
    assert planner.build_revert_command(snapshot([record], ["CustomLabel:Example"]), tmp_path) is None


def test_capability_requires_complete_original_scope(tmp_path):
    payload = field_fixture(tmp_path)["payload"]
    payload["selectors"]["metadata_args"].append("CustomField:Request__c.Unknown__c")
    assert revert_capabilities.compute_revertibility("deploy_metadata", payload)["automatic_revertible"] is False


def test_selected_name_cannot_point_to_valid_checksummed_sibling_file(tmp_path):
    manifest = field_fixture(tmp_path)
    selected, _, sibling = manifest["payload"]["files"]
    selected["filePath"], selected["before_checksum"] = sibling["filePath"], sibling["before_checksum"]
    assert planner.build_revert_command(manifest, tmp_path) is None


@pytest.mark.parametrize("state", ["absent", "unknown", "retrieve_failed"])
def test_whole_object_with_known_uncaptured_child_cannot_silently_restore_partially(tmp_path, state):
    manifest = field_fixture(tmp_path)
    manifest["payload"]["selectors"]["metadata_args"] = ["CustomObject:Request__c"]
    manifest["payload"]["files"][0]["before_state"] = state
    assert planner.build_revert_command(manifest, tmp_path) is None
    assert revert_capabilities.compute_revertibility("deploy_metadata", manifest["payload"])["automatic_revertible"] is False


@pytest.mark.parametrize("missing", ["body", "meta"])
def test_apex_required_companion_cannot_be_omitted(tmp_path, missing):
    body = capture(tmp_path, "classes/Example.cls", "ApexClass", "Example", "public class Example {}")
    meta = capture(tmp_path, "classes/Example.cls-meta.xml", "ApexClass", "Example")
    scope.write_capture_inventory(tmp_path / "metadata-before")
    Path((body if missing == "body" else meta)["filePath"]).unlink()
    assert planner.build_revert_command(snapshot([meta if missing == "body" else body], ["ApexClass:Example"]), tmp_path) is None


def test_legacy_apex_with_both_indexed_files_remains_supported(tmp_path):
    body = capture(tmp_path, "classes/Example.cls", "ApexClass", "Example", "public class Example {}")
    meta = capture(tmp_path, "classes/Example.cls-meta.xml", "ApexClass", "Example")
    command = planner.build_revert_command(snapshot([body, meta], ["ApexClass:Example"]), tmp_path)
    assert set(selectors(command)) == {body["filePath"], meta["filePath"]}


def test_legacy_unindexed_companion_is_not_assumed_to_be_original(tmp_path):
    body = capture(tmp_path, "classes/Example.cls", "ApexClass", "Example", "public class Example {}")
    capture(tmp_path, "classes/Example.cls-meta.xml", "ApexClass", "Example")
    assert planner.build_revert_command(snapshot([body], ["ApexClass:Example"]), tmp_path) is None


@pytest.mark.parametrize("change", ["add", "remove", "modify"])
def test_bundle_cannot_import_unrecorded_or_changed_companion(tmp_path, change):
    record = capture(tmp_path, "lwc/example/example.js-meta.xml", "LightningComponentBundle", "example")
    js = Path(record["filePath"]).with_name("example.js")
    js.write_text("original", encoding="utf-8")
    scope.write_capture_inventory(tmp_path / "metadata-before")
    if change == "add": js.with_name("uncaptured.js").write_text("new", encoding="utf-8")
    elif change == "remove": js.unlink()
    else: js.write_text("modified", encoding="utf-8")
    assert planner.build_revert_command(snapshot([record], ["LightningComponentBundle:example"]), tmp_path) is None


@pytest.mark.parametrize("link_parent", [False, True])
def test_snapshot_or_parent_symlink_cannot_cross_capture_boundary(tmp_path, link_parent):
    real = tmp_path / "real" / "snapshot"
    manifest = field_fixture(real)
    alias = tmp_path / "alias"
    alias.symlink_to(real.parent if link_parent else real, target_is_directory=True)
    selected_dir = alias / "snapshot" if link_parent else alias
    assert planner.build_revert_command(manifest, selected_dir) is None


def test_traversal_source_path_is_not_returned_for_staging(tmp_path):
    manifest = field_fixture(tmp_path)
    record = manifest["payload"]["files"][0]
    record["filePath"] = str(tmp_path / "metadata-before" / ".." / "metadata-before" / "objects/Request__c/fields/Note__c.field-meta.xml")
    assert planner.build_revert_command(manifest, tmp_path) is None


def test_retrieve_records_inventory_for_companion_not_listed_by_cli(tmp_path, monkeypatch):
    import subprocess
    def retrieve(command, **kwargs):
        base = Path(kwargs["cwd"]) / "retrieved" / "classes"
        base.mkdir(parents=True)
        body = base / "Example.cls"; body.write_text("public class Example {}", encoding="utf-8")
        (base / "Example.cls-meta.xml").write_text("<ApexClass/>", encoding="utf-8")
        result = {"status": 0, "result": {"status": "Succeeded", "files": [{"type": "ApexClass", "fullName": "Example", "state": "Changed", "filePath": str(body)}]}}
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")
    monkeypatch.setattr(snapshot_pre.subprocess, "run", retrieve)
    captured = snapshot_pre.run_pre_snapshot_retrieve(["ApexClass:Example"], "synthetic", tmp_path / "metadata-before")
    inventory = json.loads((tmp_path / "metadata-before" / scope.INVENTORY_FILE).read_text(encoding="utf-8"))
    assert set(inventory["files"]) == {"classes/Example.cls", "classes/Example.cls-meta.xml"}
    record = captured.files[0]
    payload = snapshot([{"type": record.type, "fullName": record.fullName, "before_state": record.state, "filePath": record.file_path, "before_checksum": record.checksum}], ["ApexClass:Example"])
    assert len(selectors(planner.build_revert_command(payload, tmp_path))) == 2


def test_drift_checks_only_requested_field_not_incidental_parent(tmp_path, monkeypatch):
    manifest = field_fixture(tmp_path)
    record = manifest["payload"]["files"][0]
    seen = []
    def retrieve(selectors, target, output, timeout_seconds):
        seen.extend(selectors)
        return snapshot_pre.RetrieveResult("Succeeded", [snapshot_pre.FileClassification("CustomField", "Request__c.Note__c", "present", record["before_checksum"], record["filePath"], None)], output / "result.json")
    monkeypatch.setattr(snapshot_pre, "run_pre_snapshot_retrieve", retrieve)
    result = stale_detector.classify_metadata_drift(tmp_path, manifest, "synthetic")
    assert seen == ["CustomField:Request__c.Note__c"]
    assert len(result) == 1 and result[0].state is stale_detector.DriftState.PRESENT_SAME


@pytest.mark.parametrize("inside_project", [True, False])
def test_wrapper_recovery_uses_only_scoped_source_even_inside_unrelated_project(tmp_path, monkeypatch, inside_project):
    manifest = field_fixture(tmp_path)
    observed = {}
    class Context:
        def __init__(self, **kwargs):
            self.manifest = {}; self.snap_dir = tmp_path / "revert-child"; self.snapshot_id = "child"
        def resolve_org(self): return 0
        def acquire_org_lock(self): return 0
        def release_lock(self): pass
        def init_snapshot_dir(self): self.snap_dir.mkdir()
        def set_revert_capabilities(self): pass
        def save(self): pass
        def update_phase(self, *args, **kwargs): pass
    def invoke(command, timeout_seconds, cwd):
        project = Path(cwd)
        observed["paths"] = sorted(str(p.relative_to(project/"force-app")) for p in (project/"force-app").rglob("*") if p.is_file())
        observed["command"] = command
        assert json.loads((project/"sfdx-project.json").read_text(encoding="utf-8"))["packageDirectories"][0]["path"] == "force-app"
        return 0, json.dumps({"status": 0, "result": {"id": "0Af000000000001AAA", "status": "Succeeded", "done": True, "success": True}}), ""
    monkeypatch.setattr(_common, "WrapperContext", Context)
    monkeypatch.setattr(_common, "in_sfdx_project", lambda: inside_project)
    monkeypatch.setattr(_common, "run_sf_subprocess", invoke)
    import argparse
    args = argparse.Namespace(target_org="synthetic", metadata=[], manifest=[], source_dir=selectors(planner.build_revert_command(manifest, tmp_path)), pre_destructive_changes=None, dry_run=False, invoking_intent="synthetic", operation_type="revert", parent_snapshot_id="scope-test")
    assert deploy.run(args) == 0
    assert observed["paths"] == ["objects/Request__c/fields/Note__c.field-meta.xml"]
    assert observed["command"][observed["command"].index("--source-dir")+1] == "force-app"
