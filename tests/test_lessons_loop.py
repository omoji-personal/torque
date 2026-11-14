"""The lessons loop through `torque` itself: a lesson captured with a session record is
found again at the point of use, for its own client only."""
import json

import pytest

from torque import cli, workspace as ws

FLOW = ("When: you are about to compare a choice-based screen field in a flow formula. "
        "Do: compare the field to the choice resource in a decision. Because: the formula sees the label.")
FIRM = ("When: you deploy a new custom field. Do: ship field permissions in a permission set in the same deploy. "
        "Because: a field created through the Metadata API has no field access.")


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in ("TORQUE_WORKSPACE", "JSC_ROOT", "TORQUE_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    root = ws.init_workspace(tmp_path / "workspace", "Synthetic workspace")
    ws.add_client(root, "Alpha Nonprofit", "alpha-dev")
    ws.add_client(root, "Beta Land Trust")
    from torque import engagements
    engagements.add_initiative(root, "Internal Playbook")
    return root


@pytest.fixture
def torque(root, capsys):
    """torque("lesson", "for", "--type", "Flow", client="alpha-nonprofit") -> (code, out, err)."""
    def call(*args, client="alpha-nonprofit", initiative=None):
        scope = ["--workspace", str(root)]
        scope += ["--initiative", initiative] if initiative else (["--client", client] if client else [])
        try:
            code = cli.main([*args, *scope])
        except SystemExit as exc:
            code = exc.code
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return call


def test_a_lesson_saved_with_a_session_comes_back_for_its_client_and_never_for_another(torque, root):
    code, out, _ = torque("session", "add", "--summary", "Fixed the gift entry flow", "--status", "executed",
                          "--learned", FLOW, "--learned-from", "consultant", "--json")
    entry = json.loads(out)
    assert code == 0 and entry["lesson_problems"] == []
    lesson = entry["lessons"][0]
    assert (lesson["status"], lesson["state"], lesson["scope"]) == ("saved", "active", "client")
    assert lesson["applies"]["types"] == ["Flow"]
    saved = (root / "clients" / "alpha-nonprofit" / "lessons" / f"{lesson['id']}.md").read_text(encoding="utf-8")
    assert f"session {entry['id']}" in saved and "type: consultant-said" in saved
    # The session record itself is unchanged in shape: a plain torque.session/1 record.
    stored = json.loads((root / "clients" / "alpha-nonprofit" / "sessions" / f"{entry['id']}.json").read_text())
    assert "lessons" not in stored and stored["summary"] == "Fixed the gift entry flow"

    code, out, _ = torque("session", "add", "--summary", "Field work", "--learned", FIRM, "--learned-scope", "firm",
                          "--learned-from", "consultant")
    assert code == 0 and "(firm, active): Ship field permissions in a permission set in the same deploy" in out

    # 1. torque context for the client shows it, in text and in JSON.
    code, out, _ = torque("context")
    assert code == 0 and "Lessons from earlier work: 1 of 1 for this client (firm 1, platform " in out
    assert f"[{lesson['id']}] WHEN you are about to compare a choice-based screen field in a flow formula" in out
    assert "torque lesson for TOPIC --workspace" in out and "--client alpha-nonprofit" in out
    context = json.loads(torque("context", "--json")[1])["lessons"]
    assert [item["id"] for item in context["shown"]] == [lesson["id"]]
    assert (context["engagement_active"], context["firm_active"], context["candidates_waiting"]) == (1, 1, 0)
    # 2. It is found by metadata type.
    code, out, _ = torque("lesson", "for", "--type", "Flow", "--json")
    found = json.loads(out)["lessons"]
    assert code == 0 and found[0]["id"] == lesson["id"] and found[0]["scope"] == "client"
    # 3. Never for the other client: not in context, not in recall, not by id, not in the listing.
    other = torque("context", client="beta-land-trust")[1]
    assert lesson["id"] not in other and "choice" not in other
    assert "Lessons: none active for this client yet (firm 1, platform " in other
    listed = json.loads(torque("lesson", "for", "--type", "Flow", "--limit", "50", "--json", client="beta-land-trust")[1])
    assert lesson["id"] not in [item["id"] for item in listed["lessons"]]
    code, out, err = torque("lesson", "show", lesson["id"], client="beta-land-trust")
    assert code == 1 and "choice" not in out + err
    assert lesson["id"] not in torque("lesson", "list", "--json", client="beta-land-trust")[1]
    # 4. The firm lesson appears for both clients and for the initiative.
    for selection in ({"client": "alpha-nonprofit"}, {"client": "beta-land-trust"}, {"initiative": "internal-playbook"}):
        found = json.loads(torque("lesson", "for", "--type", "CustomField", "--json", **selection)[1])["lessons"]
        assert found[0]["scope"] == "firm" and found[0]["do"].startswith("ship field permissions")


def test_what_the_agent_learned_waits_as_a_candidate_and_is_counted(torque):
    code, out, _ = torque("session", "add", "--summary", "Tried a deploy", "--learned",
                          "The deploy needs the permission set first")
    assert code == 0 and "(client, candidate)" in out and "torque lesson review" in out
    context = json.loads(torque("context", "--json")[1])["lessons"]
    assert context["shown"] == [] and context["candidates_waiting"] == 1
    text = torque("context")[1]
    assert "1 candidate lesson(s) wait for review: torque lesson review" in text
    code, out, _ = torque("lesson", "for", "deploy", "permission", "--json")
    answer = json.loads(out)
    assert all(item["scope"] == "platform" for item in answer["lessons"]) and answer["candidates_not_shown"] == 1
    shown = json.loads(torque("lesson", "for", "deploy", "permission", "--include-candidates", "--json")[1])
    assert shown["lessons"][0]["state"] == "candidate"


def test_a_lesson_that_cannot_be_saved_never_loses_the_session_record(torque, root):
    secret = "Wint3r" + "!Pass99"
    code, out, err = torque("session", "add", "--summary", "Rotated the integration user", "--learned",
                            f"The integration user's password: {secret}", "--learned", FLOW, "--json")
    entry = json.loads(out)
    assert code == 4 and secret not in out + err
    assert "is recorded; do not add it again" in err and f"--session {entry['id']}" in err
    assert [problem["learned"] for problem in entry["lesson_problems"]] == [1]
    assert len(entry["lessons"]) == 1
    sessions = json.loads(torque("session", "list", "--json")[1])
    assert [item["id"] for item in sessions] == [entry["id"]]
    # Without --learned nothing about lessons is added to the answer.
    plain = json.loads(torque("session", "add", "--summary", "Plain", "--json")[1])
    assert "lessons" not in plain and "lesson_problems" not in plain


def test_lessons_work_for_an_initiative_as_for_a_client(torque, root):
    code, out, _ = torque("session", "add", "--summary", "Wrote the playbook", "--learned", FLOW,
                          "--learned-from", "consultant", initiative="internal-playbook")
    assert code == 0 and "(initiative, active)" in out
    assert list((root / "initiatives" / "internal-playbook" / "lessons").glob("L-*.md"))
    assert "for this initiative" in torque("context", initiative="internal-playbook")[1]
    code, out, _ = torque("lesson", "add", "always retrieve first", "--from", "consultant",
                          initiative="internal-playbook")
    assert code == 0 and "(initiative, active)" in out
    listed = torque("lesson", "list", initiative="internal-playbook")[1]
    assert "Always retrieve first" in listed and "initiative internal-playbook + firm + platform" in listed
    assert "Always retrieve first" not in torque("lesson", "list")[1]


def test_the_handoff_carries_the_same_short_block(torque, root):
    torque("session", "add", "--summary", "Fixed the flow", "--learned", FLOW, "--learned-from", "consultant")
    code, out, _ = torque("handoff")
    assert code == 0 and "## Lessons" in out and "WHEN you are about to compare a choice-based screen field" in out
    assert out.index("## Lessons") < out.index("## Working context")
    # A handoff travels: it names the workspace as `.`, never the author's folder.
    assert "torque lesson for TOPIC --workspace . --client alpha-nonprofit" in out and str(root) not in out
    # A handoff is a document, not a point of use: rendering it shows nothing to an agent.
    counters = json.loads(torque("lesson", "list", "--json")[1])["lessons"][0]["counters"]
    assert counters["shown"] == 0
    torque("context")
    assert json.loads(torque("lesson", "list", "--json")[1])["lessons"][0]["counters"]["shown"] == 1


def test_scope_selection_on_the_lesson_route(torque, root, monkeypatch):
    assert torque("lesson", "for", "flow", client=None)[0] == 0                 # firm and platform
    monkeypatch.chdir(root.parent)
    assert cli.main(["lesson", "for", "flow"]) == 0                             # no workspace: platform only
    for words in (["lesson", "add", "a lesson"], ["lesson", "capture", "--", "--help"],
                  ["lesson", "helpful", "L-20261114-aaaa"]):
        assert cli.main(words) == 2                                             # these need a workspace
    code, _, err = torque("lesson", "list", "--initiative", "internal-playbook")
    assert code == 2 and "not both" in err
    assert cli.main(["lesson", "list", "--initiative", "internal-playbook"]) == 2
    assert cli.main(["lesson", "list", "--workspace", str(root), "--initiative", "no-such-initiative"]) == 2
    # The other delegated routes are still for clients only.
    assert cli.main(["qa", "run", "x", "--workspace", str(root), "--initiative", "internal-playbook"]) == 2


FLOW_DEPLOY = ["--metadata", "Flow:Gift_Entry", "--target-org", "example-dev"]


def test_shipped_lessons_and_the_advisory_catalogue_both_answer_a_flow_deploy(torque):
    code, out, _ = torque("lesson", "for", "--command", "sf project deploy start " + " ".join(FLOW_DEPLOY),
                          "--limit", "10", "--json", client=None)
    found = json.loads(out)["lessons"]
    assert code == 0 and all(item["scope"] == "platform" for item in found)
    origins = {item["origin"] for item in found}
    assert origins == {"shipped", "advisory"}
    assert "A-flow-activation-on-deploy" in [item["id"] for item in found]
    shipped = [item for item in found if item["origin"] == "shipped"]
    assert any("Flow" in item["applies"]["types"] for item in shipped)
    # Every shipped lesson reads as a platform lesson with honest evidence and a review date.
    listed = json.loads(torque("lesson", "list", "--scope", "platform", "--json", client=None)[1])["lessons"]
    seeds = [item for item in listed if item["origin"] == "shipped"]
    assert len(seeds) >= 12 and all(item["state"] == "active" and item["review_by"] for item in seeds)
    assert all(item["evidence_type"] in ("official-doc", "session", "reproduced") for item in seeds)
    assert sum(bool(item["applies"]["errors"]) for item in seeds) >= 4
    # An error just met finds what fixed it before, also among the shipped lessons.
    code, out, _ = torque("lesson", "for", "--error", "No such column 'Region__c' on entity 'Account'", client=None)
    assert code == 0 and "WHEN a query fails on an unknown field or relationship name" in out


def test_deploy_and_data_print_matching_lessons_before_the_step_and_never_fail(torque, root, monkeypatch, capsys):
    from jsc_revert import cli as revert_cli
    from jsc_memory import recall
    assert torque("lesson", "add", "--when", "you deploy a flow for this client", "--do",
                  "read the active version back", "--because", "a deploy can leave it inactive", "--from",
                  "consultant", "--json")[0] == 0
    before_step: list[str] = []
    monkeypatch.setattr(revert_cli, "cmd_deploy", lambda args: before_step.append(capsys.readouterr().err) or 0)
    monkeypatch.setattr(revert_cli, "cmd_data_update", lambda args: before_step.append(capsys.readouterr().err) or 7)
    assert torque("deploy", *FLOW_DEPLOY)[0] == 0
    printed = before_step[0]
    assert "WHEN you deploy a flow for this client" in printed and printed.rstrip().endswith(recall.FOOTER)
    assert printed.count("\n[") + printed.startswith("[") <= 3
    events = (root / "clients" / "alpha-nonprofit" / ".torque" / "lessons" / "events.jsonl").read_text("utf-8")
    assert '"via": "deploy"' in events and "example-dev" not in events
    # A data change gets the catalogue's notes; the record's values are never matched, shown or logged.
    code, _, _ = torque("data", "update", "--target-org", "example-dev", "--sobject", "Account", "--record-id",
                        "001A000001AbCdEFGH", "--values", "Name=Flow Deploy Secretperson")
    assert code == 7 and "[A-" in before_step[1] and "Secretperson" not in before_step[1]
    assert "Flow" not in before_step[1].splitlines()[0]          # the values did not steer the match
    logs = "".join(path.read_text("utf-8") for path in root.rglob("events.jsonl"))
    assert "Secretperson" not in logs and '"via": "data"' in logs
    # No lessons, or a failure inside the lessons code: the operation runs all the same.
    monkeypatch.setattr(recall, "find", lambda *args, **more: (_ for _ in ()).throw(RuntimeError("boom")))
    assert torque("deploy", *FLOW_DEPLOY)[0] == 0 and before_step[2] == ""
    monkeypatch.setattr("jsc_memory.recall.for_command", lambda *args, **more: (_ for _ in ()).throw(OSError("x")))
    assert torque("deploy", *FLOW_DEPLOY)[0] == 0 and len(before_step) == 4


def test_advisory_notes_add_scoped_lessons_under_their_own_key(torque, root):
    torque("lesson", "add", "--when", "you deploy a flow for this client", "--do", "read the active version back",
           "--from", "consultant")
    command = "sf project deploy start --metadata Flow:Gift_Entry"
    code, out, _ = torque("advisory", "notes", "--command", command, "--limit", "4", "--json")
    answer = json.loads(out)
    assert code == 0 and set(answer) == {"platform_notes", "scoped_lessons"}
    assert answer["platform_notes"] and all("remedy" in note for note in answer["platform_notes"])
    scoped = answer["scoped_lessons"]
    assert scoped[0]["scope"] == "client" and scoped[0]["when"] == "you deploy a flow for this client"
    assert {item["scope"] for item in scoped} <= {"client", "firm", "platform"}
    assert not any(item["id"].startswith("A-") for item in scoped)          # the catalogue is the other key
    text = torque("advisory", "notes", "--command", command)[1]
    assert "remedy :" in text and "WHEN you deploy a flow for this client" in text
    # Without a selected client the answer keeps its earlier shape: the list of notes.
    code, out, _ = torque("advisory", "notes", "--command", command, "--json", client=None)
    assert code == 0 and isinstance(json.loads(out), list)
    # Another client gets its own answer, never this client's lesson.
    other = json.loads(torque("advisory", "notes", "--command", command, "--json", client="beta-land-trust")[1])
    assert "you deploy a flow for this client" not in json.dumps(other)


def test_one_unreadable_lesson_file_breaks_no_command_and_doctor_names_it(torque, root):
    torque("session", "add", "--summary", "Fixed the flow", "--learned", FLOW, "--learned-from", "consultant")
    folder = root / "clients" / "alpha-nonprofit" / "lessons"
    (folder / "handedited.md").write_text("---\nid: [broken\n---\n", encoding="utf-8")
    (root / "lessons").mkdir()
    (root / "lessons" / "notes.md").write_text("just some notes, not a lesson\n", encoding="utf-8")
    good = next(folder.glob("L-*.md")).stem
    for words in (("context",), ("context", "--json"), ("handoff",), ("lesson", "for", "--type", "Flow"),
                  ("lesson", "list"), ("lesson", "show", good), ("lesson", "search", "choice"), ("lesson", "review"),
                  ("lesson", "stats"), ("lesson", "helpful", good), ("lesson", "verify", good),
                  ("lesson", "edit", good, "--set", "kind=fact"), ("lesson", "promote", good, "--to", "rule"),
                  ("lesson", "stale", good, "--reason", "testing"), ("lesson", "restore", good),
                  ("session", "add", "--summary", "More", "--learned", "When: x happens. Do: y.")):
        code, out, err = torque(*words)
        assert code == 0, (words, err)
    text = torque("context")[1]
    assert "Skipped 2 lesson file(s) that could not be read" in text
    assert "clients/alpha-nonprofit/lessons/handedited.md (the front matter is not valid YAML" in text
    assert "handedited.md" in torque("handoff")[1] and "handedited.md" in torque("lesson", "list")[1]
    code, out, _ = torque("doctor")
    assert code in (0, 3) and "Next: 2 lesson file(s) could not be read and are skipped everywhere" in out
    assert "clients/alpha-nonprofit/lessons/handedited.md" in out and "lessons/notes.md" in out
    report = json.loads(torque("doctor", "--json")[1])["lessons"]
    assert [problem["path"] for problem in report["engagement"]["unreadable"]] == [
        "clients/alpha-nonprofit/lessons/handedited.md"]
    assert [problem["path"] for problem in report["firm"]["unreadable"]] == ["lessons/notes.md"]
    # Another client's doctor does not read this client's folder: it names the firm's file only.
    other = json.loads(torque("doctor", "--json", client="beta-land-trust")[1])["lessons"]
    assert other["engagement"]["unreadable"] == [] and len(other["firm"]["unreadable"]) == 1


def test_doctor_prints_the_lessons_lines_and_the_selfcheck_passes(torque, root):
    code, out, _ = torque("lesson", "selfcheck", client=None)
    assert code == 0 and "Lessons self-check: passed" in out
    assert cli.main(["lesson", "selfcheck"]) == 0                 # also without any workspace
    code, out, _ = torque("doctor")
    assert code in (0, 3)
    assert "Lessons (firm): 0 candidate, 0 active, 0 retired; unreadable files 0; last saved never" in out
    assert "Lessons (client alpha-nonprofit): 0 candidate, 0 active, 0 retired" in out
    assert "Lessons (platform): " in out and "Lessons loop: idle" in out
    assert "Lessons self-check: passed" in out
    torque("session", "add", "--summary", "Fixed the flow", "--learned", FLOW, "--learned-from", "consultant")
    torque("context")
    out = torque("doctor")[1]
    assert "Lessons (client alpha-nonprofit): 0 candidate, 1 active, 0 retired" in out
    assert "Lessons loop: live (a lesson was saved or shown in the last 30 days)" in out
    report = json.loads(torque("doctor", "--json")[1])["lessons"]
    assert report["loop"] == "live" and report["selfcheck"]["passed"] is True
    assert report["engagement"]["last_shown"] is not None and report["platform"]["shipped"] >= 12
    # Without a workspace doctor still checks the wiring and the shipped lessons.
    plain = json.loads(run_doctor_without_workspace())["lessons"]
    assert plain["firm"] is None and plain["selfcheck"]["passed"] is True


def run_doctor_without_workspace() -> str:
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["doctor", "--json"])
    return out.getvalue()
