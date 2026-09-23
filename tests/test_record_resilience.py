"""Continuation must survive missing evidence and diagnose malformed local records."""
import contextlib
import io
import json
import os
from pathlib import Path

import pytest

from torque import changes, cli, workspace as ws


@pytest.fixture
def engagement(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    alpha = ws.add_client(root, "Alpha")
    beta = ws.add_client(root, "Beta")
    evidence = alpha / "artifacts/proof.txt"
    evidence.write_text("Original synthetic observation", encoding="utf-8")
    entry = ws.add_session(root, "Alpha", "Reported completion", "verified", evidence)
    return root, alpha, beta, evidence, entry


def invoke(root, *args):
    output, error = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        code = cli.main([*args, "--workspace", str(root), "--client", "Alpha"])
    return code, output.getvalue(), error.getvalue()


@pytest.mark.parametrize("state", ["matches_reference", "changed", "missing", "unavailable"])
def test_session_evidence_is_rechecked_without_rewriting_history(engagement, state):
    root, alpha, _, evidence, entry = engagement
    recorded = (alpha / "sessions" / f"{entry['id']}.json").read_bytes()
    if state == "changed":
        evidence.write_text("A later observation", encoding="utf-8")
    elif state == "missing":
        evidence.unlink()
    elif state == "unavailable":
        evidence.unlink()
        os.mkfifo(evidence)
    for command in (("session", "show", entry["id"]), ("context",), ("handoff",)):
        code, output, error = invoke(root, *command)
        assert code == 0 and error == ""
        if state != "matches_reference" or command == ("handoff",):
            assert state.replace("_", " ") in output
        assert "user-reported" in output
    data = json.loads(invoke(root, "context", "--json")[1])
    assert data["sessions"][0]["evidence_integrity"] == state
    assert data["sessions"][0]["independently_verified"] is False
    assert (alpha / "sessions" / f"{entry['id']}.json").read_bytes() == recorded


@pytest.mark.parametrize("evidence", [[], "bad", {}, {"path": "/tmp/synthetic"},
    {"path": "relative", "sha256": "0" * 64, "basis": "file reference recorded; contents not evaluated"},
    {"path": "/tmp/synthetic", "sha256": "not-a-hash", "basis": "file reference recorded; contents not evaluated"}])
def test_malformed_session_evidence_is_actionable_and_never_creates_a_handoff(engagement, evidence):
    root, alpha, _, _, entry = engagement
    entry["evidence"] = evidence
    path = alpha / "sessions" / f"{entry['id']}.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    destination = alpha / "artifacts/handoff.md"
    code, output, error = invoke(root, "handoff", "--output", str(destination))
    assert code == 2 and output == "" and "invalid session evidence" in error
    assert entry["id"] in error and not destination.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == entry


@pytest.mark.parametrize("field,value", [("created_at", "not-a-date"), ("created_at", "2026-09-22"),
                                        ("summary", " "), ("summary", None)])
def test_malformed_session_fields_do_not_break_the_cli(engagement, field, value):
    root, alpha, _, _, entry = engagement
    entry[field] = value
    (alpha / "sessions" / f"{entry['id']}.json").write_text(json.dumps(entry), encoding="utf-8")
    code, output, error = invoke(root, "context")
    assert code == 2 and output == "" and "invalid session record" in error


def test_retargeted_evidence_never_reads_sibling_client(engagement, monkeypatch):
    root, _, beta, evidence, _ = engagement
    evidence.unlink()
    evidence.symlink_to(beta / "context.md")
    reads = []
    original = ws._file_hash
    monkeypatch.setattr(ws, "_file_hash", lambda path: reads.append(path) or original(path))
    code, output, error = invoke(root, "handoff")
    assert code == 2 and output == "" and "different client" in error
    assert reads == []


def test_unavailable_evidence_does_not_stop_resumption(engagement, monkeypatch):
    root, _, _, _, _ = engagement
    def denied(path):
        raise PermissionError("Synthetic unreadable evidence")
    monkeypatch.setattr(ws, "_file_hash", denied)
    code, output, error = invoke(root, "handoff")
    assert code == 0 and error == "" and "unavailable" in output


def test_doctor_checks_old_sessions_and_changes_and_identifies_installation(engagement):
    root, _, _, evidence, _ = engagement
    evidence.unlink()
    for number in range(21):
        ws.add_session(root, "Alpha", f"Later synthetic session {number}")
    change = changes.create_change(root, "Alpha", "Synthetic change", "Visible outcome", ["Save"])
    proof = root / "proof.txt"
    proof.write_text("Synthetic change observation", encoding="utf-8")
    event = changes.add_check(root, "Alpha", change["id"], "AC1", "pass", "Reported pass", proof)
    change_root, _ = changes.load_change(root, "Alpha", change["id"])
    (change_root / event["evidence"]["path"]).unlink()
    code, output, error = invoke(root, "doctor", "--json")
    assert code == 0 and error == ""
    report = json.loads(output)
    assert report["client"]["sessions_checked"] == 22
    assert report["client"]["recent_sessions"] == 20
    assert report["client"]["changes_checked"] == 1
    assert report["client"]["evidence_problems"] == 2
    assert any("Review 2" in action for action in report["next_actions"])
    assert Path(report["installation"]["package"]) == Path(cli.__file__).resolve().parent
    assert report["org_calls"] is False


@pytest.mark.parametrize("kind", ["git", "worktree", "sdist"])
@pytest.mark.parametrize("command", ["init", "demo"])
def test_external_install_keeps_private_work_out_of_source(engagement, tmp_path, monkeypatch, kind, command):
    source = tmp_path / "public-source"
    (source / "src/torque").mkdir(parents=True)
    (source / "src/torque/__init__.py").touch()
    (source / "src/torque/workspace.py").touch()
    (source / "pyproject.toml").write_text('[project]\nname = "torque-salesforce"\n', encoding="utf-8")
    if kind == "git":
        (source / ".git").mkdir()
    elif kind == "worktree":
        (source / ".git").write_text("gitdir: /synthetic/worktree", encoding="utf-8")
    monkeypatch.setattr(ws, "__file__", str(tmp_path / "external/site-packages/torque/workspace.py"))
    destination = source / "private"
    if command == "init":
        with pytest.raises(ws.WorkspaceError, match="outside.*source"):
            ws.init_workspace(destination, "Private firm")
    else:
        from torque.demo import create_demo
        with pytest.raises(ws.WorkspaceError, match="outside.*source"):
            create_demo(destination)
    assert not destination.exists()


@pytest.mark.parametrize("metadata", [None, [], {"expected_components": [None]}, {"expected_components": "wrong-type"}])
def test_malformed_metadata_details_are_diagnosed_before_handoff(engagement, monkeypatch, metadata):
    from jsc_qa.dispatcher import DispatchResult
    root, _, _, _, _ = engagement
    change = changes.create_change(root, "Alpha", "Synthetic change", "Outcome")
    monkeypatch.setattr("jsc_qa.dispatcher.dispatch_meta_api", lambda *a, **kw:
                        DispatchResult("MetaAPI", "PASS", "Synthetic report"))
    event = changes.verify_deploy(root, "Alpha", change["id"], "synthetic-dev", "synthetic-job")
    event["observation"]["metadata"] = metadata
    change_root, _ = changes.load_change(root, "Alpha", change["id"])
    (change_root / "events" / f"{event['id']}.json").write_text(json.dumps(event), encoding="utf-8")
    code, output, error = invoke(root, "handoff")
    assert code == 2 and output == "" and "invalid metadata" in error


def test_capture_length_is_part_of_evidence_integrity(engagement):
    root, _, _, proof, _ = engagement
    change = changes.create_change(root, "Alpha", "Synthetic change", "Outcome", ["Save"])
    event = changes.add_check(root, "Alpha", change["id"], "AC1", "pass", "Reported pass", proof)
    event["evidence"]["bytes"] += 1
    change_root, _ = changes.load_change(root, "Alpha", change["id"])
    (change_root / "events" / f"{event['id']}.json").write_text(json.dumps(event), encoding="utf-8")
    assert changes.get_change(root, "Alpha", change["id"])["assessment"]["evidence_problems"] == 1


@pytest.mark.parametrize("condition", ["unreadable", "pipe"])
def test_unavailable_captured_evidence_keeps_handoff_usable(engagement, monkeypatch, condition):
    root, _, _, proof, _ = engagement
    change = changes.create_change(root, "Alpha", "Synthetic change", "Outcome", ["Save"])
    event = changes.add_check(root, "Alpha", change["id"], "AC1", "pass", "Reported pass", proof)
    change_root, _ = changes.load_change(root, "Alpha", change["id"])
    capture = change_root / event["evidence"]["path"]
    if condition == "pipe":
        capture.unlink()
        os.mkfifo(capture)
    else:
        original = ws._file_hash
        def denied(path):
            if path == capture:
                raise PermissionError("Synthetic unavailable capture")
            return original(path)
        monkeypatch.setattr(ws, "_file_hash", denied)
    code, output, error = invoke(root, "handoff")
    assert code == 0 and error == "" and "unavailable" in output
    assert changes.get_change(root, "Alpha", change["id"])["assessment"]["evidence_problems"] == 1
