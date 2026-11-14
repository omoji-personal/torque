"""An empty or malformed Vision answer is not a clean screenshot review.

Before, an answer of `{}` was read as status WARN with no findings, and the Vision
surface reported PASS for it. Provider output here is a fixture; no model is called."""
import pytest

from jsc_browser_tests import vision
from jsc_qa import dispatcher

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic"
VALID = '{"status": "OK", "findings": []}'
FINDING = '{"severity": "P1", "category": "missing_field", "description": "Tier is empty", "evidence": "Tier *"}'


@pytest.mark.parametrize("answer,detail", [
    ("{}", "no valid status"),
    ('{"findings": []}', "no valid status"),
    ('{"status": "FINE", "findings": []}', "no valid status"),
    ('{"status": 1, "findings": []}', "no valid status"),
    ('{"status": "OK"}', "no findings list"),
    ('{"status": "OK", "findings": null}', "not a list"),
    ('{"status": "OK", "findings": {"severity": "P0"}}', "not a list"),
    ('{"status": "WARN", "findings": ["a toast"]}', "1 finding(s) were malformed"),
    ('{"status": "WARN", "findings": [{"severity": "P9", "description": "x", "evidence": "y"}]}', "malformed"),
    ('{"status": "WARN", "findings": [{"severity": "P1", "description": "no evidence given"}]}', "malformed"),
])
def test_incomplete_answers_are_parse_failures(answer, detail):
    status, _, said = vision._parse_response(answer)
    assert status == "PARSE_FAIL" and detail in said


def test_usable_findings_survive_beside_a_malformed_one():
    status, findings, said = vision._parse_response('{"status": "ERROR", "findings": [' + FINDING + ', "noise"]}')
    assert status == "PARSE_FAIL" and [f.severity for f in findings] == ["P1"] and "1 finding(s)" in said


@pytest.mark.parametrize("answer,status,count", [
    (VALID, "OK", 0),
    ('{"status": "warn", "findings": [' + FINDING + "]}", "WARN", 1),
    ("Here is the review:\n" + VALID, "OK", 0),
])
def test_complete_answers_are_still_accepted(answer, status, count):
    parsed_status, findings, said = vision._parse_response(answer)
    assert (parsed_status, len(findings), said) == (status, count, "ok")


@pytest.fixture
def one_screenshot(monkeypatch, tmp_path):
    client = tmp_path / "client"
    shot = client / "state" / "qa-tests" / "run" / "c0" / "after_save.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(PNG)
    monkeypatch.setenv("TORQUE_WORKSPACE", str(client))
    monkeypatch.setattr(dispatcher, "_is_production_alias", lambda target: False)
    monkeypatch.setattr(dispatcher, "_vision_manifest_inventory", lambda target: (
        shot.parent.parent / "manifest.json", {"target_org": target},
        [{"path": str(shot), "flow": "save_record", "profile": "admin", "step_name": "after save"}]))
    monkeypatch.setattr(vision, "gemini_available", lambda: True)
    answers = {"text": "{}"}
    monkeypatch.setattr(vision, "_invoke_gemini", lambda prompt, *, model, timeout_s: (0, answers["text"], "", ""))
    return answers


def test_the_vision_surface_does_not_pass_on_an_empty_answer(one_screenshot):
    result = dispatcher.dispatch_vision("acme-sbx", "A7 page change")
    assert result.status == "ERROR", "the only analysis was incomplete: nothing was reviewed"
    assert "incomplete analyses: 1" in result.detail
    assert result.metadata["results"][0]["status"] == "PARSE_FAIL"


def test_the_vision_surface_still_passes_on_a_complete_clean_answer(one_screenshot):
    one_screenshot["text"] = VALID
    result = dispatcher.dispatch_vision("acme-sbx", "A7 page change")
    assert result.status == "PASS" and "incomplete analyses: 0" in result.detail


def test_a_finding_reported_beside_a_malformed_one_still_fails_the_surface(one_screenshot):
    one_screenshot["text"] = '{"status": "ERROR", "findings": [' + FINDING + ', "noise"]}'
    result = dispatcher.dispatch_vision("acme-sbx", "A7 page change")
    assert result.metadata["p0p1_count"] == 1
    assert result.status in ("FAIL", "ERROR")
