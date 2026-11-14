"""Capture: structure, applicability from the text, admission, the scrub, and the same
lesson seen again."""
import pytest

from jsc_memory import intake, lesson as record, storage
from jsc_memory.lesson import LessonError

FLOW = ("When: you are about to compare a choice-based screen field in a flow formula. "
        "Do: compare the field to the choice resource in a decision. Because: the formula sees the label.")


def test_a_structured_capture_writes_the_documented_file(scopes):
    alpha = scopes("alpha")
    added = intake.add(alpha, when="you are about to deploy a new custom field",
                       do="grant field permissions in a permission set in the same deploy",
                       because="a field created through the Metadata API has no field access",
                       title="Ship field access with every new field", tasks=["Deploy", "field build"],
                       types=["customfield", "permission set"], commands=["sf project deploy *"],
                       keywords=["FLS"], evidence=["reproduced:session 20261112T1015"], source="agent")
    item = record.parse(added.path.read_text(encoding="utf-8"))
    assert added.status == "saved" and added.path == alpha.folder("client") / f"{item.id}.md"
    assert (item.scope, item.state, item.source, item.kind) == ("client", "active", "agent", "pitfall")
    assert item.applies == {"tasks": ["deploy", "field-build"], "types": ["CustomField", "PermissionSet"],
                            "commands": ["sf project deploy *"], "keywords": ["FLS"], "errors": []}
    assert item.evidence == [{"type": "reproduced", "ref": "session 20261112T1015", "date": record.today()}]
    assert item.created == item.last_verified == record.today()
    assert item.review_by == record.later(record.today(), 90)
    assert added.inferred == {}          # everything was given, nothing guessed


def test_labelled_free_text_becomes_when_do_because_and_applicability_is_inferred(scopes):
    added = intake.add(scopes("alpha"), text=FLOW, source="consultant", session="20261114T101500000000Z-abc")
    item = added.lesson
    assert item.when == "you are about to compare a choice-based screen field in a flow formula"
    assert item.do == "compare the field to the choice resource in a decision"
    assert item.because == "the formula sees the label"
    assert item.title == "Compare the field to the choice resource in a decision" and item.body == ""
    assert added.inferred["types"] == ["Flow"] and "flow-build" in added.inferred["tasks"]
    assert item.state == "active"       # the consultant's word admits it
    assert item.evidence == [{"type": "consultant-said", "ref": "session 20261114T101500000000Z-abc",
                              "date": record.today()}]


@pytest.mark.parametrize("text,parts", [
    ("**Why:** the label is compared. **How to apply:** use the choice resource.",
     {"do": "use the choice resource", "because": "the label is compared"}),
    ("Flows and choices. When: a formula reads a choice; do: compare in a decision",
     {"when": "a formula reads a choice", "do": "compare in a decision", "lead": "Flows and choices."}),
    ("Nothing labelled here: just a remark about what to do.", {}),
    ("Because: it broke.", {}),
])
def test_the_labels_a_text_may_carry(text, parts):
    assert intake.structure(text) == parts


def test_free_text_is_kept_as_the_body_with_a_title_from_its_first_sentence(scopes):
    added = intake.add(scopes("alpha"), text="always retrieve first. Then compare with the org.\nSecond line.")
    assert added.lesson.title == "Always retrieve first"
    assert added.lesson.body == "always retrieve first. Then compare with the org.\nSecond line."
    assert added.lesson.state == "candidate" and added.lesson.last_verified == ""
    assert added.lesson.evidence == [{"type": "session", "date": record.today()}]
    assert any("free text" in note for note in added.notes)


@pytest.mark.parametrize("fields", [{"text": ""}, {"text": "   \n\t "}, {"body": "  "}, {}])
def test_an_empty_capture_saves_nothing(scopes, fields):
    with pytest.raises(LessonError, match="nothing to save"):
        intake.add(scopes("alpha"), **fields)
    assert storage.load(scopes("alpha")) == []


def test_the_default_scope_is_the_selected_engagement_and_firm_is_explicit(workspace, scopes):
    assert intake.add(scopes("alpha"), text="a client note").lesson.scope == "client"
    assert intake.add(scopes("playbook"), text="an initiative note").lesson.scope == "initiative"
    assert intake.add(scopes("playbook"), text="named as client", scope="client").lesson.scope == "initiative"
    firm = intake.add(scopes("alpha"), text="retrieve before you change anything", scope="firm")
    assert firm.lesson.scope == "firm" and firm.path.parent == workspace / "lessons"
    assert intake.add(scopes(None), text="firm without a client", scope="firm").lesson.scope == "firm"
    with pytest.raises(LessonError, match="no client or initiative is selected"):
        intake.add(scopes(None), text="nowhere to go")
    with pytest.raises(LessonError, match="ship with Torque"):
        intake.add(scopes("alpha"), text="for everyone", scope="platform")
    with pytest.raises(LessonError, match="needs a workspace"):
        intake.add(storage.Scopes(), text="no workspace", scope="firm")


@pytest.mark.parametrize("source,evidence,state", [
    ("agent", [], "candidate"), ("agent", ["session:abc"], "candidate"), ("agent", ["consultant-said"], "candidate"),
    ("agent", ["reproduced"], "active"), ("doc", ["official-doc:https://help.example.com/a"], "active"),
    ("harvested", ["second-occurrence:two sessions"], "active"), ("consultant", [], "active"),
    ("doc", [], "candidate"),
])
def test_admission(scopes, source, evidence, state):
    added = intake.add(scopes("alpha"), text=f"lesson from {source} with {evidence}", source=source,
                       evidence=evidence)
    assert added.lesson.state == state


def test_bad_words_are_reported_before_anything_is_written(scopes):
    for fields, reason in (({"evidence": ["vibes"]}, "evidence type must be one of"),
                           ({"kind": "rant"}, "unknown kind"), ({"source": "nobody"}, "unknown source"),
                           ({"scope": "galaxy"}, "unknown scope")):
        with pytest.raises(LessonError, match=reason):
            intake.add(scopes("alpha"), text="a lesson", **fields)
    assert storage.load(scopes("alpha")) == []


def test_a_credential_is_refused_in_every_scope_and_nothing_is_written(scopes):
    secret = "Summer" + "2026!"
    for scope in (None, "firm"):
        with pytest.raises(intake.Refused) as caught:
            intake.add(scopes("alpha"), text=f"The integration user's password: {secret} works", scope=scope)
        assert secret not in str(caught.value) and "password or secret" in caught.value.found.refused
    with pytest.raises(intake.Refused):
        intake.add(scopes("playbook"), when="you sign in", do="use the header Authorization: Bearer "
                   + "eyJhbGciOiJIUzI1NiJ9abcDEF123456")
    assert storage.load(scopes("alpha")) == [] and storage.load(scopes("playbook")) == []


def test_a_firm_lesson_that_names_a_client_or_an_org_is_refused_without_printing_it(scopes):
    org_id = "00D" + "5g000004XyZa" + "EAA"
    for text in ("At Alpha Nonprofit the gift batch failed on deploy", f"The org {org_id} needs the permission set",
                 "Copy it from alpha-dev before the refresh"):
        with pytest.raises(intake.Refused) as caught:
            intake.add(scopes("alpha"), text=text, scope="firm")
        message = str(caught.value)
        assert "identifies a client" in message
        assert "Alpha" not in message and org_id not in message and "alpha-dev" not in message
    assert storage.load(scopes(None)) == []
    # The same text is the client's own business: saved there, with the Id masked.
    added = intake.add(scopes("alpha"), text=f"The org {org_id} needs the permission set for Alpha Nonprofit")
    assert added.lesson.body == "The org <org-id> needs the permission set for Alpha Nonprofit"
    assert added.masked["org Id"] >= 1


def test_the_same_lesson_again_is_not_a_second_file(scopes):
    alpha = scopes("alpha")
    first = intake.add(alpha, text=FLOW, session="s1")
    assert first.lesson.state == "candidate"
    same = intake.add(alpha, text=FLOW.upper(), session="s1")
    assert (same.status, same.lesson.id) == ("duplicate", first.lesson.id)
    assert len(storage.load(alpha)) == 1 and storage.load(alpha)[0].state == "candidate"
    # On another occasion it is a second occurrence: evidence that admits the candidate.
    again = intake.add(alpha, text=FLOW, session="s2")
    assert (again.status, again.lesson.id) == ("second-occurrence", first.lesson.id)
    stored = storage.load(alpha)
    assert len(stored) == 1 and stored[0].state == "active"
    assert [entry["type"] for entry in stored[0].evidence] == ["session", "second-occurrence"]
    assert intake.add(alpha, text=FLOW, session="s2").status == "duplicate"
    # Another client's copy of the same text is its own lesson.
    assert intake.add(scopes("beta"), text=FLOW, session="s1").status == "saved"


def test_a_similar_lesson_is_pointed_out_and_still_saved(scopes):
    alpha = scopes("alpha")
    first = intake.add(alpha, when="you compare a choice field in a flow formula", do="compare it to the choice resource")
    second = intake.add(alpha, when="you compare a choice field in a screen flow formula",
                        do="compare it to the choice resource in a decision")
    assert second.status == "saved" and any(first.lesson.id in note for note in second.notes)
    assert len(storage.load(alpha)) == 2


def test_what_a_session_taught_is_saved_and_a_failure_is_reported_not_raised(scopes):
    alpha = scopes("alpha")
    saved, problems = intake.learned(alpha, [FLOW, "   ", "the password: " + "Wint3r" + "!Pass99 is in the note",
                                             "retrieve first"], source="agent", session="sess-1")
    assert [item["status"] for item in saved] == ["saved", "saved"]
    assert [problem["learned"] for problem in problems] == [2, 3]
    assert "nothing to save" in problems[0]["reason"] and "credential-looking" in problems[1]["reason"]
    assert "password or secret" in problems[1]["refused"] and "Wint3r" not in str(problems)
    stored = storage.load(alpha)
    assert len(stored) == 2 and all(item.evidence[0]["ref"] == "session sess-1" for item in stored)
    firm, _ = intake.learned(alpha, ["retrieve before you change anything"], scope="firm", source="consultant")
    assert firm[0]["scope"] == "firm" and firm[0]["state"] == "active"


def test_a_host_note_gives_its_description_and_body():
    meta, body = intake.host_note("---\nname: flows-by-metadata\ndescription: Build flows through metadata\n"
                                  "metadata:\n  type: feedback\n---\nThe rule.\n\n**Why:** it is reviewable.\n")
    assert meta == {"name": "flows-by-metadata", "description": "Build flows through metadata", "type": "feedback"}
    assert body == "The rule.\n\n**Why:** it is reviewable."
    assert intake.host_note("no front matter here") == ({}, "no front matter here")
    assert intake.host_note("---\n: [broken\n---\nbody")[0] == {}
