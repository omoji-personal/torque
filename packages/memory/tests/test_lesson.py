"""The record: one lesson, one Markdown file with a small YAML front matter."""
import pytest

from jsc_memory import lesson as record
from jsc_memory.lesson import Lesson, LessonError

EXAMPLE = """---
id: L-20261114-a3f2
title: Ship field access with every new field
scope: client
kind: pitfall
state: active
when: you are about to deploy a new custom field
do: grant field permissions in a permission set in the same deploy, then check as the intended user
because: a field created through the Metadata API has no field access, even for admins
applies:
  tasks: [deploy, field-build]
  types: [CustomField, PermissionSet]
  commands: ["sf project deploy *", "torque deploy *"]
  keywords: [field access, FLS]
evidence:
  - {type: reproduced, ref: "session 20261112T101500", date: 2026-11-12}
source: consultant
created: 2026-11-14
last_verified: 2026-11-14
review_by: 2027-02-14
supersedes: []
promoted_to: []
---
Optional body: the error text, a minimal reproduction, one example.
"""


def test_the_documented_file_reads_and_writes_back_the_same_lesson():
    item = record.parse(EXAMPLE)
    assert (item.id, item.scope, item.kind, item.state, item.source) == (
        "L-20261114-a3f2", "client", "pitfall", "active", "consultant")
    assert item.applies == {"tasks": ["deploy", "field-build"], "types": ["CustomField", "PermissionSet"],
                            "commands": ["sf project deploy *", "torque deploy *"],
                            "keywords": ["field access", "FLS"], "errors": []}
    assert item.evidence == [{"type": "reproduced", "ref": "session 20261112T101500", "date": "2026-11-12"}]
    assert (item.created, item.last_verified, item.review_by) == ("2026-11-14", "2026-11-14", "2027-02-14")
    assert item.body.startswith("Optional body")
    again = record.parse(record.render(item))
    assert again.front_matter() == item.front_matter() and again.body == item.body
    # Dates are written bare and counters are never stored in the file.
    text = record.render(item)
    assert "created: 2026-11-14\n" in text and "date: 2026-11-12}" in text
    assert "helpful" not in text and "shown" not in text


def test_text_with_colons_quotes_and_unicode_survives_a_round_trip():
    item = record.parse(EXAMPLE)
    item.when = 'the error says "FIELD_INTEGRITY_EXCEPTION: bad value" # not a comment'
    item.do = "prüfe das Feld: 'Betrag' ≥ 0"
    item.title = "- starts like a list: and has a colon"
    again = record.parse(record.render(item))
    assert (again.when, again.do, again.title) == (item.when, item.do, item.title)


def test_keys_torque_does_not_know_are_kept_when_the_file_is_rewritten():
    item = record.parse(EXAMPLE.replace("source: consultant", "source: consultant\nplatform_release: Winter '27"))
    assert item.extra == {"platform_release": "Winter '27"}
    assert "platform_release: Winter '27" in record.render(item)


@pytest.mark.parametrize("change,reason", [
    (("---\nid:", "id:"), "front matter"),
    (("review_by: 2027-02-14\nsupersedes: []\npromoted_to: []\n---\n", "review_by: 2027-02-14\n"), "not closed"),
    (("title: Ship field access with every new field", "title: [unclosed"), "not valid YAML"),
    (("id: L-20261114-a3f2", "id: 12"), "id is missing or malformed"),
    (("title: Ship field access with every new field\n", ""), "title is missing"),
    (("state: active", "state: actve"), "state must be one of"),
    (("scope: client", "scope: everyone"), "scope must be one of"),
    (("kind: pitfall", "kind: rant"), "kind must be one of"),
    (("type: reproduced", "type: vibes"), "evidence type must be one of"),
    (("created: 2026-11-14", "created: 2026-13-45"), "created is not a date"),
    (("tasks: [deploy, field-build]", "tasks: {a: 1}"), "applies.tasks must be a list"),
])
def test_a_file_that_cannot_be_used_says_what_to_fix(change, reason):
    with pytest.raises(LessonError, match=reason):
        record.parse(EXAMPLE.replace(*change))


def test_a_lesson_is_admitted_on_the_consultants_word_or_on_evidence_that_it_holds():
    said = [{"type": "consultant-said"}]
    assert record.admitted("consultant", said)
    assert not record.admitted("agent", [{"type": "session"}])
    assert not record.admitted("agent", said)          # at capture; a review may accept it (REVIEW_ADMITTING)
    for kind in ("reproduced", "official-doc", "second-occurrence"):
        assert record.admitted("agent", [{"type": "session"}, {"type": kind}])
    assert "consultant-said" in record.REVIEW_ADMITTING and "session" not in record.REVIEW_ADMITTING


def test_ids_carry_the_date_and_avoid_taken_ones():
    first = record.new_id("2026-11-14")
    assert record.ID.match(first) and first.startswith("L-20261114-") and len(first) == 19
    assert record.new_id("2026-11-14", taken={first}) != first


def test_a_title_is_the_first_sentence_cut_at_a_word():
    assert record.first_sentence("retrieve first. Then compare.") == "Retrieve first"
    assert record.first_sentence("# Heading line\nmore") == "Heading line"
    long = record.first_sentence("word " * 60)
    assert len(long) <= 93 and long.endswith("...")


def test_the_same_text_has_the_same_key_whatever_its_case_and_punctuation():
    one = Lesson(id="L-20261114-aaaa", title="x", when="You deploy a FIELD", do="Grant access.")
    other = Lesson(id="L-20261114-bbbb", title="y", when="you deploy a field!", do="grant access")
    assert record.content_key(one) == record.content_key(other)
    assert record.overlap(one, other) > 0.6
    assert record.overlap(one, Lesson(id="L-20261114-cccc", title="Sandbox refresh", when="after a refresh")) < 0.2


def test_age_is_counted_from_the_last_verification():
    item = record.parse(EXAMPLE)
    assert record.age_days(item, "2026-11-18") == 4 and record.age_text(item, "2026-11-18") == "verified 4 days ago"
    item.last_verified = ""
    assert record.age_text(item, "2026-11-15") == "saved 1 day ago"
