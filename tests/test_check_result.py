"""`torque check result`: one screen from a saved deploy result, whatever shape the file arrived in."""
import contextlib
import io
import json

import pytest

from torque import cli
from torque.checks import deploy_result

VALIDATE_OK = {"status": 0, "result": {
    "checkOnly": True, "done": True, "id": "0Af000000000003AAA", "status": "Succeeded", "success": True,
    "numberComponentsTotal": 3, "numberComponentErrors": 0,
    "files": [{"fullName": "Shift__c.Hours__c", "type": "CustomField", "state": "Created", "filePath": "x"},
              {"fullName": "Shift_User", "type": "PermissionSet", "state": "Changed", "filePath": "y"},
              {"fullName": "Shift__c", "type": "CustomObject", "state": "Unchanged", "filePath": "z"}],
    "details": {"componentSuccesses": [], "runTestResult": {"numTestsRun": "0"}}}, "warnings": []}

DEPLOY_FAILED = {"status": 1, "name": "FailedDeployError", "message": "Deploy failed.", "result": {
    "checkOnly": False, "done": True, "id": "0Af000000000004AAA", "status": "Failed", "success": False,
    "numberComponentsTotal": 2, "numberComponentErrors": 1,
    "details": {
        "componentSuccesses": {"componentType": "ApexClass", "fullName": "ShiftService", "created": "false",
                               "changed": "true", "deleted": "false"},
        "componentFailures": [{"componentType": "Flow", "fullName": "Gift_Thank_You_Task", "created": "false",
                               "changed": "false", "fileName": "flows/Gift_Thank_You_Task.flow", "lineNumber": "27",
                               "columnNumber": "5", "problemType": "Error",
                               "problem": "The element Create_Task is duplicated at this location in type Flow."}],
        "runTestResult": {"numTestsRun": "4", "numFailures": "1",
                          "failures": {"name": "ShiftServiceTest", "methodName": "addsHours", "message":
                                       "System.AssertException: Assertion Failed: Expected: 2, Actual: 1",
                                       "stackTrace": "Class.ShiftServiceTest.addsHours: line 9"},
                          "codeCoverageWarnings": [{"name": "ShiftTrigger", "message":
                                                    "Test coverage of selected Apex Trigger is 0%, at least 1% "
                                                    "test coverage is required"}]}}}}


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def test_succeeded_validate(tmp_path):
    path = tmp_path / "validate.json"
    path.write_text(json.dumps(VALIDATE_OK), encoding="utf-8")
    code, out, _ = run("check", "result", str(path))
    assert code == 0
    assert "Deploy 0Af000000000003AAA: Succeeded, check-only (validate or dry-run)." in out
    assert "Components: 1 created, 1 changed, 1 unchanged, 0 deleted, 0 failed." in out
    assert "Target org: not stated in the file." in out
    assert f"torque check metadata --changed-from {path}" in out.splitlines()[-1]


def test_failed_deploy_with_component_and_test_failures(tmp_path):
    path = tmp_path / "deploy.json"
    path.write_text(json.dumps(DEPLOY_FAILED), encoding="utf-8")
    code, out, _ = run("check", "result", str(path))
    assert code == 0
    assert "Failed, real deploy" in out
    assert ("Flow Gift_Thank_You_Task (Gift_Thank_You_Task.flow) line 27:5: The element Create_Task is duplicated"
            in out)
    assert "ShiftServiceTest.addsHours: System.AssertException: Assertion Failed" in out
    assert "Test failures (1 of 4 run)" in out
    assert "ShiftTrigger: Test coverage of selected Apex Trigger is 0%" in out
    assert "CLI said: FailedDeployError: Deploy failed." in out
    assert "fix the failures above" in out.splitlines()[-1]
    code, out, _ = run("check", "result", str(path), "--json")
    data = json.loads(out)
    assert data["counts"]["failed"] == 1 and data["counts"]["changed"] == 1
    assert data["component_failures"][0]["line"] == 27 and data["test_failures"][0]["method"] == "addsHours"


@pytest.mark.parametrize("shape", ["bom", "utf16", "utf16-nobom", "warning-first", "no-envelope", "trailing"])
def test_tolerated_shapes(tmp_path, shape):
    text = json.dumps(VALIDATE_OK, indent=2)
    path = tmp_path / "r.json"
    if shape == "bom":
        path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    elif shape == "utf16":
        path.write_bytes(text.encode("utf-16"))           # what Windows PowerShell 5.1 writes for `> file`
    elif shape == "utf16-nobom":
        path.write_bytes(text.encode("utf-16-le"))
    elif shape == "warning-first":
        path.write_text(" ›   Warning: @salesforce/cli update available from 2.150.6 to 2.158.7.\n" + text,
                        encoding="utf-8")
    elif shape == "no-envelope":
        path.write_text(json.dumps(VALIDATE_OK["result"]), encoding="utf-8")
    else:
        path.write_text(text + "\n ›   Warning: something after the JSON\n", encoding="utf-8")
    r = deploy_result.read(str(path))
    assert r.id == "0Af000000000003AAA" and r.counts()["created"] == 1
    if shape in ("bom", "utf16", "warning-first"):
        assert r.read_notes
    code, out, _ = run("check", "result", str(path))
    assert code == 0
    assert ("Read despite" in out) == bool(r.read_notes)


@pytest.mark.parametrize("content,cause", [
    (json.dumps({"status": 0, "result": {"records": [], "totalSize": 0}}), "not a deploy result"),
    (json.dumps({"status": 1, "name": "NoOrgFound", "message": "No authorization information found for x."}),
     "holds a CLI error"),
    ("Deploying v65.0 metadata to x using the v65.0 SOAP API.\nStatus: Failed\n", "human-readable output"),
    ("", "is empty"),
])
def test_unreadable_files_say_why(tmp_path, content, cause):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    code, out, err = run("check", "result", str(path))
    assert code == 2 and cause in err and out == ""


def test_missing_file_mentions_the_other_shell(tmp_path):
    code, _, err = run("check", "result", str(tmp_path / "nowhere.json"))
    assert code == 2 and "working folder" in err


def test_unfinished_deploy_points_at_report(tmp_path):
    path = tmp_path / "queued.json"
    path.write_text(json.dumps({"status": 0, "result": {"id": "0Af000000000005AAA", "status": "Queued",
                                                         "done": False}}), encoding="utf-8")
    code, out, _ = run("check", "result", str(path))
    assert code == 0 and "sf project deploy report --job-id 0Af000000000005AAA" in out


def test_changed_from_reads_the_same_tolerant_way(tmp_path):
    from check_fixtures import Tree
    t = Tree(tmp_path / "p")
    t.field("Shift__c", "Hours__c")
    path = tmp_path / "r.json"
    path.write_bytes(json.dumps(VALIDATE_OK).encode("utf-16"))
    r = t.check(changed_from=str(path))
    assert r["scope"]["in_scope"] == 1 and r["scope"]["deploy_result"]["id"] == "0Af000000000003AAA"
