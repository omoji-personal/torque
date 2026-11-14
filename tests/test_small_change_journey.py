"""The `small-change` recipe, walked: its `torque` commands run in its order against made-up inputs and a
stubbed Salesforce CLI, and each exits as the recipe says. A failing validation is read and named first.

Not walked: the `sf` steps themselves (their saved results are written here instead) and step 9,
`torque browser login`, which needs a browser tab; its own tests are in packages/browser_tests.
"""
import contextlib
import io
import json
from pathlib import Path

from check_fixtures import e10_project
from test_check_deployed import COMPONENTS, FakeSf, deploy_result
from test_check_result import DEPLOY_FAILED, VALIDATE_OK
from test_recipe_commands import commands_in
from torque import cli
from torque.checks import deployed

REPO = Path(__file__).resolve().parents[1]
RECIPE = REPO / "workflows" / "small-change.md"
# The recipe's torque steps in order (step 7's `torque deploy` is an alternative to the sf deploy).
STEPS = ["context", "lesson for", "check metadata", "check result", "check metadata", "check result",
         "check deployed", "browser login", "session add"]


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def heads():
    found = []
    for _, command, _ in commands_in(RECIPE.read_text(encoding="utf-8")):
        words = command.split()
        head = " ".join(words[1:3]) if words[1] in ("check", "lesson", "browser", "session") else words[1]
        # A step named again in the next sentence ("fix what `torque check result` names") is one step.
        if head != "deploy" and found[-1:] != [head]:
            found.append(head)
    return found


def test_the_recipe_lists_its_torque_steps_in_this_order():
    assert heads() == STEPS, heads()


def test_a_small_change_walked_from_context_to_the_session_record(tmp_path, monkeypatch):
    work = tmp_path / "firm"
    scope = ("--workspace", work, "--client", "acme")
    assert run("workspace", "init", work, "--name", "Firm", "--no-hook")[0] == 0
    assert run("client", "add", "Acme", "--workspace", work, "--org", "acme-sbx")[0] == 0

    # 1, 2: context and the lessons for the task.
    code, out, err = run("context", *scope)
    assert code == 0, err
    code, out, err = run("lesson", "for", "--task", "field-build", "--type", "CustomField", *scope)
    assert code == 0, err

    # 5: the files, scoped to the component; findings are reported and the report completes (exit 0).
    project = e10_project(tmp_path / "project").root
    code, out, err = run("check", "metadata", project, "--only", "CustomField:Shift__c.Hours__c", "--analyzer", "off")
    assert code == 0, err
    assert "doc.description-missing" in out and "Shift__c.Hours__c" in out

    # 6: a failed validation is read first: the result names the component, the line and the next step.
    failed = tmp_path / "dry-run-failed.json"
    failed.write_text(json.dumps(DEPLOY_FAILED), encoding="utf-8")
    code, out, err = run("check", "result", failed)
    assert code == 0, err                                     # the file was read, whatever the deploy outcome
    assert "Failed" in out and "Gift_Thank_You_Task" in out and "line 27" in out
    assert "fix the failures above" in out.splitlines()[-1]

    # 6 again, after the fix: the validation passes and its scope drives the metadata check.
    dry_run = tmp_path / "dry-run.json"
    dry_run.write_text(json.dumps(VALIDATE_OK), encoding="utf-8")
    code, out, err = run("check", "result", dry_run)
    assert code == 0 and "Succeeded, check-only" in out, err
    code, out, err = run("check", "metadata", project, "--changed-from", dry_run, "--analyzer", "off")
    assert code == 0, err

    # 7, 8: the deploy result, then the read-back against a stubbed org.
    fake = FakeSf(project)
    monkeypatch.setattr(deployed, "run_tool", fake)
    monkeypatch.setattr("jsc_advisory.sf.run_tool", fake)
    deploy = deploy_result(tmp_path / "deploy.json", COMPONENTS)
    code, out, err = run("check", "result", deploy)
    assert code == 0 and "Succeeded, real deploy" in out, err
    code, out, err = run("check", "deployed", "--target-org", "acme-sbx", "--changed-from", deploy,
                         "--source-dir", project, "--as-permission-set", "Shift_Coordinator", "--json")
    assert code == 0, err
    report = json.loads(out)
    assert report["components"] and all("--target-org" in call for call in fake.calls)

    # 10: the session record with what the check rested on and what is left in the org.
    code, out, err = run("session", "add", *scope, "--status", "verified", "--json",
                         "--summary", "Added Shift hours. Left in the org: nothing.",
                         "--check", "read-back", "--check", "rendered", "--as-user", "staff user",
                         "--in-org", "acme-sbx", "--not-covered", "mobile layout")
    assert code == 0, err
    record = json.loads(out)
    text = json.dumps(record)
    for word in ("read-back", "rendered", "staff user", "acme-sbx", "mobile layout", "Left in the org"):
        assert word in text, word
