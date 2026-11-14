"""Judging a lesson: helpful, stale, verify, retire, restore, edit; and the review report."""
import json

import pytest

from jsc_memory import cli, feedback, intake, lesson as record, recall, review, storage
from jsc_memory.lesson import Lesson, LessonError

FLOW = ("When: you compare a choice-based screen field in a flow formula. Do: compare the field to the choice "
        "resource in a decision. Because: the formula sees the label.")


@pytest.fixture
def run(workspace, monkeypatch, capsys):
    def call(*args, client="alpha"):
        folder = workspace if client is None else workspace / "clients" / client
        monkeypatch.setenv("TORQUE_WORKSPACE", str(folder))
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return call


def snapshot(workspace):
    """Every lesson file and events log under the workspace, to prove nothing changed."""
    return {str(path.relative_to(workspace)): path.read_bytes() for path in workspace.rglob("*")
            if path.is_file() and (path.name == "events.jsonl" or path.suffix == ".md" and ".torque" not in path.parts)}


def flow_ids(scopes):
    return [hit.lesson.id for hit in recall.find(scopes, types=["Flow"], limit=None).hits
            if hit.lesson.scope != "platform"]


@pytest.mark.parametrize("command", [("helpful",), ("stale", "--reason", "x"), ("verify",), ("retire", "--reason", "x"),
                                     ("restore",), ("edit", "--set", "title=x")])
@pytest.mark.parametrize("wanted,code", [("", 3), ("   ", 3), ("L-", 3), ("0", 3), ("L-2020", 3),
                                         ("L-20991231-zzzz", 1)])
def test_an_empty_ambiguous_or_unknown_id_changes_nothing_and_exits_nonzero(run, workspace, scopes, command,
                                                                            wanted, code):
    for suffix in ("ab01", "ab02"):
        storage.save(Lesson(id=f"L-20200101-{suffix}", title=f"Lesson {suffix}", state="active",
                            created="2020-01-01"), scopes("alpha").folder("client"))
    before = snapshot(workspace)
    name, *options = command
    status, out, err = run(name, wanted, *options, "--json")
    assert status == code and snapshot(workspace) == before
    answer = json.loads(out)
    assert answer["exit_code"] == code and "torque lesson:" in err
    if wanted == "L-2020":
        assert "nothing was changed" in err and len(answer["candidates"]) == 2


def test_helpful_counts_and_shows_in_listings(run, scopes):
    alpha = scopes("alpha")
    item = intake.add(alpha, text=FLOW, source="consultant").lesson
    for _ in range(2):
        code, out, _ = run("helpful", item.id, "--note", "saved a failed deploy")
    assert code == 0 and out.startswith(f"Marked helpful: {item.id}") and "helpful 2" in out
    assert feedback.tally_of(alpha, item).counters() == {"shown": 0, "helpful": 2, "stale": 0}
    assert "helpful 2" in run("list")[1] and "helpful 2" in run("show", item.id)[1]
    events = storage.read_events(alpha.engagement)
    assert events[0]["note"] == "saved a failed deploy" and "choice" not in json.dumps(events)
    # The lesson's file is not touched by feedback: counters are derived, never stored.
    assert "helpful" not in item.path.read_text(encoding="utf-8")


def test_one_stale_mark_with_a_reason_takes_a_lesson_out_of_recall_at_once_and_restore_brings_it_back(run, scopes):
    alpha = scopes("alpha")
    item = intake.add(alpha, text=FLOW, source="consultant").lesson
    for _ in range(3):
        feedback.helpful(alpha, item.id)                      # helpful marks do not shield a wrong lesson
    assert flow_ids(alpha) == [item.id]
    assert run("stale", item.id)[0] == 2                      # a reason is required
    code, out, _ = run("stale", item.id, "--reason", "the formula now sees the value")
    assert code == 0 and "out of recall from now on" in out and f"torque lesson restore {item.id}" in out
    assert flow_ids(alpha) == []
    assert recall.context_section(alpha)["shown"] == [] and recall.context_section(alpha)["marked_stale"] == 1
    stored = storage.load(alpha)[0]
    assert stored.state == "active"                           # the mark is in the log; the state is unchanged
    listed = run("list", "--state", "stale")[1]
    assert item.id in listed and " stale " in listed
    assert "marked stale" in run("show", item.id)[1] and "the formula now sees the value" in run("show", item.id)[1]
    code, out, _ = run("restore", item.id)
    assert code == 0 and "the stale mark is cleared" in out and flow_ids(alpha) == [item.id]
    assert "nothing to restore" in run("restore", item.id)[1]
    # verify clears a mark as well.
    feedback.stale(alpha, item.id, "check again")
    assert flow_ids(alpha) == []
    feedback.verify(alpha, item.id)
    assert flow_ids(alpha) == [item.id]


def test_a_stale_lesson_can_name_the_one_that_replaces_it(scopes):
    alpha = scopes("alpha")
    old = intake.add(alpha, text="Old advice about flows and formulas", source="consultant").lesson
    new = intake.add(alpha, text=FLOW, source="consultant").lesson
    item, newer = feedback.stale(alpha, old.id, "replaced", superseded_by=new.id)
    assert newer.id == new.id and storage.resolve(storage.load(alpha), new.id).supersedes == [old.id]
    assert feedback.tally_of(alpha, old).stale_mark["superseded_by"] == new.id
    with pytest.raises(LessonError, match="cannot supersede itself"):
        feedback.stale(alpha, new.id, "x", superseded_by=new.id)
    with pytest.raises(storage.NotFound):
        feedback.stale(alpha, new.id, "x", superseded_by="L-20991231-zzzz")
    assert feedback.tally_of(alpha, new).stale_mark is None


def test_verify_dates_a_lesson_and_admits_a_candidate_only_on_a_basis(run, scopes):
    alpha = scopes("alpha")
    candidate = intake.add(alpha, text=FLOW).lesson
    assert candidate.state == "candidate" and flow_ids(alpha) == []
    code, _, err = run("verify", candidate.id)
    assert code == 2 and "becomes active on a basis" in err and storage.load(alpha)[0].state == "candidate"
    assert run("verify", candidate.id, "--evidence", "session:only a session")[0] == 2
    code, out, _ = run("verify", candidate.id, "--evidence", "consultant-said", "--json")
    answer = json.loads(out)
    assert code == 0 and (answer["state"], answer["outcome"]) == ("active", "now active")
    stored = storage.load(alpha)[0]
    assert stored.last_verified == record.today() and stored.review_by == record.later(record.today())
    assert [entry["type"] for entry in stored.evidence] == ["session", "consultant-said"]
    assert flow_ids(alpha) == [candidate.id]
    assert run("verify", candidate.id, "--evidence", "reproduced:the sandbox run of today")[0] == 0
    assert storage.load(alpha)[0].evidence[-1] == {"type": "reproduced", "ref": "the sandbox run of today",
                                                   "date": record.today()}
    assert run("verify", candidate.id, "--evidence", "vibes")[0] == 2


def test_retire_keeps_the_file_and_restore_returns_it_to_what_its_evidence_supports(run, scopes):
    alpha = scopes("alpha")
    active = intake.add(alpha, text=FLOW, source="consultant").lesson
    waiting = intake.add(alpha, text="An unreviewed remark about flows").lesson
    for item in (active, waiting):
        assert run("retire", item.id, "--reason", "no longer how we work")[0] == 0
    assert {item.state for item in storage.load(alpha)} == {"retired"} and active.path.is_file()
    assert flow_ids(alpha) == [] and recall.context_section(alpha)["candidates_waiting"] == 0
    assert run("helpful", active.id)[0] == 2 and run("verify", active.id)[0] == 2      # restore it first
    assert "Retired" not in run("list", "--state", "active")[1] and active.id in run("list", "--state", "retired")[1]
    assert "restored as active" in run("restore", active.id)[1]
    assert "restored as candidate" in run("restore", waiting.id)[1]
    assert flow_ids(alpha) == [active.id]


def test_edit_changes_fields_validates_them_and_scrubs_them(run, scopes):
    alpha = scopes("alpha")
    item = intake.add(alpha, text="always retrieve first", source="consultant").lesson
    code, out, _ = run("edit", item.id, "--set", "when=you are about to change a component", "--set",
                       "do=retrieve it from the org first", "--set", "applies.types=flow, CustomField", "--set",
                       "applies.errors=No such column", "--set", "kind=convention", "--set", "review_by=2027-06-30")
    assert code == 0 and "edit the file" in out
    stored = storage.load(alpha)[0]
    assert (stored.when, stored.do, stored.kind, stored.review_by) == (
        "you are about to change a component", "retrieve it from the org first", "convention", "2027-06-30")
    assert stored.applies["types"] == ["Flow", "CustomField"] and stored.applies["errors"] == ["No such column"]
    assert stored.body == "always retrieve first" and stored.created == item.created
    before = stored.path.read_bytes()
    for bad, code in ((("--set", "state=sleeping"), 2), (("--set", "id=L-20261114-0000"), 2), (("--set", "scope=firm"), 2),
                      (("--set", "review_by=soon"), 2), (("--set", "title="), 2), ((), 2), (("--set", "novalue"), 2),
                      (("--set", "do=use the password: " + "Wint3r" + "!Pass99"), 4)):
        assert run("edit", item.id, *bad)[0] == code
    assert stored.path.read_bytes() == before
    assert run("edit", item.id, "--set", "do=mail jane@example.org")[0] == 0
    assert storage.load(alpha)[0].do == "mail <email>"


def test_a_platform_lesson_is_judged_here_but_never_rewritten(run, workspace, scopes):
    note = "A-flow-activation-on-deploy"
    assert note in [hit.lesson.id for hit in recall.find(scopes("alpha"), types=["Flow"], limit=None).hits]
    assert run("helpful", note)[0] == 0
    code, out, _ = run("stale", note, "--reason", "not true in our orgs any more")
    assert code == 0
    assert note not in [hit.lesson.id for hit in recall.find(scopes("alpha"), types=["Flow"], limit=None).hits]
    # The mark lives in the workspace's log, so it holds for every client of this workspace.
    assert note not in [hit.lesson.id for hit in recall.find(scopes("beta"), types=["Flow"], limit=None).hits]
    assert storage.read_events(workspace)[-1]["id"] == note and storage.read_events(scopes("alpha").engagement) == []
    for command in (("retire", note, "--reason", "x"), ("edit", note, "--set", "title=x")):
        code, _, err = run(*command)
        assert code == 5 and "ships with Torque" in err and "torque lesson stale" in err
    assert "recorded for this workspace" in run("verify", note)[1]
    assert note in [hit.lesson.id for hit in recall.find(scopes("alpha"), types=["Flow"], limit=None).hits]
    # A reason about a platform lesson goes to the firm's log, so it may not identify a client.
    code, _, err = run("stale", note, "--reason", "Alpha Nonprofit does it differently")
    assert code == 4 and "Alpha" not in err


def test_the_review_reports_and_changes_nothing(run, workspace, scopes, monkeypatch):
    alpha = scopes("alpha")
    waiting = intake.add(alpha, text="An unreviewed remark about data loads").lesson
    one = intake.add(alpha, when="you compare a choice field in a flow formula", do="compare it to the choice resource",
                     source="consultant").lesson
    twin = intake.add(alpha, when="you compare a choice field in a screen flow formula",
                      do="compare it to the choice resource in a decision", source="consultant").lesson
    named = intake.add(alpha, when="you deploy for Alpha Nonprofit", do="use the alpha-dev sandbox first, on Gift__c",
                       source="consultant").lesson
    overdue = intake.add(alpha, when="you load a CSV", do="check the row count", source="consultant").lesson
    feedback.edit(alpha, overdue.id, ["review_by=2026-01-31"])
    loved = intake.add(alpha, when="you retrieve a layout", do="name the namespace in the layout name",
                       source="consultant").lesson
    for _ in range(3):
        feedback.helpful(alpha, loved.id)
    ignored = intake.add(alpha, when="you open a report type", do="keep only fields present in every org",
                         source="consultant").lesson
    storage.log(alpha.engagement, [{"event": "shown", "id": ignored.id, "via": "for"}] * 5)
    wrong = intake.add(alpha, when="you assign a permission set group", do="assign the sets one by one",
                       source="consultant").lesson
    feedback.stale(alpha, wrong.id, "groups can be assigned directly")
    feedback.stale(alpha, "A-deploy-order", "we deploy in another order")
    (alpha.folder("client") / "broken.md").write_text("no front matter", encoding="utf-8")
    before = snapshot(workspace)

    report = review.build(alpha)
    ids = lambda name: [entry["id"] for entry in report[name]]
    assert ids("candidates") == [waiting.id]
    assert [(pair["keep"], pair["other"]) for pair in report["duplicates"]] == [tuple(sorted((one.id, twin.id)))]
    recheck = {entry["id"]: entry["reasons"] for entry in report["recheck"]}
    assert recheck == {overdue.id: ["past its review date (2026-01-31)"],
                       ignored.id: ["shown 5 times and never marked helpful"]}
    assert sorted(ids("stale")) == sorted([wrong.id, "A-deploy-order"])
    assert ids("promote") == [loved.id] and report["promote"][0]["reasons"] == ["marked helpful 3 times"]
    assert named.id not in ids("firm_worthy") and waiting.id not in ids("firm_worthy")
    assert {one.id, twin.id, overdue.id, loved.id, ignored.id} <= set(ids("firm_worthy"))
    assert [problem["path"] for problem in report["unreadable"]] == ["clients/alpha/lessons/broken.md"]
    assert snapshot(workspace) == before                       # building the report changes nothing

    code, out, _ = run("review")
    assert code == 0 and "# Lessons review: client alpha + firm + platform" in out
    for text in (f"torque lesson verify {waiting.id} --evidence", f"--superseded-by", f"torque lesson promote {loved.id}",
                 "It ships with Torque", "clients/alpha/lessons/broken.md: no front matter"):
        assert text in out
    saved = workspace / "clients" / "alpha" / ".torque" / "lessons" / "proposals" / f"{record.today()}.md"
    assert saved.is_file() and saved.read_text(encoding="utf-8") in out
    assert {key: value for key, value in snapshot(workspace).items()} == before
    answer = json.loads(run("review", "--json")[1])
    assert answer["report"] == str(saved) and [entry["id"] for entry in answer["candidates"]] == [waiting.id]
    # Without a client the firm is reviewed, and the report is kept with the workspace.
    code, out, _ = run("review", client=None)
    assert code == 0 and "# Lessons review: firm + platform" in out and waiting.id not in out
    assert (workspace / ".torque" / "lessons" / "proposals" / f"{record.today()}.md").is_file()


def test_lessons_not_shown_for_half_a_year_are_listed_for_a_check(scopes, monkeypatch):
    alpha = scopes("alpha")
    old = intake.add(alpha, when="you refresh a sandbox", do="pass the API version", source="consultant").lesson
    feedback.edit(alpha, old.id, ["last_verified=2026-01-01"])
    stored = storage.load(alpha)[0]
    stored.created, stored.review_by = "2026-01-01", "2099-01-01"
    storage.save(stored, alpha.folder("client"))
    assert review.build(alpha)["recheck"][0]["reasons"] == ["never shown since it was saved (2026-01-01)"]
    with open(storage.events_path(alpha.engagement), "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": "2026-02-01T10:00:00+00:00", "event": "shown", "id": old.id}) + "\n")
    assert review.build(alpha)["recheck"][0]["reasons"] == ["not shown in 180 days"]
    storage.log(alpha.engagement, [{"event": "shown", "id": old.id}])
    assert review.build(alpha)["recheck"] == []
