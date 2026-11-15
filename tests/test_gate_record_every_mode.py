"""N17: the promise that consent, approval and control records change only through
torque commands holds in every mode, for nested and Windows commands too.

Reproduces the end-of-audit check (tmp/final-check/test_full_mode_nested_shells.py):
on the base, full mode let `bash -c`, `sh -c`, `eval`, a variable-built path and every
PowerShell form overwrite or delete a consent record; build-only let `eval 'rm ...'`
through. The record check now reads each command with the build-only scan's own reader
(gate._read_commands), which unwraps bash -c / sh -c / zsh -c and eval, fills in a
variable set earlier in the command and the word of a ${NAME:-word}, decodes ANSI-C
quoting and reads PowerShell as the build-only scan does. Full mode stays as open as
before for ordinary commands. Synthetic workspace only."""
import hashlib
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

from torque import gate, gate_antigravity as ga, hosts


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "firm"
    (base / "clients" / "acme" / "approvals").mkdir(parents=True)
    (base / "project").mkdir()
    (base / "clients" / "acme" / "consent.json").write_text("{}", encoding="utf-8")
    (base / "clients" / "acme" / "notes.md").write_text("ok", encoding="utf-8")
    (base / ".torque").mkdir()
    (base / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(base))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TORQUE_CLIENT", raising=False)
    # gate_antigravity.main() states its host in this variable (and sets the project
    # directory); registering both here lets the teardown take them out again, so a
    # later test does not run as an Antigravity session.
    monkeypatch.setenv(hosts.HOOK_HOST_ENV, "")
    return base


def set_mode(root, mode):
    root.joinpath("workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Synthetic", "profile": "generic", "ai_access": mode}),
        encoding="utf-8")


def decide(root, tool, command):
    event = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command},
             "cwd": str(root)}
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = gate.evaluate(json.dumps(event))
    return code != 0


# The end-of-audit cases, both the writes (must be refused in every mode) and the plain
# reads of an ordinary note (full mode must still allow).
R = "clients/acme/consent.json"
W = "clients\\acme\\consent.json"
WRITES = [
    ("Bash", f"echo x > {R}"),
    ("Bash", f"rm {R}"),
    ("Bash", f"bash -c 'echo x > {R}'"),
    ("Bash", f"sh -c \"rm {R}\""),
    ("Bash", f"zsh -c 'rm {R}'"),
    ("Bash", f"bash -lc 'rm {R}'"),
    ("Bash", f"bash -c \"bash -c 'rm {R}'\""),
    ("Bash", f"eval 'rm {R}'"),
    ("Bash", f"eval rm {R}"),
    ("Bash", f"eval \"rm {R}\""),
    ("Bash", f"C=clients/acme; rm $C/consent.json"),
    ("Bash", f"C=clients/acme && rm \"${{C}}/consent.json\""),
    ("Bash", f"rm ${{Y:-{R}}}"),
    ("Bash", f"echo {R} | xargs rm"),
    ("Bash", f"echo $(rm {R})"),
    ("Bash", "git clean -fdx clients"),
    ("PowerShell", f"Remove-Item {W}"),
    ("PowerShell", f"Remove-Item -Path {W}"),
    ("PowerShell", f"powershell -Command \"Remove-Item {W}\""),
    ("PowerShell", f"Set-Content {W} '{{}}'"),
    ("PowerShell", f"$C = 'clients\\acme'; Remove-Item $C\\consent.json"),
]


@pytest.mark.parametrize("mode", ["full", "build-only"])
@pytest.mark.parametrize("tool,command", WRITES)
def test_a_record_write_is_refused_in_every_mode(root, mode, tool, command):
    set_mode(root, mode)
    before = hashlib.sha256((root / "clients" / "acme" / "consent.json").read_bytes()).hexdigest()
    assert decide(root, tool, command), (mode, tool, command)
    # The record on disk is untouched: a refusal is before the call runs, and the test
    # never runs the command itself.
    after = hashlib.sha256((root / "clients" / "acme" / "consent.json").read_bytes()).hexdigest()
    assert before == after


# Full mode stays open for ordinary commands, including ones that read (not change) a
# client note or merely mention a client folder in text.
FULL_ALLOWED = [
    ("Bash", "ls project"),
    ("Bash", f"cat {R.replace('consent.json', 'notes.md')}"),
    ("Bash", "C=clients/acme; cat $C/notes.md"),
    ("Bash", "rm -rf build"),
    ("Bash", "echo see clients for the layout"),
    ("Bash", "git status"),
    ("PowerShell", "Get-ChildItem project"),
    ("PowerShell", "Get-ChildItem -Recurse -Filter *.md"),
]


@pytest.mark.parametrize("tool,command", FULL_ALLOWED)
def test_full_mode_stays_open_for_ordinary_commands(root, tool, command):
    set_mode(root, "full")
    assert not decide(root, tool, command), (tool, command)


def test_the_reproduction_matches_the_audit_result(root):
    """Every end-of-audit write case denies in both modes (the base allowed several)."""
    for mode in ("full", "build-only"):
        set_mode(root, mode)
        for tool, command in WRITES:
            assert decide(root, tool, command), (mode, tool, command)


# Round 2 (P1): when the shared reader cannot read a text (here brace expansion would
# pass the gate's length limit before it reaches the deletion segment), it emits an
# error event. The record check routes that error into its fail-closed handling, so a
# command whose text names a record stays blocked in every mode.
OVERFLOW = "rm clients/acme/consent.json; echo " + "x" * 6000 + "{a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p}"


def test_the_reader_emits_an_error_for_the_overflowing_text(root):
    kinds = [kind for kind, _ in gate._read_commands(OVERFLOW, root, root)]
    assert kinds and kinds[0] == "error", kinds  # the emitted-error path, not a raised exception


@pytest.mark.parametrize("mode", ["full", "build-only"])
def test_a_reader_error_keeps_a_record_naming_command_blocked(root, mode):
    set_mode(root, mode)
    assert decide(root, "Bash", OVERFLOW), mode


# Round 2 (P1): PowerShell started from the Bash tool is read as PowerShell, as it is
# for the PowerShell tool: -Command, -c and -EncodedCommand, for powershell and pwsh.
def _encoded(text):
    import base64
    return base64.b64encode(text.encode("utf-16-le")).decode("ascii")


BASH_HOSTED_POWERSHELL = [
    'powershell -Command "Remove-Item clients/acme/consent.json"',
    'pwsh -Command "Remove-Item clients/acme/consent.json"',
    'powershell.exe -NoProfile -c "Remove-Item clients\\\\acme\\\\consent.json"',
    "pwsh -NoProfile -Command \"Set-Content clients/acme/consent.json '{}'\"",
    "powershell -EncodedCommand " + _encoded("Remove-Item clients\\acme\\consent.json"),
    "pwsh -enc " + _encoded("Remove-Item clients/acme/consent.json"),
]


@pytest.mark.parametrize("mode", ["full", "build-only"])
@pytest.mark.parametrize("command", BASH_HOSTED_POWERSHELL)
def test_powershell_started_from_bash_is_read_as_powershell(root, mode, command):
    set_mode(root, mode)
    assert decide(root, "Bash", command), (mode, command)


def test_ordinary_powershell_from_bash_still_passes_in_full_mode(root):
    set_mode(root, "full")
    assert not decide(root, "Bash", 'powershell -Command "Get-ChildItem project"')
    assert not decide(root, "Bash", "pwsh -NoProfile -Command 'git status'")


# Round 2b: a command that runs inline code whose text names a protected record or the
# approval key is refused in every mode (the same text net the error path uses). Code
# that names no record passes in full mode, and python -m torque stays allowed.
INLINE_CODE_RECORD = [
    "python -c \"import os; os.remove('clients/acme/consent.json')\"",
    "python3 -c \"open('clients/acme/consent.json','w').write('x')\"",
    "/usr/bin/python3 -c \"open('clients/acme/consent.json','w')\"",
    "py -c \"open('clients/acme/approvals/a1.json','w')\"",
    "node -e \"require('fs').unlinkSync('clients/acme/consent.json')\"",
    "node --eval=\"require('fs').unlinkSync('clients/acme/consent.json')\"",
    "perl -e 'unlink \"clients/acme/consent.json\"'",
    "perl -E 'unlink \"initiatives/plan/binding.json\"'",
    "ruby -e \"File.delete('clients/acme/consent.json')\"",
    "php -r \"unlink('clients/acme/consent.json');\"",
    "python -c \"open(os.path.expanduser('~/.config/torque/approval.key'))\"",
]


@pytest.mark.parametrize("mode", ["full", "build-only"])
@pytest.mark.parametrize("command", INLINE_CODE_RECORD)
def test_inline_interpreter_code_naming_a_record_is_refused(root, mode, command):
    set_mode(root, mode)
    assert decide(root, "Bash", command), (mode, command)


INLINE_CODE_OK = [
    "python -c \"print(1 + 1)\"",
    "python -m torque workflows list",
    "node -e \"console.log('build the project')\"",
    "perl -e 'print 2 + 2'",
    "ruby -e \"puts 42\"",
]


@pytest.mark.parametrize("command", INLINE_CODE_OK)
def test_inline_code_that_names_no_record_passes_in_full_mode(root, command):
    set_mode(root, "full")
    assert not decide(root, "Bash", command), command


# The Antigravity adapter rewrites run_command into the shell tool and routes it through
# gate.evaluate, so a bound-or-unbound Antigravity session gets the same every-mode answer.
def ag_answer(root, monkeypatch, command):
    payload = {"toolCall": {"name": "run_command", "args": {"CommandLine": command}},
               "workspacePaths": [str(root)], "conversationId": "c", "stepIdx": 0}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        ga.main()
    return json.loads(out.getvalue())


@pytest.mark.parametrize("mode", ["full", "build-only"])
@pytest.mark.parametrize("command", [f"eval 'rm {R}'", f"bash -c 'rm {R}'", f"C=clients/acme; rm $C/consent.json"])
def test_antigravity_run_command_gets_the_same_answer(root, monkeypatch, mode, command):
    set_mode(root, mode)
    assert ag_answer(root, monkeypatch, command)["decision"] == "deny", (mode, command)
    # An ordinary command is not denied for the record reason.
    reply = ag_answer(root, monkeypatch, "ls project")
    assert reply["decision"] != "deny" or "torque commands" not in reply["reason"]
