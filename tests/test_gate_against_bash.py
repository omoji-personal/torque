"""The gate against real Bash: every `sf` command Bash runs must be a route the gate reports.

Each line wraps one or two `sf sobject describe -o orgN` calls in a shell construct that has
hidden a command from the gate at some point: quoted and unquoted substitutions, a `)` in quotes,
in an ANSI-C string or in a `${...}` expansion, case patterns, comments, here-documents, line
continuations, groups. In Bash, `sf` is a shell function that records its arguments. The gate may
report more than Bash runs (it is the cautious side); it must not report less. Skipped where
there is no Bash."""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from torque.connected_routes import classify

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs a Bash to compare with")
PRELUDE = "sf() { { printf 'sf'; for a in \"$@\"; do printf ' %s' \"$a\"; done; printf '\\n'; } >> ..out; }\n"
S = "sf sobject describe -s A -o org0"
T = "sf sobject describe -s A -o org1"
LINES = [
    S, f"( {S} )", f"{{ {S}; }}", f"if true; then {S}; fi", f"true && {S}", f"false || {S}", f"! {S}",
    f'echo "$({S})"', f"echo $({S})", f"x=$({S})", f'x="$({S})"', f'echo "`{S}`"', f"echo `{S}`",
    f'echo "$(echo ")" ; {S})"', f"echo \"$(echo ')' ; {S})\"", f'echo "$(echo \\) ; {S})"', f"echo $(echo $')' ; {S})",
    f"echo $(echo $'\\'' ; {S})", f'echo "$(echo ${{x//)/y}} ; {S})"', f'echo "$(echo "(" ; {S})"',
    f'echo "$(case x in x) {S};; esac)"', f'echo "$(case x in (x) {S};; *) true;; esac)"',
    f"echo $(case x in y) true;; x) {S};; esac)", f'echo "$( (case x in x) {S};; esac) )"',
    f'echo "$(echo ok # )\n{S}\n)"', f'echo "$(echo ok # it\'s )\n{S}\n)"', f"echo $(: # x \\\n{S}\n)",
    f'echo "$(cat <<E\n)\nE\n{S})"', f"echo \"$(cat <<'E'\n) \" '\nE\n{S})\"", f'echo "$(echo "$({S})")"',
    f'echo "$(echo "$(echo ")" ; {S})")"', f'echo "`echo ")" ; {S}`"', f"echo ok # note \\\n{S}",
    f"echo a # it's\n{S} # don't", f"cat <<E\nit's a body\nE\n{S}", f": \\\n; {S}", f"echo 'a' \"b\" ; {S}",
    f'echo "a ; b" | cat ; {S}', f'echo "$(echo a; {S}; echo b)"', f"for i in 1; do {S}; done",
    f"case x in x) {S};; esac", f"f() {{ {S}; }}; f", f"echo $(( 1 + 2 )) ; {S}", f"echo ${{x:-$({S})}}",
    f'echo "${{x:-$({S})}}"', f"cat <<E\n$({S})\nE", f"cat <<E\nit's\nE\necho \"$(echo \\) ; {S})\"",
    f"cat <<E\nit's a body\nE\n# c \\\necho \"$(echo ok # it's )\n{S}\n)\"", f"{S} # c \\\n{T}", f"{S}\n# c \\\n{T}",
    f'echo "$(echo ")" ; {S})" ; {T}', f"echo a # it's\n{S}\necho b # it's\n{T}",
    # Bash does not run these; the gate may report them or not
    f"echo a # {S}", f'echo "{S}"', f"echo '$({S})'", f'echo "\\$({S})"', f"false && {S}", f"true || {S}",
    f": <<E\n{S}\nE", f'echo "$(: # ) ; {S}\n)"', f"if false; then {S}; fi",
]
# A `${...}` expansion whose word has quoting of its own, also inside double quotes. `S` stands
# for the first call and `T` for the second.
PARAMETER_FORMS = [
    """echo "${x:-"'"}"; S #'""", """echo "${x:-'}'}" ; S #'""", """echo "${x#'}"'}" ; S #'""",
    """echo "${x%'}"'}" ; S #'""", """echo "${x/'}"'/y}" ; S #'""", """echo ${x:-'}'} ; S""",
    """echo ${x:-"}"} ; S""", """echo ${x:-"'"} ; S #'""", """echo ${x:-'"'} ; S #\"""",
    """echo "${x:-"$(S)"}\"""", """echo "${x:-`S`}\"""", """: "${x:=$(S)}\"""",
    """echo "${x:-"a b" 'c' "d"}" ; S""", """echo "${x:-${y:-"'"}}" ; S #'""", """echo "${x%"'"}" ; S #'""",
    """echo "${x:-"}"}" ; S""", """echo "${x:-\\"}" ; S #\"""", """echo "${x[$(S)]}\"""",
    """echo ${x:-$'a\\'b'} ; S""", """echo "${x:-$'a'}" ; S""", """echo "${x:-'a'"'"}" ; S #'""",
    """echo "${x:+"'"}"; S #'""", """echo "${x-"'"}"; S #'""", """echo "${x:-"'"}" '}' ; S #'""",
    """echo "${x:-"a"}" "${y:-"'"}" ; S #'""", """echo ${x:-"'"}"${y:-"'"}" ; S #'""",
    """echo "${x:-'"'"'"}" ; S #'""", """echo "${x:-\\}"'"}" ; S #'""", """echo "${x:-$(echo "'")}" ; S #'""",
    """echo "${x:-$(echo ')' "}")}" ; S""", """echo "$(echo "${x:-"'"}" ; S)" #'""",
    """echo "$(echo "${x:-")"}" ; S)\"""", """echo "${x:-"`echo "'"`"}" ; S #'""", """echo "${x//\\'/\\"}" ; S""",
    """echo "${x:-"\\""}" ; S #\"""", """x="${y:-"'"}"; S #'""", """echo ${x:-"'"} "'" ; T ; echo "'" ; S""",
    """cat <<E\n${x:-"'"}\nE\nS #'""", """echo "${x:-"'"}" # c\nS #'""",
    # Bash does not run these; the gate may report them or not
    """echo "${x:-"; S #"}\"""", """echo ${x:-'; S #'}""", """echo "${x:-'; S'}\"""",
]
LINES += [form.replace(" ; T ;", f" ; {T} ;").replace("S", S) for form in PARAMETER_FORMS]
# A here-document whose delimiter is any word, written with or without quoting: its body holds a
# quote that must not pair with one after the body.
LINES += [f"cat <<{opening}\necho '\n{closing}\n{S}\n#'" for opening, closing in [
    ("\\!", "!"), ("~", "~"), ("@EOF@", "@EOF@"), ("$x", "$x"), ("'E F'", "E F"), ('E"O"F', "EOF"), ("\\E", "E"),
    ("E-1", "E-1"), (" 'a b'", "a b"), ("E%", "E%"), ("a/b", "a/b"), ("{E}", "{E}"), ("E\\ F", "E F"), ('"$x"', "$x"),
    ("'it''s'", "its"), ("E,F", "E,F"), ("=", "="), ("\\\\", "\\"), ("E#F", "E#F"), ("[E]", "[E]"), ("*", "*"),
]]
# A backslash before CR LF: Bash on Linux and macOS escapes the CR and ends the command at the LF; Git
# Bash on Windows joins the lines. Whichever Bash runs these, the gate must report what it runs. And
# `exec -a NAME` runs the command after the name.
LINES += [f"echo ok \\\r\n{S}", f"echo ok \\\r\n{S} \\\r\n", f"echo ok \\\n ; {S}", f"(exec -a echo {S})",
          f"(exec -cla x {S})", f"echo a \\\r\n; {S}", "sf sobject \\\r\n describe -s A -o org0",
          "sf sobject describe -s A \\\r\n -o org0", f"echo a \\\r\n b ; {S}"]
# Between backticks Bash takes one backslash off before `$`, a backtick and a backslash (and before a
# double quote too when the backticks stand in double quotes), and reads what is left as the command:
# `\\"` becomes `\"`, a quote that opens nothing. `$(...)` takes nothing off.
LINES += [f'echo `echo \\\\"x; {S} #\\\\"`', f"echo `echo \\\\'x; {S} #\\\\'`", f'echo "`echo \\\\"x; {S} #\\\\"`"',
          f'echo `echo \\"x; {S} #\\"`', f'echo `echo \\`echo \\\\\\\\"x; {S} #\\\\\\\\"\\``',
          f'x=`echo \\\\"x; {S} #\\\\"`', f"echo `echo \\$({S})`", f'echo "`echo \\$({S})`"',
          f'echo `echo \\\\"x; {S} #\\\\"` ; {T}', f"echo `: \\\\'; {S} #\\\\'` `: \\\\\"; {T} #\\\\\"`",
          # Bash does not run these; the gate may report them or not
          f'echo `echo \\\\\\\\"x; {S} #\\\\\\\\"`', f'echo "`echo \\"x; {S} #\\"`"',
          f'echo $(echo \\\\"x; {S} #\\\\")']
LINES += [f"cat <<-E\n\techo '\n\tE\n{S}\n#'", f"cat <<A <<'B C'\necho '\nA\necho \"\nB C\n{S}\n#'",
          f"cat <<\\! ; {T}\necho '\n!\n{S}\n#'", f"echo \"$(cat <<\\!\necho '\n!\n)\" ; {S} #'"]


# A word Bash evaluates a second time: an array subscript given to a builtin that takes a name, a value
# expanded as a prompt, as arithmetic or through another name. The substitution is written in pieces
# (`'a[$'"(...)]"`), so it is no substitution when the line is read, and runs when the word is evaluated.
LINES += [f"read 'a[$'\"({S})]\" <<< 1", f"printf -v 'a[$'\"({S})]\" x", f"a=(1); unset 'a[$'\"({S})]\"",
          f"a=(1); test -v 'a[$'\"({S})]\"", f"a=(1); [[ -v 'a[$'\"({S})]\" ]]", f"[[ 'a[$'\"({S})]\" -eq 0 ]]",
          f"declare 'a[$'\"({S})]=x\"", f"y='$'\"({S})\"; echo \"${{y@P}}\"", f"PS4='$'\"({S})\"; set -x; true",
          f"y='a[$'\"({S})]\"; x=abc; echo \"${{x:y}}\"", f"ref='a[$'\"({S})]\"; echo \"${{!ref}}\"",
          f"i='a[$'\"({S})]\"; arr=(1 2); echo \"${{arr[i]}}\"", f"read 'a[`'\"{S}\"'`]' <<< 1",
          f"read 'a[$'\"({S})]\" <<< 1 ; {T}"]


def bash_runs(line: str, folder: Path) -> set | None:
    out = folder / "..out"
    if out.exists():
        out.unlink()
    (folder / "..script").write_bytes((PRELUDE + line + "\n").encode("utf-8"))
    env = {"PATH": os.path.dirname(BASH), "HOME": str(folder), "SystemRoot": os.environ.get("SystemRoot", "")}
    done = subprocess.run([BASH, "--noprofile", "--norc", "..script"], cwd=folder, env=env, capture_output=True,
                          timeout=30, stdin=subprocess.DEVNULL)
    if b"syntax error" in done.stderr or b"unexpected EOF" in done.stderr:
        return None
    return set(re.findall(r"org\d", out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""))


def test_the_gate_reports_every_sf_command_bash_runs(tmp_path):
    # First prove that Bash runs here and that the recording works: a Bash that cannot
    # start would otherwise look like one that ran nothing, and every line would pass.
    if bash_runs(S, tmp_path) != {"org0"} or bash_runs(f"{S} ; {T}", tmp_path) != {"org0", "org1"}:
        pytest.skip("Bash is present but did not run the baseline line here")
    compared, ran_something, missed = 0, 0, []
    for line in LINES:
        ran = bash_runs(line, tmp_path)
        if ran is None:
            continue
        compared += 1
        ran_something += bool(ran)
        seen = {route.org for route in classify("Bash", {"command": line}) if route.org}
        if not ran <= seen:
            missed.append((line, sorted(ran), sorted(seen)))
    assert not missed, missed
    assert compared >= len(LINES) - 6          # nearly every line is one Bash accepts
    assert ran_something >= 100                # and most of them really run their sf call
