"""The demo remains useful without auth or executable dependencies, and never overwrites."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET

import pytest
from torque import demo
from torque import workspace as ws


@pytest.fixture
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("offline demo attempted a process or network call")
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setenv("PATH", "")


def test_demo_is_a_private_resumable_workflow_without_external_commands(tmp_path, monkeypatch, offline):
    monkeypatch.setenv("TORQUE_WORKSPACE", str(tmp_path / "unrelated-private-client"))
    before_environment = dict(os.environ)
    result = demo.create_demo(tmp_path / "new-demo")
    root = Path(result["workspace"])
    client = Path(result["client_root"])
    assert result["synthetic"] is True and result["org_calls"] is False
    assert dict(os.environ) == before_environment
    assert not (tmp_path / "unrelated-private-client").exists()
    _, firm, config = ws.load_client(root, result["client"])
    assert firm["profile"] == "generic" and config["org"] is None
    assert root.stat().st_mode & 0o777 == 0o700
    assert client.stat().st_mode & 0o777 == 0o700
    for path in root.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o777 == 0o600
    assert (root / ".gitignore").read_text().splitlines()[-1] == "*"
    context = ws.get_context(root, result["client"])
    assert "AC1" in json.dumps(context) and "fictional" in json.dumps(context)
    sessions = ws.list_sessions(root, result["client"], limit=None)
    assert len(sessions) == 2
    assert {entry["status"] for entry in sessions} == {"prepared", "incomplete"}
    assert all(entry["independently_verified"] is False for entry in sessions)
    assert Path(result["handoff"]).read_text() == ws.render_handoff(root, result["client"])
    assert "NOT_RUN" in Path(result["handoff"]).read_text()
    from torque.changes import get_change
    change = get_change(root, result["client"], result["change_id"])
    assert change["assessment"]["reported_pass"] == 0
    assert change["assessment"]["not_yet_reported_pass"] == ["AC1", "AC2", "AC3"]
    assert "torque context --workspace . --client synthetic-community-center" in Path(result["start_here"]).read_text()


def test_starter_and_evidence_connect_to_requirements_without_live_claims(tmp_path, offline):
    result = demo.create_demo(tmp_path / "demo")
    project = Path(result["project"])
    evidence = json.loads(Path(result["evidence"]).read_text())
    project_config = json.loads((project / "sfdx-project.json").read_text())
    assert project_config["packageDirectories"] == [{"path": "force-app", "default": True}]
    assert "target-org" not in project_config
    assert len(list(project.rglob("*.xml"))) == 4
    for path in project.rglob("*.xml"):
        ET.parse(path)
    assert evidence["scope"] == "local files only"
    assert evidence["deployment"] == {"status": "NOT_RUN", "job_id": None}
    assert {item["criterion"] for item in evidence["acceptance"]} == {"AC1", "AC2", "AC3"}
    assert all(item["status"] == "NOT_RUN" for item in evidence["acceptance"])
    assert all(item["status"] == "OBSERVED_LOCAL" for item in evidence["observations"])
    assert evidence["observations"][0]["xml_file_count"] == 4
    assert evidence["observations"][1]["sample_count"] == 2
    for reference in evidence["files"]:
        assert hashlib.sha256((Path(result["client_root"]) / reference["path"]).read_bytes()).hexdigest() == reference["sha256"]
    prepared = next(entry for entry in ws.list_sessions(result["workspace"], result["client"]) if entry["status"] == "prepared")
    assert prepared["evidence"]["sha256"] == hashlib.sha256(Path(result["evidence"]).read_bytes()).hexdigest()


@pytest.mark.parametrize("kind", ["file", "empty-directory", "nonempty-directory", "symlink", "dangling-symlink"])
def test_existing_destination_is_untouched(tmp_path, kind, offline):
    destination = tmp_path / "existing"
    other = tmp_path / "other"
    other.mkdir()
    marker = other / "keep.txt"
    marker.write_text("existing private content")
    if kind == "file":
        destination.write_text("keep this file")
    elif kind == "empty-directory":
        destination.mkdir()
    elif kind == "nonempty-directory":
        destination.mkdir()
        (destination / "keep.txt").write_text("keep this directory")
    else:
        destination.symlink_to(other if kind == "symlink" else tmp_path / "missing", target_is_directory=True)
    before = sorted((str(path.relative_to(tmp_path)), path.is_symlink(), path.read_bytes() if path.is_file() and not path.is_symlink() else None) for path in tmp_path.rglob("*"))
    with pytest.raises(ws.WorkspaceError, match="already exists"):
        demo.create_demo(destination)
    after = sorted((str(path.relative_to(tmp_path)), path.is_symlink(), path.read_bytes() if path.is_file() and not path.is_symlink() else None) for path in tmp_path.rglob("*"))
    assert before == after
    assert marker.read_text() == "existing private content"


def test_init_failure_removes_only_the_new_destination(tmp_path, monkeypatch, offline):
    marker = tmp_path / "keep.txt"
    marker.write_text("untouched")
    def fail(root, *args):
        (root / "partial.txt").write_text("interrupted generation")
        raise ws.WorkspaceError("synthetic initialization failure")
    monkeypatch.setattr(ws, "init_workspace", fail)
    with pytest.raises(ws.WorkspaceError, match="synthetic initialization failure"):
        demo.create_demo(tmp_path / "new-demo")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]
    assert marker.read_text() == "untouched"


def test_late_generation_failure_leaves_no_partial_workspace(tmp_path, monkeypatch, offline):
    def fail(*args):
        raise ws.WorkspaceError("synthetic evidence failure")
    monkeypatch.setattr(demo, "_evidence", fail)
    with pytest.raises(ws.WorkspaceError, match="synthetic evidence failure"):
        demo.create_demo(tmp_path / "new-demo")
    assert not list(tmp_path.iterdir())


def test_refuses_source_checkout_or_missing_parent_without_creating_dirs(tmp_path, monkeypatch, offline):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(ws, "_source_checkout", lambda: source)
    with pytest.raises(ws.WorkspaceError, match="outside"):
        demo.create_demo(source / "new-demo")
    assert not list(source.iterdir())
    with pytest.raises(ws.WorkspaceError, match="parent"):
        demo.create_demo(tmp_path / "missing-parent" / "new-demo")
    assert not (tmp_path / "missing-parent").exists()


def test_two_creators_do_not_merge_or_remove_each_others_workspace(tmp_path, offline):
    target = tmp_path / "demo"
    def create():
        try:
            return demo.create_demo(target)
        except (ws.WorkspaceError, FileExistsError):
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert (target / "START-HERE.md").is_file()
    assert len(ws.list_sessions(target, demo.CLIENT, limit=None)) == 2
