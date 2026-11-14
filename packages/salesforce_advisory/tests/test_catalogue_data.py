"""The catalogue's content: entries the field record showed to be wrong stay corrected, and
`torque advisory notes` shows the note a plain field deploy needs."""
from __future__ import annotations

import json
import re

from jsc_advisory import cli
from jsc_advisory.catalogue import entries, provenance

FIELD_DEPLOY = "sf project deploy start --metadata CustomField:Account.Region__c --target-org example-dev"


def _entry(name: str) -> dict:
    return next(item for item in entries() if item["id"] == name)


def _text(item: dict) -> str:
    return " ".join(str(item.get(key, "")) for key in ("title", "symptom", "cause", "remedy", "requires", "gotcha"))


def test_field_definition_is_not_said_to_be_tooling_only():
    # 48 standard-API queries on FieldDefinition and EntityDefinition succeeded in the field record.
    item = _entry("tooling-api-refuses-permission-objects")
    assert "ONLY in the Tooling API" not in _text(item)
    assert "FieldDefinition through the Tooling API" not in item["requires"]
    assert "with or without it" in item["remedy"]


def test_deploy_order_gives_one_order_for_apex_and_permission_sets():
    item = _entry("deploy-order")
    assert "then Apex, then permission sets" not in item["remedy"]
    assert "then permission sets, so FLS exists before anything references it; then Apex" not in item["requires"]
    # Both texts now say the same thing: a permission set that grants class access goes after the classes.
    assert "class access" in item["remedy"] and "classes they grant access to" in item["requires"]


def test_governor_limits_state_the_current_heap():
    cause = _entry("governor-limits-core")["cause"]
    assert "10 MB heap synchronous (25 MB async)" in cause
    assert "6 MB heap synchronous (12 MB async)," not in cause


def test_each_correction_is_recorded_in_the_catalogue_provenance():
    corrected = {item["entry"] for item in provenance()["adaptations"]}
    assert {"tooling-api-refuses-permission-objects", "deploy-order", "governor-limits-core"} <= corrected


def test_every_note_says_by_hand_what_it_applies_to():
    """`applies` is the contract the lessons recall reads in place of guessing from a note's title."""
    try:
        from jsc_memory import vocab
    except ImportError:  # the advisory package also works without the lessons package
        vocab = None
    for item in entries():
        applies = item.get("applies")
        assert isinstance(applies, dict), item["id"]
        assert set(applies) <= {"tasks", "types", "errors"} and {"tasks", "types"} <= set(applies), item["id"]
        for key, values in applies.items():
            assert isinstance(values, list) and all(isinstance(value, str) and value.strip() for value in values), \
                (item["id"], key)
        assert applies["tasks"], item["id"]
        assert all(re.fullmatch(r"[a-z][a-z-]*", task) for task in applies["tasks"]), item["id"]
        assert all(len(matcher.replace("*", "").strip()) >= 4 for matcher in applies.get("errors", [])), item["id"]
        if vocab is not None:
            assert all(vocab.TYPES.get(kind.lower()) == kind for kind in applies["types"]), item["id"]
    # The two notes the guess got wrong: a browser note is not a field-build note.
    assert _entry("lightning-field-labels-live-in-shadow-dom")["applies"]["tasks"] == ["browser-test"]
    assert "field-build" in _entry("fls-not-automatic")["applies"]["tasks"]


def test_the_default_notes_for_a_field_deploy_include_field_level_security(capsys):
    assert cli.main(["notes", "--command", FIELD_DEPLOY, "--json"]) == 0
    shown = [item["id"] for item in json.loads(capsys.readouterr().out)]
    assert "fls-not-automatic" in shown and len(shown) == 3
