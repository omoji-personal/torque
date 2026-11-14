"""The catalogue's content: entries the field record showed to be wrong stay corrected, and
`torque advisory notes` shows the note a plain field deploy needs."""
from __future__ import annotations

import json

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


def test_the_default_notes_for_a_field_deploy_include_field_level_security(capsys):
    assert cli.main(["notes", "--command", FIELD_DEPLOY, "--json"]) == 0
    shown = [item["id"] for item in json.loads(capsys.readouterr().out)]
    assert "fls-not-automatic" in shown and len(shown) == 3
