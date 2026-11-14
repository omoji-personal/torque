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
