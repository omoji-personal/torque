"""A record field of the wrong type makes a damaged record, never a crash: informational
readers (context, handoff, change show) skip the record and name it, and strict readers
(approval and recovery code) refuse it with a WorkspaceError. A path that would leave its
folder stays a boundary error, which every reader refuses. Synthetic workspaces only."""
import contextlib
import copy
import io
import json

import pytest
from torque import changes, cli
from torque import workspace as ws

# Values of the wrong type for any field: a list and a dict cannot be hashed, and none is text.
WRONG = (["AC1"], {"AC1": 1}, 7)


def invoke(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def history(tmp_path):
    """One client with a session and a change holding every kind of event Torque writes."""
    root = ws.init_workspace(tmp_path / "firm", "Example firm")
    alpha = ws.add_client(root, "Alpha")
    proof = alpha / "artifacts" / "proof.txt"
    proof.write_text("Synthetic observation", encoding="utf-8")
    ws.add_session(root, "Alpha", "Session with evidence", "executed", proof)
    change = changes.create_change(root, "Alpha", "Synthetic change", "Outcome", ["Saves", "Reports"])
    changes.add_check(root, "Alpha", change["id"], "AC1", "pass", "Synthetic check", proof)
    changes.add_note(root, "Alpha", change["id"], "Synthetic decision", "decision")
    changes.add_note(root, "Alpha", change["id"], "Synthetic next step", "next_step")
    change_root, record = changes.load_change(root, "Alpha", change["id"])
    changes._append(change_root, record, {
        "kind": "metadata_observation", "summary": "Synthetic report", "basis": "salesforce_metadata_api",
        "business_acceptance_proven": False, "target_org": "synthetic-dev", "job_id": "synthetic-job",
        "result": "pass", "requested_components": ["Flow:Synthetic"],
        "observation": {"metadata": {"expected_components": ["Flow:Synthetic"]}},
        "evidence": changes._capture_file(change_root, proof), "manifest": changes._capture_file(change_root, proof)})
    # Every approval kind is checked by the same code; these two carry each field a handoff prints.
    for kind in ("approval_request", "approval_grant"):
        changes.append_approval_event(root, "Alpha", change["id"], kind,
                                      {key: f"synthetic-{key}" for key in changes.APPROVAL_EVENT_FIELDS[kind]})
    changes._append(change_root, record, {"kind": changes.BEFORE_STATE_KIND, "summary": "Synthetic before-state",
                                          "basis": "torque_before_state", "path": "evidence/before-synthetic",
                                          "files": [{"path": "evidence/before-synthetic/Flow.xml",
                                                     "sha256": "0" * 64}], "sha256": "0" * 64})
    return root, alpha, change["id"]


def records(alpha):
    """Each record file of the client: its sessions, its change and the change's events."""
    return sorted(path for pattern in ("sessions/*.json", "changes/*/change.json", "changes/*/events/*.json")
                  for path in alpha.glob(pattern))


def mutations(value):
    """(description, damaged copy) for every field, one level into objects and lists too."""
    for key, field in value.items():
        for wrong in WRONG:
            yield f"{key}={wrong!r}", {**value, key: wrong}
        inner = (field.items() if isinstance(field, dict)
                 else enumerate(field) if isinstance(field, list) else ())
        for name, item in inner:
            for wrong in WRONG:
                damaged = copy.deepcopy(value)
                damaged[key][name] = wrong
                yield f"{key}[{name!r}]={wrong!r}", damaged


def read_everything(root, change_id, group):
    """The readers of the damaged record: the informational ones must not raise; a strict
    one (approval and recovery code read this way) may refuse with a WorkspaceError."""
    ws.get_context(root, "Alpha")
    if group == "events":
        changes.render_change(root, "Alpha", change_id, problems=[])
    else:
        ws.render_handoff(root, "Alpha")
    strict = (lambda: ws.list_sessions(root, "Alpha")) if group == "sessions" else (
        lambda: changes.get_change(root, "Alpha", change_id))
    try:
        strict()
    except ws.BoundaryError as exc:
        raise AssertionError(f"a wrong type is not a boundary error: {exc}") from exc
    except ws.WorkspaceError:
        pass


@pytest.mark.parametrize("group", ["sessions", "change.json", "events"])
def test_a_field_of_the_wrong_type_is_a_damaged_record(history, group):
    root, alpha, change_id = history
    files = [path for path in records(alpha) if group in path.as_posix()]
    assert files
    for path in files:
        original = path.read_text(encoding="utf-8")
        for description, damaged in mutations(json.loads(original)):
            path.write_text(json.dumps(damaged), encoding="utf-8")
            try:
                read_everything(root, change_id, group)
            except Exception as exc:
                raise AssertionError(f"{path.name} with {description}: {type(exc).__name__}: {exc}") from exc
            finally:
                path.write_text(original, encoding="utf-8")


def test_a_wrong_typed_criterion_keeps_the_rest_of_the_history(history):
    root, alpha, change_id = history
    change_root, _ = changes.load_change(root, "Alpha", change_id)
    check = next(path for path in (change_root / "events").glob("*.json")
                 if json.loads(path.read_text(encoding="utf-8"))["kind"] == "check")
    event = json.loads(check.read_text(encoding="utf-8"))
    event["criterion"] = ["AC1"]
    check.write_text(json.dumps(event), encoding="utf-8")
    context = ws.get_context(root, "Alpha")
    assert context["sessions"][0]["summary"] == "Session with evidence"
    assert context["changes"][0]["decisions"][0]["summary"] == "Synthetic decision"
    assert [problem["path"] for problem in context["record_problems"]] == [
        f"changes/{change_id}/events/{check.name}"]
    with pytest.raises(ws.WorkspaceError, match="invalid reported acceptance check"):
        changes.get_change(root, "Alpha", change_id)
    code, output, error = invoke("change", "show", change_id, "--workspace", str(root), "--client", "Alpha")
    assert code == 0 and error == "" and check.name in output and "Synthetic decision" in output


@pytest.mark.parametrize("path", ["evidence/../../../../beta/artifacts/proof.txt", "evidence/../change.json",
                                  "/etc/hosts", r"C:\outside\proof.txt", r"evidence\..\..\beta\proof.txt"])
def test_change_evidence_that_leaves_its_folder_stops_every_reader(history, path):
    root, alpha, change_id = history
    change_root, _ = changes.load_change(root, "Alpha", change_id)
    check = next(item for item in (change_root / "events").glob("*.json")
                 if json.loads(item.read_text(encoding="utf-8"))["kind"] == "check")
    event = json.loads(check.read_text(encoding="utf-8"))
    event["evidence"]["path"] = path
    check.write_text(json.dumps(event), encoding="utf-8")
    for read in (lambda: ws.get_context(root, "Alpha"), lambda: ws.render_handoff(root, "Alpha"),
                 lambda: changes.get_change(root, "Alpha", change_id),
                 lambda: changes.get_change(root, "Alpha", change_id, problems=[])):
        with pytest.raises(ws.BoundaryError, match="out of the change's evidence folder"):
            read()
    code, output, error = invoke("context", "--workspace", str(root), "--client", "Alpha")
    assert code == 2 and output == "" and "out of the change's evidence folder" in error


@pytest.mark.parametrize("value", ["notes/proof.txt", "", "evidence/a\0b"])
def test_a_malformed_change_evidence_path_is_a_damaged_record(history, value):
    root, alpha, change_id = history
    change_root, _ = changes.load_change(root, "Alpha", change_id)
    check = next(item for item in (change_root / "events").glob("*.json")
                 if json.loads(item.read_text(encoding="utf-8"))["kind"] == "check")
    event = json.loads(check.read_text(encoding="utf-8"))
    event["evidence"]["path"] = value
    check.write_text(json.dumps(event), encoding="utf-8")
    context = ws.get_context(root, "Alpha")
    assert [problem["reason"] for problem in context["record_problems"]] == ["invalid evidence path"]
    with pytest.raises(ws.WorkspaceError, match="invalid evidence path") as refused:
        changes.get_change(root, "Alpha", change_id)
    assert not isinstance(refused.value, ws.BoundaryError)


@pytest.mark.parametrize("value", ["../outside.txt", "clients/alpha/../../../outside.txt", "/etc/hosts",
                                   r"C:\outside\proof.txt", r"clients\alpha\..\..\..\outside.txt"])
def test_session_evidence_that_leaves_the_workspace_stops_every_reader(history, monkeypatch, value):
    root, alpha, change_id = history
    path = next(alpha.glob("sessions/*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["evidence"]["workspace_path"] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    reads = []
    original = ws._file_hash
    monkeypatch.setattr(ws, "_file_hash", lambda item: reads.append(item) or original(item))
    for read in (lambda: ws.get_context(root, "Alpha"), lambda: ws.render_handoff(root, "Alpha"),
                 lambda: ws.list_sessions(root, "Alpha"), lambda: ws.list_sessions(root, "Alpha", problems=[])):
        with pytest.raises(ws.BoundaryError, match="leaves the workspace"):
            read()
    # Refused before that path is opened: only the change's own captured files were read.
    assert all("changes" in item.parts for item in reads), reads
    code, output, error = invoke("context", "--workspace", str(root), "--client", "Alpha")
    assert code == 2 and output == "" and "leaves the workspace" in error


def test_a_damaged_session_that_also_leaves_the_workspace_still_stops_the_read(history):
    root, alpha, change_id = history
    path = next(alpha.glob("sessions/*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["created_at"] = "not a time"
    record["evidence"]["workspace_path"] = "../outside.txt"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ws.BoundaryError, match="leaves the workspace"):
        ws.get_context(root, "Alpha")


def test_a_damaged_event_that_also_leaves_the_evidence_folder_still_stops_the_read(history):
    root, alpha, change_id = history
    change_root, _ = changes.load_change(root, "Alpha", change_id)
    check = next(item for item in (change_root / "events").glob("*.json")
                 if json.loads(item.read_text(encoding="utf-8"))["kind"] == "check")
    event = json.loads(check.read_text(encoding="utf-8"))
    event["created_at"] = "not a time"
    event["evidence"]["path"] = "evidence/../../../../beta/artifacts/proof.txt"
    check.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(ws.BoundaryError, match="out of the change's evidence folder"):
        ws.get_context(root, "Alpha")
