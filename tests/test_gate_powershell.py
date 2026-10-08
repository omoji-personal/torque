"""PowerShell command lines in build-only mode. On Windows it is the shell Claude
Code's PowerShell tool and Antigravity's run_command use, so the gate has to read
its cmdlets, its quoting and the ways it runs a string as a command."""
import base64
import json

import pytest
from torque import gate


def encoded(text):
    return base64.b64encode(text.encode("utf-16-le")).decode()


@pytest.fixture
def root(tmp_path):
    for folder in ("clients/acme", "src", ".agents/rules", ".claude"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "clients/acme/notes.md").write_text("x", encoding="utf-8")
    (tmp_path / ".agents/hooks.json").write_text("{}", encoding="utf-8")
    (tmp_path / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic", "ai_access": "build-only"}),
        encoding="utf-8")
    return tmp_path


def decide(root, command, tool="PowerShell"):
    return gate.decide(tool, {"command": command}, root, "build-only", root)


ORG_CALLS = [
    "sf.cmd data query --target-org example -q 'SELECT Id FROM Account'",
    "& sf.cmd project deploy start --target-org example",
    r"& 'C:\Program Files\sf\bin\sf.cmd' project deploy start -o example",
    "sf.ps1 project deploy start -o example",
    "SF.CMD Project Deploy Start -o example",
    "s`f.cmd project deploy start -o example",
    "sf.cmd project deploy start `\n -o example",
    "cmd /c sf project deploy start -o example",
    "cmd /c 'sf.cmd project deploy start -o example'",
    "cmd.exe /k \"sf project deploy start -o example\"",
    "iex 'sf.cmd project deploy start --target-org example'",
    "Invoke-Expression 'sf project deploy start --target-org example'",
    "powershell -Command 'sf project deploy start -o example'",
    "powershell 'sf project deploy start -o example'",
    "pwsh -c 'sf.cmd data query -o example'",
    "powershell -Command \"iex 'sf project deploy start -o example'\"",
    "& ([scriptblock]::Create('sf.cmd project deploy start -o example'))",
    "Invoke-Command -ScriptBlock ([scriptblock]::Create('sf data query -o example'))",
    "Start-Process sf.cmd -ArgumentList 'project','deploy','start','-o','example'",
    "Start-Job { sf.cmd project deploy start -o example }",
    "1..1 | ForEach-Object { sf.cmd data query -o example }",
    "powershell -EncodedCommand " + encoded("sf.cmd project deploy start -o example"),
    "pwsh -enc " + encoded("sf data query -o example"),
    "powershell.exe -NoProfile -e " + encoded("sf data query -o example"),
    r"node C:\Users\x\AppData\Roaming\npm\node_modules\@salesforce\cli\bin\run.js project deploy start -o example",
    "npx @salesforce/cli@latest data query -o example",
    r".\.venv\Scripts\torque.exe context --workspace . --client acme",
]
CLIENT_CONTEXT = [
    r"Get-Content clients\acme\notes.md",
    r"type .\clients\acme\notes.md",
    r"Set-Location clients\acme; Get-Content notes.md",
    "Get-ChildItem -Recurse",
    "Get-ChildItem -Recurse -Path .",
    "gci -r",
    "GCI -REC .",
    "dir -s",
    "ls -r .",
    "Get-ChildItem . -Depth 3",
    "Get-ChildItem -Recurse | Select-String secret",
    "cd ..; gci -r",
    "Set-Location src; Set-Location ..; Get-ChildItem -Recurse",
    r"Copy-Item -Recurse . C:\Temp\out",
    r"Copy-Item -Recurse clients C:\Temp\out",
    "tree /f",
    "cmd /c dir /s /b",
    "cmd /c \"dir /s /b\"",
    "cmd /c findstr /s /i secret *.md",
    r"xcopy clients C:\Temp\out /s",
    r"robocopy clients C:\Temp\out /E",
]
HOOK_CONFIGURATION = [
    "Set-Content workspace.json '{}'",
    r"Set-Content .agents\hooks.json '{}'",
    r"Remove-Item .agents\hooks.json",
    r"ren .agents\hooks.json hooks.off",
    r"Clear-Content .agents\hooks.json",
    "Remove-Item -Recurse -Force .agents",
    "Rename-Item .agents .agents-off",
    "Move-Item .agents ..",
    r"Remove-Item -Recurse .claude",
    "cmd /c rd /s /q .agents",
]
ORDINARY_WORK = [
    "git status",
    "Get-Content README.md",
    "sf.cmd --version",
    "sf.cmd project generate --name demo",
    r".\.venv\Scripts\python.exe -m pytest -q tests",
    "Get-ChildItem",
    "dir",
    "Get-ChildItem src -Recurse",
    "gci -r src",
    "Copy-Item -Recurse src build",
    "Remove-Item -Recurse -Force build",
    r"Set-Content src\notes.md 'text with several words'",
    r"Get-Content .agents\rules\evidence.md",
    r"Set-Content .agents\rules\local.md 'x'",
    "git commit -m 'Describe sf project deploy start --target-org usage'",
    "cmd /c 'git status'",
    "powershell -Command 'git status'",
    "powershell -enc " + encoded("git status"),
    "Write-Output 'iex is only mentioned here'",
]


@pytest.mark.parametrize("command", ORG_CALLS)
def test_org_calls_are_blocked_however_powershell_runs_them(root, command):
    allowed, reason = decide(root, command)
    assert not allowed and ("Salesforce org" in reason or "torque" in reason), reason


@pytest.mark.parametrize("command", CLIENT_CONTEXT)
def test_client_context_is_blocked_for_cmdlets_and_cmd_switches(root, command):
    allowed, reason = decide(root, command)
    assert not allowed and "client context" in reason, reason


@pytest.mark.parametrize("command", HOOK_CONFIGURATION)
def test_hook_configuration_is_kept_from_powershell(root, command):
    allowed, reason = decide(root, command)
    assert not allowed and ("hook configuration" in reason or "workspace.json" in reason), reason


@pytest.mark.parametrize("command", ORDINARY_WORK)
def test_ordinary_powershell_work_is_allowed(root, command):
    assert decide(root, command) == (True, ""), command


def test_an_encoded_command_that_cannot_be_read_is_blocked(root):
    allowed, reason = decide(root, "powershell -enc not-base64!!")
    assert not allowed and "encoded PowerShell command" in reason


def test_a_quoted_string_is_a_command_only_beside_something_that_runs_one():
    inner = "sf data query -o example"
    assert inner in gate._powershell_commands(f"iex '{inner}'")
    assert inner in gate._powershell_commands(f"cmd.exe /c '{inner}'")
    assert gate._powershell_commands(f"git commit -m '{inner}'") == [f"git commit -m '{inner}'"]
    # A batch file's extension is not the cmd command.
    assert gate._powershell_commands(f"npm.cmd run note -- '{inner}'") == [f"npm.cmd run note -- '{inner}'"]


def test_more_quoted_strings_than_the_gate_reads_are_blocked(root):
    command = "iex " + " ".join(f"'echo word{n}'" for n in range(gate._PS_MAX_STRINGS + 1))
    allowed, reason = decide(root, command)
    assert not allowed and "quoted strings" in reason


def test_the_same_text_keeps_its_bash_meaning_for_the_bash_tool(root):
    # ls -r is reverse order in Bash and -Recurse in PowerShell.
    assert decide(root, "ls -r .", "Bash") == (True, "")
    assert not decide(root, "ls -r .")[0]


@pytest.mark.parametrize("command", [
    "node /usr/lib/node_modules/@salesforce/cli/bin/run.js data query -o example",
    "node node_modules/@salesforce/cli/bin/run data query -o example",
    "npx @salesforce/cli@latest data query -o example",
    "rm -rf .agents",
    "mv .agents .agents-off",
    "cp other.json .agents/hooks.json",
])
def test_bash_gets_the_same_additions(root, command):
    assert not decide(root, command, "Bash")[0], command


def test_the_antigravity_hook_file_is_hook_configuration_for_the_file_tools(root):
    for tool in ("Write", "Edit"):
        allowed, reason = gate.decide(tool, {"file_path": str(root / ".agents" / "hooks.json")}, root, "build-only", root)
        assert not allowed and "hook configuration" in reason
    rule = {"file_path": str(root / ".agents" / "rules" / "local.md")}
    assert gate.decide("Write", rule, root, "build-only", root) == (True, "")
    assert gate.decide("Read", {"file_path": str(root / ".agents" / "hooks.json")}, root, "build-only", root) == (True, "")
