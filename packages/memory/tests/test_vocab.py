"""Words to tasks and metadata types: deterministic, small, printed for correction."""
import pytest

from jsc_memory import vocab


@pytest.mark.parametrize("text,tasks,types", [
    ("Read the active version of the flow, not only the retrieved latest", {"flow-build", "retrieve"}, {"Flow"}),
    ("Ship field access with every new field in a permission set", {"field-build", "access"},
     {"CustomField", "PermissionSet"}),
    ("Deploy a new validation rule inactive to production", {"deploy", "validation", "production"},
     {"ValidationRule"}),
    ("Bulk-edit Lightning page XML from a fresh retrieve", {"layout", "retrieve"}, {"FlexiPage"}),
    ("In a refreshed sandbox the scheduled jobs are wrong", {"sandbox-refresh", "sandbox", "scheduling"}, set()),
    ("Sign in through the front door and drive the browser", {"sign-in", "browser-test"}, set()),
    ("The data load upserts from a CSV", {"data-load"}, set()),
    ("A SOQL query on CampaignMember for the rollup", {"query", "rollup"}, set()),
    ("The named credential of the integration", {"integration"}, {"NamedCredential"}),
    ("Test as a real user with a dummy record", {"testing"}, set()),
    ("Write the description on each CustomField and PermissionSet", {"documentation"},
     {"CustomField", "PermissionSet"}),
])
def test_text_points_to_tasks_and_types(text, tasks, types):
    found_tasks, found_types = vocab.infer(text)
    assert tasks <= set(found_tasks) and types == set(found_types)


@pytest.mark.parametrize("text", [
    "The deploy reports success and the workflow of the team improved.",
    "Report the result to the lead; profile the slow step later.",
])
def test_everyday_words_do_not_become_metadata_types(text):
    assert "Report" not in vocab.infer(text)[1] and "Flow" not in vocab.infer(text)[1]


def test_distinctive_tokens_become_keywords_and_quoted_errors_become_error_matchers():
    text = ("FIELD_CUSTOM_VALIDATION_EXCEPTION on Gift_Batch__c: check FlowDefinitionView and "
            "CampaignMember.AccountId. Salesforce said so. PermissionSet too.")
    assert vocab.keywords(text) == ["Gift_Batch__c", "FlowDefinitionView", "CampaignMember.AccountId"]
    assert vocab.errors(text) == ["FIELD_CUSTOM_VALIDATION_EXCEPTION"]
    assert vocab.errors("The query said: No such column 'Foo__c' on entity 'Account'; then MALFORMED_QUERY, and "
                        "later sObject type 'FlowDefinitionView' is not supported; the field cannot be grouped.") == [
        "MALFORMED_QUERY", "No such column", "sObject type * is not supported", "can*not be grouped"]
    assert vocab.errors("Nothing failed here; the flow ran and the column was fine.") == []


@pytest.mark.parametrize("matcher,error,expected", [
    ("No such column", "ERROR at Row:1:Column:8\nNo such column 'Region__c' on entity 'Account'.", True),
    ("no SUCH   column", "No such column 'X' on entity 'Y'", True),
    ("sObject type * is not supported", "sObject type 'FlowDefinitionView' is not supported.", True),
    ("sObject type * is not supported", "the sObject is not supported as a type", False),      # the order matters
    ("can*not be grouped", "field 'Status__c' can not be grouped in a query call", True),
    ("can*not be grouped", "Field cannot be grouped", True),
    ("UNABLE_TO_LOCK_ROW", "unable_to_lock_row: unable to obtain exclusive access to this record", True),
    ("Disjunctions not supported", "No such column 'X'", False),
    ("a.c", "abc", False),                                    # nothing but * is special: no regular expressions
    ("[a-z]+", "abc", False),
    ("*", "anything at all", False),                          # a matcher needs some text of its own
    ("ab*", "abc", False),
])
def test_an_error_matcher_is_a_piece_of_the_text_with_a_star_for_anything(matcher, error, expected):
    assert vocab.error_matches(matcher, vocab.normal_error(error)) is expected


def test_two_error_texts_of_one_class_share_a_code_or_their_wording():
    symptom = "\"sObject type 'FieldPermissions' is not supported\" from a query that is valid SOQL"
    assert vocab.shares_wording("sObject type 'FlowDefinitionView' is not supported.", symptom)
    assert vocab.shares_wording("Error: UNABLE_TO_LOCK_ROW on 3 rows", "fails intermittently with UNABLE_TO_LOCK_ROW")
    assert vocab.shares_wording("field 'Body' can not be filtered in a query call",
                                "'field cannot be filtered in a query call' on a history field")
    assert not vocab.shares_wording("No such column 'X' on entity 'Y'", symptom)
    assert not vocab.shares_wording("", symptom) and not vocab.shares_wording("it is not supported", symptom)


@pytest.mark.parametrize("path,kind", [
    ("force-app/main/default/flows/Gift_Entry.flow-meta.xml", "Flow"),
    (r"force-app\main\default\objects\Account\fields\Region__c.field-meta.xml", "CustomField"),
    ("Admin.permissionset-meta.xml", "PermissionSet"),
    ("classes/GiftService.cls", "ApexClass"),
    ("classes/GiftService.cls-meta.xml", "ApexClass"),
    ("force-app/main/default/flexipages", "FlexiPage"),
    ("force-app/main/default/objects/Account", "CustomObject"),
    ("lwc/giftForm/giftForm.js", "LightningComponentBundle"),
])
def test_a_file_name_tells_the_metadata_type(path, kind):
    assert vocab.types_for_path(path) == [kind]


def test_an_unknown_path_tells_nothing():
    assert vocab.types_for_path("notes/meeting.md") == [] and vocab.types_for_path("") == []


@pytest.mark.parametrize("command,tasks,types", [
    ("sf project deploy start --metadata Flow:Gift_Entry --target-org example-dev", {"deploy"}, {"Flow"}),
    ("sf project deploy start --source-dir force-app/main/default/permissionsets", {"deploy"}, {"PermissionSet"}),
    ("torque deploy --metadata CustomField:Account.Region__c --metadata PermissionSet:Admin --target-org x",
     {"deploy"}, {"CustomField", "PermissionSet"}),
    ("sf project retrieve start --metadata Layout", {"retrieve"}, {"Layout"}),
    ('sf data query --query "SELECT Id FROM Account"', {"query"}, set()),
    ("torque data bulk upsert --sobject Contact --file contacts.csv", {"data-load"}, set()),
    ("sf org open --target-org example-dev", {"sign-in"}, set()),
])
def test_a_command_points_to_tasks_and_types(command, tasks, types):
    found_tasks, found_types = vocab.for_command(command)
    assert tasks <= set(found_tasks) and types == set(found_types)


def test_types_are_spelled_as_salesforce_spells_them():
    assert [vocab.canonical_type(word) for word in ("flow", "Flows", "customfield", "permission set", "Bot")] == [
        "Flow", "Flow", "CustomField", "PermissionSet", "Bot"]
    assert vocab.tag("Data Load") == "data-load"


def test_topic_words_drop_filler():
    assert vocab.topic_words(["the", "Flow", "for", "gift-entry", "and", "FLS"]) == ["flow", "gift", "entry", "fls"]
