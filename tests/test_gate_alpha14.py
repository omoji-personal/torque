"""Alpha 14: regression tests for the long-command gap a spot-check of alpha 13 found.

One regular-expression call does not release the GIL, so the hook's watchdog thread
cannot run while it does. The brace-expansion pattern took quadratic time on a long
run of word characters (or of commas after a brace), and nothing limited a command's
length, so a 100,000-character command held the gate past its watchdog and past the
host's hook timeout, which lets the call run. The gate now blocks an over-long
string before any pattern runs, and brace expansion takes linear time. Each block
has an ordinary counterpart that must still pass. "acme" is a neutral placeholder
client name.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from torque import gate

REPO = Path(__file__).resolve().parents[1]


def _make_ws(root):
    (root / "clients" / "acme").mkdir(parents=True)
    (root / "clients" / "acme" / "notes.md").write_text("client notes", encoding="utf-8")
    (root / "project" / "src").mkdir(parents=True)
    (root / "project" / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".torque").mkdir()
    (root / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    (root / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic",
         "ai_access": "build-only"}), encoding="utf-8")
    return Path(os.path.realpath(str(root)))


@pytest.fixture
def ws(tmp_path):
    return _make_ws(tmp_path / "w")


def _timed(tool, inp, ws, cwd):
    start = time.monotonic()
    result = gate.decide(tool, inp, ws, "build-only", cwd)
    return result, time.monotonic() - start


def _hook(event, project_dir):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "torque.gate"], input=json.dumps(event), capture_output=True,
                          text=True, env=env)


# --- The input-length limit ---

def test_the_limit_is_a_named_constant_of_about_twenty_thousand():
    assert gate.MAX_INPUT_CHARS == 20_000


LONG_SHAPES = [
    "echo " + "x" * 100_000 + "; cat ../clients/acme/notes.md",
    "echo " + "x" * 120_000,
    "echo {" + "a," * 60_000 + "; cat ../clients/acme/notes.md",
    "git commit -m '" + "word " * 25_000 + "'",
    "echo " + "${" * 60_000,
]


@pytest.mark.parametrize("command", LONG_SHAPES, ids=["x-then-read", "x-only", "commas", "words", "params"])
def test_a_command_over_the_limit_blocks_in_under_a_second(ws, command):
    assert len(command) > 100_000
    (allowed, reason), elapsed = _timed("Bash", {"command": command}, ws, ws / "project")
    assert not allowed
    assert "20,000" in reason and "characters" in reason
    assert elapsed < 1.0, elapsed


def test_the_limit_applies_to_any_command_running_tool(ws):
    command = "x" * 100_000
    for tool, inp in [("Monitor", {"command": command}), ("PowerShell", {"command": command}),
                      ("mcp__shell__run", {"args": {"cmd": command}}),
                      ("mcp__shell__run", {"cmd": ["echo", command]})]:
        (allowed, reason), elapsed = _timed(tool, inp, ws, ws / "project")
        assert not allowed and "20,000" in reason, tool
        assert elapsed < 1.0, (tool, elapsed)


def test_the_limit_applies_to_a_joined_command_list(ws):
    parts = ["echo", *["y" * 15_000] * 2]
    allowed, reason = gate.decide("mcp__shell__run", {"cmd": parts}, ws, "build-only", ws / "project")
    assert not allowed and "20,000" in reason


def test_the_limit_applies_to_any_mcp_string(ws):
    allowed, reason = gate.decide("mcp__notes__search", {"q": {"text": "z" * 30_000}}, ws, "build-only",
                                  ws / "project")
    assert not allowed and "20,000" in reason


def test_the_limit_applies_to_a_file_tool_path(ws):
    allowed, reason = gate.decide("Read", {"file_path": "a/" * 15_000}, ws, "build-only", ws / "project")
    assert not allowed and "20,000" in reason


def test_a_command_at_the_limit_is_not_blocked_for_its_length(ws):
    command = "echo " + "x" * (gate.MAX_INPUT_CHARS - 5)
    assert len(command) == gate.MAX_INPUT_CHARS
    (allowed, reason), elapsed = _timed("Bash", {"command": command}, ws, ws / "project")
    assert (allowed, reason) == (True, "")
    assert elapsed < 2.0, elapsed
    allowed, reason = gate.decide("Bash", {"command": command + "x"}, ws, "build-only", ws / "project")
    assert not allowed and "20,000" in reason


def test_file_contents_the_gate_does_not_parse_are_not_limited(ws):
    body = "<xml>" + "field value " * 5_000 + "</xml>"
    assert len(body) > gate.MAX_INPUT_CHARS
    target = str(ws / "project" / "src" / "big.xml")
    for tool, inp in [("Write", {"file_path": target, "content": body}),
                      ("Edit", {"file_path": target, "old_string": "a", "new_string": body}),
                      ("MultiEdit", {"file_path": target, "edits": [{"old_string": "a", "new_string": body}]})]:
        assert gate.decide(tool, inp, ws, "build-only", ws / "project") == (True, ""), tool


def test_the_limit_is_off_outside_build_only(ws):
    assert gate.decide("Bash", {"command": "x" * 100_000}, ws, "full", ws) == (True, "")


def _commit_message(n):
    words = ("Refactor the contact preference trigger handler so bulk updates respect "
             "the opt-out flag; add tests for 200-record batches (see ticket 42). ").split()
    out, i = [], 0
    while len(" ".join(out)) < n:
        out.append(words[i % len(words)])
        i += 1
    return " ".join(out)[:n]


@pytest.mark.parametrize("cwd_name", ["project", "."])
def test_a_long_commit_message_is_still_allowed(ws, cwd_name):
    message = _commit_message(5_000)
    command = f'git commit -m "{message}"'
    (allowed, reason), elapsed = _timed("Bash", {"command": command}, ws, ws / cwd_name)
    assert (allowed, reason) == (True, "")
    assert elapsed < 2.0, elapsed


def test_the_hook_blocks_a_100k_command_well_inside_its_watchdog(ws):
    command = "echo " + "x" * 100_000 + "; cat ../clients/acme/notes.md"
    start = time.monotonic()
    result = _hook({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(ws / "project")}, ws)
    elapsed = time.monotonic() - start
    assert result.returncode == 2, result.stderr
    assert "20,000" in result.stderr
    assert elapsed < gate.GATE_TIME_BUDGET, elapsed


def test_the_hook_still_allows_a_long_commit_message(ws):
    command = f'git commit -m "{_commit_message(5_000)}"'
    result = _hook({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(ws / "project")}, ws)
    assert result.returncode == 0, result.stderr


# --- Brace expansion in linear time ---

BRACE_CASES = [
    ("cat {clients,project}/acme/notes.md", "cat clients/acme/notes.md project/acme/notes.md"),
    ("x{a,b}", "xa xb"),
    ("{a,b}y", "ay by"),
    ("cp f{,.bak}", "cp f f.bak"),
    ("echo {a,{b,c}}", "echo {a,b a,c}"),
    ("ls 'q'{a,b}", "ls 'q'a b"),
    ("cat c{l,x}ients/acme", "cat clients/acme cxients/acme"),
    ("echo {}", "echo {}"),
    ("echo {a}", "echo {a}"),
    ("rm -rf {.claude,x}/settings.json", "rm -rf .claude/settings.json x/settings.json"),
    ('echo "{a,b}"', 'echo "a b"'),
    ("git log --format=%{H,x}", "git log --format=%H --format=%x"),
    ("a{b,c}d{e,f}", "abd acde f"),
    ("a{b,c}{d,e}", "ab acd e"),
    ("cat ${HOME}/{a,b}", "cat ${HOME}/a /b"),
    ("echo a{b,c", "echo a{b,c"),
    ("echo a{b c,d}", "echo a{b c,d}"),
    ("{x}y{a,b}", "{x}ya yb"),
]


@pytest.mark.parametrize("command,expanded", BRACE_CASES)
def test_brace_expansion_keeps_its_behaviour(command, expanded):
    assert gate._expand_braces(command) == expanded


def _reference_expand(command):
    """The alpha 13 expansion, kept here to compare against on short inputs."""
    pattern = re.compile(r"([^\s{}'\"]*)\{([^{}\s]*,[^{}\s]*)\}([^\s{}'\"]*)")
    for _ in range(8):
        expanded = pattern.sub(lambda m: " ".join(m.group(1) + alt + m.group(3)
                                                  for alt in m.group(2).split(",")), command)
        if expanded == command:
            break
        command = expanded
    return command


def test_brace_expansion_matches_alpha13_on_many_short_inputs():
    import random
    rng = random.Random(14)
    alphabet = "ab,{} '\"/.x"
    for _ in range(4000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 18)))
        assert gate._expand_braces(text) == _reference_expand(text), repr(text)


@pytest.mark.parametrize("text", [
    "x" * 19_999,
    "{" + "a," * 9_999,
    "{" * 19_999,
    "a{" * 9_999,
    "{a" * 9_999,
    ("x" * 50 + "{a,b} ") * 350,
    "x" * 10_000 + "{a,b}" + "y" * 9_990,
], ids=["run", "commas", "open-braces", "a-brace", "brace-a", "many-groups", "one-group-long-words"])
def test_brace_expansion_is_fast_below_the_limit(text):
    start = time.monotonic()
    gate._expand_braces(text)
    assert time.monotonic() - start < 0.5


@pytest.mark.parametrize("command", [
    "echo " + "x" * 19_900,
    "echo {" + "a," * 9_950,
    "echo " + "${" * 9_950,
    "echo " + "stash@{" * 2_840,
    "echo " + "$'" * 9_950,
    "echo " + "(" * 19_900,
    "echo " + "x" * 19_900 + "; cat ../clients/acme/notes.md",
], ids=["run", "commas", "params", "stash", "ansi-c", "parens", "run-then-read"])
def test_any_shape_below_the_limit_finishes_inside_the_budget(ws, command):
    assert len(command) <= gate.MAX_INPUT_CHARS
    (allowed, reason), elapsed = _timed("Bash", {"command": command}, ws, ws / "project")
    # Path work is cooperative: past the budget the call blocks. No pattern stalls it.
    assert elapsed < gate.GATE_TIME_BUDGET + 1.0, elapsed
    if "clients" in command:
        assert not allowed


# --- Records ---

def test_version_is_alpha14_or_later():
    import torque
    assert re.fullmatch(r"2\.0\.0a(\d+)", torque.__version__) and int(torque.__version__[6:]) >= 14
    assert f'version = "{torque.__version__}"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_changelog_and_record_for_alpha14():
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "\n## 2.0.0a14 - " in changelog
    record = (REPO / "docs" / "validation-alpha14.md").read_text(encoding="utf-8")
    assert "Python 3." in record and "## Review scope" in record and "20,000" in record


def test_alpha13_record_says_the_long_command_gap_is_closed():
    text = " ".join((REPO / "docs" / "validation-alpha13.md").read_text(encoding="utf-8").split())
    assert "closed in alpha 14" in text and "20,000" in text


def test_build_only_docs_state_the_length_limit():
    text = " ".join((REPO / "docs" / "ai-access.md").read_text(encoding="utf-8").split())
    assert "20,000 characters" in text


def test_readme_status_and_neutral_lineage():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    top = text.split("\n## ", 1)[0]
    import torque
    assert torque.__version__ in top and "development alpha" in top
    assert "Justiceserver" not in top and "JusticeServer" not in top
    assert "earlier consulting toolkit" in " ".join(top.split())


def test_brace_expansion_that_multiplies_the_length_blocks_fast(ws):
    command = "echo " + "p" * 7_000 + "{" + "," * 6_000 + "}"
    assert len(command) <= gate.MAX_INPUT_CHARS
    (allowed, reason), elapsed = _timed("Bash", {"command": command}, ws, ws / "project")
    assert not allowed and "brace expansion" in reason
    assert elapsed < 2.0, elapsed


def test_an_ordinary_brace_copy_of_a_long_name_is_allowed(ws):
    command = "cp src/" + "x" * 5_000 + ".py{,.bak}"
    assert gate.decide("Bash", {"command": command}, ws, "build-only", ws / "project") == (True, "")
