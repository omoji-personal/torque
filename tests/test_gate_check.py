"""Build-only mode and `torque check`: `check metadata` and `check result` read local files
only, so they run when the whole command is that one check and nothing they would read
(each PATH, the baseline, the result, the project folder and Code Analyzer's workspace)
reaches clients/. `check deployed` reads an org and stays blocked. Synthetic workspace only."""
import os
from pathlib import Path

import pytest

from torque import cli, gate, workspace as ws

PROJECT_ALLOWED = [
    "torque check metadata force-app --analyzer off",
    "torque check result result.json",
    "torque check metadata force-app --changed-from result.json",
    "torque check metadata force-app --baseline baseline",
    "torque check metadata force-app --only Flow:Gift_Entry --json --fail-on error --ignore doc.help-text-missing",
    "torque check metadata force-app --analyzer=off --changed-from=result.json",
    "torque check metadata force-app/main/default/flows/Gift_Entry.flow-meta.xml",
    "torque check metadata",                                  # the current folder, project/
    "torque check result result.json --json",
    "python -m torque check metadata force-app --analyzer off",
    "torque check --help",
    "torque check metadata --help",
    "torque check result -h",
    "torque check deployed --help",
]
ROOT_ALLOWED = [
    "torque check metadata project/force-app --changed-from project/result.json",
    "torque check result project/result.json",
    "torque check metadata project/force-app project/baseline --analyzer off",
]
PROJECT_BLOCKED = [
    # reads an org
    "torque check deployed --target-org S --changed-from result.json",
    "torque check deployed --changed-from result.json",
    # other subcommands, and none
    "torque check",
    "torque check results result.json",
    "torque check metadata-all force-app",
    # a path inside clients/, or one that reaches it
    "torque check metadata ../clients/acme",
    "torque check metadata ../clients/acme/force-app --analyzer off",
    "torque check result ../clients/acme/result.json",
    "torque check metadata force-app --changed-from ../clients/acme/result.json",
    "torque check metadata force-app --baseline ../clients/acme",
    "torque check metadata force-app --baseline=../clients",
    "torque check metadata force-app/../../clients/acme",
    "torque check metadata ..",                               # the workspace root holds clients/
    "torque check metadata force-app ..",
    # a worktree copy's clients/
    "torque check metadata ../.claude/worktrees/copy/clients/acme --analyzer off",
    "torque check metadata ../.claude/worktrees/copy --analyzer off",
    # an org, a client, an initiative, a workspace
    "torque check metadata force-app --target-org S",
    "torque check metadata force-app -o S",
    "torque check metadata force-app --client acme",
    "torque check metadata force-app --client=acme",
    "torque check metadata force-app --initiative playbook",
    "torque check metadata force-app --workspace .",
    "torque check result result.json --target-org S",
    # an option cut short, an unknown option, a bad value, `--`
    "torque check metadata force-app --changed result.json",
    "torque check metadata force-app --chan=result.json",
    "torque check metadata force-app --analyz off",
    "torque check metadata force-app --target S",
    "torque check metadata force-app --analyzer maybe",
    "torque check metadata force-app --json=yes",
    "torque check metadata force-app --changed-from",
    "torque check metadata -- force-app",
    "torque check metadata force-app --help --client acme",
    "torque check result",
    "torque check result result.json other.json",
    # words the shell expands
    "torque check metadata $X",
    'torque check metadata "$X"',
    "torque check metadata force-*",
    "torque check metadata ~/force-app",
    "torque check metadata force-app --changed-from ${R}",
    # anything else on the line
    "torque check metadata force-app --analyzer off && echo done",
    "torque check metadata force-app --analyzer off; ls",
    "ls; torque check metadata force-app --analyzer off",
    "torque check metadata force-app --analyzer off | cat",
    "torque check metadata force-app --json > report.json",
    "torque check metadata force-app > ../sfdx-project.json",
    "cd . && torque check metadata force-app --analyzer off",
    "bash -c 'torque check metadata force-app --analyzer off'",
    "env -C . torque check metadata force-app --analyzer off",
    "time torque check metadata force-app --analyzer off",
    "TORQUE_WORKSPACE=.. torque check metadata force-app --analyzer off",
    "echo $(torque check result result.json)",
    # the legacy scripts stay help-only
    "jsc-qa check metadata force-app",
]


@pytest.fixture
def root(tmp_path, monkeypatch):
    for name in ("TORQUE_WORKSPACE", "JSC_ROOT", "TORQUE_CLIENT"):
        monkeypatch.delenv(name, raising=False)
    base = ws.init_workspace(tmp_path / "workspace", "Synthetic workspace")
    ws.add_client(base, "Acme")
    base = Path(os.path.realpath(base))
    project = base / "project"
    flows = project / "force-app" / "main" / "default" / "flows"
    flows.mkdir(parents=True)
    (flows / "Gift_Entry.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    (project / "baseline").mkdir()
    (project / "sfdx-project.json").write_text('{"packageDirectories": [{"path": "force-app"}]}', encoding="utf-8")
    (project / "result.json").write_text("{}", encoding="utf-8")
    (base / "clients" / "acme" / "force-app").mkdir(parents=True)
    (base / "clients" / "acme" / "result.json").write_text("{}", encoding="utf-8")
    ws.set_ai_access(base, "build-only")
    return base


def decide(root, command, cwd=None, tool="Bash"):
    return gate.decide(tool, {"command": command}, root, "build-only", cwd=cwd or root / "project")


@pytest.mark.parametrize("command", PROJECT_ALLOWED)
def test_check_metadata_and_result_run_in_build_only(root, command):
    assert decide(root, command) == (True, ""), command


@pytest.mark.parametrize("command", ROOT_ALLOWED)
def test_from_the_workspace_root_with_paths_under_project(root, command):
    assert decide(root, command, cwd=root) == (True, ""), command


@pytest.mark.parametrize("command", PROJECT_BLOCKED)
def test_every_other_check_form_stays_blocked(root, command):
    allowed, reason = decide(root, command)
    assert not allowed and reason, command


def test_the_workspace_root_is_not_a_path_to_check(root):
    for command in ("torque check metadata", "torque check metadata .", "torque check metadata . --analyzer off"):
        allowed, reason = decide(root, command, cwd=root)
        assert not allowed and "project/" in reason, command


def test_check_deployed_says_why_and_names_the_two_forms(root):
    _, reason = decide(root, "torque check deployed --target-org S --changed-from result.json")
    assert "reads an org" in reason and "torque check metadata PATH" in reason
    assert "torque check result RESULT.json" in reason and reason.endswith("Run it yourself outside the AI session.")


def test_a_chained_check_says_to_run_it_on_its_own(root):
    _, reason = decide(root, "torque check metadata force-app --analyzer off && echo done")
    assert "on its own" in reason


def _link(target: Path, link: Path):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import _winapi
        try:
            _winapi.CreateJunction(str(target), str(link))
            return
        except OSError as exc:
            pytest.skip(f"cannot create a link here: {exc}")
    pytest.skip("cannot create a link here")


def test_a_link_to_the_workspace_or_into_clients_is_read_where_it_leads(root):
    _link(root, root / "project" / "up")
    _link(root / "clients" / "acme", root / "project" / "acme")
    assert not decide(root, "torque check metadata up --analyzer off")[0]
    assert not decide(root, "torque check metadata acme --analyzer off")[0]
    assert not decide(root, "torque check result acme/result.json")[0]


def test_a_path_is_also_read_as_the_command_reads_it(root):
    # `deep/..` is project/ for the command (abspath first) but the link target's parent
    # for the system; a link `out` to a folder outside makes `out/../..` the workspace root.
    outside = root.parent / "outside" / "deep"
    outside.mkdir(parents=True)
    _link(outside, root / "project" / "out")
    assert not decide(root, "torque check metadata out/../.. --analyzer off")[0]
    assert decide(root, "torque check metadata out --analyzer off")[0]


def test_a_worktree_copy_does_not_change_the_answer(root):
    (root / ".claude" / "worktrees" / "copy" / "clients" / "acme").mkdir(parents=True)
    for command in PROJECT_ALLOWED:
        assert decide(root, command) == (True, ""), command
    assert not decide(root, "torque check metadata .claude/worktrees/copy/clients/acme --analyzer off", cwd=root)[0]


def test_a_project_folder_that_holds_clients_is_not_read(root):
    # A Salesforce project at the workspace root: Code Analyzer's workspace, and the folder
    # read around file paths, would be the whole workspace.
    (root / "sfdx-project.json").write_text("{}", encoding="utf-8")
    (root / "project" / "sfdx-project.json").unlink()
    flow = "project/force-app/main/default/flows/Gift_Entry.flow-meta.xml"
    for command in ("torque check metadata project/force-app", f"torque check metadata {flow} --analyzer off",
                    "torque check metadata project/force-app --changed-from project/result.json"):
        allowed, reason = decide(root, command, cwd=root)
        assert not allowed and "sfdx-project.json" in reason, command
    # Folders only, with the analyzer off: only those folders are read.
    assert decide(root, "torque check metadata project/force-app --analyzer off", cwd=root)[0]


def test_code_analyzers_shared_folder_must_not_hold_clients(root):
    (root / "project" / "sfdx-project.json").unlink()
    (root / "docs").mkdir()
    assert not decide(root, "torque check metadata force-app ../docs")[0]
    assert decide(root, "torque check metadata force-app ../docs --analyzer off")[0]
    assert decide(root, "torque check metadata force-app")[0]


def test_a_client_selected_in_the_environment_keeps_check_metadata_out(root, monkeypatch):
    # check metadata prints, and marks as shown, the lessons of the engagement TORQUE_WORKSPACE selects.
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "acme"))
    allowed, reason = decide(root, "torque check metadata force-app --analyzer off")
    assert not allowed and "TORQUE_WORKSPACE" in reason
    assert decide(root, "torque check result result.json")[0]          # reads no lessons
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root))
    assert decide(root, "torque check metadata force-app --analyzer off")[0]


POWERSHELL_ALLOWED = [
    "torque check metadata force-app --analyzer off",
    "torque check metadata .\\force-app --changed-from .\\result.json",
    "torque check result result.json --json",
]
POWERSHELL_BLOCKED = [
    "torque check metadata ..\\clients\\acme --analyzer off",
    "torque check metadata force-app --analyzer off; Get-ChildItem",
    "torque check metadata force-app --analyzer off | Out-File report.txt",
    "torque check metadata $env:X",
    "cmd /c torque check metadata force-app --analyzer off",
    "powershell -Command \"torque check metadata force-app --analyzer off\"",
    "torque check deployed --target-org S --changed-from result.json",
]


@pytest.mark.parametrize("command", POWERSHELL_ALLOWED)
def test_powershell_forms_that_run(root, command):
    assert decide(root, command, tool="PowerShell") == (True, ""), command


@pytest.mark.parametrize("command", POWERSHELL_BLOCKED)
def test_powershell_forms_that_stay_blocked(root, command):
    assert not decide(root, command, tool="PowerShell")[0], command


def test_the_allowed_forms_run_and_leave_clients_alone(root, monkeypatch, capsys):
    monkeypatch.chdir(root / "project")
    before = sorted(p.relative_to(root).as_posix() for p in (root / "clients").rglob("*"))
    assert decide(root, "torque check metadata force-app --analyzer off --json")[0]
    assert cli.main(["check", "metadata", "force-app", "--analyzer", "off", "--json"]) == 0
    assert '"command": "torque check metadata"' in capsys.readouterr().out
    assert sorted(p.relative_to(root).as_posix() for p in (root / "clients").rglob("*")) == before
