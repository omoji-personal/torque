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


# Brace expansion makes words from the text of the line alone: `{sf,sobject,describe}` is three words.
LINES += ["{sf,sobject,describe,-s,A,-o,org0}", "echo a; {sf,sobject,describe,-s,A,-o,org0}",
          "{sf,sobject,describe} -s A -o org0", "sf {sobject,describe} -s A -o org0", "sf sobject describe -s A {-o,org0}",
          "s{f,} sobject describe -s A -o org0", "{s,}f sobject describe -s A -o org0", "{sf,sobject,{describe,-s},A,-o,org0}",
          "sf sobject describe -s A -o org{0..0}", "echo $({sf,sobject,describe,-s,A,-o,org0})",
          f"{{sf,sobject,describe,-s,A,-o,org0}} ; {T}",
          # Bash does not expand these; the gate may report them or not
          "'{sf,sobject,describe,-s,A,-o,org0}'", "echo {sf,sobject,describe,-s,A,-o,org0}"]


# A line continuation between a file descriptor's digits and the redirection is no gap for Bash:
# `-o 1\<newline>>&2 org0` names the org org0, and sends the output to descriptor 2.
LINES += ["sf sobject describe -s A -o 1\\\n>&2 org0", "sf sobject describe -s A -o 1\\\n\\\n>&2 org0",
          "sf sobject describe -s A -o 1\\\n2>/dev/null org0", "sf sobject describe -s A 2\\\n>&1 -o org0"]


# After the `)` of a substitution, a process substitution or an array the same word goes on: a `#` there
# begins no comment, and the command after it runs with the org as its quotes give it. After the `)` of a
# subshell a `#` does begin a comment.
LINES += ['echo $(echo a)#b ; sf sobject describe -s A -o "org0"', "echo $(echo a)#b ; sf sobject describe -s A -o 'org0'",
          'echo a$(echo 1)#b ; sf sobject describe -s A -o "org0"', 'echo $((1))#b ; sf sobject describe -s A -o "org0"',
          'echo <(echo a)#b ; sf sobject describe -s A -o "org0"', 'x=(1 2)#b ; sf sobject describe -s A -o "org0"',
          'echo $(echo $(echo a)#b)#c ; sf sobject describe -s A -o "org0"',
          'echo $( (echo a) )#b ; sf sobject describe -s A -o "org0"', 'echo $(( (1) ))#b ; sf sobject describe -s A -o "org0"',
          "echo $(echo a)#' ; sf sobject describe -s A -o org1 ; echo ' ; sf sobject describe -s A -o \"org0\"",
          'x=(${x:-#=))} b)#c ; sf sobject describe -s A -o "org0"', "echo $(echo a)#b ; sf sobject describe -s A -o or\\g0",
          # Bash does not run this one; the gate may report it or not
          '( echo a )#b ; sf sobject describe -s A -o "org0"']
# Bash runs the commands of a line before it reads the next one: these run their sf call, and then meet a
# line whose quotes do not pair. (They are run with the random lines below: the runner there goes on after
# a line Bash cannot finish.)
BROKEN_LATER = ['sf sobject describe -s A -o o"r"g0 # "${x:-\'\'1\n}"', "sf sobject describe -s A -o or\\g0\necho a\necho '",
                'sf sobject describe -s A -o "org0"\necho "', "echo 'a\nb' ; sf sobject describe -s A -o 'org0' # '\n'",
                'echo a && sf sobject describe -s A -o org\\0 # "${x:- \\`\\"\\\n1}"']


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


# Random lines. Each is a few statement shapes with pieces of text in them (F) and one or two sf calls in
# ordinary places (CALL). A piece is a quoted string, an ANSI-C string, an expansion with a default or a
# bare word that holds characters which mean something elsewhere, written the way its kind keeps such a
# character inside; the rest is chance. The seed is fixed, so the lines are the same in every run. Bash
# runs each line in a subshell of its own, with sf as a function that records its arguments; the other
# words on a line (a, b, x, E) name no program.
RANDOM_INNER = ["'", '"', "`", "$", "{", "}", "(", ")", ";", "#", "<", ">", "|", "&", " ", " ", "a", "b", "\\", "\n", "=",
                "!", "~", "%", "1", "2", "sf", "$(", "${", "$'", "<<", "\\\n", "\r\n", "\t", "x", ",", "-", ":", "\\\\",
                "''", '""', "\\'", '\\"', "\\}", "\\)", "\\`", "\\$", "#'", '#"', "))", "((", "$((", "E", "<<E", "2>&1",
                ">&2", "1>"]
RANDOM_BARE = ["a", "1", "-x", "a\\;b", "a\\ b", "a\\'b", 'a\\"b', "a\\#b", "\\$x", "a\\&b", "{a,b}", "$x", "${x}", "~",
               "a\\\nb", "a\\|b", "\\(a\\)", "a\\<b", "$'a'", "\"a\"'b'", "a\\\\"]
RANDOM_SHAPES = [
    "CALL", "echo F", "echo F; CALL", "echo F F; CALL", "x=F; CALL", 'echo "$(CALL)"', "( CALL )", "{ CALL; }",
    "if true; then CALL; fi", "echo F | CALL", "CALL # F", "echo F # F\nCALL", "cat <<E\nF\nE\nCALL",
    "echo ${x:-F}; CALL", "echo $(echo F); CALL", ": F; CALL", "echo F\nCALL", "echo F && CALL", "echo F || true; CALL",
    "echo F > /dev/null; CALL", "echo F 2>&1; CALL", "CALL 2>&1", "echo `echo F`; CALL", "for i in F; do CALL; done",
    "case x in x) CALL;; esac", 'echo F; echo "${x:-$(CALL)}"', "[[ -n F ]] ; CALL", "echo $((1+1)) F; CALL",
    "echo F;CALL", "echo F F F", "cat <<'E'\nF\nE\nCALL", "echo F <<< F; CALL", "echo $(echo F # F\n); CALL",
    "echo F \\\n F; CALL", "echo F; ( CALL ) 2>/dev/null", "CALL > /dev/null 2>&1", "echo $(echo F)#F ; CALL",
    "echo a$(echo F)#F\nCALL", "echo <(echo F)#F ; CALL", "x=(F F)#F ; CALL", "echo $((1))#F ; CALL", "( echo F )#F\nCALL",
    "echo `echo F`#F ; CALL", "echo ${x:-F}#F ; CALL", "echo F#F ; CALL", "echo $(echo F) #F\nCALL",
]
# other ways to write the org's name, and the command's
RANDOM_ORG = ['"org%s"', "'org%s'", "or\\g%s", 'o"r"g%s', "org%s''", "$'org%s'"]
RANDOM_CALL = ['"sf"', "'sf'", "s\\f", "\\sf", "s''f", 's"f"']
# what can stand between an option and its value without being a word of the command
RANDOM_BETWEEN = ["2>&1", "1\\\n>&2", ">/dev/null", "2>/dev/null", "<&0", "1>&2", "\\\n", "2\\\n>&1", "12>&1", "$'' ",
                  "\"\"''", "2>&1 >/dev/null"]
RUNNER = r"""
sf() { { printf 'case %s sf' "$n"; for a in "$@"; do printf ' %s' "$a"; done; printf '\n'; } >> "$out"; }
out="$PWD/..out"
n=0
while IFS= read -r -d '' line; do
  ( eval "$line" ) >/dev/null 2>&1 </dev/null
  n=$((n+1))
done < ..lines
printf 'end\n' >> "$out"
"""


def random_fragment(rng) -> str:
    inner = [rng.choice(RANDOM_INNER) for _ in range(rng.randint(0, 6))]
    kind = rng.choice(["sq", "dq", "ansi", "param", "qparam", "bare", "sq", "dq", "param"])
    if kind == "sq":
        return "'" + "".join(c for c in inner if "'" not in c) + "'"
    if kind == "dq":
        return '"' + "".join('\\"' if c == '"' else c for c in inner) + '"'
    if kind == "ansi":
        return "$'" + "".join("\\'" if c == "'" else c for c in inner if c != "\\") + "'"
    if kind in ("param", "qparam"):
        text = "${x:-" + "".join("\\}" if c == "}" else c for c in inner) + "}"
        return '"' + text + '"' if kind == "qparam" else text
    return rng.choice(RANDOM_BARE)


def random_lines(count: int, seed: int) -> list:
    import random
    rng, lines = random.Random(seed), []
    for _ in range(count):
        calls, made = rng.sample([S, T], 2), []
        for _ in range(rng.randint(1, 3)):
            body = rng.choice(RANDOM_SHAPES)
            while "F" in body:
                body = body.replace("F", "\0", 1).replace("\0", random_fragment(rng).replace("F", "\1"), 1)
            body = body.replace("\1", "F")
            while "CALL" in body:
                call = calls.pop() if calls else "echo c"
                if call.startswith("sf") and rng.random() < 0.25:
                    call = call.replace("-o ", "-o " + rng.choice(RANDOM_BETWEEN) + " ")
                elif call.startswith("sf") and rng.random() < 0.25:
                    call = call[:-4] + rng.choice(RANDOM_ORG) % call[-1]
                if call.startswith("sf") and rng.random() < 0.1:
                    call = rng.choice(RANDOM_CALL) + call[2:]
                body = body.replace("CALL", call, 1)
            made.append(body)
        if len(calls) == 2:             # no statement took a call
            made.append(calls.pop())
        lines.append(rng.choice([" ; ", "\n", "; ", " && "]).join(made))
    return lines


# (The gate's older ANSI-C helper decodes with Python's own codec, which warns about an escape it does not
# know, such as `\)`; it keeps such an escape as written, as Bash does. The random strings hold many.)
@pytest.mark.filterwarnings("ignore:invalid escape sequence:DeprecationWarning")
def test_random_lines_run_in_bash(tmp_path):
    # The gate has to report the org of every sf call Bash ran, or refuse the line outright. A call only
    # asked about without its org is a miss.
    lines = BROKEN_LATER + random_lines(600, 17)
    (tmp_path / "..lines").write_bytes(b"".join(line.encode("utf-8") + b"\0" for line in lines))
    (tmp_path / "..runner").write_bytes(RUNNER.encode("utf-8"))
    env = {"PATH": os.path.dirname(BASH), "HOME": str(tmp_path), "SystemRoot": os.environ.get("SystemRoot", "")}
    subprocess.run([BASH, "--noprofile", "--norc", "..runner"], cwd=tmp_path, env=env, capture_output=True,
                   timeout=900, stdin=subprocess.DEVNULL)
    out = tmp_path / "..out"
    text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    if not text.rstrip().endswith("end"):
        pytest.skip("Bash is present but did not run the lines here")
    ran: dict = {}
    for number, org in re.findall(r"case (\d+) sf .*?(org\d)", text):
        ran.setdefault(int(number), set()).add(org)
    missed = []
    for number, orgs in ran.items():
        routes = classify("Bash", {"command": lines[number]})
        if not orgs <= {route.org for route in routes} \
                and not {"no_org", "admin", "credential", "all_orgs"} & {route.kind for route in routes}:
            missed.append((lines[number], sorted(orgs), sorted({route.org for route in routes if route.org})))
    assert not missed, missed[:5]
    assert len(ran) >= 300              # on most lines Bash really ran an sf call
    assert all(ran.get(number) == {"org0"} for number in range(len(BROKEN_LATER))), ran
