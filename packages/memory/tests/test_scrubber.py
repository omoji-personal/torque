"""Scrubber tests — uses fixtures/pii_shapes.yaml + R5 mandated edge cases."""
from pathlib import Path
import pytest
import yaml

from jsc_memory import scrubber


FIXTURES_PATH = Path(__file__).parent / "fixtures" / "pii_shapes.yaml"


@pytest.fixture(scope="module")
def fixtures_data():
    return yaml.safe_load(FIXTURES_PATH.read_text())


def test_fixtures_load(fixtures_data):
    assert "fixtures" in fixtures_data
    assert len(fixtures_data["fixtures"]) >= 25, f"Need >=25 fixtures per plan-v5; got {len(fixtures_data['fixtures'])}"


def test_source_type_detection(fixtures_data):
    """Each fixture's input must classify to expected source_type."""
    failures = []
    for f in fixtures_data["fixtures"]:
        if "source_type_expected" not in f:
            continue
        # Handle the inline `"x" * 65540` style for large_snippet
        inp = f["input"]
        if isinstance(inp, str) and inp.startswith("x") and len(inp) >= 5 and inp.count("x") < len(inp):
            # YAML literal — no eval needed; just take what's there
            pass
        actual = scrubber.detect_source_type(inp)
        expected = f["source_type_expected"]
        if actual != expected:
            failures.append(f"{f['name']}: expected {expected}, got {actual}")
    assert not failures, "Source detection mismatches:\n" + "\n".join(failures)


def test_large_snippet_returns_narrative():
    big = "x" * 70000
    assert scrubber.detect_source_type(big) == "narrative"


def test_apex_inline_soql_precedence():
    snippet = "[SELECT Id FROM Contact WHERE Email__c = 'foo@bar.com']"
    assert scrubber.detect_source_type(snippet) == "soql"


def test_invalid_json_falls_through():
    snippet = "{this is not actually json"
    assert scrubber.detect_source_type(snippet) == "narrative"


def test_scrub_email_in_narrative():
    snippet = "Send to john.smith@example.com please"
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="lenient")
    assert "<email>" in scrubbed
    assert "john.smith@example.com" not in scrubbed


def test_scrub_ssn_in_narrative():
    snippet = "SSN: 123-45-6789"
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="lenient")
    assert "<ssn>" in scrubbed
    assert "123-45-6789" not in scrubbed


def test_paranoid_drops_multi_pii_narrative():
    snippet = "John (DOB 03/15/1985, SSN 123-45-6789, phone 555-123-4567) called"
    scrubbed, drop_reason = scrubber.scrub_snippet(snippet, mode="paranoid")
    assert drop_reason is not None, "PARANOID should drop on multi-PII narrative"


def test_lenient_keeps_multi_pii_narrative():
    snippet = "Two emails: a@b.com and c@d.com"
    scrubbed, drop_reason = scrubber.scrub_snippet(snippet, mode="lenient")
    assert drop_reason is None or "<email>" in scrubbed


def test_soql_keeps_field_names():
    snippet = "SELECT Id, Email__c, Phone__c FROM Contact"
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="paranoid")
    assert "Email__c" in scrubbed, "Field NAME in SELECT must be kept"
    assert "Phone__c" in scrubbed


def test_soql_scrubs_where_literals():
    snippet = "SELECT Id FROM Contact WHERE Email__c = 'jane@doe.com'"
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="lenient")
    assert "Email__c" in scrubbed, "Field name kept"
    assert "<email>" in scrubbed, "WHERE literal email scrubbed"
    assert "jane@doe.com" not in scrubbed


def test_json_keeps_keys_scrubs_values():
    snippet = '{"name": "John Smith", "email": "j@example.com"}'
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="lenient")
    assert '"name"' in scrubbed or "name" in scrubbed
    assert "<email>" in scrubbed
    assert "j@example.com" not in scrubbed


def test_git_sha_not_scrubbed():
    snippet = "Commit abc123def456789012345678 is broken"
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="lenient")
    assert "abc123def456789012345678" in scrubbed, "Git SHA should NOT match SF ID regex"


def test_uuid_not_scrubbed():
    snippet = "UUID is 550e8400-e29b-41d4-a716-446655440000"
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="lenient")
    assert "550e8400" in scrubbed, "UUID should NOT match SF ID regex"


def test_sf_id_account_scrubbed():
    snippet = "Found Account 0011a00000aBcDeFgH"
    scrubbed, _ = scrubber.scrub_snippet(snippet, mode="lenient")
    assert "<sf_id>" in scrubbed
    assert "0011a00000" not in scrubbed


def test_scrub_candidate_dict():
    candidate = {
        "id": "abc",
        "title": "Test lesson",
        "short": "Email j@e.com mentioned",
        "full_text": "Long text with email j@e.com",
    }
    scrubbed, drop = scrubber.scrub_candidate(candidate, mode="lenient")
    assert drop is None
    assert "<email>" in scrubbed["short"]
    assert "<email>" in scrubbed["full_text"]
