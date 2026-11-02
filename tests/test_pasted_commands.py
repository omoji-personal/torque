"""Commands Torque prints for someone to paste run as printed (audit item N15, round 2).

The approval request a refusal names gives the workspace by its own path, quoted for the shell
the session uses, so it works from a client project folder below the workspace, where a deploy
runs. The grant command `torque approval request` prints keeps every word whole in the
consultant's shell. The shells read the lines themselves: Windows PowerShell's own parser
(parse only), and Windows PowerShell, cmd.exe and Bash with `torque` replaced by something that
only records the words it was given. Nothing here starts Torque or a Salesforce tool."""
from collections import namedtuple
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from torque import approval, browser_guard, changes, cli, consent, gate_connected as gc, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")
Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url")
ORG = Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")
SYSTEM = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
CMD = os.environ.get("COMSPEC") if os.name == "nt" else None
POWERSHELL = SYSTEM / "WindowsPowerShell" / "v1.0" / "powershell.exe" if CMD else None
POWERSHELL = POWERSHELL if POWERSHELL is not None and POWERSHELL.is_file() else None
BASH = shutil.which("bash")
ECHO = "import json, sys\nopen(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(sys.argv[2:]))\n"
WRITE = "sf project deploy start -m Flow:Case_Escalation -o acme-sbx"


def make_workspace(path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(path.parent / "home"))
    monkeypatch.setenv("APPDATA", str(path.parent / "appdata"))
    root = ws.init_workspace(path, "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    letter = path.parent / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-sbx"], ["Contact"],
                           presence=YES, resolve={"acme-sbx": ORG}.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    monkeypatch.setattr("jsc_revert.org_detect.resolve_org", lambda alias, **k: ORG if alias == "acme-sbx" else None)
    return Path(os.path.realpath(root))


def _powershell(script: str, folder: Path) -> None:
    """Run a script in Windows PowerShell. PowerShell is not one of the offline runner's local
    tools, so cmd.exe (which is) starts it, as the review's reproduction did; the script only
    parses, or hands words to the recording program, and its PATH holds Windows' own folders."""
    (folder / "script.ps1").write_text(script, encoding="utf-8-sig")
    (folder / "run.cmd").write_text(f'@"{POWERSHELL}" -NoProfile -NonInteractive -ExecutionPolicy Bypass '
                                    '-File "%~dp0script.ps1"\n', encoding="utf-8")
    env = {"PATH": f"{SYSTEM}{os.pathsep}{SYSTEM / 'WindowsPowerShell' / 'v1.0'}",
           "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": str(folder), "TMP": str(folder),
           "USERPROFILE": str(folder)}
    subprocess.run([CMD, "/d", "/c", str(folder / "run.cmd")], cwd=folder, env=env, capture_output=True,
                   timeout=180, stdin=subprocess.DEVNULL)


PARSE = r"""
$lines = [IO.File]::ReadAllText((Join-Path $PWD 'lines.json'), [Text.Encoding]::UTF8) | ConvertFrom-Json
$found = foreach ($line in $lines) {
  $t = $null; $e = $null
  $ast = [System.Management.Automation.Language.Parser]::ParseInput($line, [ref]$t, [ref]$e)
  if ($e.Count) { 'ERROR ' + $e[0].Message; continue }
  $words = foreach ($part in $ast.EndBlock.Statements[0].PipelineElements[0].CommandElements) {
    if ($part -is [System.Management.Automation.Language.StringConstantExpressionAst] -or
        $part -is [System.Management.Automation.Language.ExpandableStringExpressionAst]) { $part.Value }
    elseif ($part -is [System.Management.Automation.Language.CommandParameterAst]) { $part.Extent.Text }
    else { 'NOT TEXT ' + $part.Extent.Text }
  }
  @($words) -join [char]31
}
[IO.File]::WriteAllText((Join-Path $PWD 'words.json'), (ConvertTo-Json -InputObject @($found)))
"""


def powershell_words(lines: list[str], folder: Path) -> list[list[str]]:
    """The words Windows PowerShell's parser reads in each line. Parse only: nothing runs."""
    folder.mkdir(exist_ok=True)
    (folder / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
    _powershell(PARSE, folder)
    found = json.loads((folder / "words.json").read_text(encoding="utf-8-sig"))
    found = [found] if isinstance(found, str) else found
    assert not [line for line in found if line.startswith("ERROR ")], found
    return [line.split("\x1f") for line in found]


def _recorder(folder: Path) -> list[str]:
    folder.mkdir(exist_ok=True)
    (folder / "echo.py").write_text(ECHO, encoding="utf-8")
    return [sys.executable, str(folder / "echo.py"), str(folder / "got.json")]


def powershell_hands_on(line: str, folder: Path) -> list[str]:
    """What a program receives when Windows PowerShell runs the line with `torque` replaced by
    a recording program, called as a program is (so `--` and every word go to it as PowerShell
    hands them on)."""
    assert line.startswith("torque ")
    _powershell("& " + ws.command_text(_recorder(folder), windows=True) + line[len("torque"):], folder)
    return json.loads((folder / "got.json").read_text(encoding="utf-8"))


def cmd_hands_on(line: str, folder: Path) -> list[str]:
    """What a program receives when cmd.exe runs the line, `torque` replaced by a recording program."""
    assert line.startswith("torque ")
    program = subprocess.list2cmdline(_recorder(folder))
    (folder / "line.cmd").write_text("@echo off\n" + program + line[len("torque"):] + "\n", encoding="utf-8")
    subprocess.run([CMD, "/d", "/c", str(folder / "line.cmd")], cwd=folder, capture_output=True, timeout=120,
                   stdin=subprocess.DEVNULL)
    return json.loads((folder / "got.json").read_text(encoding="utf-8"))


def bash_hands_on(line: str, folder: Path) -> list[str]:
    """The words Bash hands `torque`, a shell function that records them."""
    folder.mkdir(exist_ok=True)
    prelude = "torque() { for a in \"$@\"; do printf '%s\\0' \"$a\"; done > got.bin; }\n"
    (folder / "line.sh").write_bytes((prelude + line + "\n").encode("utf-8"))
    env = {"PATH": os.path.dirname(BASH), "HOME": str(folder), "SystemRoot": os.environ.get("SystemRoot", "")}
    subprocess.run([BASH, "--noprofile", "--norc", "line.sh"], cwd=folder, env=env, capture_output=True, timeout=60,
                   stdin=subprocess.DEVNULL)
    return (folder / "got.bin").read_bytes().decode("utf-8").split("\0")[:-1]


# Words a path or a value can hold: spaces, apostrophes (straight and curly), what PowerShell
# or cmd.exe reads as their own syntax, what double quotes do not protect, a UNC path and a
# trailing backslash. (Not `--%`: Windows PowerShell 5.1 drops that word on its way to a
# program even in quotes, seen here on 2026-10-29; no path or name Torque prints is that word.)
WORDS = [
    "plain-word_1.2:3=4+5/6", r"C:\Users\example\firm", r"C:\Users\example\firm's-work",
    r"C:\Users\user name\work space", r"C:\Users\example\a&b;c(d)e,f@g#h^i|j<k>l", r"C:\Users\example\pay$5 `tick",
    r"C:\Users\example\100%", "C:\\Users\\example\\it\u2019s", r"\\server\share\firm", "trailing\\",
    "with space\\", "wow!", "@first", "#first", "-dash",
]


def grant_argv(word: str) -> list[str]:
    return ["torque", "approval", "grant", "req-0123456789ab", "--workspace", word, "--client", "Acme"]


@pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell")
def test_windows_form_is_read_whole_by_windows_powershell(tmp_path):
    # A word that ends in a backslash and holds a space is written with that backslash doubled:
    # Windows PowerShell's parser keeps both, and the program receives one (checked below).
    same = [word for word in WORDS if not (word.endswith("\\") and " " in word)]
    lines = [ws.command_text(grant_argv(word), windows=True) for word in same]
    assert powershell_words(lines, tmp_path / "parse") == [grant_argv(word) for word in same]
    for word in WORDS:
        line = ws.command_text(grant_argv(word), windows=True)
        assert powershell_hands_on(line, tmp_path / "run") == grant_argv(word)[1:], line


@pytest.mark.skipif(CMD is None, reason="needs cmd.exe")
def test_windows_form_is_read_whole_by_cmd(tmp_path):
    # A word only PowerShell's single quotes can carry is not cmd.exe's; the rest must be. A
    # batch file is read in the console's code page, so the words here are ASCII.
    shared = [word for word in WORDS
              if word.isascii() and not ws.command_text([word], windows=True).startswith("'")]
    assert len(shared) >= 10
    for word in shared:
        line = ws.command_text(grant_argv(word), windows=True)
        assert cmd_hands_on(line, tmp_path / "cmd") == grant_argv(word)[1:], line


@pytest.mark.skipif(BASH is None, reason="needs Bash")
def test_posix_form_is_read_whole_by_bash(tmp_path):
    for word in WORDS:
        line = ws.command_text(grant_argv(word), windows=False)
        assert bash_hands_on(line, tmp_path / "bash") == grant_argv(word)[1:], line


def test_the_printed_grant_command_survives_the_consultants_shell(tmp_path, monkeypatch, capsys):
    root = make_workspace(tmp_path / "firm's work", monkeypatch)
    cid = changes.create_change(root, "Acme", "Layout", "Cases show Tier", [], "acme-sbx")["id"]
    monkeypatch.chdir(root)
    assert cli.main(["approval", "request", "--workspace", ".", "--client", "Acme", "--change", cid,
                     "--org", "acme-sbx", "--browser", "--purpose", "Review the Case layout"]) == 0
    out = capsys.readouterr().out
    line = next(text.strip() for text in out.splitlines() if text.strip().startswith("torque approval grant "))
    request_id = re.search(r"Request (req-[0-9a-f]{12}) recorded", out).group(1)
    expected = ["torque", "approval", "grant", request_id, "--workspace", str(root), "--client", "Acme"]
    checked = 0
    if POWERSHELL is not None:
        assert powershell_words([line], tmp_path / "parse") == [expected], line
        assert powershell_hands_on(line, tmp_path / "ps") == expected[1:], line
        checked += 1
    if CMD is not None:
        assert cmd_hands_on(line, tmp_path / "cmd") == expected[1:], line
        checked += 1
    if BASH is not None:
        # Git Bash on Windows reads the Windows form the same for a path like this one.
        assert bash_hands_on(line, tmp_path / "bash") == expected[1:], line
        checked += 1
    assert checked, "no shell to check the printed line with"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A connected workspace and a Salesforce project folder below it, the session's working folder."""
    root = make_workspace(tmp_path / "firm", monkeypatch)
    folder = root / "clients" / "acme" / "projects" / "demo"
    flow = folder / "force-app" / "main" / "default" / "flows" / "Case_Escalation.flow-meta.xml"
    flow.parent.mkdir(parents=True)
    (folder / "sfdx-project.json").write_text(json.dumps({"packageDirectories": [{"path": "force-app"}]}),
                                              encoding="utf-8")
    flow.write_text("<Flow/>", encoding="utf-8")
    monkeypatch.chdir(folder)
    return root, Path(os.path.realpath(folder))


@pytest.mark.parametrize("source", ["write from Bash", "window from Bash", "write from PowerShell",
                                    "window from Torque's browser"])
def test_the_request_a_refusal_names_runs_from_a_project_folder(project, tmp_path, monkeypatch, capsys, source):
    root, folder = project
    cid = changes.create_change(root, "Acme", "Flow", "Cases escalate", [], "acme-sbx")["id"]
    if source.endswith("Torque's browser"):
        monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "acme"))
        with pytest.raises(browser_guard.GuardRefused) as caught:
            browser_guard.connected_guard("acme-sbx")
        told, windows = str(caught.value), os.name == "nt"
    else:
        tool = "PowerShell" if source.endswith("PowerShell") else "Bash"
        command = WRITE if source.startswith("write") else "torque browser run --target-org acme-sbx"
        decision = gc.decide_connected(tool, {"command": command}, root, folder, env={"TORQUE_CLIENT": "acme"},
                                       permission_mode="default", session_id="s1", tool_use_id="t1")
        assert decision.action == "deny"
        told, windows = decision.reason, tool == "PowerShell"
    if windows and POWERSHELL is None:
        pytest.skip("needs Windows PowerShell to read a PowerShell line")
    hint = re.search(r"`(torque approval request [^`]+)`", told).group(1)
    # What the session fills in before it runs the request.
    line = hint.replace("--change ID", f"--change {cid}").replace("--purpose TEXT", "--purpose Review-the-Case-layout")
    line = line.replace("<this exact command>", WRITE)
    words = ["torque", *powershell_hands_on(line, tmp_path / "ps")] if windows else shlex.split(line)
    assert words[words.index("--workspace") + 1] == str(root)
    assert cli.main(words[1:]) == 0, (source, line, capsys.readouterr().err)
    capsys.readouterr()
    assert cli.main(["approval", "list", "--workspace", str(root), "--client", "Acme", "--json"]) == 0
    requests = json.loads(capsys.readouterr().out)["requests"]
    assert len(requests) == 1
    if source.startswith("write"):
        # Recorded for the folder the write runs in, the project folder.
        request = approval.load_request(root, "Acme", requests[0]["id"])
        assert request["command"] == WRITE and os.path.realpath(request["cwd"]) == str(folder)


def test_the_consent_hint_beside_them_names_the_workspace_too(project):
    root, folder = project
    told = gc.decide_connected("Bash", {"command": "sf org list"}, root, folder, env={"TORQUE_CLIENT": "acme"},
                               permission_mode="default", session_id="s1", tool_use_id="t1").reason
    words = shlex.split(re.search(r"`(torque client consent show [^`]+)`", told).group(1))
    assert words[words.index("--workspace") + 1] == str(root) and cli.main(words[1:]) == 0


@pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell")
def test_the_printed_repair_command_reads_any_path_as_written(tmp_path, monkeypatch):
    from jsc_common import winacl
    monkeypatch.setattr(winacl, "problem", lambda path: "access")
    paths = [r"C:\tq\plain\approval.key", r"C:\tq\with space\approval.key", r"C:\tq\it's\approval.key",
             r"C:\tq\$env\$(x)\approval.key", r"C:\tq\back`tick\approval.key", r"C:\tq\a;b&c\approval.key",
             r"C:\tq\curly" + chr(0x2019) + r"s\approval.key", r"C:\tq\ends with" + chr(92)]
    assert not [path for path in paths if any(ord(char) < 32 for char in path)]
    lines = []
    for path in paths:
        message = winacl.refusal(path, key=True)
        lines.append(message.split("Delete it with: ", 1)[1])
    assert powershell_words(lines, tmp_path / "parse") == [["Remove-Item", "-LiteralPath", path] for path in paths]
