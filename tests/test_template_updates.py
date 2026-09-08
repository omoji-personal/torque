"""Synthetic package/workspace upgrades; no processes, auth or network."""
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from torque import template_updates as updates
from torque import workspace as ws

ALPHA = ".claude/commands/alpha.md"
SKILL = ".claude/skills/review/SKILL.md"
SECOND_SKILL = ".agents/skills/review/SKILL.md"


def put(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def snapshot(root):
    return {str(p.relative_to(root)): (p.is_dir(), p.is_symlink(), p.stat().st_mtime_ns,
            p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in [root, *root.rglob("*")]}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    package = tmp_path / "package"
    put(package, "data/commands/alpha.md", "alpha v1\n")
    put(package, "data/skills/review/SKILL.md", "review v1\n")
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    (root / "clients").mkdir()
    put(root, "workspace.json", json.dumps({"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic"}))
    monkeypatch.setattr(updates.resources, "files", lambda _: package)
    monkeypatch.setattr(updates, "__version__", "test-v1")
    return root, package


def baseline(environment):
    root, package = environment
    ws._materialize_workflows(root)
    updates.record_initial_templates(root)
    return root, package


def manifest(root):
    return json.loads((root / updates.MANIFEST).read_text())


def test_initial_record_only_adopts_matching_materialized_defaults(environment):
    root, package = environment
    ws._materialize_workflows(root)
    put(root, ALPHA, "local customized command")
    before = (root / ALPHA).read_bytes()
    report = updates.record_initial_templates(root)
    assert (root / ALPHA).read_bytes() == before
    assert ALPHA not in manifest(root)["files"]
    assert set(manifest(root)["files"]) == {SKILL, SECOND_SKILL}
    assert report["conflicts"][0]["reason"] == "unmanaged_local"
    assert (root / updates.MANIFEST).stat().st_mode & 0o777 == 0o600


def test_update_defaults_add_assets_and_keep_retired_files(environment, monkeypatch):
    root, package = baseline(environment)
    put(package, "data/commands/alpha.md", "alpha v2\n")
    put(package, "data/commands/new.md", "new default\n")
    (package / "data/skills/review/SKILL.md").unlink()
    monkeypatch.setattr(updates, "__version__", "test-v2")
    result = updates.update_templates(root)
    assert result["counts"] == {"update": 1, "add": 1, "retired": 2}
    assert (root / ALPHA).read_text() == "alpha v2\n"
    assert (root / ".claude/commands/new.md").read_text() == "new default\n"
    assert (root / SKILL).read_text() == "review v1\n"
    assert (root / SECOND_SKILL).read_text() == "review v1\n"
    entry = manifest(root)["files"][ALPHA]
    assert entry == {"sha256": hashlib.sha256(b"alpha v2\n").hexdigest(), "version": "test-v2"}
    assert manifest(root)["files"][SKILL]["version"] == "test-v1"
    assert (root / ALPHA).stat().st_mode & 0o777 == 0o600


def test_local_edit_keeps_baseline_and_reports_merge_suggestion(environment):
    root, package = baseline(environment)
    old = manifest(root)["files"][ALPHA]
    put(root, ALPHA, "private local change")
    put(package, "data/commands/alpha.md", "new packaged change")
    result = updates.update_templates(root)
    assert (root / ALPHA).read_text() == "private local change"
    assert manifest(root)["files"][ALPHA] == old
    conflict = next(row for row in result["conflicts"] if row["path"] == ALPHA)
    assert conflict["reason"] == "modified_local" and conflict["suggestion"]
    assert conflict["packaged_source"] == "torque/data/commands/alpha.md"


def test_legacy_adopts_matches_adds_missing_and_never_claims_different_local(environment):
    root, package = environment
    put(root, ALPHA, "unknown legacy customization")
    put(root, SKILL, "review v1\n")
    extra = put(root, ".claude/commands/local-only.md", "private local workflow")
    result = updates.update_templates(root)
    assert result["counts"] == {"add": 1, "preserve": 1, "adopt": 1}
    assert ALPHA not in manifest(root)["files"]
    assert set(manifest(root)["files"]) == {SKILL, SECOND_SKILL}
    assert extra.read_text() == "private local workflow"
    assert (root / ALPHA).read_text() == "unknown legacy customization"


@pytest.mark.parametrize("managed", [False, True])
def test_check_is_readonly_even_without_metadata_directory(environment, managed):
    root, package = baseline(environment) if managed else environment
    put(package, "data/commands/alpha.md", "new default")
    before = snapshot(root)
    result = updates.update_templates(root, check=True)
    assert snapshot(root) == before
    assert result["check"] is True and result["manifest_updated"] is False
    assert all(row["applied"] is False for row in result["actions"])
    if not managed:
        assert not (root / ".torque").exists()


def test_interrupted_manifest_commit_reconciles_on_retry(environment, monkeypatch):
    root, package = baseline(environment)
    old_manifest = (root / updates.MANIFEST).read_bytes()
    put(package, "data/commands/alpha.md", "new default")
    original = updates._publish
    def interrupt(fd, path, contents, expected):
        if path == updates.MANIFEST:
            raise OSError("synthetic interruption before manifest commit")
        return original(fd, path, contents, expected)
    monkeypatch.setattr(updates, "_publish", interrupt)
    with pytest.raises(OSError, match="interruption"):
        updates.update_templates(root)
    assert (root / ALPHA).read_text() == "new default"
    assert (root / updates.MANIFEST).read_bytes() == old_manifest
    monkeypatch.setattr(updates, "_publish", original)
    result = updates.update_templates(root)
    assert result["counts"]["adopt"] == 1
    assert manifest(root)["files"][ALPHA]["sha256"] == hashlib.sha256(b"new default").hexdigest()


def test_atomic_publish_failure_leaves_old_file_manifest_and_no_temp(environment, monkeypatch):
    root, package = baseline(environment)
    old = (root / updates.MANIFEST).read_bytes()
    put(package, "data/commands/alpha.md", "new default")
    def interrupt(*args, **kwargs):
        raise OSError("synthetic replace interruption")
    monkeypatch.setattr(updates.os, "replace", interrupt)
    with pytest.raises(OSError, match="interruption"):
        updates.update_templates(root)
    assert (root / ALPHA).read_text() == "alpha v1\n"
    assert (root / updates.MANIFEST).read_bytes() == old
    assert not list(root.rglob(".torque-update-*"))


@pytest.mark.parametrize("relative,directory", [
    (".claude", True), (ALPHA, False), (".torque", True),
    (updates.MANIFEST, False), (".torque/templates.lock", False),
])
@pytest.mark.parametrize("check", [False, True])
def test_symlink_paths_never_touch_outside_workspace(environment, tmp_path, relative, directory, check):
    root, package = environment
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = put(outside, "marker.md", "outside content")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside if directory else marker, target_is_directory=directory)
    before = snapshot(outside)
    with pytest.raises(ws.WorkspaceError, match="symlink"):
        updates.update_templates(root, check=check)
    assert snapshot(outside) == before


def test_new_local_file_created_during_update_is_not_overwritten_or_tracked(environment, monkeypatch):
    root, package = environment
    original = updates._publish
    def race(fd, path, contents, expected):
        if path == ALPHA:
            put(root, ALPHA, "new local file written concurrently")
        return original(fd, path, contents, expected)
    monkeypatch.setattr(updates, "_publish", race)
    result = updates.update_templates(root)
    assert (root / ALPHA).read_text() == "new local file written concurrently"
    assert ALPHA not in manifest(root)["files"]
    assert any(item["reason"] == "changed_during_update" for item in result["conflicts"])


def test_edit_between_plan_and_write_is_preserved(environment, monkeypatch):
    root, package = baseline(environment)
    old = manifest(root)["files"][ALPHA]
    put(package, "data/commands/alpha.md", "new packaged default")
    original = updates._publish
    def race(fd, path, contents, expected):
        if path == ALPHA:
            put(root, ALPHA, "local edit while update was planning")
        return original(fd, path, contents, expected)
    monkeypatch.setattr(updates, "_publish", race)
    updates.update_templates(root)
    assert (root / ALPHA).read_text() == "local edit while update was planning"
    assert manifest(root)["files"][ALPHA] == old


def test_new_file_appearing_at_atomic_publication_is_preserved(environment, monkeypatch):
    root, package = environment
    original = updates.os.link
    def race(source, destination, **kwargs):
        if destination == "alpha.md":
            put(root, ALPHA, "local file appeared after comparison")
        return original(source, destination, **kwargs)
    monkeypatch.setattr(updates.os, "link", race)
    report = updates.update_templates(root)
    assert (root / ALPHA).read_text() == "local file appeared after comparison"
    assert ALPHA not in manifest(root)["files"]
    assert any(row["path"] == ALPHA and row["reason"] == "changed_during_update"
               for row in report["conflicts"])


def test_simultaneous_updates_serialize_and_produce_one_complete_baseline(environment):
    root, package = environment
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: updates.update_templates(root), range(2), timeout=10))
    assert sum(result["counts"].get("add", 0) for result in results) == 3
    assert len(manifest(root)["files"]) == 3
    for name, entry in manifest(root)["files"].items():
        assert _digest(root / name) == entry["sha256"]
    assert not list(root.rglob(".torque-update-*"))


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_invalid_manifest_cannot_extend_managed_scope(environment):
    root, package = environment
    put(root, updates.MANIFEST, json.dumps({"schema": "torque.templates/1", "files": {
        "../../private.md": {"sha256": "0" * 64, "version": "old"}}}))
    with pytest.raises(ws.WorkspaceError, match="invalid"):
        updates.update_templates(root)
    assert not (root / ALPHA).exists()
