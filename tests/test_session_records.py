"""The daily record commands: session add, context, handoff. All data is synthetic."""
import contextlib
import io
import json
import os
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


def test_a_verified_record_without_a_basis_saves_and_hints_and_with_one_keeps_every_field(firm):
    code, out, err = add(firm, "--summary", "Checked the page", "--status", "verified")
    assert code == 0, err
    hint = [line for line in out.splitlines() if line.startswith("Hint: ")]
    assert len(hint) == 1 and all(word in hint[0] for word in ("--check", "--as-user", "--in-org", "--not-covered"))
    entry = json.loads(add(firm, "--summary", "Checked again", "--status", "verified", "--json")[1])
    assert entry["hints"] and "check" not in entry
    stored = json.loads((firm / "clients/alpha/sessions" / f"{entry['id']}.json").read_text(encoding="utf-8"))
    assert "hints" not in stored
    entry = json.loads(add(firm, "--summary", "Checked as staff", "--status", "verified", "--check", "rendered",
                           "--as-user", "staff user", "--in-org", "alpha-sandbox", "--not-covered", "mobile",
                           "--json")[1])
    assert entry["hints"] == []
    assert entry["check"] == {"kinds": ["rendered"], "as_user": "staff user", "org": "alpha-sandbox",
                              "not_covered": "mobile"}
    context = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--json")[1])
    assert next(s for s in context["sessions"] if s["id"] == entry["id"])["check"]["as_user"] == "staff user"
    text = invoke("context", "--workspace", firm, "--client", "alpha")[1]
    basis = "Basis: check rendered; as staff user; in alpha-sandbox; not covered: mobile"
    assert basis in text
    assert basis in invoke("handoff", "--workspace", firm, "--client", "alpha")[1]


def test_the_org_type_comes_from_the_consent_record_when_the_workspace_knows_it(firm):
    consent = firm / "clients/alpha/consent.json"
    consent.write_text(json.dumps({"schema": "torque.consent/1", "client": "alpha", "approved_orgs": [
        {"alias": "alpha-sandbox", "org_id_18": "00D000000000000AAA", "kind": "sandbox"}]}), encoding="utf-8")
    entry = json.loads(add(firm, "--summary", "x", "--check", "live", "--in-org", "alpha-sandbox", "--json")[1])
    assert entry["check"]["org_type"] == "sandbox"
    entry = json.loads(add(firm, "--summary", "x", "--in-org", "other-org", "--json")[1])
    assert "org_type" not in entry["check"]


def test_accepted_needs_someone_who_accepted_it(firm):
    code, _, err = add(firm, "--summary", "Lead said yes", "--status", "accepted")
    assert code == 2 and "--accepted-by" in err and sessions(firm) == []
    entry = json.loads(add(firm, "--summary", "Lead said yes", "--status", "accepted", "--accepted-by",
                           "project lead", "--json")[1])
    assert entry["status"] == "accepted" and entry["accepted_by"] == "project lead"
    assert "accepted by project lead (reported)" in invoke("context", "--workspace", firm, "--client", "alpha")[1]


def test_corrects_links_without_rewriting_and_an_unknown_id_names_nothing_private(firm):
    first = json.loads(add(firm, "--summary", "Secret-ish words: the Flow passed", "--status", "verified",
                           "--json")[1])
    other = json.loads(add(firm, "--summary", "Beta work", "--json", client="beta")[1])
    original = (firm / "clients/alpha/sessions" / f"{first['id']}.json").read_bytes()
    for wrong in ("20991231T000000000000Z-000000000000", other["id"], "not an id"):
        code, out, err = add(firm, "--summary", "x", "--corrects", wrong)
        assert code == 2 and "session list" in err
        assert "Secret-ish" not in err + out and "Beta work" not in err + out and wrong not in err
    fix = json.loads(add(firm, "--summary", "The Flow failed for staff users", "--status", "executed",
                         "--corrects", first["id"], "--json")[1])
    assert fix["corrects"] == first["id"]
    assert (firm / "clients/alpha/sessions" / f"{first['id']}.json").read_bytes() == original
    context = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--json")[1])
    earlier = next(s for s in context["sessions"] if s["id"] == first["id"])
    assert earlier["corrected_by"] == [fix["id"]]
    text = invoke("context", "--workspace", firm, "--client", "alpha")[1]
    assert f"CORRECTED by {fix['id']}" in text
    assert f"CORRECTED by {fix['id']}" in invoke("handoff", "--workspace", firm, "--client", "alpha")[1]


def test_a_record_with_a_damaged_basis_is_skipped_and_named(firm):
    entry = json.loads(add(firm, "--summary", "x", "--check", "scripted", "--json")[1])
    path = firm / "clients/alpha/sessions" / f"{entry['id']}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["check"] = {"kind": "guessed"}
    path.write_text(json.dumps(value), encoding="utf-8")
    context = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--json")[1])
    assert context["sessions"] == [] and context["record_problems"][0]["reason"] == "its check basis is not valid"


def test_several_check_kinds_are_kept_and_the_hint_names_each_unstated_part(firm):
    entry = json.loads(add(firm, "--summary", "x", "--status", "verified", "--check", "read-back", "--check",
                           "rendered", "--json")[1])
    assert entry["check"]["kinds"] == ["read-back", "rendered"]
    assert "Basis: check read-back + rendered" in invoke("context", "--workspace", firm, "--client", "alpha")[1]
    only_user = json.loads(add(firm, "--summary", "x", "--status", "verified", "--as-user", "admin", "--json")[1])
    [hint] = only_user["hints"]
    assert "how it was checked" in hint and "in which org" in hint and "what was not covered" in hint
    assert "as whom" not in hint
    document = firm / "record.json"
    document.write_text(json.dumps({"summary": "x", "check": ["scripted", "live"]}), encoding="utf-8")
    assert json.loads(add(firm, "--input", document, "--json")[1])["check"]["kinds"] == ["scripted", "live"]


def test_a_correction_link_to_itself_or_a_later_record_is_named_and_not_followed(firm):
    first = json.loads(add(firm, "--summary", "first", "--json")[1])
    second = json.loads(add(firm, "--summary", "second", "--json")[1])
    path = firm / "clients/alpha/sessions" / f"{first['id']}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["corrects"] = second["id"]            # an earlier record naming a later one: a cycle by hand
    path.write_text(json.dumps(value), encoding="utf-8")
    context = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--json")[1])
    assert [p["reason"] for p in context["record_problems"]] == ["its corrects link names itself or a later record"]
    assert "corrected_by" not in context["sessions"][0]


def test_a_correction_of_a_record_outside_the_window_is_shown_and_older_ones_are_counted(firm):
    ids = write_records(firm, 25)
    # an old correction (record 3 corrects record 1), outside the window like the record it corrects
    path = firm / "clients/alpha/sessions" / f"{ids[3]}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**value, "corrects": ids[1]}), encoding="utf-8")
    fix = json.loads(add(firm, "--summary", "record 0 was wrong", "--corrects", ids[0], "--json")[1])
    text = invoke("context", "--workspace", firm, "--client", "alpha")[1]
    assert f"Correction: {fix['id']} corrects {ids[0]} (made 2026-10-01" in text and "not shown here" in text
    assert "Corrections, not shown: 1 older correction record(s)" in text


def test_after_exit_4_the_documented_retry_does_not_record_the_session_twice(firm):
    secret = "Wint3r" + "!Pass99"
    code, out, err = add(firm, "--summary", "Rotated the integration user", "--learned",
                         f"The integration user's password: {secret}")
    assert code == 4 and secret not in out + err
    entry_id = out.split()[1]
    assert f"session {entry_id} is recorded; do not add it again" in err and "IS saved" in err
    retry = err.split("with: ", 1)[1].strip()
    assert f"--session {entry_id}" in retry and retry.startswith("torque lesson add ")
    assert len(sessions(firm)) == 1
    code, _, err = invoke("lesson", "add", "--workspace", firm, "--client", "alpha", "--session", entry_id,
                          "When: an integration user's password is rotated Do: update the named credential "
                          "Because: the integration fails at night otherwise")
    assert code == 0, err
    assert len(sessions(firm)) == 1
    data = json.loads(add(firm, "--summary", "again", "--learned", f"The password: {secret}", "--json")[1])
    assert data["recorded"] is True and data["do_not_add_again"] is True and data["id"] in data["lesson_retry_command"]


# ---- step 5: change records that can grow

def test_a_criterion_added_later_can_be_checked_and_a_retired_one_keeps_its_checks(firm):
    scope = ["--workspace", firm, "--client", "alpha"]
    created = json.loads(invoke("change", "create", *scope, "--title", "Draft", "--outcome", "Staff save a preference",
                                "--json")[1])
    cid = created["id"]
    assert invoke("change", "check", cid, *scope, "--criterion", "AC1", "--result", "pass", "--summary", "x")[0] == 2
    added = json.loads(invoke("change", "criterion", "add", cid, *scope, "--text", "Staff can save it", "--json")[1])
    assert added["criterion"] == "AC1"
    assert invoke("change", "criterion", "add", cid, *scope, "--text", "Others cannot edit it")[1].startswith(
        f"Added AC2 in {cid}")
    assert invoke("change", "check", cid, *scope, "--criterion", "AC1", "--result", "pass", "--summary", "ok")[0] == 0
    assert invoke("change", "check", cid, *scope, "--criterion", "AC2", "--result", "fail", "--summary", "no")[0] == 0
    assert invoke("change", "criterion", "retire", cid, "AC2", *scope, "--reason", "out of scope")[0] == 0
    code, _, err = invoke("change", "check", cid, *scope, "--criterion", "AC2", "--result", "pass", "--summary", "x")
    assert code == 2 and "active criteria: AC1" in err
    shown = json.loads(invoke("change", "show", cid, *scope, "--json")[1])
    by_id = {c["id"]: c for c in shown["criteria"]}
    assert by_id["AC1"]["reported_result"] == "pass" and by_id["AC2"]["retired"] is True
    assert by_id["AC2"]["checks"] == 1 and shown["assessment"]["not_yet_reported_pass"] == []
    assert sum(e["kind"] == "check" for e in shown["events"]) == 2
    third = json.loads(invoke("change", "criterion", "add", cid, *scope, "--text", "Third", "--json")[1])
    assert third["criterion"] == "AC3"                         # a retired ID is never reused
    assert "retired: out of scope" in invoke("change", "show", cid, *scope)[1]


def test_a_session_linked_to_a_change_appears_in_the_change_handoff_and_others_do_not(firm):
    scope = ["--workspace", firm, "--client", "alpha"]
    cid = json.loads(invoke("change", "create", *scope, "--title", "T", "--outcome", "O", "--json")[1])["id"]
    linked = json.loads(add(firm, "--summary", "Built the field", "--change", cid, "--check", "scripted", "--json")[1])
    add(firm, "--summary", "Unrelated work")
    assert linked["change"] == cid
    text = invoke("change", "handoff", cid, *scope)[1]
    assert "## Linked session records" in text and "Built the field" in text and "Unrelated work" not in text
    data = json.loads(invoke("change", "handoff", cid, *scope, "--json")[1])
    assert [s["id"] for s in data["linked_sessions"]] == [linked["id"]]
    focused = invoke("handoff", *scope, "--change", cid)[1]
    assert "Built the field" in focused and "Unrelated work" not in focused
    code, out, _ = add(firm, "--summary", "x", "--change", "chg-000000000000", "--json")
    assert code == 2 and "no change" in json.loads(out)["message"] and len(sessions(firm)) == 2


# ---- step 7: clients as they really are

def test_set_org_changes_the_alias_records_it_and_names_what_depended_on_the_old_one(firm):
    scope = ["--workspace", firm, "--client", "alpha"]
    consent = firm / "clients/alpha/consent.json"
    consent.write_text(json.dumps({"schema": "torque.consent/1", "client": "alpha", "approved_orgs": [
        {"alias": "alpha-sandbox", "org_id_18": "00D000000000000AAA", "kind": "sandbox"}]}), encoding="utf-8")
    before = consent.read_bytes()
    cid = json.loads(invoke("change", "create", *scope, "--title", "T", "--outcome", "O", "--org", "alpha-sandbox",
                            "--json")[1])["id"]
    code, out, err = invoke("client", "set-org", *scope, "--target-org", "alpha-uat", "--json")
    assert code == 0, err
    result = json.loads(out)
    assert (result["from"], result["to"], result["org_calls"]) == ("alpha-sandbox", "alpha-uat", False)
    assert any("Consent does not approve alpha-uat" in note for note in result["notes"])
    assert any(cid in note and "plans the old org alpha-sandbox" in note for note in result["notes"])
    assert consent.read_bytes() == before                       # consent is never changed here
    config = json.loads((firm / "clients/alpha/client.json").read_text(encoding="utf-8"))
    assert config["org"] == "alpha-uat" and config["org_history"][-1]["from"] == "alpha-sandbox"
    context = invoke("context", *scope)[1]
    assert "Configured org: alpha-uat" in context and "Org alias changed from alpha-sandbox to alpha-uat" in context
    # the same alias again: nothing changes but the record, and the output says what an alias is
    same = json.loads(invoke("client", "set-org", *scope, "--target-org", "alpha-uat", "--json")[1])
    assert any("The alias is unchanged" in note for note in same["notes"])
    # a client that had no org
    none = json.loads(invoke("client", "set-org", "--workspace", firm, "--client", "beta", "--target-org",
                             "beta-sandbox", "--json")[1])
    assert none["from"] is None and none["notes"] == []
    assert "beta: Beta (org: beta-sandbox)" in invoke("client", "list", "--workspace", firm)[1]
    assert invoke("client", "set-org", *scope, "--target-org", "two words")[0] == 2


def test_a_client_can_be_added_without_an_org(firm):
    code, out, err = invoke("client", "add", "Gamma", "--workspace", firm, "--json")
    assert code == 0, err
    assert "gamma: Gamma (org: not configured)" in invoke("client", "list", "--workspace", firm)[1]
    assert add(firm, "--summary", "Discovery call notes", client="gamma")[0] == 0


# ---- step 9: first hour

def test_init_refuses_a_folder_with_other_files_explains_force_and_prints_next_steps(tmp_path, monkeypatch):
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "notes.txt").write_text("mine", encoding="utf-8")
    code, out, err = invoke("workspace", "init", busy, "--name", "Firm", "--no-hook")
    assert code == 2 and "already holds other files (notes.txt)" in err and "--force" in err
    assert sorted(p.name for p in busy.iterdir()) == ["notes.txt"]
    code, out, err = invoke("workspace", "init", busy, "--name", "Firm", "--no-hook", "--force")
    assert code == 0, err
    assert (busy / "notes.txt").read_text(encoding="utf-8") == "mine" and (busy / "workspace.json").is_file()
    steps = out.split("Next:", 1)[1]
    assert "1. torque client add CLIENT --workspace" in steps and "2. torque doctor" in steps
    assert "3. start your agent" in steps and "torque context --workspace . --client CLIENT" in steps
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    data = json.loads(invoke("workspace", "init", repo, "--name", "Firm", "--no-hook", "--json")[1])
    assert len(data["next_steps"]) == 3
    assert error_object(invoke("workspace", "init", busy / "..", "--name", "X", "--json")[1])["code"] == \
        "folder_not_empty"


def test_top_level_help_groups_the_commands_in_the_order_they_are_met():
    code, out, _ = invoke()          # `torque` alone prints the same help as --help
    assert code == 0
    headings = ["Start here:", "Daily records:", "Salesforce work:", "Lessons and other tools:",
                "Controls the owner runs:"]
    assert [out.index(h) for h in headings] == sorted(out.index(h) for h in headings)
    listed = [line.split()[0] for line in out.splitlines() if line.startswith("  ") and not line.startswith("  -")]
    names = set(cli.build_parser()._subparsers._group_actions[0].choices)
    assert sorted(listed) == sorted(names) and len(listed) == len(names)
    assert out.index("  workspace") < out.index("  session") < out.index("  deploy")
    assert "Each command has its own help: torque COMMAND --help." in out


def test_doctor_host_checks_are_read_only_and_say_resolves_and_clean_json_as_two_facts(monkeypatch):
    from torque import host_checks
    import subprocess
    calls = []

    def fake(answer_out, answer_err="", code=0):
        def run(argv, **options):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, code, answer_out, answer_err)
        return run
    monkeypatch.setattr(host_checks.shutil, "which", lambda name: "/stub/sf" if name == "sf" else None)
    clean = host_checks.report(fake('{"cliVersion": "2.140.0"}'))["salesforce_cli"]
    assert (clean["resolves"], clean["json_clean"]) == (True, True)
    noisy = host_checks.report(fake('Warning: @salesforce/cli update available from 2.1 to 2.2.\n{"v": 1}'))
    assert (noisy["salesforce_cli"]["resolves"], noisy["salesforce_cli"]["json_clean"]) == (True, False)
    text = host_checks.lines(noisy)
    assert "Salesforce CLI: resolves and answers --version" in text
    assert any(line.startswith("Salesforce CLI JSON output: NOT clean") for line in text)
    assert all(argv == ["/stub/sf", "--version", "--json"] for argv in calls)   # version only: no org, no update
    monkeypatch.setattr(host_checks.shutil, "which", lambda name: None)
    assert host_checks.report(fake(""))["salesforce_cli"]["resolves"] is False


# ---- step 6: errors an agent can act on

def error_object(out):
    value = json.loads(out)
    assert value["schema"] == "torque.error/1" and value["ok"] is False and value["exit_code"] != 0
    assert isinstance(value["code"], str) and isinstance(value["message"], str) and "next_command" in value
    return value


def test_argument_errors_name_the_subcommand_and_its_usage_in_text_and_json(firm):
    cases = [
        (["client", "add", "--workspace", firm, "--name", "Acme Food Bank", "--org", "acme-sandbox"],
         "torque client add", "unrecognized arguments: --name"),
        (["change", "create", "--workspace", firm, "--client", "alpha", "--outcome", "o"],
         "torque change create", "the following arguments are required: --title"),
        (["context", "--workspace", firm, "--client", "alpha", "--bogus"], "torque context",
         "unrecognized arguments: --bogus"),
    ]
    for args, prog, words in cases:
        code, out, err = invoke(*args)
        assert code == 2 and out == ""
        assert f"usage: {prog} " in err and f"{prog}: error: {words}" in err
        assert "{workspace,demo,client" not in err          # not the top-level usage
        code, out, err = invoke(*args, "--json")
        value = error_object(out)
        assert code == 2 and value["code"] == "usage" and value["usage"].startswith(f"usage: {prog} ")
        assert words in value["message"] and value["next_command"] == f"{prog} --help"
    assert "usage: torque client add" in invoke("client", "add", "--workspace", firm)[2]


def test_record_failures_come_back_as_one_json_object_and_as_plain_text_without_json(firm):
    code, out, err = add(firm, "--summary", " ", "--json")
    assert code == 2 and error_object(out)["code"] == "invalid_input" and "summary must be nonempty" in err
    code, out, err = add(firm, "--summary", " ")
    assert code == 2 and out == "" and err.startswith("torque: session summary must be nonempty")
    code, out, _ = invoke("session", "show", "20991231T000000000000Z-000000000000", "--workspace", firm, "--client",
                          "alpha", "--json")
    assert code == 2 and "not found" in error_object(out)["message"]
    code, out, _ = invoke("change", "check", "chg-000000000000", "--workspace", firm, "--client", "alpha",
                          "--criterion", "AC1", "--result", "pass", "--summary", "x", "--json")
    assert code == 2 and "no change" in error_object(out)["message"]


def test_a_sub_folder_and_an_unknown_client_are_two_different_messages(firm, monkeypatch):
    monkeypatch.chdir(firm / "clients")
    code, out, err = add(".", "--summary", "x")
    assert code == 2 and "this folder is not a Torque workspace" in err and f"the nearest one is {firm}" in err
    code, out, _ = add(".", "--summary", "x", "--json")
    value = error_object(out)
    assert value["code"] == "not_a_workspace" and str(firm) in value["next_command"]
    code, out, err = add(firm, "--summary", "x", client="alph")
    assert code == 2 and 'no client "alph" in this workspace' in err and "clients: alpha, beta." in err
    assert "cannot read valid local configuration" not in err
    value = error_object(add(firm, "--summary", "x", "--json", client="alph")[1])
    assert value["code"] == "unknown_client" and "client list" in value["next_command"]
    # Build-only and connected mode: a session works for one client and is not told the others.
    config = json.loads((firm / "workspace.json").read_text(encoding="utf-8"))
    (firm / "workspace.json").write_text(json.dumps({**config, "ai_access": "build-only"}), encoding="utf-8")
    err = add(firm, "--summary", "x", client="alph")[2]
    assert 'no client "alph"' in err and "beta" not in err and "client list" in err


def test_the_deploy_route_says_deploy_not_its_internal_name(firm):
    code, out, err = invoke("deploy", "--workspace", firm, "--metadata", "Flow:X", "--target-org", "x")
    assert code == 2 and err.startswith("torque: deploy needs a selected client") and "revert" not in err
    value = error_object(invoke("deploy", "--workspace", firm, "--metadata", "Flow:X", "--target-org", "x",
                                "--json")[1])
    assert value["code"] == "no_client_selected" and value["next_command"].startswith("torque deploy ")


def test_an_operating_system_error_is_said_in_words():
    long_path = "C:\\" + "\\".join(["d" * 60] * 5)
    error = OSError(2, "The system cannot find the path specified", long_path)
    text = cli._os_error_text(error)
    if os.name == "nt":
        assert text.startswith("a path is too long for Windows") and "long paths" in text
    else:
        assert text.startswith("a file or folder on this path does not exist")
    assert "does not exist: C:\\short" in cli._os_error_text(OSError(2, "x", "C:\\short"))


# ---- step 3: context that says what it left out

def write_records(root, count, client="alpha", incomplete=()):
    """`count` records, one a day from 2026-10-01; the ones in `incomplete` (by number) are incomplete."""
    folder = root / "clients" / client / "sessions"
    folder.mkdir(exist_ok=True)
    ids = []
    for number in range(count):
        day = f"202610{number + 1:02d}"
        entry_id = f"{day}T090000000000Z-{number:012x}"
        record = {"schema": "torque.session/1", "id": entry_id, "created_at": f"2026-10-{number + 1:02d}T09:00:00+00:00",
                  "client": client, "summary": f"record {number}",
                  "status": "incomplete" if number in incomplete else "executed",
                  "status_basis": "user_reported", "independently_verified": False, "evidence": None}
        (folder / f"{entry_id}.json").write_text(json.dumps(record), encoding="utf-8")
        ids.append(entry_id)
    return ids


def test_context_says_how_many_it_left_out_and_counts_older_open_records(firm):
    ids = write_records(firm, 23, incomplete=(1,))
    context = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--json")[1])
    assert (context["sessions_total"], context["sessions_returned"], context["sessions_truncated"]) == (23, 20, True)
    assert context["more_command"].endswith("--all") and "context" in context["more_command"]
    assert [s["id"] for s in context["sessions"]] == ids[::-1][:20]
    older = context["where_things_stand"]["open_not_shown"]
    assert older["count"] == 1 and older["oldest"].startswith("2026-10-02")
    text = invoke("context", "--workspace", firm, "--client", "alpha")[1]
    assert "History: 20 of 23 session record(s)" in text and "the rest: " in text and "--all" in text
    assert "Open, not shown: 1 older record(s) marked incomplete" in text
    assert text.index("Where things stand:") < text.index("Lessons") < text.index("History:")
    every = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--limit", "30", "--json")[1])
    assert (every["sessions_returned"], every["sessions_truncated"], every["more_command"]) == (23, False, None)
    assert every["where_things_stand"]["open"] == [ids[1]] and every["where_things_stand"]["open_not_shown"]["count"] == 0
    assert json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--all", "--json")[1])[
        "sessions_returned"] == 23
    since = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--since", "2026-10-20T00:00Z",
                              "--json")[1])
    assert [s["summary"] for s in since["sessions"]] == [f"record {n}" for n in range(22, 18, -1)]
    assert since["sessions_truncated"] is True
    assert invoke("context", "--workspace", firm, "--client", "alpha", "--since", "last week")[0] == 2
    assert invoke("context", "--workspace", firm, "--client", "alpha", "--limit", "0")[0] == 2


def test_an_open_record_that_was_corrected_is_not_counted_as_open(firm):
    ids = write_records(firm, 23, incomplete=(0, 1))
    fix = json.loads(add(firm, "--summary", "record 1 is done after all", "--corrects", ids[1], "--json")[1])
    context = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--json")[1])
    assert context["where_things_stand"]["open_not_shown"]["count"] == 1
    assert [(c["id"], c["corrects"], c["corrected_shown"]) for c in context["where_things_stand"]["corrections"]] \
        == [(fix["id"], ids[1], False)]
    assert f"Correction: {fix['id']} corrects {ids[1]}" in invoke("context", "--workspace", firm, "--client",
                                                                     "alpha")[1]


def test_notes_over_the_budget_are_all_listed_the_cut_ones_marked_and_the_total_stays_within(firm):
    alpha = firm / "clients/alpha"
    (alpha / "context.md").write_text("scope " * 100, encoding="utf-8")
    (alpha / "context/big1.md").write_text("x" * 70000, encoding="utf-8")
    (alpha / "context/big2.md").write_text("y" * 70000, encoding="utf-8")
    (alpha / "context/small.md").write_text("z" * 50, encoding="utf-8")
    paths = ["profile.md", "clients/alpha/context.md", "clients/alpha/context/big1.md",
             "clients/alpha/context/big2.md", "clients/alpha/context/small.md"]
    text = invoke("context", "--workspace", firm, "--client", "alpha")[1]
    assert len(text) <= ws.CONTEXT_BUDGET
    for path in paths:
        assert f"- {path}: " in text
    assert "clients/alpha/context/big1.md: 70,000 bytes, CUT at" in text
    assert "clients/alpha/context/big2.md: 70,000 bytes, NOT SHOWN (over the budget)" in text
    assert "Cut to fit 40,000 characters: notes (clients/alpha/context/big1.md, clients/alpha/context/big2.md" in text
    assert "--json" in text.splitlines()[-1]
    # JSON is complete (each file up to its own 65,536-character limit) unless a budget is given.
    context = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--json")[1])
    assert context["notes_budget"] is None and len(context["notes"]["Client context: big2.md"]) > 65536
    bounded = json.loads(invoke("context", "--workspace", firm, "--client", "alpha", "--max-chars", "24000",
                                "--json")[1])
    assert sum(len(text) for text in bounded["notes"].values()) <= 24000
    states = {item["path"]: item["state"] for item in bounded["notes_files"]}
    assert states == {"profile.md": "full", "clients/alpha/context.md": "full",
                      "clients/alpha/context/big1.md": "cut", "clients/alpha/context/big2.md": "not shown",
                      "clients/alpha/context/small.md": "not shown"}
    assert "read the rest in clients/alpha/context/big1.md" in bounded["notes"]["Client context: big1.md"]


def test_the_whole_context_text_stays_within_its_budget_and_names_every_cut_source(firm):
    alpha = firm / "clients/alpha"
    (alpha / "context.md").write_text("Scope and decisions. " * 2000, encoding="utf-8")
    (alpha / "context/decisions.md").write_text("A decision. " * 3000, encoding="utf-8")
    for number in range(4):
        add(firm, "--summary", f"lesson session {number}", "--learned-from", "consultant", "--learned",
            f"When: deploying flow number {number} Do: activate it after the deploy Because: a deploy leaves "
            f"version {number} inactive")
    folder = alpha / "sessions"
    for number in range(30):
        entry_id = f"202610{number % 28 + 1:02d}T{number:02d}0000000000Z-{number:012x}"
        (folder / f"{entry_id}.json").write_text(json.dumps({
            "schema": "torque.session/1", "id": entry_id, "created_at": f"2026-10-{number % 28 + 1:02d}T09:00:00+00:00",
            "client": "alpha", "summary": f"session {number}: " + "what was done and checked. " * 40,
            "status": "incomplete" if number % 7 == 0 else "executed", "status_basis": "user_reported",
            "independently_verified": False, "evidence": None}), encoding="utf-8")
    for budget in (None, "12000", "4000"):
        args = ["context", "--workspace", firm, "--client", "alpha", "--limit", "30"]
        code, text, err = invoke(*args, *(["--max-chars", budget] if budget else []))
        assert code == 0, err
        limit = int(budget) if budget else ws.CONTEXT_BUDGET
        assert len(text) <= limit, (budget, len(text))
        assert "Where things stand:" in text and "Lessons from earlier work" in text
        last = text.rstrip().splitlines()[-1]
        assert last.startswith(f"Cut to fit {limit:,} characters:") and "history (" in last and "notes (" in last
    assert invoke("context", "--workspace", firm, "--client", "alpha", "--max-chars", "500")[0] == 2


# ---- step 4: hand-off for the task in hand

def test_handoff_since_renders_that_window_current_state_first_and_re_export_replaces_the_file(firm):
    ids = write_records(firm, 23, incomplete=(2,))
    proof = firm / "clients/alpha/artifacts/proof.txt"
    proof.write_text("p", encoding="utf-8")
    latest = json.loads(add(firm, "--summary", "Latest: checked as staff", "--status", "verified", "--check",
                            "rendered", "--as-user", "staff user", "--evidence", proof, "--json")[1])
    code, body, err = invoke("handoff", "--workspace", firm, "--client", "alpha", "--since", "2026-10-22T00:00Z")
    assert code == 0, err
    assert "\nrecord 21\n" in body and "\nrecord 22\n" in body and "Latest: checked as staff" in body
    assert "\nrecord 20\n" not in body and "\nrecord 3\n" not in body
    assert body.index("## Where things stand") < body.index("## Lessons") < body.index("## History")
    assert body.index("Latest: checked as staff") < body.index("## History")
    assert "Open, not shown: 1 older record(s)" in body and "--all" in body
    assert "Evidence reference: clients/alpha/artifacts/proof.txt (in this workspace)" in body
    assert str(firm) not in body
    history = body.split("## History", 1)[1]
    assert history.index("Latest: checked as staff") < history.index("\nrecord 22\n") < history.index("\nrecord 21\n")
    target = firm / "clients/alpha/artifacts/handoff.md"
    assert invoke("handoff", "--workspace", firm, "--client", "alpha", "--output", target)[0] == 0
    first = target.read_text(encoding="utf-8")
    add(firm, "--summary", "One more record")
    code, out, err = invoke("handoff", "--workspace", firm, "--client", "alpha", "--output", target)
    assert code == 0, err
    assert "One more record" in target.read_text(encoding="utf-8")
    assert (firm / "clients/alpha/artifacts/handoff.md.bak").read_text(encoding="utf-8") == first
    assert "handoff.md.bak" in out
    assert not [p for p in target.parent.iterdir() if p.name.startswith(".torque-")]
    target.write_text("my own edits", encoding="utf-8")
    code, out, _ = invoke("handoff", "--workspace", firm, "--client", "alpha", "--output", target)
    assert code == 0 and "was edited since Torque wrote it" in out and "--force" in out
    assert target.read_text(encoding="utf-8") == "my own edits"
    assert "One more record" in (target.parent / "handoff.md.new").read_text(encoding="utf-8")
    assert invoke("handoff", "--workspace", firm, "--client", "alpha", "--output", target, "--force")[0] == 0
    assert (target.parent / "handoff.md.bak").read_text(encoding="utf-8") == "my own edits"
    assert "One more record" in target.read_text(encoding="utf-8")
    full = invoke("handoff", "--workspace", firm, "--client", "alpha", "--all")[1]
    assert all(f"record {n}\n" in full for n in range(23)) and "the full journal" in full


def test_handoff_json_and_the_change_filter(firm):
    write_records(firm, 3)
    created = json.loads(invoke("change", "create", "--workspace", firm, "--client", "alpha", "--title", "Contact pref",
                                "--outcome", "Staff record a preference", "--criterion", "Staff can save it",
                                "--json")[1])
    json.loads(invoke("change", "create", "--workspace", firm, "--client", "alpha", "--title", "Other change",
                      "--outcome", "Something else", "--json")[1])
    code, out, err = invoke("handoff", "--workspace", firm, "--client", "alpha", "--format", "json")
    assert code == 0, err
    data = json.loads(out)
    assert data["schema"] == "torque.handoff/1" and data["sessions_total"] == 3 and len(data["changes"]) == 2
    assert str(firm) not in out
    only = json.loads(invoke("handoff", "--workspace", firm, "--client", "alpha", "--change", created["id"],
                             "--json")[1])
    assert [c["id"] for c in only["changes"]] == [created["id"]] and only["sessions"] == []
    text = invoke("handoff", "--workspace", firm, "--client", "alpha", "--change", created["id"])[1]
    assert "Contact pref" in text and "Other change" not in text
    target = firm / "clients/alpha/artifacts/handoff.json"
    report = json.loads(invoke("handoff", "--workspace", firm, "--client", "alpha", "--format", "json", "--output",
                               target, "--json")[1])
    assert report["replaced"] is False and json.loads(target.read_text(encoding="utf-8"))["schema"] == "torque.handoff/1"
    assert invoke("handoff", "--workspace", firm, "--client", "alpha", "--change", "chg-000000000000")[0] == 2


def test_change_handoff_names_evidence_relative_to_the_workspace_and_can_be_re_exported(firm):
    created = json.loads(invoke("change", "create", "--workspace", firm, "--client", "alpha", "--title", "T",
                                "--outcome", "O", "--criterion", "C", "--json")[1])
    proof = firm / "clients/alpha/artifacts/run.txt"
    proof.write_text("ran", encoding="utf-8")
    assert invoke("change", "check", created["id"], "--workspace", firm, "--client", "alpha", "--criterion", "AC1",
                  "--result", "pass", "--summary", "ran", "--evidence", proof)[0] == 0
    target = firm / "clients/alpha/artifacts/change.md"
    for _ in range(2):
        code, _, err = invoke("change", "handoff", created["id"], "--workspace", firm, "--client", "alpha",
                              "--output", target)
        assert code == 0, err
    text = target.read_text(encoding="utf-8")
    assert f"`clients/alpha/changes/{created['id']}/evidence/" in text and str(firm) not in text
    assert (target.parent / "change.md.bak").is_file()


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
