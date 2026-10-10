"""The gate against real PowerShell: every `sf` command PowerShell runs must be a route the gate reports.

The same idea as test_gate_against_bash.py, for the shell Claude Code and Antigravity use on
Windows. `sf` is a PowerShell function that records its arguments and the number of the case
it ran in; all cases run in one PowerShell process, whose PATH holds Windows' own folders only,
so nothing here starts a real program. Skipped where there is no Windows PowerShell."""
import base64
import os
import re
import shutil
import subprocess

import pytest

from torque.connected_routes import classify

POWERSHELL = shutil.which("powershell") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell to compare with")
S = "sf sobject describe -s A -o org0"
T = "sf sobject describe -s A -o org1"
LINES = [
    S, f"& {S}", f"$x = {S}", f"[string]$x = {S}", f"$env:X = {S}", f"({S})", f"@({S})", f"$({S})", f'echo "$({S})"',
    f'Write-Output "a $({S}) b"', f"if ($true) {{ {S} }}", f"1..1 | ForEach-Object {{ {S} }}",
    f"try {{ {S} }} catch {{ }}", f"function f {{ {S} }}; f", f"& {{ {S} }}", f". {{ {S} }}", f"{S} | Out-Null",
    f"echo a; {S}", f"$x = 1; $y = {S}",
    # here-strings
    f"echo @\"\n\" '\n\"@\n{S}\n#'", f"echo @'\n\" '\n'@\n{S}", f"$t = @\"\nit's\n\"@\n{S} # don't",
    f"$t = @\"\n$({S})\n\"@", f"$t = @'\nit's \"x\n'@; {S}", f"echo @\"\nline\n\"@ ; {S}", f"$t = @\"\n\"@\n{S}",
    # block and line comments
    f"<# ' #> {S} #'", f"<#\nit's\n#>\n{S}", f"{S} <# x #>", f"<# \" #>\n{S}\n<# \" #>", f"echo a # it's\n{S}",
    f"echo a # \"x\n{S} # \"y", f"# it's\n{S}\n# don't", f"<# ' #> {S} #'\n<# c #>\n<# ' #> {T} #'\n# c",
    f"# it's\n<# x #> {S}", f"echo a # don't\n<# \" #> {S} # \"", f"echo a#'b' ; {S}", f"echo a #'b' ; {T}\n{S}",
    # escapes and doubled quotes
    f'echo "a`"" ; {S}', f'echo "x`"; echo `"y"; {S}', f'echo "a""b" ; {S}', f"echo 'a''b' ; {S}",
    f"echo 'it''s' ; {S} # '", f'echo "a`$b" ; {S}', f"echo a`;b ; {S}", f"echo `\n a ; {S}", f"echo 'a' `\n ; {S}",
    f'echo "C:\\x\\" ; {S}', f"echo C:\\x\\ ; {S}", f"echo ok \\\n{S}", f"echo `$({S})", f"echo a`$ ; {S}",
    f'echo "Paid `$5" ; {S}', f"echo ``$({S})", f'echo "``$({S})"',
    # the quote and space characters PowerShell accepts beside the ASCII ones
    f"echo “ ' ” ; {S} #'", f"echo ‘ \" ’ ; {S} #\"", f"echo “ ' \" ; {S} #'",
    f"echo „ ' “ ; {S} #'", f"echo @“\n ' \n”@\n{S} #'", S.replace(" ", " "),
    f"echo a ; {S}", S.replace("sf ", "sf　"),
    # the stop-parsing token ends at a pipe or at the line's end
    f"echo --% ' | {S} #'", f"echo --% '\n{S} #'", f"echo a --% | {S}",
    # strings Bash has and PowerShell does not, and a variable name that holds a quote
    f"echo $'a\\' ; {S} #'", f"echo $\"a\\\" ; {S} #\"", f"echo ${{a'b}} ; {S} #'", f"echo \"${{a\"b}}\" ; {S} #\"",
    f"echo $(({S}))", f"(({S}))",
    # a backtick inside `${...}` makes the next character part of the name, a closing brace among them
    f"echo ${{v`}}' }}; {S} #'", f"echo \"${{v`}}\\\" }}\" ; {S} #\"", f"${{a`}}b}} = 1; {S}", f"echo ${{v``}} ; {S}",
    # what can stand before a command in a statement
    f"& {{ return {S} }}", f"try {{ throw {S} }} catch {{ }}", f". {S}", f"foreach ($x in {S}) {{ }}",
    f"@{{a = {S}}}", f"@{{'a' = {S}; b = {T}}}", f"$x, $y = {S}", f"${{x}} = {S}", f"$x = $y = {S}",
    f"$x = . {S}", f"if ($true) {{{S}}}", f"&{{{S}}}", f"1 | % {{{S}}}", f"for ($i = {S}; $false; ) {{ }}",
    # strings inside a subexpression inside a string
    f"echo \"$( \"a # b\" ; {S} )\"", f"echo \"$( \"<#\" ; {S} )\"", f"echo \"$( 'it''s' ; {S} )\"",
    f"echo \"a $( \"b $( 'c' ; {S} ) \" ) \"", f"echo \"$( \"<# ' #>\" ; {S} )\" #'",
    # what a hash literal or a script block holds, under a command whose arguments are otherwise data
    f"echo @{{a = {S}}}", f"echo @{{'a' = 1; b = {S}}}", f"Write-Output @{{ a = @{{ b = {S} }} }}",
    f"echo @{{\n  a = {S}\n}}", f"1 | sort {{ {S} }}", f"1 | sort {{{S}}}", f"echo a | % {{ {S} }} | sort",
    f"$h = @{{a = {S}}}", f"echo @{{a = 1}}.Count ; {S}", f"sleep -Milliseconds @{{a = {S}}}.Count",
    # found by running every pair of forms: a string inside a subexpression inside a string, and a
    # pipe inside double quotes after the stop-parsing token
    f"echo \"$( \"<#\" ; {S} )\" ; echo @{{(1+1)={T}}}", f"echo \"$( \"a\" )\" <# x #> ; {S}",
    f"echo \"a $( \"b\" + (1) ) c\" ; {S} # \"", f"echo --% \" | {S} #\" ; 1 | % {{{T}}}",
    f"echo --% \" | {S} #\" ; 1 | sort {{ {T} }}", f"echo --% \"a | b\" c | {S}", f"echo --% \" | x\n{S} #\"",
    # an assignment, whatever its target looks like and however many are chained
    f"echo @{{1=$1={S}}}", f"echo @{{1=$PSVersionTable.'PSVersion' = {S}}}", f"echo @{{1=$PSVersionTable['a]b'] = {S}}}",
    f"echo @{{1=$a=$b=$c=$d=$e=$f=$g={S}}}", f"$a=$b=$c=$d=$e=$f=$g=$h=$i=$j={S}", f"$1 = {S}",
    f"$x = @{{}}; $x.'y z' = {S}", f"$x = @{{}}; $x['a]b'] = {S}", f"[string[]]$x = {S}", f"$global:x += {S}",
    f"if ($r = {S}) {{ }}", f"echo @{{a = 1; b = $x = {S}}}",
    # a hash literal's key can be anything
    f"echo @{{1={S}}}", f"echo @{{-1={S}}}", f"echo @{{0x1={S}}}", f"echo @{{1.5={S}}}", f"echo @{{1e2={S}}}",
    f"echo @{{[int]1={S}}}", f"echo @{{ [int] 1 = {S} }}", f"echo @{{a=1; 2={S}}}", f"echo @{{1={S}; 2={T}}}",
    f"echo @{{1=echo a=b; 2={S}}}", f"echo @{{1=@{{2={S}}}}}", f"echo @{{(1+1)={S}}}", f"echo @{{$null={S}}}",
    f"echo @{{1=\n{S}}}", f"echo @{{\n1={S}\n2={T}\n}}",
    # a string handed to something that runs strings
    f'Invoke-Expression "{S}"', f"iex '{S}'", f"echo 'it''s' ; {S} # '\n\niex '{T}' # c",
    # PowerShell does not run these; the gate may report them or not
    f"# {S}", f'echo "{S}"', f"echo '{S}'", f"echo '$({S})'", f"<# {S} #>", f"$t = @'\n{S}\n'@", f"if ($false) {{ {S} }}",
    f'echo "`$({S})"',
]
PRELUDE = ("function sf { ('case ' + $global:case + ' sf ' + ($args -join ' ')) | Add-Content -LiteralPath '..out' "
           "-Encoding UTF8 }\n")


def powershell_runs(lines, folder) -> dict | None:
    """{case number: the orgs of the sf calls PowerShell made in that case}, or None when
    PowerShell did not run the script to its end."""
    script = PRELUDE + "".join(f"$global:case = {n}\n{line}\n" for n, line in enumerate(lines))
    script += "'end' | Add-Content -LiteralPath '..out' -Encoding UTF8\n"
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = {"PATH": system + os.pathsep + os.path.join(system, "WindowsPowerShell", "v1.0"),
           "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": str(folder), "TMP": str(folder),
           "USERPROFILE": os.environ.get("USERPROFILE", str(folder))}
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=folder, env=env,
                   capture_output=True, timeout=180, stdin=subprocess.DEVNULL)
    out = folder / "..out"
    text = out.read_text(encoding="utf-8-sig", errors="replace") if out.exists() else ""
    if not text.rstrip().endswith("end"):
        return None
    ran: dict = {}
    for number, org in re.findall(r"case (\d+) sf .*?(org\d)", text):
        ran.setdefault(int(number), set()).add(org)
    return ran


# What Windows PowerShell hands a native program is not always the words PowerShell had: it builds
# one command line and does not escape what an argument holds. Each of these follows
# `PROGRAM read --target-org org0`; SETUP lines run first.
NATIVE_FORMS = [
    "--since x", "--since 'a b'", "--since \"a b\"", "--since 'it''s here'",
    # a double quote inside an argument ends the argument there
    "--since 'a\" --target-org org1'", "--since 'Birthdate = 2000-01-01\" --target-org org1'",
    "--since \"a`\" --target-org org1\"", "--since \"a\"\" --target-org org1\"",
    "--since '2026-01-01T00:00:00Z\n\" --target-org org1'", "--since 'a\\\" --target-org org1'",
    "--since @'\na\" --target-org org1\n'@", "--since \"a $('\" --target-org org1') b\"",
    "--since 'a^\" --target-org org1'", "--since a`\" --target-org org1",
    # a backslash before the closing quote swallows the words after it
    "--since 'a b\\' --target-org org1", "--since 'a b\\' x 'c d\" --target-org org1'",
    "--since 'C:\\my folder\\' --target-org org1",
    # a comma with a space beside it makes the whole list an array, passed as separate words
    "--since 'x',--target-org, org1", "--since 'x',--target-org,org1", "--since x,--target-org,org1",
    "--since x, --target-org, org1", "--since x ,--target-org ,org1", "--since x,\n--target-org,\norg1",
    "--since @('x','--target-org','org1')", "--since ('x','--target-org','org1')",
    "--since $('x','--target-org','org1')",
    # the stop-parsing token, and plain second options
    "--since x --% --target-org org1", "--since 'a b' --target-org org1", "--since x --target-org=org1",
    "--since \u201ca\u201d --target-org org1", "--since 'x'\"y z\" --target-org org1", "--since '' --target-org org1",
    # a word that begins with a quoted string ends at its closing quote
    "--since \"x\"--target-org org1", "--since 'x'--target-org org1", "--since \"x\"-o org1",
    "--since \"a b\"--target-org org1", "--since 'x'\"\"--target-org org1", "--since 'x''y'--target-org org1",
    "--since \"x\"y\"z\" --target-org org1", "--since x\"y\"--target-org org1", "--since \"x\"y", "--since x\"y\"z",
    # an empty argument is not handed on at all
    "--since x --target-org '' org1", "--since x --target-org \"\" org1", "--since '' x",
]
NATIVE_SETUPS = {
    "$w = 'a\" --target-org org1'": ["--since \"$w\"", "--since $w", "--since \"x $w\"", "--since \"${w}\""],
    "$m = '--target-org','org1'": ["--since x @m", "--since x $m", "--since x \"$m\""],
}
RECORDER = ("import json, sys\n"
            "open(sys.argv[1], 'a', encoding='utf-8').write(json.dumps(sys.argv[2:]) + '\\n')\n")


def native_orgs(cases, folder) -> dict | None:
    """{case number: the values of the org options the program received}."""
    import json
    import sys
    (folder / "argv.py").write_text(RECORDER, encoding="utf-8")
    # the paths once, in variables: the whole script has to fit on one command line
    script = f"$p = '{sys.executable}'; $s = '{folder / 'argv.py'}'; $o = '{folder / 'argv.out'}'\n"
    script += "".join(f"{setup}\n& $p $s $o {n} read --target-org org0 {form}\n" for n, (setup, form) in enumerate(cases))
    script += "'end' | Add-Content -LiteralPath '..end' -Encoding UTF8\n"
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = {"PATH": system + os.pathsep + os.path.join(system, "WindowsPowerShell", "v1.0"),
           "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": str(folder), "TMP": str(folder),
           "USERPROFILE": os.environ.get("USERPROFILE", str(folder)), "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=folder, env=env,
                   capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
    out = folder / "argv.out"
    if not (folder / "..end").exists() or not out.exists():
        return None
    got: dict = {}
    for row in out.read_text(encoding="utf-8").splitlines():
        argv = json.loads(row)
        orgs = got.setdefault(int(argv[0]), [])
        for n, word in enumerate(argv):
            if word in ("--target-org", "-o") and n + 1 < len(argv):
                orgs.append(argv[n + 1])
            elif word.startswith("--target-org="):
                orgs.append(word[13:])
    return got


def test_the_gate_reads_or_stops_at_what_powershell_hands_a_native_program(tmp_path):
    cases = [("", form) for form in NATIVE_FORMS] + [(s, form) for s, forms in NATIVE_SETUPS.items() for form in forms]
    got = native_orgs(cases, tmp_path)
    if got is None or got.get(0) != ["org0"]:
        pytest.skip("Windows PowerShell is present but did not run the script here")
    missed, split = [], 0
    for number, (setup, form) in enumerate(cases):
        real = set(got.get(number, []))
        split += len(real) > 1
        for text in (f"torque logs --target-org org0 {form}", f"sf apex get log --target-org org0 {form}"):
            routes = classify("PowerShell", {"command": f"{setup}; {text}" if setup else text})
            stopped = bool({route.kind for route in routes} & {"admin", "no_org", "unverifiable"})
            seen = {route.org for route in routes if route.org}
            # one org, and the one the gate read; anything else must have been refused or asked about
            if not stopped and (len(real) != 1 or not real <= seen):
                missed.append((setup, form, sorted(real), sorted(seen)))
    assert not missed, missed
    assert split >= 12                         # the program really did receive a second org option in these
    # and plain forms stay plain: the gate reads them as one read of the org
    for form in ("--since x", "--since 'a b'", "--since \"a b\"", "--since 'it''s here'"):
        routes = classify("PowerShell", {"command": f"sf apex get log --target-org org0 {form}"})
        assert [(route.kind, route.org) for route in routes] == [("read", "org0")], (form, routes)


def test_a_cmd_launcher_fills_in_percent_names_and_the_gate_asks(tmp_path, monkeypatch):
    # `sf` is often sf.cmd: cmd.exe reads each argument again on its way to the program
    import json
    import sys
    (tmp_path / "argv.py").write_text(RECORDER, encoding="utf-8")
    (tmp_path / "viacmd.cmd").write_text(f'@"{sys.executable}" "%~dp0argv.py" "%~dp0argv.out" %*\r\n', encoding="utf-8")
    forms = ["read --target-org org0 --since %TQ_PCT%", "read --target-org org0 --since '%TQ_PCT%'",
             "read --target-org org0 --since \"%TQ_PCT%\"", "read --target-org org0 --since a%TQ_PCT%"]
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = {"PATH": os.pathsep.join([str(tmp_path), system, os.path.join(system, "WindowsPowerShell", "v1.0")]),
           "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": str(tmp_path), "TMP": str(tmp_path),
           "USERPROFILE": os.environ.get("USERPROFILE", str(tmp_path)), "PATHEXT": ".COM;.EXE;.BAT;.CMD",
           "ComSpec": os.path.join(system, "cmd.exe"), "TQ_PCT": "x --target-org org1"}
    script = "".join(f"viacmd {form}\n" for form in forms)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=tmp_path, env=env,
                   capture_output=True, timeout=180, stdin=subprocess.DEVNULL)
    out = tmp_path / "argv.out"
    if not out.exists():
        pytest.skip("Windows PowerShell is present but did not run the launcher here")
    rows = [json.loads(row) for row in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(forms) and all("org1" in row for row in rows), rows       # the program got a second org
    monkeypatch.setenv("TQ_PCT", "x --target-org org1")
    for form in forms:
        kinds = {route.kind for route in classify("PowerShell", {"command": "sf apex get log" + form[4:]})}
        assert "unverifiable" in kinds, (form, kinds)


def test_a_variable_set_on_the_same_line_reaches_the_launcher_and_the_gate_asks(tmp_path):
    # The name is not set where the gate runs: the line itself sets it, inside braces, before the launcher starts.
    import json
    import sys
    (tmp_path / "argv.py").write_text(RECORDER, encoding="utf-8")
    (tmp_path / "viacmd.cmd").write_text(f'@"{sys.executable}" "%~dp0argv.py" "%~dp0argv.out" %*\r\n', encoding="utf-8")
    value = "'x\" --target-org org1 \"'"
    setters = [f"echo @{{1=$env:TQ_SET0={value}}}", f"echo @{{a=1; b=${{env:TQ_SET1}}={value}}}",
               f"echo @{{1=Set-Item env:TQ_SET2 {value}}}", f"echo 1 | select @{{n='x';e={{$env:TQ_SET3={value}}}}}",
               f"echo @{{a=@{{b=$env:TQ_SET4={value}}}}}"]
    lines = [f"{setter}; PROGRAM --target-org org0 --since \"a%TQ_SET{n}% \"" for n, setter in enumerate(setters)]
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    env = {"PATH": os.pathsep.join([str(tmp_path), system, os.path.join(system, "WindowsPowerShell", "v1.0")]),
           "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": str(tmp_path), "TMP": str(tmp_path),
           "USERPROFILE": os.environ.get("USERPROFILE", str(tmp_path)), "PATHEXT": ".COM;.EXE;.BAT;.CMD",
           "ComSpec": os.path.join(system, "cmd.exe")}
    script = "".join(line.replace("PROGRAM", "viacmd read") + "\n" for line in lines)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=tmp_path, env=env,
                   capture_output=True, timeout=180, stdin=subprocess.DEVNULL)
    out = tmp_path / "argv.out"
    if not out.exists():
        pytest.skip("Windows PowerShell is present but did not run the launcher here")
    rows = [json.loads(row) for row in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(lines) and all("org1" in row for row in rows), rows        # the program got a second org
    for number, line in enumerate(lines):
        assert f"TQ_SET{number}" not in os.environ
        command = line.replace("PROGRAM", "sf apex get log")
        kinds = {route.kind for route in classify("PowerShell", {"command": command})}
        assert "unverifiable" in kinds, (command, kinds)
    # without the statement that sets the name, the same call is a plain read
    plain = classify("PowerShell", {"command": 'sf apex get log --target-org org0 --since "a%TQ_SET0% "'})
    assert [(route.kind, route.org) for route in plain] == [("read", "org0")], plain


TOKENIZER = r"""
$P = [System.Management.Automation.Language.Parser]
for ($c = 1; $c -le 0xFFFF; $c++) {
  if (($c -ge 0xD800 -and $c -le 0xDFFF) -or ($c -ge 0x20 -and $c -lt 0x7F) -or $c -eq 9 -or $c -eq 10 -or $c -eq 13) { continue }
  $ch = [string][char]$c
  $t = $null; $e = $null
  [void]$P::ParseInput('rec a' + $ch + 'b', [ref]$t, [ref]$e)
  $t2 = $null; $e2 = $null
  [void]$P::ParseInput('rec ' + $ch + 'x', [ref]$t2, [ref]$e2)
  $start = (($t2 | ForEach-Object { $_.Kind }) -join ',')
  $kind = ''
  if ($t.Count -eq 4 -and -not (($t | ForEach-Object { $_.Kind }) -contains 'NewLine')) { $kind = 'space' }
  elseif ($start -match 'Parameter') { $kind = 'dash' }
  elseif ($start -match 'StringLiteral') { $kind = 'single' }
  elseif ($start -match 'StringExpandable') { $kind = 'double' }
  elseif ($t.Count -ne 3 -or $t2.Count -ne 3) { $kind = 'other' }
  elseif (@($t + $t2 | Where-Object { 'Identifier', 'Generic', 'EndOfInput' -notcontains [string]$_.Kind }).Count) { $kind = 'other' }
  if ($kind) { '{0:X4} {1}' -f $c, $kind }
}
'end ' + $ExecutionContext.SessionState.LanguageMode
"""


def test_the_gates_table_of_powershell_characters_is_the_tokenizers_own(tmp_path):
    # Asks PowerShell's tokenizer about every character of the Basic Multilingual Plane outside printable
    # ASCII: between two letters (a space splits the word) and at the start of a word (a dash begins a
    # parameter, a quote a string). Nothing is run; the text is only tokenized.
    from torque.connected_routes import _PS_CHARACTERS
    encoded = base64.b64encode(TOKENIZER.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=tmp_path,
                          capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    rows = [row.strip() for row in done.stdout.splitlines() if row.strip()]
    if not rows or rows[-1] != "end FullLanguage":
        pytest.skip("Windows PowerShell is present but its tokenizer could not be asked here")
    real = {chr(int(code, 16)): kind for code, kind in (row.split() for row in rows[:-1])}
    assert "other" not in real.values(), sorted(hex(ord(c)) for c, kind in real.items() if kind == "other")
    names = {" ": "space", "-": "dash", "'": "single", '"': "double"}
    table = {chr(code): names[written] for code, written in _PS_CHARACTERS.items()}
    assert real == table, sorted((hex(ord(c)), real.get(c), table.get(c)) for c in set(real) ^ set(table))


def test_the_gate_reports_every_sf_command_powershell_runs(tmp_path):
    ran = powershell_runs(LINES, tmp_path)
    if ran is None or ran.get(0) != {"org0"}:
        pytest.skip("Windows PowerShell is present but did not run the script here")
    missed = []
    for number, line in enumerate(LINES):
        seen = set()
        for route in classify("PowerShell", {"command": line}):
            seen.update(re.findall(r"org\d", route.org or ""))
        if not ran.get(number, set()) <= seen:
            missed.append((line, sorted(ran[number]), sorted(seen)))
    assert not missed, missed
    assert len(ran) >= 80                      # most cases really run their sf call


# Spellings of a method call, and some that are none. PowerShell's own parser says which is which.
METHOD_FORMS = [
    "echo $x.Invoke()", "echo $x.Invoke ()", "echo $x.Invoke<# c #>()", "echo $x.Invoke`\n()", "echo $x . Invoke()",
    "echo $x.\nInvoke()", "echo $x::Invoke()", "echo $x.Invoke.Invoke()", "echo $x.$env:NAME()", "echo $x.${name}()",
    "echo $x.\"$name\"()", "echo $x.'Invoke'()", "echo $x.(\"In\"+\"voke\")()", "echo $x.$(\"Invoke\")()",
    "echo 'abc'.ToUpper()", "echo \"abc\".ToUpper()", "echo abc.ToUpper()", "echo [string]::Join('a','b')",
    "echo ([string]::Join('a','b'))", "echo @{1=[string]::Join('a','b')}", "echo $x.\"a b\"()", "echo $x.ForEach{ 1 }",
    "echo $x.Where({ 1 })", "echo $x[0].Invoke()", "echo $x.y[0]()", "echo $x.Invoke`()", "echo $x.In`voke()",
    "echo $($x).Invoke()", "echo @(1).Count.ToString()", "echo $x.Invoke\t()", "echo 1.ToString()",
    "echo (1).ToString()", "echo $x.y.z()", "echo $x.y::z()", "echo ${x}.Invoke()", "echo $env:X.ToUpper()",
    "echo \"$x\".ToUpper()", "echo \"a$($x.Invoke())b\"", "echo $x.Invoke(1)(2)", "echo @{1=$x.Invoke()}",
    "echo @{1=$x.${name}()}", "echo $x.Where{ $_ }", "echo 1 | select @{n='x';e={$_.Run()}}", "echo $x.$y.$z()",
    "echo 'abc'.$env:NAME()", "echo $x.\"$a $b\"()", "echo $x::$name()", "echo @{a=1; b=[type]::$env:NAME()}",
    "echo $x.Invoke( )", "echo $x.'a b'{ 1 }", "echo $x.$name{ 1 }", "echo $x.${name}{ 1 }", "echo $x.\"$name\"{ 1 }",
    "echo $x::$name{ 1 }", "echo $x.$env:NAME{ 1 }", "echo @{1=$x.${name}{ 1 }}", "echo $x.\"a $b\"{ 1 }",
    # no call: a property, a text, a type's member that is only named
    "echo $x.Name", "echo $x.y.z", "echo [math]::Pi", "echo $env:X.Length", "echo 'see a.b() there'", "echo $x.${name}",
    "echo @{a=$x.Name; b=$y.Count}", "echo 1 | select @{n='x';e={$_.Name}}",
]
PARSER = r"""
$P = [System.Management.Automation.Language.Parser]
$forms = Get-Content -LiteralPath 'forms.json' -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($form in $forms) {
  $t = $null; $e = $null
  $ast = $P::ParseInput($form, [ref]$t, [ref]$e)
  $calls = @($ast.FindAll({ $args[0] -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)).Count
  '{0} {1}' -f $calls, $e.Count
}
"""


def test_the_gate_asks_about_every_method_call_powershells_parser_finds(tmp_path):
    # Nothing runs: each text is parsed, and the parser is asked whether its tree holds a method call.
    import json
    (tmp_path / "forms.json").write_text(json.dumps(METHOD_FORMS), encoding="utf-8")
    encoded = base64.b64encode(PARSER.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=tmp_path,
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    rows = [row.split() for row in done.stdout.splitlines() if row.strip()]
    if len(rows) != len(METHOD_FORMS):
        pytest.skip("Windows PowerShell is present but its parser could not be asked here")

    def asked(form):
        return any(route.kind == "unverifiable" for route in classify("PowerShell", {"command": form}))

    calls = {form: int(count) for form, (count, _) in zip(METHOD_FORMS, rows)}
    missed = [form for form, count in calls.items() if count and not asked(form)]
    assert not missed, missed
    assert sum(count > 0 for count in calls.values()) >= 30      # most of the forms are calls for the parser
    # and where the parser finds no call and nothing else is unusual, the gate does not ask
    for form in METHOD_FORMS[-8:]:
        assert calls[form] == 0 and not asked(form), (form, calls[form])


# Lines where a `#`, a `<` or a `>` stands in the middle of a word, or where a string, a comment or a
# here-string ends in an unusual place. PowerShell reads such a character as part of the word among the
# arguments of a command and as a comment or a redirection in an expression; its parser says which sf
# commands each line holds, and with which org (`-o org0>x` names the org `org0>x`).
WORD_LINES = [
    'echo "\n" ; sf sobject describe -s A -o org0 #"="',
    'echo "$${" ; sf sobject describe -s A -o org0 ; echo "}"',
    'echo @"\n`\n"@ ; sf sobject describe -s A -o org0\n"@\nsf sobject describe -s A -o org1',
    "sf sobject describe -s A -o *>&1 org0",
    "echo ${x}#y ; sf sobject describe -s A -o org0",
    "echo $$#y ; sf sobject describe -s A -o org0",
    "echo a<#b ; sf sobject describe -s A -o org0 ; echo #>",
    "echo '''\"\"'`{<#''\nsf sobject describe -s A -o *>&1 org0",
    "$n = 1<# ' #> ; sf sobject describe -s A -o org0 #'",
    "$n = 1#'\nsf sobject describe -s A -o org0 #'",
    "$n = $x#'\nsf sobject describe -s A -o org0 #'",
    "echo $x.y#'\nsf sobject describe -s A -o org0 #'",
    "echo [int]#b ; sf sobject describe -s A -o org0",
    "$n = [int]#'\nsf sobject describe -s A -o org0 #'",
    'echo x@"\n" ; sf sobject describe -s A -o org0 ; echo "\n"',
    "sf sobject describe -s A -o org0>x",
    "sf sobject describe -s A -o org1<x",
    "sf sobject describe -s A -o org0#x",
    "sf sobject describe -s A -o org1<#x#>",
    "sf sobject describe -s A -o org0>>x",
    "sf sobject describe -s A -o 2>&1<# c #> org0",
    "sf sobject describe -s A -o 2>&1#c\necho a ; sf sobject describe -s A -o org1",
    'echo @{k="b""`=`$"}; sf sobject describe -s A -o org0',
    "echo 'a'#'\nsf sobject describe -s A -o org0",
    "echo a`;#b ; sf sobject describe -s A -o org0",
    "sf sobject describe -s A -o >a>b org0",
    "echo @'\na\n'@#'\nsf sobject describe -s A -o org0",
    "echo a#b c#d e#f g#h ; sf sobject describe -s A -o org0",
    "$x>'a' ; sf sobject describe -s A -o org0",
    "echo a 2>&1#' \nsf sobject describe -s A -o org0",
    "echo {1}#'\nsf sobject describe -s A -o org0",
    "echo (1)<# ' #> ; sf sobject describe -s A -o org0",
    # A subexpression inside a word or a string: its end is found by counting parentheses and nothing else,
    # a doubled quote inside it is one quote, and after its parenthesis the same word goes on. At the start
    # of a token, or after a parameter's name or a member, it is read as code of its own.
    'echo a$("#|#><")#c ; sf sobject describe -s A -o org0',
    "echo a$(1)#b ; sf sobject describe -s A -o org0",
    "echo -x$(1)#'\nsf sobject describe -s A -o org0",
    'echo "a$(""b"")c" ; sf sobject describe -s A -o org0',
    'echo a$("\'\'``"-")#<#`$""--%#> ; sf sobject describe -s A -o org0',
    "echo $x.y$(1)#'\nsf sobject describe -s A -o org0",
    'echo [int]#<#$()#`"#> ; sf sobject describe -s A -o org1<#x#>',
    'echo a$("#|#><“`"")#@\'\na\n\'@ ; sf sobject describe -s A -o org0<#x#>',
    "echo -x$(\"(\")#'\nsf sobject describe -s A -o org0",
    'echo $x.y$(")") ; sf sobject describe -s A -o org0',
    'echo "a$("“")z" ; sf sobject describe -s A -o org0',
    'echo a$(echo "“-`\'=->")<#${\'\'" <#<}#> ; sf sobject describe -s A -o <# c #> org0',
]
TREE = r"""
$P = [System.Management.Automation.Language.Parser]
$lines = Get-Content -LiteralPath 'lines.json' -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($line in $lines) {
  $t = $null; $e = $null
  $ast = $P::ParseInput([string]$line, [ref]$t, [ref]$e)
  $orgs = @()
  foreach ($c in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.CommandAst] }, $true)) {
    if ($c.GetCommandName() -eq 'sf') {
      $els = $c.CommandElements
      for ($k = 0; $k -lt $els.Count - 1; $k++) {
        if ($els[$k].Extent.Text -eq '-o') {
          $v = $els[$k + 1]     # a word or a string without variables has its value in the tree
          if ($v -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $orgs += $v.Value }
          else { $orgs += $v.Extent.Text }
        }
      }
    }
  }
  '{0} {1}' -f $e.Count, ($orgs -join ' ')
}
"""


def test_the_gate_reports_the_org_of_every_sf_command_in_powershells_tree(tmp_path):
    # Nothing runs: each line is parsed, and the parser is asked for the org of every sf command in its tree.
    import json
    (tmp_path / "lines.json").write_text(json.dumps(WORD_LINES), encoding="utf-8")
    encoded = base64.b64encode(TREE.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=tmp_path,
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    rows = [row.split() for row in done.stdout.splitlines() if row.strip()]
    if len(rows) != len(WORD_LINES):
        pytest.skip("Windows PowerShell is present but its parser could not be asked here")
    missed = []
    for line, (errors, *orgs) in zip(WORD_LINES, rows):
        assert errors == "0" and orgs, (line, errors)      # each line is one PowerShell runs an sf command from
        seen = {route.org for route in classify("PowerShell", {"command": line})}
        if not set(orgs) <= seen:
            missed.append((line, orgs, sorted(filter(None, seen))))
    assert not missed, missed


# Random lines. Each is a few statement shapes with pieces of text in them (F) and one or two sf calls in
# ordinary places (CALL). A piece is a quoted string, a braced variable, a comment, a here-string or a bare
# word that holds characters which mean something elsewhere, written the way its kind keeps such a
# character inside; the rest is chance. Some lines are raw pieces in any order. The seed is fixed.
RANDOM_INNER = ["'", '"', "`", "$", "{", "}", "(", ")", ";", "#", "<", ">", "@", "|", "&", " ", " ", "a", "b", "-", "\\",
                "\n", "=", ",", "%", "’", "“", " ", "sf", "<#", "#>", "--%", "''", '""', "`}", "`{", "`'",
                '`"', "`$", "``", "${", "$(", "@{", "\r\n", "\t", "x"]
RANDOM_BARE = ["a", "1", "-x", "a`;b", "a`'b", 'a`"b', "a`#b", "$x", "$x.y", "[int]1", "a,b", "`$y", "a`}b", "a#b", "a<#b",
               "a>b", "a<b", "$x#b", "a#>", "1#", "-x#b", 'x@"q"', "a`{", "`;", "$$", "${x}", "a$(1)#b", "$(1)#b",
               "a$(1)", "$x$(1)>b"]
RANDOM_SHAPES = [
    "CALL", "echo F", "echo F F", "$x = F", "echo F ; CALL", "echo @{k=CALL}", "& { CALL }", 'echo "a $(CALL) b"',
    "if ($true) { CALL }", "echo F | % { CALL }", "echo F # F", "CALL # F", "echo F F ; CALL", "$x = F ; CALL",
    "echo @{F=1; k=CALL}", "echo F; echo @{k=CALL} #F", "function f { CALL }", "echo (CALL)", "F", "echo F,F",
    "CALL | % { echo F }", "$n = 1#F\nCALL", "$n = $x<#F#> ; CALL", "echo a#F ; CALL", "echo a<#F#> ; CALL",
    "echo $x#F ; CALL", 'echo x@"\n" ; CALL ; echo "\n"@', "echo a>F ; CALL", "$x>F ; CALL", "echo ${x}#F ; CALL",
    'echo "a"#F\nCALL', "echo a 2>&1#F\nCALL", 'echo x"a"#F ; CALL', "echo $x.y#F\nCALL", "echo [int]#F ; CALL",
    "$n = [int]#F\nCALL", "echo F#F ; CALL", "echo F<#F#> ; CALL", "$n = F#F\nCALL", "$n = F<#F#> ; CALL",
    "echo F>F ; CALL", "echo F<F ; CALL", "echo a#F<#F#>F ; CALL", "$n = 1 + 2#F\nCALL", "echo @'\nF\n'@#F\nCALL",
    "echo x@'\n' ; CALL ; echo '\n'@", "switch (1) { 1 { CALL } }", "try { CALL } catch { echo F }",
    "foreach ($i in F) { CALL }", "while ($false) { CALL }", "do { CALL } while ($false)", "$x = CALL", "$x, $y = CALL",
    "[void](CALL)", "@(CALL)", "$(CALL)", "CALL | Out-Null", "echo F > x.txt ; CALL", ". { CALL }", '"$(echo F)" ; CALL',
    "echo @(F, F) ; CALL", "echo $(F) ; CALL", "echo F -f F ; CALL", "echo F[0] ; CALL", "echo F | CALL",
    "echo F.Length ; CALL", "$x = F + F ; CALL", "echo @{F=F; F=F} ; CALL", "if (F -eq F) { CALL } else { echo F }",
    "echo F`\n ; CALL", "echo F <# F #> ; CALL", "& { echo F ; CALL }", "echo F ; & CALL", "return CALL",
    "echo @{k=F}; echo @{k=CALL}", "$x = @{k=CALL}", "echo F;CALL", "echo F\nCALL", "echo a$(F)#F ; CALL",
    "echo $(F)#F\nCALL", "echo a$(echo F)<#F#> ; CALL", "echo -x$(F)#F\nCALL", "echo F$(F)F ; CALL", "echo F(F)#F\nCALL",
]
# what can stand between an option and its value without being a word of the command, and what an org can
# end in that is PowerShell's own at the start of a token
RANDOM_BETWEEN = ["2>&1", "<# c #>", "`\n", "2>$null", "*>&1", "<#'#>", '<#"#>', "`\r\n", "3>&1 2>&1", "2>x#c", ">a>b",
                  "2>&1<#c#>", ">x<#c", "2>>a<b", "*>>x"]
RANDOM_ENDING = ["#x", ">x", "<#x#>", "<x", "#", "<#", ">>x"]
# other ways to write the org's name, and to call the command
RANDOM_ORG = ["'org%s'", '"org%s"', 'o"r"g%s', "org%s''", "or`g%s", "'o'rg%s", '"or"\'g\'%s']
RANDOM_CALL = ["& sf", "& 'sf'", '& "sf"', ". sf", "&sf"]
RANDOM_PIECES = [
    " ", " ", " ", ";", "\n", "|", "&", "(", ")", "{", "}", "@{", "@(", "$(", '"', '"', "'", "'", "`", "`", "$", "${",
    "#", "<#", "#>", '@"\n', '\n"@', "@'\n", "\n'@", "=", ",", "--%", "$x", "k=", "1", "-", ".", "::", "[", "]", "\\",
    ">", "2>&1", " ", "“", "’", " ", "\r\n", "if", "else", "function f", "return", "echo", "a",
    "x", "&&", "||", "%", "?", "!", ":", "+", "*", "\t", "}}", "{{", "''", '""', "`n", "`$", '`"', "`}", "`{", "$_",
    "@x", "[int]", "-eq", "foreach", "in", "try", "catch", "while", "do", "switch", "param", "-join", "..",
]


def random_fragment(rng) -> str:
    inner = [rng.choice(RANDOM_INNER) for _ in range(rng.randint(0, 6))]
    kind = rng.choice(["sq", "dq", "var", "comment", "here", "bare", "sq", "dq", "var"])
    if kind == "sq":
        return "'" + "".join("''" if c == "'" else c for c in inner) + "'"
    if kind == "dq":
        return '"' + "".join('`"' if c == '"' else c for c in inner) + '"'
    if kind == "var":
        return "${" + "".join("`}" if c == "}" else c for c in inner if c not in ("\n", "\r\n")) + "}"
    if kind == "comment":
        return "<#" + "".join(c for c in inner if c != "#>") + "#>"
    if kind == "here":
        mark = rng.choice("'\"")
        return "@" + mark + "\n" + "".join(c for c in inner if c not in ("\n", "\r\n")) + "\n" + mark + "@"
    return rng.choice(RANDOM_BARE)


def random_statement(rng, calls: list) -> str:
    body = rng.choice(RANDOM_SHAPES)
    while "F" in body:
        body = body.replace("F", "\0", 1).replace("\0", random_fragment(rng).replace("F", "\1"), 1)
    body = body.replace("\1", "F")
    while "CALL" in body:
        call = calls.pop() if calls else "echo c"
        if call.startswith("sf") and rng.random() < 0.25:
            call = call.replace("-o ", "-o " + rng.choice(RANDOM_BETWEEN) + " ")
        elif call.startswith("sf") and rng.random() < 0.1:
            call += rng.choice(RANDOM_ENDING)
        elif call.startswith("sf") and rng.random() < 0.2:
            call = call[:-4] + rng.choice(RANDOM_ORG) % call[-1]
        if call.startswith("sf") and rng.random() < 0.1:
            call = rng.choice(RANDOM_CALL) + call[2:]
        body = body.replace("CALL", call, 1)
    return body


def random_lines(count: int, seed: int) -> list:
    import random
    rng, lines = random.Random(seed), []
    for _ in range(count):
        if rng.random() < 0.15:
            parts = [rng.choice(RANDOM_PIECES) for _ in range(rng.randint(3, 12))]
            for call in rng.sample([S, T], rng.randint(1, 2)):
                parts.insert(rng.randint(0, len(parts)), " " + call + " ")
            lines.append("".join(parts))
            continue
        calls = rng.sample([S, T], 2)
        made = [random_statement(rng, calls) for _ in range(rng.randint(1, 4))]
        if len(calls) == 2:             # no statement took a call
            made.append(calls.pop())
        lines.append(rng.choice([" ; ", "\n", ";", "\r\n"]).join(made))
    return lines


# (The gate's older ANSI-C helper decodes with Python's own codec, which warns about an escape it does not
# know; it keeps such an escape as written. The random strings hold many.)
@pytest.mark.filterwarnings("ignore:invalid escape sequence:DeprecationWarning")
def test_random_lines_read_beside_powershells_parser(tmp_path):
    # Nothing runs. For every line PowerShell can parse, the gate has to report the org of each sf command
    # in the tree, as that command's own words give it; or refuse the line outright, or say that it holds
    # more places to read each way than it reads. A command only asked about without its org is a miss.
    import json
    lines = random_lines(4000, 17)
    (tmp_path / "lines.json").write_text(json.dumps(lines), encoding="utf-8")
    encoded = base64.b64encode(TREE.encode("utf-16-le")).decode("ascii")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], cwd=tmp_path,
                          capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
    rows = [row.split() for row in done.stdout.splitlines() if row.strip()]
    if len(rows) != len(lines):
        pytest.skip("Windows PowerShell is present but its parser could not be asked here")
    held, missed = 0, []
    for line, (errors, *orgs) in zip(lines, rows):
        orgs = {org for org in orgs if re.match(r"org\d", org)}
        if errors != "0" or not orgs:
            continue                    # PowerShell runs nothing from a line it cannot parse
        held += 1
        routes = classify("PowerShell", {"command": line})
        if orgs <= {route.org for route in routes}:
            continue
        if not {"no_org", "admin", "credential", "all_orgs"} & {route.kind for route in routes} \
                and not any("in the middle of a word in more than" in route.detail for route in routes):
            missed.append((line, sorted(orgs), sorted({route.org for route in routes if route.org})))
    assert not missed, missed[:5]
    assert held >= 1500                 # about half of the lines parse and hold an sf command
