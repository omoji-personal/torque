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
    f'echo "C:\\x\\" ; {S}', f"echo C:\\x\\ ; {S}", f"echo ok \\\n{S}",
    # the quote and space characters PowerShell accepts beside the ASCII ones
    f"echo “ ' ” ; {S} #'", f"echo ‘ \" ’ ; {S} #\"", f"echo “ ' \" ; {S} #'",
    f"echo „ ' “ ; {S} #'", f"echo @“\n ' \n”@\n{S} #'", S.replace(" ", " "),
    f"echo a ; {S}", S.replace("sf ", "sf　"),
    # the stop-parsing token ends at a pipe or at the line's end
    f"echo --% ' | {S} #'", f"echo --% '\n{S} #'", f"echo a --% | {S}",
    # strings Bash has and PowerShell does not, and a variable name that holds a quote
    f"echo $'a\\' ; {S} #'", f"echo $\"a\\\" ; {S} #\"", f"echo ${{a'b}} ; {S} #'", f"echo \"${{a\"b}}\" ; {S} #\"",
    f"echo $(({S}))", f"(({S}))",
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
