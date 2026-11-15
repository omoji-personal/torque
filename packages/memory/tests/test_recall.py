"""Recall at the point of use: match over every readable lesson, then rank; print few."""
from datetime import date, timedelta

import pytest

from jsc_memory import intake, recall, storage
from jsc_memory.lesson import Lesson

FLOW_DEPLOY = "sf project deploy start --metadata Flow:Gift_Entry --target-org example-dev"


def save(scopes, scope, suffix, **fields):
    applies = {"tasks": [], "types": [], "commands": [], "keywords": [], "errors": [],
               **{key: fields.pop(key) for key in ("tasks", "types", "commands", "keywords", "errors")
                  if key in fields}}
    # An active lesson needs a basis in its record to be shown (lesson.unbacked_active): the consultant's word here.
    fields.setdefault("source", "consultant")
    item = Lesson(id=f"L-20250303-{suffix}", title=fields.pop("title", f"Lesson {suffix}"), scope=scope,
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
    assert recall.render(nothing) == (
        "No lesson matches quantum, chromodynamics (client alpha + firm + platform). If this work teaches something, "
        'save it: torque session add ... --learned "When: ... Do: ... Because: ..."')
    # Without selectors the lessons that matter most come back, bounded.
    assert len(recall.find(alpha).hits) == 5 and recall.find(alpha).matched >= 60


def days_ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


def test_ranking_is_match_strength_with_a_bonus_for_nearness_then_feedback_then_recency(scopes):
    alpha = scopes("alpha")
    save(alpha, "firm", "f001", types=["Flow"], tasks=["deploy"])                    # strong firm match: 7 + 1
    save(alpha, "client", "c001", tasks=["deploy"])                                  # weak client match: 3 + 2
    save(alpha, "client", "c002", types=["Flow"], tasks=["deploy"])                  # strongest client match: 7 + 2
    save(alpha, "client", "c003", types=["Flow"], last_verified=days_ago(400))       # same strength as c004, older
    save(alpha, "client", "c004", types=["Flow"], last_verified=days_ago(100))
    save(alpha, "client", "c005", types=["Flow"], last_verified=days_ago(700))       # oldest, but marked helpful
    storage.log(alpha.engagement, [{"event": "helpful", "id": "L-20250303-c005"}])
    result = recall.find(alpha, types=["Flow"], tasks=["deploy"], limit=None)
    assert own(result) == ["c002", "f001", "c005", "c004", "c003", "c001"]
    first = {hit.lesson.id[-4:]: hit for hit in result.hits}
    assert first["c002"].score == 7 and first["c002"].reasons == ["type Flow", "task deploy"]
    # A weak client match does not jump ahead of stronger platform matches.
    order = [hit.lesson.id[-4:] for hit in result.hits]
    assert any(hit.lesson.scope == "platform" for hit in result.hits[:order.index("c001")])


def test_an_exact_platform_error_match_outranks_a_weak_client_match_and_is_never_cut(scopes):
    alpha = scopes("alpha")
    for suffix in ("w001", "w002", "w003"):
        save(alpha, "client", suffix, tasks=["query"], when=f"you write a query ({suffix})")
    result = recall.find(alpha, error="No such column 'Gift_Total__c' on entity 'Contact'", tasks=["query"], limit=3)
    assert result.hits[0].lesson.scope == "platform" and "error" in result.hits[0].reasons
    # Even when nearer lessons are stronger on other selectors, an exact error match keeps a place.
    for suffix in ("s001", "s002", "s003"):
        save(alpha, "client", suffix, tasks=["query", "data-load"], types=["Contact", "Account"],
             when=f"strong ({suffix})")
    crowded = recall.find(alpha, error="No such column 'X__c' on entity 'Contact'", tasks=["query", "data-load"],
                          types=["Contact", "Account"], limit=3)
    assert [hit.lesson.id[-4:] for hit in crowded.hits[:2]] == ["s001", "s002"]
    assert recall._exact_error(crowded.hits[2]) and crowded.hits[2].lesson.scope == "platform"


def test_equal_matches_are_ordered_by_evidence_then_by_recency_in_coarse_steps_never_by_a_few_days(scopes):
    alpha = scopes("alpha")
    reproduced = [{"type": "reproduced", "date": days_ago(80)}]
    session = [{"type": "session", "date": days_ago(1)}]
    told = [{"type": "consultant-said", "date": days_ago(1)}]
    save(alpha, "client", "a001", types=["Flow"], evidence=session, last_verified=days_ago(60))
    save(alpha, "client", "a002", types=["Flow"], evidence=told, last_verified=days_ago(2))
    save(alpha, "client", "a003", types=["Flow"], evidence=reproduced, last_verified=days_ago(80))
    save(alpha, "client", "a004", types=["Flow"], evidence=session, last_verified=days_ago(1))
    save(alpha, "client", "a005", types=["Flow"], evidence=session, last_verified=days_ago(200))
    save(alpha, "client", "a006", types=["Flow"], evidence=reproduced, last_verified=days_ago(500))
    # Reproduced before consultant-said before session, whatever the dates; within one
    # evidence class, within 90 days before within a year; inside a step, the id decides.
    assert own(recall.find(alpha, types=["Flow"], limit=None)) == ["a003", "a006", "a002", "a001", "a004", "a005"]


def test_candidates_stale_marks_retired_and_promoted_lessons_stay_out_of_default_recall(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "0001", types=["Flow"])
    save(alpha, "client", "0002", types=["Flow"], state="candidate")
    save(alpha, "client", "0003", types=["Flow"], state="retired")
    save(alpha, "client", "0004", types=["Flow"], promoted_to=["firm:L-20250303-ffff"])
    save(alpha, "client", "0005", types=["Flow"])
    storage.log(alpha.engagement, [{"event": "stale", "id": "L-20250303-0005", "reason": "fixed upstream"}])
    result = recall.find(alpha, types=["Flow"], limit=None)
    assert own(result) == ["0001"] and result.candidates == 1
    assert "1 matching candidate(s) wait for review" in recall.render(result)
    assert own(recall.find(alpha, types=["Flow"], include_candidates=True, limit=None)) == ["0001", "0002"]
    storage.log(alpha.engagement, [{"event": "restored", "id": "L-20250303-0005"}])
    assert own(recall.find(alpha, types=["Flow"], limit=None)) == ["0001", "0005"]


def test_the_clients_own_matching_candidates_are_hints_when_asked_and_never_lessons(scopes):
    alpha, beta = scopes("alpha"), scopes("beta")
    save(alpha, "client", "0001", types=["Flow"], when="you deploy a flow", do="read the active version")
    for suffix, evidence in (("c001", "session"), ("c002", "reproduced"), ("c003", "session")):
        save(alpha, "client", suffix, types=["Flow"], state="candidate", when=f"candidate {suffix}",
             evidence=[{"type": evidence, "date": "2026-11-14"}])
    save(alpha, "client", "c004", types=["ApexClass"], state="candidate", when="not about flows")
    save(alpha, "firm", "c005", types=["Flow"], state="candidate", when="a firm candidate")
    save(beta, "client", "c006", types=["Flow"], state="candidate", when="another client's candidate")
    result = recall.find(alpha, types=["Flow"], limit=None, hints=recall.HINTS)
    assert "0001" in own(result) and all(hit.lesson.state == "active" for hit in result.hits)
    assert [hit.lesson.id[-4:] for hit in result.hits if hit.lesson.id.endswith(("c001", "c002", "c003"))] == []
    assert [hit.lesson.id[-4:] for hit in result.hits if hit.lesson.id.endswith(("c004", "c005", "c006"))] == []
    assert [hit.lesson.id[-4:] for hit in result.hints] == ["c002", "c001"]          # at most two, best first
    assert result.candidates == 2                                                    # c003 and the firm's c005
    text = recall.render(result).splitlines()
    first_hint = text.index("Not reviewed yet, from this client's own work (2):")
    assert text.index("[L-20250303-0001] WHEN you deploy a flow") < first_hint
    assert text[first_hint + 1] == "[L-20250303-c002] WHEN candidate c002"
    assert text[first_hint + 2].startswith("  (client, candidate, not reviewed: a hint, check it, reproduced, ")
    assert text[first_hint + 3] == "  If it worked: torque lesson verify L-20250303-c002 --evidence reproduced"
    assert text[-2] == recall.FOOTER and text[-1].startswith("2 matching candidate(s) wait for review")
    answer = recall.as_json(result)
    assert [item["id"][-4:] for item in answer["candidate_hints"]] == ["c002", "c001"]
    assert all(item["state"] == "active" for item in answer["lessons"])
    # Only an explicit query asks for hints: not the default, not the context block, not before a step.
    assert recall.find(alpha, types=["Flow"]).hints == [] and recall.find(alpha, hints=2).hints == []
    assert "candidate c00" not in recall.for_command("torque deploy --metadata Flow:X", scopes=alpha)
    assert "candidate c00" not in " ".join(recall.context_section(alpha)["lines"])
    assert recall.find(beta, types=["Flow"], hints=2).hints[0].lesson.id.endswith("c006")
    assert len(recall.find(beta, types=["Flow"], hints=2).hints) == 1
    # Nothing reviewed matches, but a candidate does: one line says so, then the hint.
    only = recall.find(beta, types=["Flow"], advisory=False, hints=2)
    only.hits = []
    assert recall.render(only).splitlines()[:2] == [
        "No reviewed lesson matches Flow (client beta + firm + platform).",
        "Not reviewed yet, from this client's own work (1):"]


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


def test_a_loose_word_is_not_a_match(scopes):
    alpha = scopes("alpha")
    # What a lesson says it does or why is not what it is about: its `do` and `because` are not matched.
    save(alpha, "client", "verb", title="Read the deploy result", tasks=["deploy"],
         when="a deploy finishes", do="read the component results; it reports success while reporting nothing",
         because="the dashboard of the job shows the job, not the components")
    save(alpha, "client", "dash", title="A dashboard runs as its running user", types=["Dashboard"], tasks=["report"],
         when="you build a dashboard for many viewers", do="choose the running user on purpose")
    save(alpha, "client", "snap", title="Analytic snapshots run as their owner", when="you schedule one",
         do="check the owner is active")
    save(alpha, "client", "when", title="Check the target first", when="you schedule a snapshot for an owner",
         do="check the owner is active")
    save(alpha, "client", "flow", title="Snapshots of a flow interview", types=["Flow"], when="you debug a flow")
    found = lambda *topic, **more: own(recall.find(alpha, topic=topic, limit=None, **more))
    # A vocabulary word is asked as its task or type, and only lessons about it answer.
    assert found("report", "dashboard") == ["dash"] and found("reports") == ["dash"]
    # A whole word, with a plural or singular ending and nothing more.
    assert found("snapshot") == ["flow", "snap"] and found("snapshots") == ["flow", "snap"]
    assert found("snap") == [] and found("shot") == []
    # One word in `when` alone is not enough; two different words are.
    assert "when" not in found("snapshot") and "when" in found("snapshot", "owner")
    # Words do not rescue a lesson about other component types than the ones asked for.
    assert "flow" not in found("snapshot", types=["Dashboard"]) and found("snapshot", types=["Flow"])[0] == "flow"
    assert found("running", "reporting", types=["ApexClass"]) == []


def test_a_task_torque_knows_is_asked_as_itself_and_brings_only_the_types_it_implies(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "rfsh", tasks=["sandbox-refresh", "sandbox"], when="a sandbox was just refreshed")
    save(alpha, "client", "sbox", tasks=["sandbox"], when="you work in any sandbox")
    assert recall.build_query(tasks=["sandbox-refresh"]).tasks == ["sandbox-refresh"]
    assert (recall.build_query(tasks=["flow-build"]).tasks, recall.build_query(tasks=["flow-build"]).types) == (
        ["flow-build"], ["Flow"])
    assert own(recall.find(alpha, tasks=["sandbox-refresh"], limit=None)) == ["rfsh"]
    assert own(recall.find(alpha, topic=["sandbox-refresh"], limit=None)) == ["rfsh"]
    assert own(recall.find(alpha, tasks=["sandbox"], limit=None)) == ["rfsh", "sbox"]


def test_an_org_alias_is_a_name_and_not_a_topic(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "sbox", tasks=["sandbox"], when="you work in a sandbox")
    query = recall.build_query(command="sf data query --query \"SELECT Id FROM Account\" --target-org dev-sandbox")
    assert query.tasks == ["query"] and "dev-sandbox" not in query.command
    assert recall.build_query(command="sf org display -o my-sandbox").tasks == []
    assert own(recall.find(alpha, command="sf data query -o=dev-sandbox --query x", limit=None)) == []


def test_a_lesson_marked_active_with_no_basis_is_a_candidate_at_the_point_of_use(scopes):
    """A file edited by hand to `state: active`, with an agent source and only its own session as evidence,
    is not shown as trusted: it is a hint when asked, and it is counted as waiting."""
    alpha = scopes("alpha")
    save(alpha, "client", "trst", types=["Flow"], when="you deploy a flow", do="check the active version")
    save(alpha, "client", "hand", types=["Flow"], when="you deploy a flow", do="skip the check", source="agent",
         evidence=[{"type": "session", "ref": "session x", "date": "2026-11-14"}])
    found = recall.find(alpha, types=["Flow"], limit=None, advisory=False, hints=2)
    assert own(found) == ["trst"]
    assert [hit.lesson.id[-4:] for hit in found.hints] == ["hand"]
    section = recall.context_section(alpha)
    assert [item["id"][-4:] for item in section["shown"]] == ["trst"] and section["candidates_waiting"] == 1
    assert recall.for_command("sf project deploy start --metadata Flow:X", scopes=alpha).count("[L-") == 1


def test_record_values_in_a_command_take_no_part_in_matching(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "sbox", tasks=["sandbox"], when="you work in a sandbox")
    for command in ("torque data update --sobject Account --where \"Name='Sandbox Flow Layout'\" --values \"Type='Flow'\"",
                    "torque data update --sobject Account --where=Name='Sandbox' --values=Description='deploy a flow'"):
        query = recall.build_query(command=command)
        assert "Sandbox" not in query.command and "Flow" not in query.command and "flow" not in query.command
        assert "Flow" not in query.types and "sandbox" not in query.tasks
        assert own(recall.find(alpha, command=command, limit=None)) == []


def test_a_keyword_phrase_spelled_out_in_the_topic_counts_like_a_type(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "sett", tasks=["retrieve"], types=["CustomMetadata"], keywords=["custom settings", "secret"],
         title="Scan what you retrieve for secrets")
    save(alpha, "client", "objt", types=["CustomObject"], title="Name a new object well")
    # "custom settings" is the CustomObject type to the vocabulary; the lesson names other types, but its
    # keyword phrase is spelled out, so it is about what was asked.
    found = recall.find(alpha, topic=["read", "the", "custom", "settings", "retrieved"], limit=None)
    assert own(found)[0] == "sett" and found.hits[0].reasons[0] == "keyword custom settings"
    assert own(recall.find(alpha, topic=["custom", "objects"], limit=None)) == ["objt"]       # no phrase: excluded
    one = recall.find(alpha, topic=["settings"], limit=None)                              # one word is not a phrase
    assert one.hits[0].reasons == ["word settings"] and one.hits[0].score == 1


def test_a_command_pattern_that_names_an_option_or_value_outweighs_a_broad_one(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "broa", tasks=["deploy"], commands=["sf project deploy *", "torque deploy *"],
         evidence=[{"type": "reproduced"}])
    save(alpha, "client", "dest", tasks=["deploy"], commands=["*destructive*"])
    command = "sf project deploy start --manifest package.xml --post-destructive-changes destructiveChanges.xml"
    scores = lambda result: [(hit.lesson.id[-4:], hit.score) for hit in result.hits if hit.lesson.scope == "client"]
    assert scores(recall.find(alpha, command=command, limit=None)) == [("dest", 9), ("broa", 7)]
    plain = recall.find(alpha, command="sf project deploy start --source-dir force-app", limit=None)
    assert scores(plain) == [("broa", 7), ("dest", 3)]                                    # on its task only


def test_the_heading_names_each_thing_asked_for_once_whatever_its_spelling():
    assert recall.build_query(topic=["report", "dashboard"]).label() == "report, Dashboard"
    assert recall.build_query(tasks=["Flow"], types=["flow"]).label() == "flow, flow-build"
    assert recall.build_query(topic=["Gift", "gift"], error="x").label() == "this error, gift"


def test_shipped_lessons_answer_report_dashboard_only_with_lessons_about_reports_or_dashboards():
    result = recall.find(storage.Scopes(), topic=["report", "dashboard"], limit=None)
    assert (result.query.tasks, result.query.types, result.query.words) == (["report"], ["Dashboard"], [])
    for hit in result.hits:                   # the shipped data changes; whatever answers is about reports
        assert set(hit.reasons) <= {"task report", "type Dashboard"}, (hit.lesson.id, hit.lesson.title)
    if not result.hits:
        assert recall.render(result).startswith("No lesson matches report, Dashboard (platform).")


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
    assert note.lesson.because and note.lesson.extra["updated"] and not note.lesson.last_verified
    assert "A-fls-not-automatic" not in found                         # a field note, not for a flow deploy
    field = recall.find(storage.Scopes(), command="sf project deploy start --metadata CustomField:Account.X__c",
                        limit=None)
    assert "A-fls-not-automatic" in ids(field)
    assert recall.platform_lessons(advisory=False) == [item for item in recall.platform_lessons()
                                                      if item.origin != "advisory"]


@pytest.fixture
def catalogue(monkeypatch):
    """Made-up catalogue notes in place of the shipped ones, for one test."""
    from jsc_advisory import catalogue as module

    def use(*notes):
        monkeypatch.setattr(module, "entries", lambda: tuple(notes))
        recall._advisory.cache_clear()
    yield use
    recall._advisory.cache_clear()


def _note(name, **more):
    return {"id": name, "title": "Field labels are hidden from text selectors", "domain": "auth",
            "confidence": "practitioner", "triggers": ["field label", "shadowRoot"], "updated": "2026-08-04",
            "symptom": "the selector finds no element for a field label", "cause": "labels sit in shadow roots",
            "remedy": "walk the shadow roots", **more}


def test_a_catalogue_note_that_says_what_it_applies_to_is_taken_at_its_word(catalogue):
    given = {"tasks": ["browser-test"], "types": [], "errors": ["no element * field label"]}
    catalogue(_note("given", applies=given), _note("guessed"))
    notes = {item.id: item for item in recall.platform_lessons() if item.origin == "advisory"}
    assert (notes["A-given"].applies["tasks"], notes["A-given"].applies["types"],
            notes["A-given"].applies["errors"]) == (["browser-test"], [], ["no element * field label"])
    # Without `applies`, the note's tasks and types are inferred as before (the title says "field labels").
    assert notes["A-guessed"].applies["tasks"] == ["sign-in", "field-build"]
    assert notes["A-guessed"].applies["types"] == ["CustomField"] and notes["A-guessed"].applies["errors"] == []
    found = lambda **query: [hit.lesson.id for hit in recall.find(storage.Scopes(), limit=None, **query).hits
                             if hit.lesson.origin == "advisory"]
    assert found(tasks=["field-build"], types=["CustomField"]) == ["A-guessed"]
    assert found(tasks=["browser-test"]) == ["A-given"]
    # Given error matchers answer the error; the guessed note still answers through its symptom's wording.
    assert found(error="Timeout: no element matched the field label 'Amount'") == ["A-given"]
    assert found(error="the selector finds no element for the label") == ["A-guessed"]
    # An `applies` that is not lists of words is ignored, and the note is inferred.
    catalogue(_note("odd", applies={"tasks": "browser-test"}))
    assert [item.applies["tasks"] for item in recall.platform_lessons() if item.origin == "advisory"] == [
        ["sign-in", "field-build"]]


def test_a_catalogue_notes_update_date_is_not_a_verification_and_its_confidence_is_its_evidence(catalogue):
    from jsc_memory import cli
    flow = {"tasks": ["flow-build"], "types": ["Flow"]}
    catalogue(_note("z-live", confidence="verified-live", updated=days_ago(300), applies=flow),
              _note("y-doc", confidence="documented", updated=days_ago(300), applies=flow,
                    source="Developer Guide, a made-up page"),
              _note("x-practice-new", confidence="practitioner", updated=days_ago(1), applies=flow),
              _note("w-practice-old", confidence="practitioner", updated=days_ago(200), applies=flow))
    notes = {item.id: item for item in recall.platform_lessons() if item.origin == "advisory"}
    expected = {"A-z-live": ("reproduced", "harvested"), "A-y-doc": ("official-doc", "doc"),
                "A-x-practice-new": ("session", "harvested"), "A-w-practice-old": ("session", "harvested")}
    assert {name: (item.evidence_type, item.source) for name, item in notes.items()} == expected
    assert all(item.last_verified == "" and item.created == "" for item in notes.values())
    assert all("date" not in item.evidence[0] for item in notes.values())
    old = notes["A-w-practice-old"]
    assert recall.lines(old)[-1] == f"  (platform, catalogue note, practitioner, updated {days_ago(200)})"
    assert recall.lines(notes["A-z-live"])[-1] == f"  (platform, catalogue note, verified-live, updated {days_ago(300)})"
    assert not any("days ago" in line or "verified today" in line for item in notes.values()
                   for line in recall.lines(item))
    summary = recall.summary(old)
    assert (summary["updated"], summary["confidence"], summary["last_verified"]) == (days_ago(200), "practitioner", "")
    shown = cli._full(old, storage.Tally())
    assert f"catalogue note, practitioner; updated {days_ago(200)}; never verified as a lesson" in shown
    assert "last verified" not in shown
    # Evidence from the confidence first: a practitioner note updated yesterday does not outrank a
    # reproduced or documented one; among equals the update date counts in coarse steps.
    found = [hit.lesson.id for hit in recall.find(storage.Scopes(), types=["Flow"], tasks=["flow-build"],
                                                   limit=None).hits if hit.lesson.origin == "advisory"]
    assert found == ["A-y-doc", "A-z-live", "A-x-practice-new", "A-w-practice-old"]


def test_torques_wrappers_and_the_commands_they_run_count_as_one_command(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "quik", commands=["sf project deploy quick*"])
    save(alpha, "client", "upsr", commands=["sf data upsert bulk*"])
    save(alpha, "client", "apex", commands=["torque apex run*"])
    found = lambda command: own(recall.find(alpha, command=command, limit=None))
    assert found("torque deploy-quick --job-id 0Af000000000001 --target-org x") == ["quik"]
    assert found("torque data bulk upsert --sobject Contact --file c.csv --external-id Email") == ["upsr"]
    assert found("sf apex run --file x.apex") == ["apex"]


def test_a_flow_deployed_with_its_fields_still_gets_the_field_lessons_and_a_folder_walk_says_where_it_stopped(
        scopes, tmp_path, monkeypatch):
    alpha = scopes("alpha")
    save(alpha, "client", "fls1", types=["CustomField", "PermissionSet"], when="you deploy a new field")
    command = ("sf project deploy start --metadata Flow:Update_Layout_Flow --metadata CustomField:Account.Region__c "
               "--metadata PermissionSet:Sales")
    result = recall.find(alpha, command=command, limit=None)
    assert "fls1" in own(result) and "Layout" not in result.query.types       # the flow's name is one name
    monkeypatch.setattr(recall, "_WALK_LIMIT", 3)
    folder = tmp_path / "force-app" / "flows"
    folder.mkdir(parents=True)
    for number in range(5):
        (folder / f"F{number}.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    walked = recall.find(alpha, paths=[str(tmp_path / "force-app")], limit=None)
    assert walked.query.notes == ["Read the first 3 names under force-app; component types further down were not "
                                  "seen: name the folder or files of the change (--path)."]
    assert recall.render(walked).splitlines()[-1] == walked.query.notes[0]
    assert recall.as_json(walked)["notes"] == walked.query.notes


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


def test_a_path_is_never_looked_into_inside_another_clients_or_initiatives_folder(workspace, scopes):
    def source(owner, folder, name):
        target = workspace / owner / "force-app" / "main" / "default" / folder
        target.mkdir(parents=True)
        (target / name).write_text("<x/>", encoding="utf-8")
    source("clients/alpha", "flows", "Gift_Entry.flow-meta.xml")
    source("clients/beta", "permissionsets", "Staff.permissionset-meta.xml")
    source("initiatives/playbook", "layouts", "Account-Main.layout-meta.xml")
    manifest = workspace / "clients" / "beta" / "package.xml"
    manifest.write_text("<Package><types><members>*</members><name>Layout</name></types></Package>", encoding="utf-8")
    alpha, beta, firm = scopes("alpha"), scopes("beta"), scopes(None)
    theirs = workspace / "clients" / "beta"
    for path in (theirs, theirs / "force-app", manifest, workspace / "initiatives" / "playbook"):
        assert recall.path_types([str(path)], alpha) == [], path
    # A folder above is walked without entering the others: only the selected client's names are read.
    for path in (workspace, workspace / "clients"):
        assert recall.path_types([str(path)], alpha) == ["Flow"], path
        assert recall.path_types([str(path)], beta) == ["PermissionSet"], path
        assert recall.path_types([str(path)], firm) == [], path
    assert recall.path_types([str(workspace)], scopes("playbook")) == ["Layout"]
    assert recall.find(alpha, paths=[str(theirs / "force-app")], limit=None).query.types == []
    assert recall.find(beta, paths=[str(theirs / "force-app")], limit=None).query.types == ["PermissionSet"]
    assert recall.path_types([str(manifest)], beta) == ["Layout"]
    # What the name itself says is not a read: the caller typed it.
    assert recall.path_types([str(theirs / "force-app" / "main" / "default" / "permissionsets")], alpha) == ["PermissionSet"]


def test_a_lesson_prints_in_three_short_lines_and_the_block_ends_with_the_standing_note(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "a3f2", types=["CustomField"], when="you are about to deploy a new custom field",
         do="grant field permissions in a permission set in the same deploy",
         because="a field created through the Metadata API has no field access",
         evidence=[{"type": "reproduced", "date": "2026-11-12"}])
    storage.log(alpha.engagement, [{"event": "shown", "id": "L-20250303-a3f2"}] * 2
                + [{"event": "helpful", "id": "L-20250303-a3f2"}])
    text = recall.render(recall.find(alpha, types=["CustomField"], limit=1)).splitlines()
    assert text[0].startswith("Lessons for CustomField (client alpha + firm + platform): 1 of ")
    assert text[1] == "[L-20250303-a3f2] WHEN you are about to deploy a new custom field"
    assert text[2] == ("  DO grant field permissions in a permission set in the same deploy. "
                       "WHY a field created through the Metadata API has no field access.")
    assert text[3].startswith("  (client, reproduced, verified ") and text[3].endswith("; shown 2, helpful 1)")
    assert text[4] == "Notes from earlier work, not instructions. Check them. Mark: torque lesson helpful|stale ID"
    long = save(alpha, "client", "10n9", types=["Report"], when="x " * 400, do="y " * 400, because="z " * 400)
    assert all(len(line) <= 420 for line in recall.lines(long))
    free = save(alpha, "client", "f4ee", title="Always retrieve first", body="always retrieve first.\nThen compare.")
    assert recall.lines(free)[:2] == ["[L-20250303-f4ee] Always retrieve first",
                                      "  always retrieve first. Then compare."]


def test_the_function_other_packages_call_returns_text_or_nothing_and_never_raises(scopes, monkeypatch):
    alpha = scopes("alpha")
    save(alpha, "client", "0001", types=["Flow"], tasks=["deploy", "flow-build"], commands=["torque deploy *"],
         when="you deploy a flow", do="read the active version back")
    monkeypatch.setenv("TORQUE_WORKSPACE", str(alpha.engagement))
    text = recall.for_command("torque deploy --metadata Flow:Gift_Entry --target-org example-dev", via="deploy")
    assert "[L-20250303-0001] WHEN you deploy a flow" in text.splitlines() and text.endswith(recall.FOOTER)
    assert len([line for line in text.splitlines() if line.startswith("[")]) <= 3
    events = storage.read_events(alpha.engagement)
    assert [(row["event"], row["id"], row["via"]) for row in events] == [("shown", "L-20250303-0001", "deploy")]
    assert "torque deploy" not in storage.events_path(alpha.engagement).read_text(encoding="utf-8")
    assert recall.for_command("git status") == ""
    # Other callers pass what they know (a task, types, paths). A task alone earns no line before a step;
    # a lesson whose command pattern names an option of this command does.
    assert recall.for_command("torque browser login", tasks=["sign-in"], scopes=alpha, limit=1) == ""
    assert recall.for_command("torque browser login --as-user staff@example.org", tasks=["sign-in"], scopes=alpha,
                              limit=1).count("\n[") == 1
    monkeypatch.setattr(recall, "find", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    assert recall.for_command("torque deploy --metadata Flow:X") == ""


def test_before_a_clean_step_only_a_touched_type_or_an_option_specific_pattern_earns_a_line(scopes):
    alpha = scopes("alpha")
    save(alpha, "client", "flow", types=["Flow"], tasks=["deploy"], when="you deploy a flow")
    save(alpha, "client", "any", tasks=["deploy"], commands=["sf project deploy *"], when="you deploy anything")
    save(alpha, "client", "err", types=["Flow"], errors=["FlowDefinition not found"], when="a flow deploy fails")
    save(alpha, "client", "dest", tasks=["deploy"], commands=["*destructive*"], errors=["is referenced elsewhere"],
         when="you delete a field")
    save(alpha, "client", "apex", types=["ApexClass"], tasks=["apex"], when="you change a class")
    printed = lambda command, **more: [line.split("]")[0].split("-")[-1] for line in recall.for_command(
        command, scopes=alpha, limit=None, **more).splitlines() if line.startswith("[L-20250303")]
    # A type the step touches: yes. A broad pattern or the deploy task alone: no. A lesson that answers an
    # error waits for it, unless its own pattern names an option of this command.
    assert printed("sf project deploy start --metadata Flow:Renewal --target-org dev") == ["flow"]
    assert printed("sf project deploy start --source-dir force-app --target-org dev") == []
    assert printed("sf project deploy start --manifest p.xml --post-destructive-changes d.xml") == ["dest"]
    assert printed("sf project deploy start --metadata Flow:Renewal", error="FlowDefinition not found")[0] == "err"
    # A type word in the command is not a component the step touches; a path or a given type is.
    assert printed("sf apex run --file scripts/check.apex") == []
    assert printed("sf project deploy start", paths=["classes/Gift.cls"]) == ["apex"]
    assert printed("torque check metadata", types=["Flow"], tasks=["deploy"]) == ["flow"]
    # `torque lesson for` is unchanged: an explicit question still gets the broad ones.
    asked = recall.find(alpha, command="sf project deploy start --source-dir force-app", limit=None)
    assert {"any", "flow"} <= {hit.lesson.id.split("-")[-1] for hit in asked.hits}


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
    storage.log(alpha.engagement, [{"event": "helpful", "id": "L-20250303-c000"}])
    (alpha.folder("client") / "broken.md").write_text("not a lesson", encoding="utf-8")
    section = recall.context_section(alpha)
    # Five in all: the client's best four, then the firm's one (the firm keeps up to two places).
    assert [item["id"][-4:] for item in section["shown"]] == ["c000", "c006", "c005", "c004", "f001"]
    assert (section["engagement_shown"], section["firm_shown"]) == (4, 1)
    assert (section["engagement_active"], section["firm_active"], section["candidates_waiting"]) == (7, 1, 1)
    assert section["platform_active"] >= 40 and section["note"] == recall.FOOTER
    assert section["lines"][0] == "[L-20250303-c000] WHEN case 0"
    assert section["lines"][-1].startswith("  (firm, ")
    assert [problem["path"] for problem in section["unreadable"]] == ["clients/alpha/lessons/broken.md"]
    assert storage.read_events(alpha.engagement)[-1]["event"] == "helpful"     # reading logs nothing
    recall.context_section(alpha, via="context")
    assert [row["via"] for row in storage.read_events(alpha.engagement) if row["event"] == "shown"] == ["context"] * 4
    # Another client sees the firm's lessons, never alpha's; consultant-sourced firm lessons come first,
    # and with no lessons of its own the firm takes every place up to the limit.
    for number in range(6):       # an agent's lesson is active only with a basis: reproduced evidence here
        save(alpha, "firm", f"g{number:03d}", source="consultant" if number == 5 else "agent",
             evidence=[{"type": "reproduced", "ref": "a run", "date": "2026-11-14"}])
    other = recall.context_section(scopes("beta"))
    assert other["engagement_active"] == 0 and other["firm_active"] == 7
    assert [item["id"][-4:] for item in other["shown"]][:1] == ["g005"] and len(other["shown"]) == 5
    assert all(item["scope"] == "firm" for item in other["shown"])


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
