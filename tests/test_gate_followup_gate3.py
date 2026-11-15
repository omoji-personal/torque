"""Follow-up to the gate fixes (N6, N7, N17), from the 2026-10-29 re-check: the doctor's
attached `-c` forms, inline interpreter code that writes a literal record path (and the
ordinary code the net must not refuse), the doctor's command read as the installed doctor
reads it, `find --`, and connected help behind a `--`. Synthetic workspace only."""
import io
import json
import os
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from torque import gate, workspace as ws
from test_gate_sf_local_n6 import _connected_decide, _connected_fixture

QUERY = "data query --query 'SELECT Id FROM Account' --target-org acme-prod"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    base = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(base, "Acme")
    (Path(base) / "project").mkdir(exist_ok=True)
    (Path(base) / "clients" / "acme" / "consent.json").write_text("{}", encoding="utf-8")
    (Path(base) / "clients" / "acme" / "notes.md").write_text("ok", encoding="utf-8")
    ws.set_ai_access(base, "full")
    return Path(os.path.realpath(base))


def build_blocked(root, command, cwd=None):
    ws.set_ai_access(root, "build-only")
    ok, _ = gate.decide("Bash", {"command": command}, root, "build-only", cwd or root / "project")
    return not ok


def full_denied(root, command, cwd=None):
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
             "cwd": str(cwd or root)}
    with redirect_stderr(io.StringIO()):
        return gate.evaluate(json.dumps(event)) != 0


# P1 (N6): every spelling oclif accepts for the doctor's command flag reads that command.
@pytest.mark.parametrize("flags", ['-c "%s"', '-c"%s"', "-c='%s'", '-c="%s"', '--command "%s"',
                                   '--command="%s"', '-lc "%s"'])
def test_attached_doctor_command_forms_are_not_local(root, flags):
    command = "sf doctor " + flags % ("sf " + QUERY.replace("'", ""))
    assert build_blocked(root, command), command


def test_doctor_command_reading(root):
    read = gate._sf_doctor_command
    assert read(["doctor", "-cdata query"]) == "sf data query"
    assert read(["doctor", "-c=data query"]) == "sf data query"
    assert read(["doctor", "-c", "sf data query"]) == "sf data query"
    assert read(["doctor", "--command=sfdx force:org:list"]) == "sfdx force:org:list"
    assert read(["doctor", "-lc", "org list"]) == "sf org list"
    assert read(["doctor", "--list"]) is None
    assert read(["doctor", "--", "-c", "x"]) is None
    assert read(["org", "list", "-c", "x"]) is None


def test_plain_doctor_stays_local(root):
    ws.set_ai_access(root, "build-only")
    for command in ("sf doctor", "sf doctor --list", "sf doctor --help", "sf doctor --command org --help"):
        ok, _ = gate.decide("Bash", {"command": command}, root, "build-only", root / "project")
        assert ok, command


def test_connected_attached_doctor_needs_the_same_consent(tmp_path, monkeypatch):
    w = _connected_fixture(tmp_path, monkeypatch, ["metadata"])
    bare = "sf " + QUERY
    assert _connected_decide(w, bare).action == "deny"
    for form in ('sf doctor -c"%s"', "sf doctor -c='%s'", 'sf doctor -c "%s"', 'sf doctor --command "%s"'):
        assert _connected_decide(w, form % bare).action == "deny", form


# P1 (N17): literal inline-interpreter writes to a record are refused whatever the
# interpreter's name or option spelling, and relative paths resolve from the command's cwd.
WRITE = "open('clients/acme/consent.json','w').write('x')"


@pytest.mark.parametrize("command", [
    f'python -Ic "{WRITE}"',
    f'python3.11 -c "{WRITE}"',
    f'python3.12.exe -c "{WRITE}"',
    f'python -Bc "{WRITE}"',
    f'python -I -c "{WRITE}"',
    f'python -X utf8 -c "{WRITE}"',
    f'py -3.11 -c "{WRITE}"',
    f'sudo python3.11 -c "{WRITE}"',
    f'env A=1 python -c "{WRITE}"',
    f'A=1 python -c "{WRITE}"',
    f'echo hi; python -c "{WRITE}"',
    f"if true; then python -c \"{WRITE}\"; fi",
    f'find . -exec python -c "{WRITE}" \\;',
    "node -pe \"require('fs').writeFileSync('clients/acme/consent.json','x')\"",
    "node --eval \"require('fs').writeFileSync('clients/acme/consent.json','x')\"",
    "perl -le 'open(F,\">clients/acme/consent.json\")'",
    "perl -i.bak -pe 's/a/b/' -e 'unlink q(x)' -e 'unlink \"clients/acme/consent.json\"'",
    "ruby -ne \"File.write('clients/acme/consent.json','x')\"",
    "php -nr \"file_put_contents('clients/acme/consent.json','x');\"",
])
def test_literal_record_write_by_inline_code_is_refused(root, command):
    assert full_denied(root, command), command


@pytest.mark.parametrize("command,cwd", [
    ("python -c \"open('consent.json','w').write('x')\"", "clients/acme"),
    ("python3.11 -c \"open('./consent.json','w')\"", "clients/acme"),
    ("python -c \"open('../acme/consent.json','w')\"", "clients/acme"),
    ("python -c \"open('acme/consent.json','w')\"", "clients"),
    ("cd clients/acme && python -c \"open('consent.json','w')\"", ""),
    ("cd clients/acme; echo x; python -Ic \"open('consent.json','w')\"", ""),
])
def test_relative_record_path_in_inline_code_resolves_from_the_cwd(root, command, cwd):
    assert full_denied(root, command, root / cwd if cwd else root), command


def test_a_cd_back_out_leaves_the_record_name_harmless(root):
    command = "cd clients/acme && cd ../.. && python -c \"print(open('notes.md').read())\""
    assert not full_denied(root, command)


# P2 (N17): ordinary code, and words that only look like an interpreter call, pass.
@pytest.mark.parametrize("command", [
    "python -c \"print('clients')\"",
    "node -e \"console.log('clients')\"",
    "python -c \"print(open('clients/acme/notes.md').read())\"",
    "echo python -c clients",
    "echo python -c \"open('clients/acme/consent.json','w')\"",
    "grep -n 'python -c' project/notes.md",
    "python script.py -c \"clients/acme/consent.json\"",
    "python -m torque workflows list",
    "python -c \"print('initiatives')\"",
    "python -c \"open('consent.json').read()\"",     # no such file in the project folder
    "perl -e 'print \"clients\"'",
])
def test_ordinary_inline_code_is_not_refused(root, command):
    assert not full_denied(root, command, root / "project"), command


def test_removing_a_folder_that_holds_records_by_inline_code_is_still_refused(root):
    assert full_denied(root, "python -c \"import shutil; shutil.rmtree('clients')\"")
    assert full_denied(root, "node -e \"require('fs').rmSync('clients',{recursive:true})\"")
    assert not full_denied(root, "python -c \"print('clients'.replace('c','C'))\"")


# P2 (N6): the doctor's command is read as the doctor reads it, and outer help runs nothing.
def test_connected_doctor_routes_the_command_as_an_sf_command(tmp_path, monkeypatch):
    w = _connected_fixture(tmp_path, monkeypatch, ["metadata"])
    for command in ('sf doctor --command "org display --target-org acme-prod"',
                    'sf doctor --command "sf org display --target-org acme-prod"'):
        decision = _connected_decide(w, command)
        assert decision.action == "deny" and "credential" in decision.reason.casefold(), (command, decision)
    for command in (f'sf doctor --command "{QUERY}"', f'sf doctor -c"{QUERY}"'):
        assert _connected_decide(w, command).action == "deny", command
    for command in ('sf doctor --help', 'sf doctor --command "org display" --help',
                    f'sf doctor --command "sf {QUERY}" --help', 'sf doctor --command "help"',
                    'sf doctor --command "sf help"'):
        assert _connected_decide(w, command).action == "allow", command


# P2 (N7): find's option terminator is consumed before its paths.
def test_find_double_dash_roots_at_the_named_folder(root):
    ws.set_ai_access(root, "build-only")
    assert not build_blocked(root, "find -- project -name x", root)    # rootless find from here would search clients/
    assert not build_blocked(root, "find -H -- project -name x", root)
    assert build_blocked(root, "find -- clients -name x", root)
    assert build_blocked(root, "find -- -name x", root)


# P2 (N6): connected help stops at `--`, as build-only and oclif do.
def test_connected_help_after_double_dash_is_not_help(tmp_path, monkeypatch):
    w = _connected_fixture(tmp_path, monkeypatch, ["metadata"])
    bare = "sf " + QUERY
    assert _connected_decide(w, bare + " -- --help").action == "deny"
    assert _connected_decide(w, bare + " --help").action == "allow"



# Round 4: one reading finds the interpreter, its code and the folder it runs from.
W4 = "open('clients/acme/consent.json','w').write('x')"
CD_WRITE = "open('consent.json','w').write('x')"


@pytest.mark.parametrize("command", [
    f'> project/out python -c "{W4}"',
    f'>project/out python -c "{W4}"',
    f'2>/dev/null python -c "{W4}"',
    f'X=1 > project/out Y=2 python -c "{W4}"',
    f'env -u HOME python -c "{W4}"',
    f'nice -n 5 python -c "{W4}"',
    f'env A=1 nohup python3.11 -c "{W4}"',
    f'time python -c "{W4}"',
    f'xargs -n 1 python -c "{W4}"',
    f"python -c \"print('hi; python -c x')\"; cd clients/acme && python -c \"{CD_WRITE}\"",
    f'echo "a;b"; python -c "print(1)"; cd clients/acme; python -c "{CD_WRITE}"',
    f'(cd clients/acme && python -c "{CD_WRITE}")',
    f'pushd clients/acme; python -c "{CD_WRITE}"',
    f'cd "$X"; python -c "{CD_WRITE}"',
    "python -c \"import os; os.rename('clients','gone')\"",
    "python -c \"print(1)\"; node -e \"require('fs').rmSync('clients',{recursive:true})\"",
])
def test_round4_inline_writes_are_refused(root, command):
    assert full_denied(root, command), command


@pytest.mark.parametrize("command", [
    "python -c \"print('clients'); print('rename')\"",
    f'env echo python -c "{W4}"',
    f'sudo -u nobody echo python -c "{W4}"',
    f'nice -n 5 echo python -c "{W4}"',
    f'xargs echo python -c "{W4}"',
    f'python -c "print(1)" -c "{W4}"',
    f'python -c "print(1)" "{W4}"',
    f'> project/out echo python -c "{W4}"',
    "echo hi > project/out; python -c \"print(open('clients/acme/notes.md').read())\"",
    f'(cd clients/acme && ls); python -c "{CD_WRITE}"',
])
def test_round4_ordinary_code_is_not_refused(root, command):
    assert not full_denied(root, command), command


# Round 5: where the folder is uncertain, code that names a record file is refused;
# a removal call ties a folder literal only to its own arguments.
@pytest.mark.parametrize("command,cwd", [
    (f'false && cd project; python -c "{CD_WRITE}"', "clients/acme"),
    (f'cd project || true; python -c "{CD_WRITE}"', "clients/acme"),
    (f'true && cd .. && cd acme && python -c "{CD_WRITE}"', "clients/acme"),
    (f'cd .. | cat; python -c "{CD_WRITE}"', "clients/acme"),
    (f'if true; then cd project; fi; python -c "{CD_WRITE}"', "clients/acme"),
    (f'python > project/out -c "{W4}"', ""),
    (f'python -c > project/out "{W4}"', ""),
    (f'python -c "{W4}" 2> project/err', ""),
    (f'python 2>/dev/null -c "{CD_WRITE}"', "clients/acme"),
    (f'env -C clients/acme python -c "{CD_WRITE}"', ""),
    (f'env --chdir=clients/acme python -c "{CD_WRITE}"', ""),
    (f'env -Cclients/acme python -c "{CD_WRITE}"', ""),
    (f'sudo -D clients/acme python -c "{CD_WRITE}"', ""),
    (f'find . -maxdepth 0 -execdir python -c "{CD_WRITE}" \;', "clients/acme"),
    ("python -c \"import os; os.remove(os.path.join('clients', 'x'))\"", ""),
])
def test_round5_uncertain_folder_with_a_record_name_is_refused(root, command, cwd):
    assert full_denied(root, command, root / cwd if cwd else root), command


@pytest.mark.parametrize("command,cwd", [
    ('false && cd project; python -c "print(1)"', "clients/acme"),
    ('false && cd project; python -c "print(open(\'notes.md\').read())"', "clients/acme"),
    ('python > project/out -c "print(\'clients\')"', ""),
    ('python -c > project/out "print(\'clients\')"', ""),
    ('python -c "print(1)" 2>&1 | cat', ""),
    ('env -C project python -c "print(open(\'notes.md\').read())"', ""),
    ('cd project || exit; python -c "print(\'x\')"', ""),
    ('cd .. | cat; python -c "print(\'x\')"', "project"),
    ('cd project && python -c "open(\'consent.json\').read()"', ""),
    ("python -c \"print('clients'); import os; os.remove('project/x')\"", ""),
    ("python -c \"import shutil; shutil.rmtree('project/build'); print('clients')\"", ""),
    ("node -e \"console.log('clients'); require('fs').rmSync('project/x')\"", ""),
])
def test_round5_ordinary_code_stays_allowed_in_every_form(root, command, cwd):
    assert not full_denied(root, command, root / cwd if cwd else root), command


def test_round5_removing_the_records_folder_itself_is_still_refused(root):
    assert full_denied(root, "python -c \"import shutil; shutil.rmtree('clients'); print(1)\"")
    assert full_denied(root, "python -c \"import os; os.rename('clients', 'x')\"")
    assert full_denied(root, "perl -e 'unlink \"clients/acme/consent.json\"; print 1'")


# Round 6: a redirection does not move the shell; a failed cd, code that changes its own
# folder and env -S leave the folder uncertain; command -v runs nothing.
import shlex

ORD_WRITE = "open('project/consent.json','w').write('ordinary')"


@pytest.mark.parametrize("command,cwd", [
    (f'python -c "{ORD_WRITE}" > project/out', ""),
    (f'python -c "{ORD_WRITE}" 2>&1 >> project/out', ""),
    (f'> project/out python -c "{ORD_WRITE}"', ""),
    (f'python > project/out -c "{ORD_WRITE}"', ""),
    (f'cd project && python -c "{CD_WRITE}" > out', ""),
    ('cd project && python -c "print(1)" > out', ""),
])
def test_round6_a_redirection_keeps_the_known_folder(root, command, cwd):
    assert not full_denied(root, command, root / cwd if cwd else root), command


@pytest.mark.parametrize("command", [
    f'cd project/missing; python -c "{W4}"',
    f'cd missing && cd .. ; python -c "{CD_WRITE}"',
    "python -c \"import os; os.chdir('clients/acme'); open('consent.json','w').write('x')\"",
    "python -c \"from os import chdir; chdir('clients/acme'); open('consent.json','w')\"",
    "node -e \"process.chdir('clients/acme'); require('fs').writeFileSync('consent.json','x')\"",
    "ruby -e \"Dir.chdir('clients/acme') { File.write('consent.json','x') }\"",
    "perl -e 'chdir \"clients/acme\"; open(F,\">consent.json\")'",
    "php -r \"chdir('clients/acme'); file_put_contents('consent.json','x');\"",
    "env -S " + shlex.quote(f'python -c "{W4}"'),
    "env -S" + shlex.quote(f'python -c "{W4}"'),
    "env --split-string=" + shlex.quote(f'python -c "{W4}"'),
    "env -i -S " + shlex.quote(f'python -c "{W4}"'),
    "command -p python -c \"" + W4 + "\"",
])
def test_round6_uncertain_folder_with_a_record_file_is_refused(root, command):
    assert full_denied(root, command), command


@pytest.mark.parametrize("command", [
    'cd project/missing; python -c "print(1)"',
    'cd project/missing; python -c "print(open(\'notes.md\').read())"',
    "python -c \"import os; os.chdir('project'); print(os.getcwd())\"",
    "python -c \"print('chdir')\"",
    "node -e \"process.chdir('project'); console.log(1)\"",
    "env -S " + shlex.quote('python -c "print(1)"'),
    "env -S " + shlex.quote("echo python -c x"),
    "command -v python",
    "command -V python -c \"" + W4 + "\"",
    "command -v python -c \"" + W4 + "\"",
    "command -pv python -c \"" + W4 + "\"",
])
def test_round6_ordinary_code_and_lookups_stay_allowed(root, command):
    assert not full_denied(root, command), command
