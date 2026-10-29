"""Records survive moves, copies between Windows and macOS or Linux, and typos: evidence
resolves after a move, a stray record is skipped visibly instead of stopping context, and a
missing workspace, client or change is named as missing. Synthetic workspaces only."""
import contextlib
import hashlib
import io
import json
from pathlib import PurePosixPath
import shutil

import pytest
from torque import changes, cli
from torque import workspace as ws

BASIS = "file reference recorded; contents not evaluated"


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def firm(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    alpha = ws.add_client(root, "Alpha")
    ws.add_client(root, "Beta")
    proof = alpha / "artifacts" / "proof.txt"
    proof.write_text("Synthetic observation", encoding="utf-8")
    entry = ws.add_session(root, "Alpha", "Reported completion", "verified", proof)
    return root, alpha, proof, entry


def scope(root, *extra):
    return (*extra, "--workspace", str(root), "--client", "Alpha")


def rewrite_evidence(alpha, entry, evidence):
    """The session record as an earlier version wrote it."""
    path = alpha / "sessions" / f"{entry['id']}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["evidence"] = evidence
    path.write_text(json.dumps(record), encoding="utf-8")


def test_evidence_inside_the_workspace_is_recorded_relative_to_it(firm):
    _, _, proof, entry = firm
    assert entry["evidence"]["workspace_path"] == "clients/alpha/artifacts/proof.txt"
    assert entry["evidence"]["sha256"] == hashlib.sha256(proof.read_bytes()).hexdigest()


def test_evidence_still_matches_after_the_workspace_moves(firm, tmp_path):
    root, *_ = firm
    moved = root.rename(root.with_name("firm-moved"))
    assert ws.get_context(moved, "Alpha")["sessions"][0]["evidence_integrity"] == "matches_reference"
    code, output, error = invoke(*scope(moved, "context"))
    assert code == 0 and error == "" and "since recording" not in output
    handoff = ws.render_handoff(moved, "Alpha")
    assert "Evidence reference: clients/alpha/artifacts/proof.txt" in handoff
    assert "Evidence integrity: matches reference" in handoff and str(tmp_path) not in handoff


@pytest.mark.parametrize("old_root", [r"C:\Users\user\Work\firm", "/Users/user/Work/firm"])
def test_an_older_record_is_found_after_a_move_or_a_copy_from_another_system(firm, old_root):
    root, alpha, proof, entry = firm
    separator = "\\" if "\\" in old_root else "/"
    stored = separator.join([old_root, "clients", "alpha", "artifacts", "proof.txt"])
    rewrite_evidence(alpha, entry, {"path": stored, "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
                                    "basis": BASIS})
    session = ws.get_context(root, "Alpha")["sessions"][0]
    assert session["evidence_integrity"] == "matches_reference"
    proof.write_text("A later observation", encoding="utf-8")
    assert ws.get_context(root, "Alpha")["sessions"][0]["evidence_integrity"] == "changed"


def test_an_older_record_is_never_found_in_another_clients_folder(firm):
    root, alpha, proof, entry = firm
    beta_copy = root / "clients" / "beta" / "artifacts" / "proof.txt"
    beta_copy.write_bytes(proof.read_bytes())
    rewrite_evidence(alpha, entry, {"path": r"C:\old\firm\clients\beta\artifacts\proof.txt",
                                    "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(), "basis": BASIS})
    assert ws.get_context(root, "Alpha")["sessions"][0]["evidence_integrity"] == "missing"


def test_a_recorded_relative_path_cannot_leave_the_workspace(firm, monkeypatch):
    root, alpha, proof, entry = firm
    rewrite_evidence(alpha, entry, {"path": str(proof), "workspace_path": "../outside.txt",
                                    "sha256": "0" * 64, "basis": BASIS})
    reads = []
    original = ws._file_hash
    monkeypatch.setattr(ws, "_file_hash", lambda path: reads.append(path) or original(path))
    # A boundary error: context stops, as every reader does, and nothing is read.
    code, output, error = invoke(*scope(root, "context"))
    assert code == 2 and output == "" and "session evidence reference leaves the workspace" in error
    assert entry["id"] in error and reads == []
    with pytest.raises(ws.BoundaryError, match="leaves the workspace"):
        ws.list_sessions(root, "Alpha")


def external_proof(tmp_path):
    """A file outside the workspace, in folders named like a client's own."""
    external = tmp_path / "external" / "clients" / "alpha" / "artifacts" / "proof.txt"
    external.parent.mkdir(parents=True)
    external.write_text("Synthetic observation", encoding="utf-8")
    return external


def test_an_external_reference_is_recorded_as_external(firm, tmp_path):
    root, alpha, proof, _ = firm
    entry = ws.add_session(root, "Alpha", "Observed an external file", evidence=external_proof(tmp_path))
    assert entry["evidence"]["scope"] == "external" and "workspace_path" not in entry["evidence"]
    assert ws.get_context(root, "Alpha")["sessions"][0]["evidence_integrity"] == "matches_reference"


def test_a_missing_external_reference_never_matches_a_copy_in_the_workspace(firm, tmp_path):
    root, alpha, proof, _ = firm
    external = external_proof(tmp_path)
    # The same bytes at the path a relocation would try: clients/alpha/artifacts/proof.txt.
    proof.write_bytes(external.read_bytes())
    ws.add_session(root, "Alpha", "Observed an external file", evidence=external)
    external.unlink()
    session = ws.get_context(root, "Alpha")["sessions"][0]
    assert session["evidence_integrity"] == "missing" and session["evidence_location"] == str(external)
    assert "Evidence integrity: missing" in ws.render_handoff(root, "Alpha")


@pytest.mark.parametrize("evidence", [
    {"scope": "workspace"}, {"scope": ["external"]},
    {"scope": "external", "workspace_path": "clients/alpha/artifacts/proof.txt"}])
def test_a_scope_that_is_not_external_is_a_damaged_reference(firm, evidence):
    root, alpha, proof, entry = firm
    rewrite_evidence(alpha, entry, {"path": str(proof), "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
                                    "basis": BASIS, **evidence})
    context = ws.get_context(root, "Alpha")
    assert context["sessions"] == [] and context["record_problems"][0]["reason"] == "invalid session evidence reference"


def test_a_metadata_report_path_is_recorded_with_forward_slashes(firm, monkeypatch):
    from jsc_qa.dispatcher import DispatchResult
    root, *_ = firm
    change = changes.create_change(root, "Alpha", "Synthetic change", "Outcome")
    monkeypatch.setattr("jsc_qa.dispatcher.dispatch_meta_api", lambda *a, **kw:
                        DispatchResult("MetaAPI", "PASS", "Synthetic report", raw_output='{"synthetic": true}'))
    event = changes.verify_deploy(root, "Alpha", change["id"], "synthetic-dev", "synthetic-job")
    assert event["evidence"]["path"].startswith("evidence/") and "\\" not in event["evidence"]["path"]


def test_a_report_path_written_on_windows_reads_where_backslash_is_not_a_separator(firm, monkeypatch):
    from jsc_qa.dispatcher import DispatchResult
    root, *_ = firm
    change = changes.create_change(root, "Alpha", "Synthetic change", "Outcome")
    monkeypatch.setattr("jsc_qa.dispatcher.dispatch_meta_api", lambda *a, **kw:
                        DispatchResult("MetaAPI", "PASS", "Synthetic report", raw_output='{"synthetic": true}'))
    event = changes.verify_deploy(root, "Alpha", change["id"], "synthetic-dev", "synthetic-job")
    # The record a Windows machine wrote before this fix: "evidence\<name>".
    change_root, _ = changes.load_change(root, "Alpha", change["id"])
    event["evidence"]["path"] = event["evidence"]["path"].replace("/", "\\")
    (change_root / "events" / f"{event['id']}.json").write_text(json.dumps(event), encoding="utf-8")
    # Read the way macOS and Linux read it.
    monkeypatch.setattr(changes, "Path", PurePosixPath)
    detail = changes.get_change(root, "Alpha", change["id"])
    assert detail["events"][0]["evidence_integrity"] == "matches_capture"


@pytest.mark.parametrize("name", ["{id} (1).json", "notes-copy.json"])
def test_a_stray_session_copy_is_skipped_visibly(firm, name):
    root, alpha, _, entry = firm
    ws.add_session(root, "Alpha", "Second synthetic session")
    stray = alpha / "sessions" / name.format(id=entry["id"])
    shutil.copyfile(alpha / "sessions" / f"{entry['id']}.json", stray)
    original = stray.read_bytes()
    code, output, error = invoke(*scope(root, "context"))
    assert code == 0 and error == ""
    assert stray.name in output and "Second synthetic session" in output and "Reported completion" in output
    data = json.loads(invoke(*scope(root, "context", "--json"))[1])
    assert [problem["path"] for problem in data["record_problems"]] == [f"sessions/{stray.name}"]
    assert len(data["sessions"]) == 2
    code, handoff, error = invoke(*scope(root, "handoff"))
    assert code == 0 and error == "" and stray.name in handoff and "Second synthetic session" in handoff
    code, listing, _ = invoke(*scope(root, "session", "list"))
    assert code == 0 and "Second synthetic session" in listing
    code, raw, _ = invoke("doctor", "--workspace", str(root), "--client", "Alpha", "--json")
    report = json.loads(raw)
    assert code == 3 and report["ready"] is False and report["client"]["record_problems"] == 1
    assert any(stray.name in action for action in report["next_actions"])
    # Never rewritten or removed.
    assert stray.read_bytes() == original


def test_a_record_naming_another_client_still_stops_the_read(firm):
    root, alpha, _, entry = firm
    path = alpha / "sessions" / f"{entry['id']}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["client"] = "beta"
    path.write_text(json.dumps(record), encoding="utf-8")
    code, output, error = invoke(*scope(root, "context"))
    assert code == 2 and output == "" and "different client" in error


def test_a_stray_change_event_is_skipped_in_context_but_read_strictly_elsewhere(firm):
    root, *_ = firm
    change = changes.create_change(root, "Alpha", "Synthetic change", "Outcome", ["Save"])
    note = changes.add_note(root, "Alpha", change["id"], "Synthetic decision", "decision")
    change_root, _ = changes.load_change(root, "Alpha", change["id"])
    events = change_root / "events"
    shutil.copyfile(events / f"{note['id']}.json", events / f"{note['id']} (1).json")
    context = ws.get_context(root, "Alpha")
    assert context["changes"][0]["decisions"][0]["summary"] == "Synthetic decision"
    assert any(problem["path"].endswith(f"{note['id']} (1).json") for problem in context["record_problems"])
    # Approval and recovery code reads change history strictly, as before.
    with pytest.raises(ws.WorkspaceError, match="invalid change event"):
        changes.get_change(root, "Alpha", change["id"])
    code, output, _ = invoke(*scope(root, "change", "show", change["id"]))
    assert code == 0 and f"{note['id']} (1).json" in output and "Synthetic decision" in output


def test_an_unknown_client_is_named_as_missing_not_damaged(firm):
    root, *_ = firm
    code, output, error = invoke("context", "--workspace", str(root), "--client", "Gamma")
    assert code == 2 and output == ""
    assert 'no client "Gamma"' in error and "torque client list" in error and "torque client add" in error
    assert "cannot read valid local configuration" not in error and "beta" not in error.lower()


def test_an_unknown_initiative_is_named_as_missing(firm):
    root, *_ = firm
    code, _, error = invoke("context", "--workspace", str(root), "--initiative", "Roadmap")
    assert code == 2 and 'no initiative "Roadmap"' in error and "torque initiative add" in error


def test_a_folder_without_workspace_json_is_named_as_not_a_workspace(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    code, _, error = invoke("context", "--workspace", str(folder), "--client", "Alpha")
    assert code == 2 and "not a Torque workspace" in error and "torque workspace init" in error


def test_an_unknown_change_is_named_as_missing(firm):
    root, *_ = firm
    code, _, error = invoke(*scope(root, "change", "show", "chg-000000000000"))
    assert code == 2 and "no change chg-000000000000" in error and "torque change list" in error


def test_a_damaged_client_file_still_reads_as_damaged(firm):
    root, alpha, *_ = firm
    (alpha / "client.json").write_text("{not json", encoding="utf-8")
    code, _, error = invoke(*scope(root, "context"))
    assert code == 2 and "cannot read valid local configuration" in error


def test_show_command_keeps_the_workspace_as_given(firm, monkeypatch):
    root, *_ = firm
    changes.create_change(root, "Alpha", "Synthetic change", "Outcome")
    monkeypatch.chdir(root)
    assert "--workspace . " in ws.get_context(".", "Alpha")["changes"][0]["show_command"]
