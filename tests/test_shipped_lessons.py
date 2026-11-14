"""The platform lessons shipped in `lessons/`: each one parses, is found by what it says it applies
to, says something no other one says, answers its own error text and no other, and carries nothing
private (docs/lessons.md, "Writing a platform lesson")."""
from pathlib import Path
import re
import sys

import pytest

from jsc_memory import lesson as record, recall, scrubber, storage, vocab

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from public_hygiene import scan_text  # noqa: E402

FILES = sorted(path for path in (ROOT / "lessons").glob("*.md") if path.name.lower() != "readme.md")
SHIPPED = {path.name: record.parse(path.read_text(encoding="utf-8")) for path in FILES}

# One error text of the class each matcher answers, with made-up names. A new matcher needs a line here.
SAMPLES = {
    "No such column": "INVALID_FIELD: No such column 'Region__c' on entity 'Account'.",
    "Didn't understand relationship": "INVALID_FIELD: Didn't understand relationship 'Housholds__r' in field path.",
    "can*not be grouped": "MALFORMED_QUERY: field 'Status__c' can not be grouped in a query call",
    "Disjunctions not supported": "MALFORMED_QUERY: Disjunctions not supported",
    "sObject type * is not supported": "INVALID_TYPE: sObject type 'Example_Thing__c' is not supported.",
    "can*not be filtered": "INVALID_FIELD: field 'Description' can not be filtered in a query call",
    "does not support aggregate operator": "MALFORMED_QUERY: field Id does not support aggregate operator COUNT",
    "invalid operator on id field": "INVALID_QUERY_FILTER_OPERATOR: invalid operator on id field",
    "Implementation restriction": "MALFORMED_QUERY: Implementation restriction: ExampleEntity requires a filter by a single Id",
    "The requested resource does not exist": "NOT_FOUND: The requested resource does not exist",
    "Expected --test-level=NoTestRun to be one of": "Expected --test-level=NoTestRun to be one of: RunAllTestsInOrg, RunLocalTests, RunSpecifiedTests",
    "NoTestRun cannot be used in production": "INVALID_OPERATION: testLevel of NoTestRun cannot be used in production organizations",
    "specified in sfdx-project.json*does not exist": "The path force-app, specified in sfdx-project.json, does not exist.",
    "must be inside the current project": "The output directory must be inside the current project.",
    "does not contain a valid Salesforce DX project": "InvalidProjectWorkspaceError: /tmp/task does not contain a valid Salesforce DX project.",
    "cannot be longer than 255": "Example_Access: Description cannot be longer than 255 characters",
    "data value too large": "STRING_TOO_LONG: data value too large: Example text (max length=255)",
    "Unknown user permission": "Example_Access: Unknown user permission: ExampleFeatureAccess",
    "You can*t create a connected app": "Example_App: You can't create a connected app.",
    "This custom field is referenced elsewhere": "This custom field is referenced elsewhere in salesforce.com. : Flow Version - Example Flow",
    "Invalid identifier": "Line: 1, Column: 1 Invalid identifier: List",
    "Too many SOQL queries: 101": "System.LimitException: examplens:Too many SOQL queries: 101",
    "Unauthorized endpoint": "System.CalloutException: Unauthorized endpoint, please check Setup->Security->Remote site settings.",
    "Expecting value: line 1 column 1": "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
    "Unexpected UTF-8 BOM": "json.decoder.JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)",
    "'charmap' codec can't encode": "UnicodeEncodeError: 'charmap' codec can't encode character in position 4",
    "is duplicated at this location in type Flow": "Element decisions is duplicated at this location in type Flow (120:16)",
    "Duplicate developer name": "Example_Flow: Duplicate developer name: Check_Status",
    "The formula expression is invalid": "Example_Flow: The formula expression is invalid: Field Notes__c is a long text area",
    "can*t be DateTime": "Example_Flow: The data type of Start_Time can't be DateTime.",
    "not valid in version": "Example_Flow: Property 'exampleProperty' not valid in version 62.0",
}
# Errors no shipped lesson is about.
UNRELATED = (
    "INVALID_SESSION_ID: Session expired or invalid",
    "ENOENT: no such file or directory, open 'package.xml'",
    "FIELD_CUSTOM_VALIDATION_EXCEPTION: Region is required for this record type",
    "UNABLE_TO_LOCK_ROW: unable to obtain exclusive access to this record",
)
ALLOWED_HOSTS = ("help.salesforce.com", "architect.salesforce.com", "trailhead.salesforce.com", "github.com")


def _platform():
    return storage.Scopes()


def _top(limit=5, **selectors):
    return [hit.lesson.id for hit in recall.find(_platform(), limit=limit, **selectors).hits]


def _command(glob: str) -> str:
    """A command the pattern matches, with made-up words where it says `*`."""
    return " ".join(glob.replace("*", " sample ").split())


def test_every_shipped_lesson_parses_as_an_active_platform_lesson_with_honest_evidence():
    assert len(FILES) >= 40
    bundled = {path.name for path in (ROOT / "src" / "torque" / "data" / "lessons").glob("*.md")}
    assert bundled == set(SHIPPED)
    problems: list = []
    assert len(recall.platform_lessons(problems, advisory=False)) == len(SHIPPED) and problems == []
    ids = [item.id for item in SHIPPED.values()]
    assert len(set(ids)) == len(ids)
    for name, item in SHIPPED.items():
        assert (item.scope, item.state, item.source) == ("platform", "active", "harvested"), name
        assert item.when and item.do and item.because and item.body.startswith("Check:"), name
        assert item.review_by and item.evidence, name
        for entry in item.evidence:
            assert entry["type"] in ("official-doc", "session"), name
            if entry["type"] == "official-doc":
                assert entry["ref"].startswith("https://"), name
            else:
                assert entry["ref"].startswith("field-reported"), name
        assert any(item.applies[key] for key in ("tasks", "types", "commands", "errors")), name


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_each_shipped_lesson_is_found_by_a_selector_from_its_own_applies(name):
    item = SHIPPED[name]
    tries = [{"error": SAMPLES[matcher]} for matcher in item.applies["errors"]]
    tries += [{"command": _command(glob)} for glob in item.applies["commands"]]
    tries += [{"types": [kind]} for kind in item.applies["types"]]
    tries += [{"tasks": [task]} for task in item.applies["tasks"]]
    tries += [{"tasks": item.applies["tasks"], "types": [kind]} for kind in item.applies["types"]]
    found = [selectors for selectors in tries if item.id in _top(**selectors)]
    assert found, f"{name}: not among the first five for any of {tries}"
    # An error a lesson answers brings it into the three lessons printed before a step.
    for matcher in item.applies["errors"]:
        assert item.id in _top(limit=3, error=SAMPLES[matcher]), f"{name}: {matcher}"


def test_every_error_matcher_answers_its_own_error_class_and_no_unrelated_error():
    matchers = {matcher for item in SHIPPED.values() for matcher in item.applies["errors"]}
    assert matchers <= set(SAMPLES), sorted(matchers - set(SAMPLES))
    for matcher in matchers:
        assert vocab.error_matches(matcher, vocab.normal_error(SAMPLES[matcher])), matcher
        others = [text for key, text in SAMPLES.items() if key != matcher] + list(UNRELATED)
        hits = [text for text in others if vocab.error_matches(matcher, vocab.normal_error(text))]
        assert hits == [], f"{matcher} also matches {hits}"
    for text in UNRELATED:
        assert not [item.id for item in SHIPPED.values()
                    if any(vocab.error_matches(m, vocab.normal_error(text)) for m in item.applies["errors"])]


def test_no_two_shipped_lessons_say_the_same_thing():
    def key(item):
        return " ".join(re.findall(r"[a-z0-9]+", f"{item.when} {item.do}".lower()))
    seen: dict = {}
    for name, item in SHIPPED.items():
        assert key(item) not in seen, f"{name} repeats {seen.get(key(item))}"
        seen[key(item)] = name
    items = list(SHIPPED.items())
    close = [(a, b) for index, (a, one) in enumerate(items) for b, other in items[index + 1:]
             if record.overlap(one, other) >= 0.5]
    assert close == []


RELAXES = re.compile(r"\b(?:false|turn(?:s|ed)? (?:it |them )?off|disabl\w*|deactivat\w*|remove the restriction"
                     r"|to all users|view all|modify all|without sharing)\b", re.I)
ACCESS = re.compile(r"access|restrict|permission|override|sharing|security|field-level|\bFLS\b", re.I)
CONDITION = re.compile(r"\b(?:if|unless|only when|only where)\b", re.I)


def test_no_shipped_lesson_tells_the_agent_to_relax_access_without_a_condition():
    """A `do` that sets an access restriction off (false, disable, view all, ...) must say when."""
    unconditional = [name for name, item in SHIPPED.items()
                     if RELAXES.search(item.do) and ACCESS.search(item.do) and not CONDITION.search(item.do)]
    assert unconditional == []


def test_the_shipped_lessons_carry_no_credential_personal_data_private_path_or_private_link():
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        assert scan_text(f"lessons/{path.name}", text) == [], path.name
        _, found = scrubber.scrub(text)
        assert (found.refused, found.masked) == ({}, {}), path.name
        for url in re.findall(r"https?://[^\s\"'`)>]+", text):
            host = url.split("/")[2]
            assert host in ALLOWED_HOSTS, f"{path.name}: {host}"
