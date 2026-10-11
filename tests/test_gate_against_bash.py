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


# Completed lines still run when a quote opens much earlier than the final line.
BROKEN_LATER += [
    S.replace("org0", 'o"r"g0') + ' # "\necho \'\n' + '\n'.join(['echo VALUE'] * 49),
    S.replace("org0", 'o"r"g0') + ' # "\necho \\\necho \'\n' + '\n'.join(['echo VALUE'] * 49),
    S.replace("org0", 'o"r"g0') + '\necho $\'\n' + '\n'.join(['echo VALUE'] * 49),
    S + '\n' + '\n'.join(['echo VALUE'] * 4998) + '\necho "',
    S.replace("org0", 'o"r"g0') + ' "VALUE\nSECOND" # "\necho \\\necho \'\n'
    + '\n'.join(['echo VALUE'] * 49),
]


# ---- round 18: portable timing, a coprocess name before a compound command, command definitions
LINES += [
    f"time -p {S}", f"time -p command {S}", f"time -p {{ {S}; }}", f"time -p ( {S} ); {T}",
    f"coproc echo {{ {S}; }}; wait", f"coproc sf {{ {S}; }}; wait",
    f"coproc worker {{ {S}; {T}; }}; wait", f"coproc echo ( {S} ); wait",
    f"coproc sf ( {S} ); wait", f"coproc worker ( {S}; {T} ); wait", f"coproc {S}; wait",
    f"alias echo=hostname; {S}", f"alias user_name='echo VALUE'; {S}",
    f"echo() {{ {S}; }}; echo", f"function echo {{ {S}; }}; echo", f"function echo() {{ {S}; }}; echo",
    f"f() {{ {S}; }}; f; {T}", f"function deploy_all {{ {S}; }}; deploy_all",
    f"function deploy_all() {{ {S}; }}; deploy_all", f"function echo\n{{ {S}; }}; echo",
    f"echo() ( {S} ); echo", f"echo()( {S} ); echo",
]


# ---- round 19, classification tables and guarded lanes: literal options and endpoint text

LINES += [
    'sf data query -t -q "SELECT Id,LogUserId FROM ApexLog" -o org0',
    'sf data query -t -q "SELECT Id FROM ApexLog WHERE Operation = \'VALUE&VALUE\'" -o org0',
    "sf data get record -t -s ApexLog -i 07L000000000001AAA -o org0",
    "sf apex tail log --skip-trace-flag -o org0",
    "sf api request rest '/services/data/v66.0/tooling/query?q=SELECT+Id,businessownerid+FROM+FieldDefinition' -o org0",
    "sf api request rest '/services/data/v66.0/tooling/query?q=SELECT+permissionsapiuseronly+FROM+Profile' -o org0",
    "sf api request rest '/services/data/v66.0/sobjects/Contact/External_Id__c/%2Fsobjects%2FApexLog%2FVALUE' -o org0",
    'sf api request rest "/services/data/v66.0/query?q=SELECT+Name+FROM+Account+WHERE+Name=\'apexrest\'" -o org0',
    'sf api request rest "/services/data/v66.0/tooling/query?q=SELECT+Id+FROM+ApexClass+WHERE+Name=\'&q=SELECT+Email+FROM+User+LIMIT+1&x=\'" -o org0',
    "sf api request rest '/services/data/v66.0/tooling/query?q=SELECT+Id+FROM+ApexClass#fragment' -o org0",
    'sf api request rest /services/data/v66.0/limits -H "X-Note: executeanonymous" -o org0',
    "sf api request rest /services/data/v66.0/limits -S apexrest.json -o org0",
]


def test_r19_bash_passes_the_literal_credential_header_and_the_gate_refuses_it(tmp_path):
    if bash_runs(S, tmp_path) != {"org0"}:
        pytest.skip("Bash is present but did not run the baseline line here")
    command = 'sf api request rest /services/data/v66.0/limits -H "Authorization: Bearer VALUE" -o org0'
    assert bash_runs(command, tmp_path) == {"org0"}
    assert [r.kind for r in classify("Bash", {"command": command})] == ["credential"]


@pytest.mark.parametrize("command,data", [
    ("jsc-loganalyzer --target-org org0", "debug_logs"),
    ("jsc-advisory impact --target-org org0 --sobject Contact --where \"Email = 'VALUE'\"", "records"),
])
def test_r19_bash_packaged_script_words_use_the_delegate_class(tmp_path, command, data):
    if bash_runs(S, tmp_path) != {"org0"}:
        pytest.skip("Bash is present but did not run the baseline line here")
    stub = command.split()[0] + '() { sf "$@"; }; '
    assert bash_runs(stub + command, tmp_path) == {"org0"}
    assert [(r.kind, r.org, r.data) for r in classify("Bash", {"command": command})] == [
        ("read", "org0", data)]


# ---- round 19, Bash reading: redirections, continuations and expansion spelling

LINES += [
    S.replace("-o org0", "-o {fd}>/dev/null org0"),
    S.replace("-o org0", "-o {fds[0]}>/dev/null org0"),
    S.replace("-o org0", "-o {f\\\nd}>/dev/null org0"),
    S.replace("-o org0", "-o {fds[a[0]]}>/dev/null org0"),
    S + " >|/dev/null", S + " >\\\n|/dev/null",
    S.replace("-s A", "-s $(echo A)"), S.replace("-s A", "-s `echo A`"),
    S.replace("-s A", "-s <(echo A)"), S.replace("-s A", "-s pre$(echo A)post"),
    S.replace("-s A", "-s $(echo A)").replace("org0", "org{+0..+0}"),
    S.replace("-s A", "-s $(echo " + T + ")"),
    S.replace("-s A", "-s $(echo A; " + T + ")"),
    S.replace("-s A", "-s $(echo -o org1)"),
    S.replace("-s A", '-s $(echo "-o org1")'),
    S.replace("-s A", '-s $(printf "%s %s" -o org1)'),
    S.replace("-s A", "-s `echo -o org1`"),
    S.replace("-s A", "-s $(echo -o org{1..1})"),
    S + " > >(cat) --json", S + " > >\\\n(cat) --json",
    S + " > >(cat <(echo VALUE)) --json",
    S.replace("org0", "org{+0..+0}"), S.replace("org0", "org{0..0..+1}"),
    S.replace("org0", "org{+00..+00}"), S.replace("org0", "org{0..0..-1}"),
    '{sf,sobject,describe,-s,A,-o,org0,"VALUE,VALUE"}',
    r"{sf,sobject,describe,-s,A,-o,org0,VALUE\,VALUE}",
    f'echo {{"VALUE,VALUE",other}}; {S}; {T}',
    f'echo {{org{{"0".."0"}},other}}; {S}; {T}',
    f"echo {{org{{0..''0}},other}}; {S}; {T}",
    f"echo $'\\c0'; {S}; {T}", f"echo $'\\c\\\\'; {S}; {T}",
    f"coproc echo if {S}; then :; fi; wait",
    f"coproc echo while {S} && false; do :; done; wait",
    f"coproc echo until {S} || true; do :; done; wait",
    f"coproc echo for value in VALUE; do {S}; done; wait",
    f"coproc echo case VALUE in VALUE) {S};; esac; wait",
    f'coproc echo [[ "$({S})" = "" ]]; wait',
    f'coproc echo if {S}; then {T}; fi; wait',
    f'"for" VALUE; {S}', f"fo''r VALUE; {S}", f'fo""r VALUE; {S}',
    f'PA"TH"=VALUE; {S}', f'x\\=VALUE; {S}', f'"[[" VALUE; {S}',
    f"x=VALUE if echo VALUE; {S}", f"x=VALUE [[ VALUE; {S}",
    f"[[ 0 -eq 'HOME=1' ]]; {S}", f"[[ 'HOME=1' -eq 0 ]]; {S}",
    f"[[ 0 -eq 'HOM'\"E=1\" ]]; {S}", f"[[ 0 -eq 'x=1' ]]; {S}",
    f"[[ '(HOME=1,0)' -eq 0 ]]; {S}",
    'cat <<EOF\nEO\\\nF\n' + S.replace("org0", '"org\\\n0"') + "\n#'",
    'cat <<-EOF\n\tEO\\\nF\n' + S + "\n",
    "cat <<'EOF'\nEO\\\nF\n" + S + "\nEOF\n" + T,
    "cat <<$'EOF'\necho '\nEOF\n" + S.replace("org0", '"org\\\n0"') + "\n#'",
    "cat <\\\n<E\necho '\nE\n" + S.replace("org0", '"org\\\n0"') + "\n#'",
    "cat <<E\\\nOF\necho '\nEOF\n" + S.replace("org0", '"org\\\n0"') + "\n#'",
    'cat <<EOF\n$\\\n(' + S + ")\nEOF",
    'echo $\\\n(echo VALUE)#x ; ' + S,
    'echo <\\\n(echo VALUE)#x ; ' + S,
    'r19x=; echo "${r19x:-$\\\n(' + S + ')}"',
    'echo "$(ca\\\nse VALUE in (V*) ' + S + ';; e\\\nsac)"',
    S.replace("org0", "$\\\n'org0'"), S.replace("org0", "$\\\n\\\n'org0'"),
    'echo VALUE\r#x ; ' + S.replace("org0", '"org0"'),
]


@pytest.mark.parametrize("word,org", [
    ('{acme-prod",extra",other}', "acme-prod,extra"),
    ('{"acme-prod,extra",other}', "acme-prod,extra"),
    (r"{acme-prod\,extra,other}", "acme-prod,extra"),
    ('{org{"0".."0"},other}', "org{0..0}"),
    (r"{org{0\.\.0},other}", "org{0..0}"),
    ("{org{0..''0},other}", "org{0..0}"),
    ("org{+01..+01}", "org1"), ("org{+2..01}", "org02"),
    ("org{0..-01}", "org000"), ("org{-1..01}", "org-1"),
    ("org{02..-01}", "org002"), ("org{-00..-00}", "org000"),
    ("org{-9223372036854775808..-9223372036854775808}", "org-9223372036854775808"),
    ("org{9223372036854775807..9223372036854775807}", "org9223372036854775807"),
    ("{org{9223372036854775808..9223372036854775808},other}",
     "org{9223372036854775808..9223372036854775808}"),
    ("{org{0..0..9223372036854775808},other}", "org{0..0..9223372036854775808}"),
    ("{org{0..1..-9223372036854775808},other}", "org{0..1..-9223372036854775808}"),
    ("{org{0..9223372036854775807..9223372036854775807},other}", "org0"),
    ("{org{\u0660..\u0660},other}", "org{\u0660..\u0660}"),
    ("acme-prod\ue000Qe000", "acme-prod\ue000Qe000"),
    ('"acme-prod\ue100Qe000"', "acme-prod\ue100Qe000"),
    (r"org$'\c0'", "org\x10"), (r"org$'\c{'", "org\x1b"),
    (r"org$'\c\\'", "org\x1c"), (r"org$'\c?'", "org\x7f"),
    ("org$'\\c\u00df'", "org\x03\udc9f"),
    (r"org$'\xC3\x9F'", "org\u00df"), (r"org$'\303\237'", "org\u00df"),
    (r"org$'\xC3'$'\x9F'", "org\u00df"), (r"org$'\303'$'\237'", "org\u00df"),
    (r"org$'\xE9'", "org\udce9"), (r"org$'\377'", "org\udcff"),
    (r"org$'\x00VALUE'", "org"),
    # Git Bash's script reader drops CR; Unix Bash keeps it in the argument.
    ("org0\rVALUE", "org0VALUE" if os.name == "nt" else "org0\rVALUE"),
    ("'org0\rVALUE'", "org0VALUE" if os.name == "nt" else "org0\rVALUE"),
])
def test_r19_bash_records_the_complete_org_argument(tmp_path, word, org):
    if bash_runs(S, tmp_path) != {"org0"}:
        pytest.skip("Bash is present but did not run the baseline line here")
    command = "sf sobject describe -s A -o " + word
    assert bash_runs(command, tmp_path) is not None
    recorded = (tmp_path / "..out").read_bytes()
    argument = recorded.split(b" -o ", 1)[1].split(b" ", 1)[0].rstrip(b"\n")
    assert argument == org.encode("utf-8", "surrogateescape"), (command, recorded)
    assert org in {r.org for r in classify("Bash", {"command": command}) if r.kind == "read"}


RANDOM_SHAPES += [
    "coproc echo if CALL; then echo F; fi; wait",
    "coproc echo while CALL && false; do echo F; done; wait",
    "coproc echo until CALL || true; do echo F; done; wait",
    "coproc echo for value in F; do CALL; done; wait",
    "coproc echo case F in *) CALL;; esac; wait",
    "coproc echo if false; then :; elif CALL; then echo F; fi; wait",
    "coproc echo { echo F; CALL; }; wait",
    "coproc echo ( echo F; CALL ); wait",
    "coproc echo for ((i=0;i<1;i++)); do echo F; CALL; done; wait",
    "for ((i=0;i<1;i++)); do echo F; CALL; done",
    "if false; then :; elif true; then echo F; CALL; fi",
    "until true; do echo F; done; CALL",
    "while false; do echo F; done; CALL",
    "! { echo F; }; CALL",
    "time -p if true; then echo F; CALL; fi",
    "time -p while CALL && false; do echo F; done",
    "time -p until CALL || true; do echo F; done",
    "case F in *) echo F ;& *) CALL;; esac",
    "case F in *) echo F ;;& *) CALL;; esac",
    "case F in (*) CALL;; esac",
    "case F in (a|b|*) CALL;; esac",
    "[[ 0 -eq 'HOME=1' ]]; echo F; CALL",
    "[[ '(HOME=1,0)' -eq 0 ]]; echo F; CALL",
    'PA"TH"=VALUE; echo F; CALL',
    "'PATH=VALUE'; echo F; CALL",
    r"x\=VALUE; echo F; CALL",
    'x=""VALUE; echo F; CALL',
    '""for F; CALL',
    'for"" F; CALL',
    "f'o'r F; CALL",
    '"[[" F; CALL',
    r"\! F; CALL",
    '"{" F; CALL',
    '"function" echo F; CALL',
    '"case" F; CALL',
    "c'a'se F; CALL",
    '"coproc" echo F; CALL',
    "un't'il F; CALL",
    "th'e'n F; CALL",
    "el'if' F; CALL",
    "do'n'e F; CALL",
    "if false; then echo F; elif true; then CALL; else :; fi",
    "if false; then : F; else CALL; fi",
    "for i; do echo F; done; CALL",
    "select i in F; do CALL; done </dev/null; CALL",
    "case x in x) echo F ;& y) CALL ;; esac",
    "case x in x) echo F ;;& x) CALL ;; esac",
    "case x in (x|y) echo F; CALL ;; esac",
    "[[ F == x ]] ; CALL",
    "[[ F =~ x ]] ; CALL",
    "[[ 0 -eq 0 ]]; echo F; CALL",
    "((1)); echo F; CALL",
    "{ echo F; CALL; } 3>&1",
    "(echo F; CALL) & wait",
    "f()(echo F; CALL); f",
    "cat <(echo F; CALL); CALL",
    "cat < <(echo F; CALL); CALL",
    "echo F > >(cat); wait; CALL",
    ": F <>/dev/null; CALL",
    "echo F >|/dev/null; CALL",
    "echo F &>/dev/null; CALL",
    "echo F &>>/dev/null; CALL",
    ": F 3>&1-; CALL",
    ": F 3<&0-; CALL",
    ": F {fd}>/dev/null; CALL",
    ": F 12\\\n<>/dev/null; CALL",
    "cat <<-E\n\tF\n\tE\nCALL",
    "cat <<A <<'B'\nF\nA\nF\nB\nCALL",
    "cat <<$'E'\nF\nE\nCALL",
    "cat <<\\E\nF\nE\nCALL",
    "echo ${x-F}; CALL",
    "echo ${x+F}; CALL",
    "echo ${x=F}; CALL",
    "echo ${x?F}; CALL",
    "echo ${x#F}; CALL",
    "echo ${x%%F}; CALL",
    "echo ${x/F/VALUE}; CALL",
    "echo ${x//F/VALUE}; CALL",
    "echo ${x^F}; CALL",
    "echo ${x,,F}; CALL",
    "echo ${x:0:1} F; CALL",
    ': "${x@Q}" F; CALL',
    "echo $[1+1] F; CALL",
    "echo {F,VALUE}; CALL",
    "echo F{a..c}; CALL",
    "echo {F,VALUE}{a,b}; CALL",
    "echo $'F'; CALL",
    "printf '%s\\n' F; CALL",
    "read -r v <<< F; CALL",
    "read -r -n 1 v <<< F; CALL",
    "printf -v v '%s' F; CALL",
    "CALL |& cat; echo F",
    ": F &&\nCALL",
    "echo F \\\r\nCALL",
    "CALL 3>&1-",
    "echo $\\\n(CALL)#F",
    "echo <\\\n(CALL)#F",
    "echo >\\\n(CALL)#F",
    'echo "${r19x:-$\\\n(CALL)}"',
    'echo "$\\\n{r19x:-$(CALL)}"',
    'echo "${r19x\\\n:-$(CALL)}"',
    'echo "$(ca\\\nse F in (*) CALL;; e\\\nsac)"',
    'echo "$(case F in (*) CALL;; es\\\nac)"',
    "cat <<END\nE\\\nND\nCALL",
    "cat <<-END\n\tE\\\nND\nCALL",
    "cat <<END\n$\\\n(CALL)\nEND",
    "cat <<$'END'\nF\nEND\nCALL",
]
RANDOM_BARE += [
    r"a\,b", 'a","b', "a''b", "a\\\n#b", "$'a\\000b'",
    "${x:-VALUE}", "${x:+VALUE}", "${x#VALUE}", "${x%%VALUE}",
    "a{b,{c,d}}", "{+0..+0}", "{0..-01}", r"a\!b", r"a\~b",
    r"\for", "'if'", 'f"o"r', '""for', r"\!", "'[['", r"x\=VALUE",
]
RANDOM_BETWEEN += [
    "3>&1-", "3<&0-", "<>/dev/null", ">|/dev/null", "&>/dev/null", "&>>/dev/null",
    "{fd}>/dev/null", "{fd}<>/dev/null", "1\\\n2>&1", "1\\\n\\\n>&2",
    "5>&1 5>&-", "4<&0 4<&-", "9>&2", "{fd}>&2", "\\\n\\\n", "2\\\n\\\n>&1",
    "> >(cat)", "< <(echo VALUE)",
]
RANDOM_ORG += [
    "$'org\\x3%s'", "o$'r'g%s", "or$'g'%s", "org%s\\\n",
    '"org"%s', "or''g%s", r"o\rg%s", 'o"r""g"%s', "''org%s",
]
RANDOM_CALL += [
    "$'\\x73f'", "s$'f'", "$'s'f", "s\\\nf", "$'s\\146'",
    "'s'\"f\"", 'sf""', '""sf',
]
