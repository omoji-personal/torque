"""The daily record commands: session add, context, handoff. All data is synthetic."""
import contextlib
import io
import json
from pathlib import Path
import sys

import pytest

from torque import cli, workspace as ws


def invoke(*args, stdin=None):
    stdout, stderr = io.StringIO(), io.StringIO()
    previous = sys.stdin
    if stdin is not None:
        sys.stdin = io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")), encoding="utf-8")
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([str(a) for a in args])
    finally:
        sys.stdin = previous
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha", "alpha-sandbox")
    ws.add_client(root, "Beta")
    return root


def add(root, *args, client="alpha"):
    return invoke("session", "add", "--workspace", root, "--client", client, *args)


def sessions(root, client="alpha"):
    return sorted((root / "clients" / client / "sessions").glob("*.json"))


# ---- step 1: evidence and input that do not lose information

def test_two_evidence_files_are_both_kept_in_order_and_both_rendered(firm):
    one, two = firm / "clients/alpha/artifacts/one.txt", firm / "clients/alpha/artifacts/two.txt"
    one.write_text("first", encoding="utf-8")
    two.write_text("second", encoding="utf-8")
    code, out, err = add(firm, "--summary", "Ran both test scripts", "--status", "executed",
                         "--evidence", one, "--evidence", two, "--json")
    assert code == 0, err
    entry = json.loads(out)
    assert [Path(item["path"]).name for item in entry["evidence"]] == ["one.txt", "two.txt"]
    assert all(len(item["sha256"]) == 64 for item in entry["evidence"])
    stored = json.loads(sessions(firm)[0].read_text(encoding="utf-8"))
    assert [item["workspace_path"] for item in stored["evidence"]] == ["clients/alpha/artifacts/one.txt",
                                                                       "clients/alpha/artifacts/two.txt"]
    shown = json.loads(invoke("session", "show", entry["id"], "--workspace", firm, "--client", "alpha",
                              "--json")[1])
    assert [item["location"] for item in shown["evidence_items"]] == ["clients/alpha/artifacts/one.txt",
                                                                      "clients/alpha/artifacts/two.txt"]
    assert shown["evidence_integrity"] == "matches_reference"
    handoff = invoke("handoff", "--workspace", firm, "--client", "alpha")[1]
    assert "clients/alpha/artifacts/one.txt" in handoff and "clients/alpha/artifacts/two.txt" in handoff
    two.write_text("changed", encoding="utf-8")
    shown = json.loads(invoke("session", "show", entry["id"], "--workspace", firm, "--client", "alpha",
                              "--json")[1])
    assert [item["integrity"] for item in shown["evidence_items"]] == ["matches_reference", "changed"]
    assert shown["evidence_integrity"] == "changed"


def test_one_missing_evidence_file_writes_no_record(firm):
    one = firm / "clients/alpha/artifacts/one.txt"
    one.write_text("first", encoding="utf-8")
    code, _, err = add(firm, "--summary", "x", "--evidence", one, "--evidence", firm / "nope.txt")
    assert code == 2 and "evidence is not a readable file" in err
    assert sessions(firm) == []


def test_a_record_written_with_one_evidence_object_still_reads_and_renders(firm):
    # The shape every earlier version wrote: one object, no evidence_items.
    proof = firm / "clients/alpha/artifacts/proof.txt"
    proof.write_text("proof", encoding="utf-8")
    entry_id = "20261001T120000000000Z-0123456789ab"
    record = {"schema": "torque.session/1", "id": entry_id, "created_at": "2026-10-01T12:00:00+00:00",
              "client": "alpha", "summary": "Older record", "status": "executed", "status_basis": "user_reported",
              "independently_verified": False,
              "evidence": {"path": str(proof), "sha256": ws._file_hash(proof), "basis": ws.EVIDENCE_BASIS,
                           "workspace_path": "clients/alpha/artifacts/proof.txt"}}
    (firm / "clients/alpha/sessions").mkdir(exist_ok=True)
    (firm / "clients/alpha/sessions" / f"{entry_id}.json").write_text(json.dumps(record), encoding="utf-8")
    shown = json.loads(invoke("session", "show", entry_id, "--workspace", firm, "--client", "alpha", "--json")[1])
    assert shown["evidence_integrity"] == "matches_reference"
    assert shown["evidence_location"] == "clients/alpha/artifacts/proof.txt"
    assert [item["location"] for item in shown["evidence_items"]] == ["clients/alpha/artifacts/proof.txt"]
    assert "Evidence reference: clients/alpha/artifacts/proof.txt (in this workspace)" in invoke(
        "handoff", "--workspace", firm, "--client", "alpha")[1]
    # One file is still written as one object, so earlier versions read new records too.
    code, out, _ = add(firm, "--summary", "one file", "--evidence", proof, "--json")
    assert code == 0 and isinstance(json.loads(out)["evidence"], dict)


@pytest.mark.parametrize("form", ["json", "yaml", "stdin"])
def test_input_document_gives_the_fields_and_options_win(firm, form):
    one, two = firm / "clients/alpha/artifacts/one.txt", firm / "clients/alpha/artifacts/two.txt"
    one.write_text("1", encoding="utf-8")
    two.write_text("2", encoding="utf-8")
    summary = 'Line one with "quotes", $dollars and `ticks`.\nLine two: still the summary.'
    if form == "yaml":
        text = (f"summary: |\n  Line one with \"quotes\", $dollars and `ticks`.\n  Line two: still the summary.\n"
                f"status: verified\nevidence:\n  - {one.as_posix()}\n  - {two.as_posix()}\n")
    else:
        text = json.dumps({"summary": summary, "status": "verified", "evidence": [str(one), str(two)]})
    if form == "stdin":
        code, out, err = invoke("session", "add", "--workspace", firm, "--client", "alpha", "--input", "-",
                                "--status", "executed", "--json", stdin=text)
    else:
        document = firm / "clients/alpha/artifacts" / f"record.{form}"
        document.write_text(text, encoding="utf-8")
        code, out, err = add(firm, "--input", document, "--status", "executed", "--json")
    assert code == 0, err
    entry = json.loads(out)
    assert entry["summary"].rstrip() == summary and entry["status"] == "executed"
    assert [Path(item["path"]).name for item in entry["evidence"]] == ["one.txt", "two.txt"]
    # An option on the command line replaces the document's value, lists included.
    document = firm / "clients/alpha/artifacts/second.json"
    document.write_text(json.dumps({"summary": "from the file", "evidence": [str(one), str(two)]}), encoding="utf-8")
    entry = json.loads(add(firm, "--input", document, "--summary", "from the option", "--evidence", two,
                           "--json")[1])
    assert entry["summary"] == "from the option" and Path(entry["evidence"]["path"]).name == "two.txt"
    assert entry["status"] == "prepared"


def test_input_document_errors_name_the_problem_and_write_nothing(firm):
    bad = firm / "bad.json"
    for text, words in (('{"summary": "x", "stauts": "verified"}', "unknown field in --input"),
                        ("[1, 2]", "must hold one mapping"),
                        ('{"summary": 5}', "must be text"),
                        ('{"status": "executed"}', "session summary must be nonempty"),
                        ("summary: [unclosed", "neither JSON nor YAML")):
        bad.write_text(text, encoding="utf-8")
        code, _, err = add(firm, "--input", bad)
        assert code == 2 and words in err, (text, err)
    assert add(firm, "--input", firm / "missing.json")[0] == 2
    assert sessions(firm) == []


def test_learned_keeps_working_with_input(firm):
    document = firm / "record.json"
    document.write_text(json.dumps({"summary": "Fixed the query", "status": "executed",
                                    "learned": "When: a query fails on a field Do: describe first Because: names "
                                               "differ between orgs", "learned_from": "consultant"}),
                        encoding="utf-8")
    code, out, err = add(firm, "--input", document, "--json")
    assert code == 0, err
    entry = json.loads(out)
    assert entry["lessons"] and entry["lessons"][0]["state"] == "active"
