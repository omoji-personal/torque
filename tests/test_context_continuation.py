"""Private continuation and explicit export targets; all metadata calls are mocked."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from torque import changes, cli, workspace as ws
from jsc_qa.dispatcher import DispatchResult


@pytest.fixture
def engagement(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    alpha = ws.add_client(root, "Alpha")
    beta = ws.add_client(root, "Beta")
    item = changes.create_change(root, "Alpha", "Contact preference", "Staff can find preference",
                                 ["Select preference", "Save preference", "Negative permission case"])
    other = changes.create_change(root, "Beta", "BETA_PRIVATE_SENTINEL", "Other client")
    changes.add_note(root, "Beta", other["id"], "BETA_PRIVATE_DECISION", "decision")
    return root, alpha, beta, item


def invoke(*args):
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli.main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


def observe(root, identifier, summary="Exact synthetic deployment observed"):
    response = DispatchResult("MetaAPI", "PASS", summary,
                              raw_output='{"synthetic_private_raw":"RAW_EVIDENCE_SENTINEL"}',
                              metadata={"operation": "deployment"})
    with patch("jsc_qa.dispatcher.dispatch_meta_api", return_value=response):
        return changes.verify_deploy(root, "Alpha", identifier, "explicit-dev", "0Af000000000001AAA",
                                     ["CustomField:Account.Preference__c"])


def test_context_retains_reported_failures_decisions_and_observed_scope_without_raw_contents(engagement):
    root, alpha, beta, item = engagement
    proof = alpha / "artifacts/operator.txt"
    proof.write_text("OPERATOR_EVIDENCE_CONTENT_SENTINEL")
    changes.add_check(root, "Alpha", item["id"], "AC1", "pass", "Human reports selection", proof)
    changes.add_check(root, "Alpha", item["id"], "AC2", "fail", "Human reports failed save")
    changes.add_note(root, "Alpha", item["id"], "Keep it optional", "decision")
    changes.add_note(root, "Alpha", item["id"], "Reproduce failed save", "next_step")
    observe(root, item["id"])
    context = ws.get_context(root, "Alpha")
    current = context["changes"][0]
    assert [criterion["reported_result"] for criterion in current["criteria"]] == ["pass", "fail", "not_run"]
    assert current["assessment"]["reported_pass"] == 1
    assert current["assessment"]["business_acceptance_independently_verified"] is False
    assert current["assessment"]["not_yet_reported_pass"] == ["AC2", "AC3"]
    assert current["decisions"][0]["summary"] == "Keep it optional"
    assert current["next_steps"][0]["summary"] == "Reproduce failed save"
    observation = current["metadata_observations"][0]
    assert observation["result"] == "pass"
    assert observation["basis"] == "salesforce_metadata_api"
    assert observation["evidence_integrity"] == "matches_capture"
    assert observation["business_acceptance_proven"] is False
    assert observation["target_org"] == "explicit-dev"
    assert observation["job_id"] == "0Af000000000001AAA"
    serialized = json.dumps(context)
    assert all(token not in serialized for token in ("BETA_PRIVATE", "RAW_EVIDENCE_SENTINEL", "OPERATOR_EVIDENCE_CONTENT_SENTINEL"))
    assert "events" not in current and "raw_output" not in serialized
    assert "torque change show " + item["id"] in current["show_command"]
    assert "--client alpha" in current["show_command"]


def test_histories_are_bounded_but_counts_and_full_history_are_retained(engagement):
    root, alpha, beta, item = engagement
    for number in range(7):
        changes.add_note(root, "Alpha", item["id"], f"Decision {number}", "decision")
        changes.add_note(root, "Alpha", item["id"], f"Next step {number}", "next_step")
        observe(root, item["id"], f"Metadata observation {number}")
    summary = ws.get_context(root, "Alpha")["changes"][0]
    assert summary["history_counts"] == {"decisions": 7, "metadata_observations": 7, "next_steps": 7}
    assert all(summary["history_truncated"].values())
    assert "latest five" in summary["history_note"]
    for name in summary["history_counts"]:
        assert len(summary[name]) == 5
        assert summary[name][0]["summary"].endswith("2")
        assert summary[name][-1]["summary"].endswith("6")
    assert len(changes.get_change(root, "Alpha", item["id"])["events"]) == 21


def test_plain_context_exposes_unresolved_criteria_next_steps_and_show_hint(engagement):
    root, alpha, beta, item = engagement
    changes.add_check(root, "Alpha", item["id"], "AC1", "fail", "Save failed")
    changes.add_note(root, "Alpha", item["id"], "Investigate failed save", "next_step")
    code, output, error = invoke("context", "--workspace", str(root), "--client", "Alpha")
    assert code == 0 and error == ""
    assert "AC1 (fail)" in output and "AC2 (not_run)" in output
    assert "Investigate failed save" in output
    assert "Full change: torque change show " + item["id"] in output
    assert "operator-reported" in output and "BETA_PRIVATE" not in output


@pytest.mark.parametrize("change,json_output", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("via_symlink", [False, True])
def test_handoff_cannot_export_into_same_firm_sibling_client(engagement, tmp_path, change, json_output, via_symlink):
    root, alpha, beta, item = engagement
    changes.add_note(root, "Alpha", item["id"], "ALPHA_PRIVATE_DECISION", "decision")
    output = beta / "artifacts/alpha-handoff.md"
    if via_symlink:
        link = tmp_path / "looks-like-export"
        link.symlink_to(beta / "artifacts", target_is_directory=True)
        output = link / "alpha-handoff.md"
    command = ["change", "handoff", item["id"]] if change else ["handoff"]
    command += ["--workspace", str(root), "--client", "Alpha", "--output", str(output)]
    if json_output:
        command.append("--json")
    code, stdout, error = invoke(*command)
    assert code == 2 and "different client" in error
    assert stdout == "" and not output.exists()
    assert not list((beta / "artifacts").iterdir())


@pytest.mark.parametrize("change,json_output", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("destination", ["client", "firm", "outside"])
def test_explicit_new_exports_work_in_selected_client_firm_and_outside(engagement, tmp_path, change, json_output, destination):
    root, alpha, beta, item = engagement
    changes.add_note(root, "Alpha", item["id"], "ALPHA_EXPORT_SENTINEL", "decision")
    directory = {"client": alpha / "artifacts", "firm": root, "outside": tmp_path}[destination]
    output = directory / "handoff.md"
    command = ["change", "handoff", item["id"]] if change else ["handoff"]
    command += ["--workspace", str(root), "--client", "Alpha", "--output", str(output)]
    if json_output:
        command.append("--json")
    code, stdout, error = invoke(*command)
    assert code == 0 and error == ""
    assert "ALPHA_EXPORT_SENTINEL" in output.read_text()
    before = output.read_bytes()
    assert invoke(*command)[0] == 2
    assert output.read_bytes() == before


def project_layout(root, pyproject):
    root.mkdir()
    (root / "src/torque").mkdir(parents=True)
    (root / "src/torque/__init__.py").write_text("")
    (root / "src/torque/workspace.py").write_text("# synthetic source layout")
    (root / "pyproject.toml").write_text(pyproject)


def test_wheel_in_unrelated_private_git_venv_does_not_block_private_workspace(tmp_path, monkeypatch):
    project = tmp_path / "consulting-project"
    project_layout(project, '[project]\nname = "consulting-tools"\n[tool.example]\nname = "torque-salesforce"\n')
    (project / ".git").mkdir()
    monkeypatch.setattr(ws, "__file__", str(project / ".venv/lib/python3.10/site-packages/torque/workspace.py"))
    assert ws._source_checkout() is None
    created = ws.init_workspace(project / "private-workspace", "Synthetic private consulting")
    assert created == project / "private-workspace"


@pytest.mark.parametrize("worktree", [False, True])
def test_actual_torque_checkout_with_git_directory_or_worktree_marker_is_still_protected(tmp_path, monkeypatch, worktree):
    project = tmp_path / "torque-source"
    project_layout(project, '[project]\nname = "torque-salesforce" # packaged project identity\n')
    if worktree:
        (project / ".git").write_text("gitdir: /synthetic/worktree")
    else:
        (project / ".git").mkdir()
    monkeypatch.setattr(ws, "__file__", str(project / ".venv/lib/python3.10/site-packages/torque/workspace.py"))
    assert ws._source_checkout() == project
    with pytest.raises(ws.WorkspaceError, match="source checkout"):
        ws.init_workspace(project / "private", "Not in public source")
    assert not (project / "private").exists()


def test_project_name_inside_multiline_value_is_not_a_torque_project(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nreadme = """\nname = "torque-salesforce"\n"""\nname = "consulting-tools"\n')
    assert ws._torque_project(path) is False


def test_python_310_project_identity_fallback_uses_only_explicit_project_name(tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def without_tomllib(name, *args, **kwargs):
        if name == "tomllib":
            raise ModuleNotFoundError("Synthetic Python 3.10")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_tomllib)
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nname = "torque-salesforce" # comment\n[tool.other]\nname = "unrelated"\n')
    assert ws._torque_project(path) is True
    path.write_text('[project]\nname = "consulting-tools"\n[tool.other]\nname = "torque-salesforce"\n')
    assert ws._torque_project(path) is False
    path.write_text('[project]\nreadme = """\nname = "torque-salesforce"\n"""\nname = "consulting-tools"\n')
    assert ws._torque_project(path) is False
