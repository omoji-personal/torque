"""An unknown parameter expansion in a path: the values a shell gives it that are not a folder (unset or empty,
`.` and `..`), values that differ between expansions, a quote after a variable, removals rooted at an expansion,
and a word with more expansions than the gate checks. Cases from an independent review on 2026-11-14."""
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import time

import pytest

from torque import gate, workspace as ws


@pytest.fixture
def full_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    base = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(base, "Acme")
    (Path(base) / "project" / "src").mkdir(parents=True, exist_ok=True)
    (Path(base) / "clients" / "acme" / "consent.json").write_text("{}", encoding="utf-8")
    ws.set_ai_access(base, "full")
    return Path(os.path.realpath(base))


def full_denied(root, command, cwd=None):
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
             "cwd": str(cwd or root)}
    with redirect_stderr(io.StringIO()):
        return gate.evaluate(json.dumps(event)) != 0


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "w"
    ws.init_workspace(base, "Synthetic")
    ws.add_client(base, "Acme")
    (base / "clients" / "acme" / "notes.md").write_text("x", encoding="utf-8")
    (base / "project" / "src").mkdir(parents=True)
    return base


def decide(root, command, cwd=None, mode="build-only"):
    return gate.decide("Bash", {"command": command}, root, mode, cwd or root)


@pytest.mark.parametrize("command", [
    "cat ${A}clients/acme/notes.md",                 # unset: cat clients/acme/notes.md
    "cat ${A}${B}clients/acme/notes.md",
    "cat $A/$A/clients/acme/notes.md",               # A=. gives ././clients/acme/notes.md
    "cat ${A}/clients/acme/notes.md",
    "cat " + "${A}" * 30 + "clients/acme/notes.md",
    # A quote or backslash after an unbraced name: the shell reads $A then clients.
    'cat "$A"clients/acme/notes.md',
    "cat $A''clients/acme/notes.md",
    "cat $A\\clients/acme/notes.md",
    'cd "$A"clients && cat acme/notes.md',
    'cp "$A"clients/acme/notes.md project/',
    # Values that differ between expansions: A the root, B unset.
    "A=$PWD; cat $A/${B}clients/acme/notes.md",
    "cd $A/${B}clients && cat acme/notes.md",
])
def test_an_unknown_expansion_does_not_hide_a_client_path(root, command):
    allowed, reason = decide(root, command)
    assert not allowed and reason.startswith("Build-only mode"), (command, reason)


def test_values_that_differ_and_dot_dot_do_not_hide_a_client_path(root):
    for command in ("cat $A/$A/clients/acme/notes.md", "cat $A/$B/clients/acme/notes.md", "grep -r secret $A$B"):
        allowed, _ = decide(root, command, root / "project" / "src")
        assert not allowed, command


@pytest.mark.parametrize("command", [
    "echo $HOME",
    "ls $PWD/src",
    "cat ${A}src/a.md",
    "echo ${A}${B} done",
    "git log --since=$SINCE --author=$WHO",
    "for f in src/*.md; do cp \"$f\" \"$f.bak\"; done",
    "awk -F, '{print " + ",".join(f"${i}" for i in range(1, 41)) + "}' data.csv",
])
def test_unknown_expansions_away_from_client_paths_still_pass(root, command):
    allowed, reason = decide(root, command, root / "project")
    assert allowed, (command, reason)


def test_a_word_with_more_expansions_than_the_gate_checks_is_refused_with_its_own_reason(root):
    over = "awk -F, '{print " + ",".join(f"${i}" for i in range(1, gate._MAX_TOKEN_PARAMS + 2)) + "}' data.csv"
    start = time.monotonic()
    allowed, reason = decide(root, over, root / "project")
    assert time.monotonic() - start < 2.0
    assert not allowed and "variable expansions" in reason and "in a file" in reason, reason
    assert decide(root, "echo " + "${A}" * gate._MAX_TOKEN_PARAMS, root / "project")[0]



def test_full_mode_lets_a_word_with_many_expansions_pass_when_it_names_no_record(full_root):
    over = "awk -F, '{print " + ",".join(f"${i}" for i in range(1, gate._MAX_TOKEN_PARAMS + 2)) + "}' data.csv"
    assert not full_denied(full_root, over)


@pytest.mark.parametrize("command", [
    "rm ${A}clients/acme/consent.json",
    'rm "$A"clients/acme/consent.json',
    "rm $A/$A/clients/acme/consent.json",
    "rm -rf ${D}clients",
    "rm -rf $D/clients",
    "rm -rf ${D}clients/acme",
    "rm " + "${A}" * (gate._MAX_TOKEN_PARAMS + 1) + "clients/acme/consent.json",
    "echo x > " + "${A}" * (gate._MAX_TOKEN_PARAMS + 1) + "clients/acme/consent.json",
])
def test_full_mode_keeps_records_from_a_path_built_with_an_unknown_expansion(full_root, command):
    assert full_denied(full_root, command), command


def test_full_mode_still_lets_a_loop_remove_ordinary_files(full_root):
    assert not full_denied(full_root, 'for f in project/src/*.tmp; do rm -f "$f"; done')
    assert not full_denied(full_root, "rm -rf project/build")


def test_an_empty_value_never_makes_the_gate_resolve_or_list_outside_the_workspace(root, monkeypatch):
    # With A empty, $A/g/notes.md is /g/notes.md (another drive under Git Bash), $A/*/*/x.cls globs the drive root
    # and $A'\\host\share\x' is a network share: they are compared as text, never resolved or listed.
    touched = []
    real_resolve, real_iglob = gate._resolve, gate.glob.iglob

    def resolve(base, raw):
        touched.append(("resolve", raw))
        return real_resolve(base, raw)

    def iglob(pattern, *a, **k):
        touched.append(("glob", pattern))
        return real_iglob(pattern, *a, **k)

    monkeypatch.setattr(gate, "_resolve", resolve)
    monkeypatch.setattr(gate.glob, "iglob", iglob)
    for command in ("cat $A/g/notes.md", "ls $A/*/*/x.cls", "cat $A'\\\\host\\share\\x'", "cat $A//host/share/x"):
        start = time.monotonic()
        decide(root, command, root / "project")
        assert time.monotonic() - start < 1.0, command
    outside = [(kind, raw) for kind, raw in touched
               if raw.startswith(("/", "\\")) and not raw.replace("\\", "/").startswith(root.as_posix())]
    assert outside == [], outside
