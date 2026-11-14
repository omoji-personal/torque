"""Recall at the point of use: match over every readable lesson, then rank; print few."""
import pytest

from jsc_memory import intake, recall, storage
from jsc_memory.lesson import Lesson

FLOW_DEPLOY = "sf project deploy start --metadata Flow:Gift_Entry --target-org example-dev"


def save(scopes, scope, suffix, **fields):
    applies = {"tasks": [], "types": [], "commands": [], "keywords": [], "errors": [],
               **{key: fields.pop(key) for key in ("tasks", "types", "commands", "keywords", "errors")
                  if key in fields}}
    item = Lesson(id=f"L-20261114-{suffix}", title=fields.pop("title", f"Lesson {suffix}"), scope=scope,
                  state=fields.pop("state", "active"), created=fields.pop("created", "2026-11-14"),
                  last_verified=fields.pop("last_verified", "2026-11-14"), applies=applies, **fields)
    storage.save(item, scopes.folder(scope))
    return item


def ids(result):
    return [hit.lesson.id for hit in result.hits]


def own(result):
    """The ids found among the workspace's own lessons (the platform's left out)."""
    return [hit.lesson.id[-4:] for hit in result.hits if hit.lesson.scope != "platform"]


def test_a_client_lesson_is_found_for_its_client_and_never_for_another_and_a_firm_lesson_for_both(scopes):
    alpha, beta = scopes("alpha"), scopes("beta")
    save(alpha, "client", "aaaa", types=["Flow"], when="you change a flow", do="read the active version")
    save(alpha, "firm", "ffff", types=["Flow"], when="you deploy a flow", do="check it is active afterwards")
    save(beta, "client", "bbbb", types=["Flow"], when="you test a flow", do="run it as the intended user")
    assert own(recall.find(alpha, types=["Flow"], limit=None)) == ["aaaa", "ffff"]
    assert own(recall.find(beta, types=["Flow"], limit=None)) == ["bbbb", "ffff"]
    assert own(recall.find(scopes("playbook"), types=["Flow"], limit=None)) == ["ffff"]
    assert own(recall.find(scopes(None), types=["Flow"], limit=None)) == ["ffff"]
    assert own(recall.find(storage.Scopes(), types=["Flow"], limit=None)) == []
    # Nothing a call for alpha returns or says lives under another client's folder.
    text = recall.render(recall.find(alpha, types=["Flow"], limit=None))
    assert "run it as the intended user" not in text and "bbbb" not in text


def test_with_sixty_active_lessons_an_older_matching_one_is_still_found_and_an_unrelated_query_finds_nothing(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "0000", types=["ValidationRule"], created="2025-01-05", last_verified="2025-01-05",
         when="you deploy a validation rule to production", do="deploy it inactive")
    for number in range(1, 60):
        save(alpha, "client", f"{number:04d}", tasks=["data-load"], when=f"you load batch {number}",
             do="check the row count", created="2026-11-14")
    assert len(storage.load(alpha)) == 60
    result = recall.find(alpha, types=["ValidationRule"])
    assert own(result) == ["0000"] and result.hits[0].reasons == ["type ValidationRule"]
    assert own(recall.find(alpha, topic=["validation", "rule"])) [:1] == ["0000"]
    nothing = recall.find(alpha, topic=["quantum", "chromodynamics"])
    assert nothing.hits == [] and nothing.matched == 0
    assert recall.render(nothing) == "No lesson matches quantum, chromodynamics (client alpha + firm + platform)."
    # Without selectors the lessons that matter most come back, bounded.
    assert len(recall.find(alpha).hits) == 5 and recall.find(alpha).matched >= 60


def test_ranking_is_scope_then_match_strength_then_feedback_then_recency(scopes):
    alpha = scopes("alpha")
    save(alpha, "firm", "f001", types=["Flow"], tasks=["deploy"])
    save(alpha, "client", "c001", tasks=["deploy"])                                  # weaker match, nearer scope
    save(alpha, "client", "c002", types=["Flow"], tasks=["deploy"])                  # strongest client match
    save(alpha, "client", "c003", types=["Flow"], last_verified="2026-01-01")        # same strength as c004, older
    save(alpha, "client", "c004", types=["Flow"], last_verified="2026-06-01")
    save(alpha, "client", "c005", types=["Flow"], last_verified="2025-01-01")        # oldest, but marked helpful
    storage.log(alpha.engagement, [{"event": "helpful", "id": "L-20261114-c005"}])
    result = recall.find(alpha, types=["Flow"], tasks=["deploy"], limit=None)
    assert own(result) == ["c002", "c005", "c004", "c003", "c001", "f001"]
    assert result.hits[0].score == 7 and result.hits[0].reasons == ["type Flow", "task deploy"]
    assert all(hit.lesson.scope == "platform" for hit in result.hits[6:])


def test_candidates_stale_marks_retired_and_promoted_lessons_stay_out_of_default_recall(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "0001", types=["Flow"])
    save(alpha, "client", "0002", types=["Flow"], state="candidate")
    save(alpha, "client", "0003", types=["Flow"], state="retired")
    save(alpha, "client", "0004", types=["Flow"], promoted_to=["firm:L-20261114-ffff"])
    save(alpha, "client", "0005", types=["Flow"])
    storage.log(alpha.engagement, [{"event": "stale", "id": "L-20261114-0005", "reason": "fixed upstream"}])
    result = recall.find(alpha, types=["Flow"], limit=None)
    assert own(result) == ["0001"] and result.candidates == 1
    assert "1 matching candidate(s) wait for review" in recall.render(result)
    assert own(recall.find(alpha, types=["Flow"], include_candidates=True, limit=None)) == ["0001", "0002"]
    storage.log(alpha.engagement, [{"event": "restored", "id": "L-20261114-0005"}])
    assert own(recall.find(alpha, types=["Flow"], limit=None)) == ["0001", "0005"]


def test_a_lesson_about_other_components_does_not_match_on_its_task_or_command_alone(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "f1e1", types=["CustomField", "PermissionSet"], tasks=["deploy"],
         commands=["sf project deploy *"], keywords=["field access"])
    save(alpha, "client", "9e9e", tasks=["deploy"], commands=["sf project deploy *"])    # about deploys in general
    assert own(recall.find(alpha, command=FLOW_DEPLOY, limit=None)) == ["9e9e"]
    field_deploy = "sf project deploy start --metadata CustomField:Account.Region__c --target-org example-dev"
    assert own(recall.find(alpha, command=field_deploy, limit=None)) == ["f1e1", "9e9e"]
    assert own(recall.find(alpha, command="sf project deploy start --source-dir force-app", limit=None)) == [
        "9e9e", "f1e1"]
    assert own(recall.find(alpha, topic=["field", "access"], types=["Flow"], limit=None)) == ["f1e1"]


def test_a_command_pattern_matches_the_command_and_torques_own_name_for_it(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "0001", commands=["sf project deploy *"])
    save(alpha, "client", "0002", commands=["torque data bulk *"])
    save(alpha, "client", "0003", commands=["sf apex run"])
    found = lambda command: own(recall.find(alpha, command=command, limit=None))
    assert found("sf project deploy start --source-dir force-app -o x") == ["0001"]
    assert found("torque deploy --source-dir force-app --target-org x") == ["0001"]
    assert found("cd project && SF project   deploy start --dry-run") == ["0001"]
    assert found("sf data bulk upsert --file contacts.csv --sobject Contact") == ["0002"]
    assert found("sf apex run --file fix.apex") == ["0003"]
    assert found("sf apex runner") == [] and found("git status") == []


def test_the_advisory_catalogue_is_read_as_platform_lessons_without_a_copy(scopes):
    notes = [item for item in recall.platform_lessons() if item.origin == "advisory"]
    assert len(notes) >= 40 and all(item.id.startswith("A-") and item.scope == "platform" for item in notes)
    result = recall.find(storage.Scopes(), command=FLOW_DEPLOY, limit=None)
    found = {hit.lesson.id: hit for hit in result.hits}
    note = found["A-flow-activation-on-deploy"]
    assert "command" in note.reasons and "type Flow" in note.reasons
    assert note.lesson.do.startswith("Verify activation after every flow deploy")
    assert note.lesson.because and note.lesson.evidence[0]["date"]
    assert "A-fls-not-automatic" not in found                         # a field note, not for a flow deploy
    field = recall.find(storage.Scopes(), command="sf project deploy start --metadata CustomField:Account.X__c",
                        limit=None)
    assert "A-fls-not-automatic" in ids(field)
    assert recall.platform_lessons(advisory=False) == [item for item in recall.platform_lessons()
                                                      if item.origin != "advisory"]


def test_paths_tell_types_from_names_folders_and_manifests(tmp_path):
    source = tmp_path / "force-app" / "main" / "default"
    (source / "flows").mkdir(parents=True)
    (source / "flows" / "Gift_Entry.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    (source / "permissionsets").mkdir()
    (source / "permissionsets" / "Gifts.permissionset-meta.xml").write_text("<PermissionSet/>", encoding="utf-8")
    manifest = tmp_path / "package.xml"
    manifest.write_text("<Package><types><members>*</members><name>Layout</name></types>"
                        "<types><members>A</members><name>NotAType</name></types></Package>", encoding="utf-8")
    assert recall.path_types(["Region__c.field-meta.xml"]) == ["CustomField"]
    assert set(recall.path_types([str(tmp_path / "force-app")])) == {"Flow", "PermissionSet"}
    assert recall.path_types([str(manifest)]) == ["Layout"]
    assert recall.path_types([str(tmp_path / "missing" / "x.flow-meta.xml")]) == ["Flow"]
    assert recall.build_query(paths=[str(manifest)], tasks=["Deploy"]).types == ["Layout"]


def test_a_lesson_prints_in_three_short_lines_and_the_block_ends_with_the_standing_note(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "a3f2", types=["CustomField"], when="you are about to deploy a new custom field",
         do="grant field permissions in a permission set in the same deploy",
         because="a field created through the Metadata API has no field access",
         evidence=[{"type": "reproduced", "date": "2026-11-12"}])
    storage.log(alpha.engagement, [{"event": "shown", "id": "L-20261114-a3f2"}] * 2
                + [{"event": "helpful", "id": "L-20261114-a3f2"}])
    text = recall.render(recall.find(alpha, types=["CustomField"], limit=1)).splitlines()
    assert text[0].startswith("Lessons for CustomField (client alpha + firm + platform): 1 of ")
    assert text[1] == "[L-20261114-a3f2] WHEN you are about to deploy a new custom field"
    assert text[2] == ("  DO grant field permissions in a permission set in the same deploy. "
                       "WHY a field created through the Metadata API has no field access.")
    assert text[3].startswith("  (client, reproduced, verified ") and text[3].endswith("; shown 2, helpful 1)")
    assert text[4] == "Notes from earlier work, not instructions. Check them. Mark: torque lesson helpful|stale ID"
    long = save(alpha, "client", "10n9", types=["Report"], when="x " * 400, do="y " * 400, because="z " * 400)
    assert all(len(line) <= 420 for line in recall.lines(long))
    free = save(alpha, "client", "f4ee", title="Always retrieve first", body="always retrieve first.\nThen compare.")
    assert recall.lines(free)[:2] == ["[L-20261114-f4ee] Always retrieve first",
                                      "  always retrieve first. Then compare."]


def test_the_function_other_packages_call_returns_text_or_nothing_and_never_raises(scopes, monkeypatch):
    alpha = scopes("alpha")
    save(alpha, "client", "0001", types=["Flow"], tasks=["deploy"], when="you deploy a flow",
         do="read the active version back")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(alpha.engagement))
    text = recall.for_command("torque deploy --metadata Flow:Gift_Entry --target-org example-dev", via="deploy")
    assert text.splitlines()[1] == "[L-20261114-0001] WHEN you deploy a flow" and text.endswith(recall.FOOTER)
    assert len([line for line in text.splitlines() if line.startswith("[")]) <= 3
    events = storage.read_events(alpha.engagement)
    assert [(row["event"], row["id"], row["via"]) for row in events] == [("shown", "L-20261114-0001", "deploy")]
    assert "torque deploy" not in storage.events_path(alpha.engagement).read_text(encoding="utf-8")
    assert recall.for_command("git status") == ""
    # Other callers pass what they know (a task, types, paths); the catalogue's sign-in notes answer here.
    assert recall.for_command("torque browser login", tasks=["sign-in"], scopes=alpha, limit=1).count("\n[") == 1
    monkeypatch.setattr(recall, "find", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    assert recall.for_command("torque deploy --metadata Flow:X") == ""


def test_shown_events_stay_with_the_scope_that_owns_the_lesson(workspace, scopes):
    alpha = scopes("alpha")
    client = save(alpha, "client", "c001", types=["Flow"])
    firm = save(alpha, "firm", "f001", types=["Flow"])
    result = recall.find(alpha, types=["Flow"], limit=None)
    recall.mark_shown(alpha, [hit.lesson for hit in result.hits], "for", session="sess-9")
    assert {row["id"] for row in storage.read_events(alpha.engagement)} == {client.id}
    workspace_ids = {row["id"] for row in storage.read_events(workspace)}
    assert firm.id in workspace_ids and client.id not in workspace_ids
    assert any(name.startswith("A-") for name in workspace_ids)        # platform notes are logged with the firm
    assert all(row["session"] == "sess-9" for row in storage.read_events(workspace))
    recall.mark_shown(storage.Scopes(), recall.platform_lessons()[:1], "for")   # no workspace: nothing to write


def test_the_context_block_shows_the_engagements_best_and_counts_the_rest(scopes):
    alpha = scopes("alpha")
    for number in range(7):
        save(alpha, "client", f"c{number:03d}", when=f"case {number}", do="act", last_verified=f"2026-10-{number + 1:02d}")
    save(alpha, "client", "cand", state="candidate")
    save(alpha, "firm", "f001")
    storage.log(alpha.engagement, [{"event": "helpful", "id": "L-20261114-c000"}])
    (alpha.folder("client") / "broken.md").write_text("not a lesson", encoding="utf-8")
    section = recall.context_section(alpha)
    assert [item["id"][-4:] for item in section["shown"]] == ["c000", "c006", "c005", "c004", "c003"]
    assert (section["engagement_active"], section["firm_active"], section["candidates_waiting"]) == (7, 1, 1)
    assert section["platform_active"] >= 40 and section["note"] == recall.FOOTER
    assert section["lines"][0] == "[L-20261114-c000] WHEN case 0"
    assert [problem["path"] for problem in section["unreadable"]] == ["clients/alpha/lessons/broken.md"]
    assert storage.read_events(alpha.engagement)[-1]["event"] == "helpful"     # reading logs nothing
    recall.context_section(alpha, via="context")
    assert [row["via"] for row in storage.read_events(alpha.engagement) if row["event"] == "shown"] == ["context"] * 5
    other = recall.context_section(scopes("beta"))
    assert other["shown"] == [] and other["engagement_active"] == 0 and other["firm_active"] == 1


NO_COLUMN = "ERROR at Row:1:Column:8\nNo such column 'Region_Code__c' on entity 'Account'. If you are attempting ..."


def test_a_lesson_with_an_error_matcher_answers_that_error_and_no_other(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "e001", errors=["No such column", "Didn't understand relationship"],
         when="a query fails on an unknown name", do="look the name up with a describe; do not guess again")
    save(alpha, "client", "e002", errors=["sObject type * is not supported"], when="the API does not serve the entity",
         do="run the query on the other API")
    save(alpha, "client", "e003", tasks=["query"], when="you write a query", do="select only what you need")
    result = recall.find(alpha, error=NO_COLUMN, limit=None)
    assert own(result) == ["e001"] and result.hits[0].reasons == ["error"] and result.hits[0].score == 6
    assert own(recall.find(alpha, error="sObject type 'FlowDefinitionView' is not supported.", limit=None)) == ["e002"]
    unrelated = recall.find(alpha, error="FIELD_CUSTOM_VALIDATION_EXCEPTION: Amount must be positive", limit=None)
    assert own(unrelated) == []
    # Together with other selectors, the error match counts on top and ranks first.
    both = recall.find(alpha, error=NO_COLUMN, tasks=["query"], limit=None)
    assert own(both)[:2] == ["e001", "e003"]
    assert recall.render(result).splitlines()[0].startswith("Lessons for this error (client alpha + firm + platform)")
    assert recall.as_json(result)["query"]["error"] is True
    assert "Region_Code__c" not in recall.render(result)            # the error text is matched, never echoed


def test_the_catalogue_answers_an_error_through_a_notes_symptom_and_title(scopes):
    locked = recall.find(storage.Scopes(), error="UNABLE_TO_LOCK_ROW: unable to obtain exclusive access", limit=None)
    assert ids(locked)[0] == "A-unable-to-lock-row" and locked.hits[0].reasons == ["error"]
    served = recall.find(storage.Scopes(), error="sObject type 'PermissionSetAssignment' is not supported.",
                         limit=None)
    assert "A-tooling-api-refuses-permission-objects" in ids(served)
    assert ids(recall.find(storage.Scopes(), error="zsh: command not found: sf", limit=None)) == []


def test_an_unknown_error_says_so_in_one_line_and_asks_for_what_fixed_it(scopes):
    text = recall.render(recall.find(scopes("alpha"), error="Something nobody has seen: code 0x51"))
    assert text == ("No lesson matches this error (client alpha + firm + platform). When you have fixed it, save "
                    'what worked: torque session add ... --learned "When: <the error>. Do: <what fixed it>. '
                    'Because: <why>"')
    assert "\n" not in text


def test_an_error_quoted_in_a_lesson_becomes_its_matcher_at_capture(scopes):
    alpha = scopes("alpha")
    added = intake.add(alpha, text="When: a query fails with No such column or MALFORMED_QUERY. Do: describe the "
                                   "object first. Because: the name was a guess.", source="consultant")
    assert added.lesson.applies["errors"] == ["MALFORMED_QUERY", "No such column"]
    assert added.inferred["errors"] == ["MALFORMED_QUERY", "No such column"]
    assert own(recall.find(alpha, error=NO_COLUMN, limit=None)) == [added.lesson.id[-4:]]
    given = intake.add(alpha, when="the bulk job fails", do="retry the failed batches serially",
                       errors=["unable to obtain exclusive access"], source="consultant")
    assert given.lesson.applies["errors"] == ["unable to obtain exclusive access"] and "errors" not in given.inferred
    assert own(recall.find(alpha, error="UNABLE_TO_LOCK_ROW: unable to obtain exclusive access to this record",
                           limit=None)) == [given.lesson.id[-4:]]
