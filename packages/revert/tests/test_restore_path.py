"""Regression guards for the four defects that made revert restore impossible.

Proven end-to-end against the throwaway org sf-deploy-test on 2026-07-29: a
CustomLabel was mutated ORIGINAL-VALUE-A -> MUTATED-VALUE-F through
`jsc deploy --metadata`, reverted, and an INDEPENDENT retrieve confirmed the org
back at ORIGINAL-VALUE-A. Before these fixes the same sequence failed at four
separate points, each of which reported success or a soft warning:

  1. pre-snapshot retrieve passed --output-dir into ~/.justiceserverclaude/,
     which sf rejects (OutputDirOutsideProjectError) for ANY org and ANY
     selector — so no deploy_metadata snapshot had ever captured before-state.
  2. after staging the retrieve, sf's ABSOLUTE filePath values still pointed at
     the deleted staging dir, so correctly-captured files classified as
     `retrieve_failed`.
  3. the planner emitted a bare "jsc", which is not on PATH (and is a shell
     ALIAS to the session launcher in this workspace) -> FileNotFoundError
     AFTER "Executing revert:" had been printed.
  4. the revert deploy ran outside any SFDX project ->
     InvalidProjectWorkspaceError, leaving the org unchanged.

These are unit-level guards; they need no org. The live proof is the commit.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jsc_revert import snapshot_pre
from jsc_revert.revert_planner import _jsc_bin
from jsc_revert.wrappers import _common as c


# ── defect 1: the retrieve must be staged inside a project ────────────────
def test_retrieve_stages_a_project_and_uses_a_relative_output_dir(tmp_path, monkeypatch):
    """The sf retrieve must run with cwd inside a project it created."""
    seen = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=None, cwd=None):
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        # Inspect the staged project HERE — run_pre_snapshot_retrieve removes it
        # in a finally block, so it is gone by the time the test body resumes.
        stage = Path(cwd)
        seen["project_json"] = json.loads((stage / "sfdx-project.json").read_text(encoding="utf-8"))
        seen["pkg_dir_exists"] = (
            stage / seen["project_json"]["packageDirectories"][0]["path"]).is_dir()
        # sf writes into <cwd>/retrieved; emulate that so the move has input.
        out = Path(cwd) / "retrieved" / "labels"
        out.mkdir(parents=True, exist_ok=True)
        (out / "CustomLabels.labels-meta.xml").write_text("<x/>", encoding="utf-8")
        payload = {"status": 0, "result": {"status": "Succeeded", "files": [
            {"type": "CustomLabels", "fullName": "CustomLabels", "state": "Changed",
             "filePath": str(out / "CustomLabels.labels-meta.xml")}]}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "snapstore" / "metadata-before"
    snapshot_pre.run_pre_snapshot_retrieve(["CustomLabel:X"], "sf-deploy-test", dest)

    # RELATIVE output dir — an absolute one outside the project is what sf
    # refuses, and on macOS /var vs /private/var makes even the staging path
    # compare unequal.
    i = seen["cmd"].index("--output-dir")
    assert seen["cmd"][i + 1] == "retrieved", "output-dir must be relative to the staged project"
    assert not Path(seen["cmd"][i + 1]).is_absolute()

    # cwd must be a real project: sfdx-project.json AND the declared package dir
    assert seen["cwd"], "retrieve must run with an explicit cwd"
    assert seen["project_json"]["packageDirectories"], "staged project declares no package dir"
    assert seen["pkg_dir_exists"], (
        "declared packageDirectories path must EXIST, not just be named — sf "
        "raises MissingPackageDirectoryError otherwise")
    assert not Path(seen["cwd"]).exists(), "the staging dir must be cleaned up"


def test_retrieve_never_points_sf_at_the_snapshot_store(tmp_path, monkeypatch):
    """The regression itself: --output-dir must never be the snapshot store."""
    seen = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=None, cwd=None):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 1, json.dumps({"name": "X"}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "snapstore" / "metadata-before"
    with pytest.raises(Exception):
        snapshot_pre.run_pre_snapshot_retrieve(["CustomLabel:X"], "org", dest)
    assert str(dest) not in seen["cmd"], (
        "passing the snapshot store as --output-dir is the bug: sf raises "
        "OutputDirOutsideProjectError and capture silently produces nothing")


# ── defect 2: staged paths must be re-pointed at the snapshot store ───────
def test_absolute_staging_paths_are_repointed_so_files_classify_present(tmp_path, monkeypatch):
    def fake_run(cmd, capture_output=True, text=True, timeout=None, cwd=None):
        out = Path(cwd) / "retrieved" / "labels"
        out.mkdir(parents=True, exist_ok=True)
        (out / "CustomLabels.labels-meta.xml").write_text("<value>ORIGINAL</value>", encoding="utf-8")
        payload = {"status": 0, "result": {"status": "Succeeded", "files": [
            {"type": "CustomLabels", "fullName": "CustomLabels", "state": "Changed",
             "filePath": str(out / "CustomLabels.labels-meta.xml")}]}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "snapstore" / "metadata-before"
    res = snapshot_pre.run_pre_snapshot_retrieve(["CustomLabel:X"], "org", dest)

    assert len(res.files) == 1
    fc = res.files[0]
    assert fc.state == "present", (
        f"file was captured but classified {fc.state!r} — sf's absolute filePath "
        "still pointed into the deleted staging dir")
    assert fc.checksum, "a present file must carry a checksum"
    assert (dest / "labels" / "CustomLabels.labels-meta.xml").is_file()


# ── defect 3: the planner must emit a resolvable jsc ──────────────────────
def test_jsc_bin_is_absolute_when_the_console_script_exists():
    got = _jsc_bin()
    beside_interpreter = Path(sys.executable).parent / "jsc"
    if beside_interpreter.is_file():
        assert got == str(beside_interpreter)
        assert Path(got).is_absolute()
    else:                                   # module fallback uses this interpreter
        from jsc_revert.revert_planner import _jsc_command
        assert _jsc_command() == [sys.executable, "-m", "jsc_revert.cli"]


# ── defect 4: a deploy outside a project must be staged ──────────────────
def test_in_sfdx_project_detects_both_ways(tmp_path):
    assert not c.in_sfdx_project(str(tmp_path))
    (tmp_path / "sfdx-project.json").write_text("{}", encoding="utf-8")
    assert c.in_sfdx_project(str(tmp_path))
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert c.in_sfdx_project(str(nested)), "must walk up to find the project"


# ── net-new guidance must name the real reason ───────────────────────────
def _caps(files, selectors):
    from jsc_revert import revert_capabilities as rc
    return rc.compute_revertibility(
        "deploy_metadata", {"files": files, "selectors": selectors})


def test_net_new_guidance_does_not_blame_the_selector():
    """A --metadata deploy of components that did not exist must say SO.

    The single canned string this replaced produced, verbatim: "The pre-snapshot
    retrieve runs only for --metadata selectors; this deploy used --metadata."
    Self-contradictory, and shown at exactly the moment an operator is reading
    for help.
    """
    caps = _caps([{"type": "ApexClass", "fullName": "ZZTEST_NetNew",
                   "before_state": "absent"}],
                 {"metadata_args": ["ApexClass:ZZTEST_NetNew"]})
    msg = caps["manual_recovery_path"]
    assert caps["automatic_revertible"] is False
    assert "ABSENT" in msg and "ZZTEST_NetNew" in msg, "must name what was absent"
    assert "destructiveChanges" in msg, "must say why a create is not undone"
    assert "runs only for --metadata selectors; this deploy used --metadata" not in msg


def test_metadata_selector_with_failed_capture_points_at_the_retrieve():
    """--metadata used, nothing captured -> blame the retrieve, not the selector."""
    msg = _caps([], {"metadata_args": ["ApexClass:X"]})["manual_recovery_path"]
    assert "DID use --metadata" in msg
    assert "pre_snapshot" in msg


def test_non_metadata_selector_still_gets_the_selector_advice():
    """--source-dir genuinely IS the reason there; keep that guidance intact."""
    msg = _caps([], {"source_dirs": ["force-app"]})["manual_recovery_path"]
    assert "--source-dir" in msg
    assert "express it" in msg and "--metadata" in msg


def test_stage_source_project_copies_content_and_drops_forensics(tmp_path):
    src = tmp_path / "metadata-before"
    (src / "labels").mkdir(parents=True)
    (src / "labels" / "CustomLabels.labels-meta.xml").write_text("<value>ORIGINAL</value>", encoding="utf-8")
    (src / ".retrieve-result.json").write_text("{}", encoding="utf-8")

    stage, selectors = c.stage_source_project([str(src)])
    stage_p = Path(stage)
    try:
        assert selectors == ["force-app"], "deploy must use a project-relative selector"
        assert (stage_p / "sfdx-project.json").is_file()
        landed = stage_p / "force-app" / "labels" / "CustomLabels.labels-meta.xml"
        assert landed.is_file(), "retrieved metadata must reach the staged package dir"
        assert "ORIGINAL" in landed.read_text(encoding="utf-8")
        assert not (stage_p / "force-app" / ".retrieve-result.json").exists(), (
            "the raw retrieve result is forensics, not deployable source")
    finally:
        import shutil
        shutil.rmtree(stage, ignore_errors=True)
