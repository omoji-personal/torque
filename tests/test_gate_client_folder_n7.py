"""N7: close the client-folder gaps the docs already promise. Root-less recursive
listings, brace Globs, the ANSI-C spelling and connected mode's variable-built paths
stop reaching other clients' files. Synthetic workspace only."""
import os
from pathlib import Path

import pytest

from torque import gate, gate_connected as gc, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    base = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(base, "Acme")
    (Path(base) / "project").mkdir(exist_ok=True)
    ws.set_ai_access(base, "build-only")
    return Path(os.path.realpath(base))


def blocked(root, command, cwd=None):
    ok, _ = gate.decide("Bash", {"command": command}, root, "build-only", cwd or root)
    return not ok


# claude-A2a#2: a root-less recursive listing defaults to the current directory, which
# holds clients/, so from the workspace root it is blocked. The Bash idioms:
ROOTLESS_BLOCKED = [
    "find -name '*.md'",
    "find -type f -name '*.md'",
    "find . -name '*.md'",
    "tree -L 2",
    "tree -I node_modules",
    "ls -R -I '*.tmp'",
    "grep -r secret",
    "rg secret",
]


@pytest.mark.parametrize("command", ROOTLESS_BLOCKED)
def test_rootless_recursive_listings_are_blocked_from_the_workspace_root(root, command):
    assert blocked(root, command), command


# The same idioms, narrowed to project/, pass.
ROOTED_ALLOWED = [
    "find project -name '*.md'",
    "find project -type f",
    "tree -L 2 project",
    "tree -I node_modules project",
    "grep -r secret project",
    "rg secret project",
]


@pytest.mark.parametrize("command", ROOTED_ALLOWED)
def test_the_same_listings_narrowed_to_project_pass(root, command):
    assert not blocked(root, command), command


# The PowerShell listing idioms (the normal way to find files on Windows).
PS_ROOTLESS_BLOCKED = [
    "Get-ChildItem -Recurse -Filter *.md",
    "Get-ChildItem -Recurse -Include *.md",
    "Get-ChildItem -Depth 2",
    "Get-ChildItem *.md -Recurse",
    "Get-ChildItem -Recurse -Exclude node_modules",
    "gci -r -Filter *.cls",
]


@pytest.mark.parametrize("command", PS_ROOTLESS_BLOCKED)
def test_powershell_rootless_recursive_listings_are_blocked(root, command):
    ok, _ = gate.decide("PowerShell", {"command": command}, root, "build-only", root)
    assert not ok, command


PS_ROOTED_ALLOWED = [
    "Get-ChildItem -Recurse -Filter *.md project",
    "Get-ChildItem project -Recurse -Include *.cls",
    "Get-ChildItem -Path project -Recurse",
]


@pytest.mark.parametrize("command", PS_ROOTED_ALLOWED)
def test_powershell_listings_narrowed_to_project_pass(root, command):
    ok, _ = gate.decide("PowerShell", {"command": command}, root, "build-only", root)
    assert ok, command


# claude-A2a#10: an absolute Glob pattern with a brace group reaches clients/.
def test_absolute_glob_with_a_brace_group_is_blocked(root):
    project = root / "project"
    patterns = [f"{root.as_posix()}/{{clients,x}}/**/*.md",
                f"{root.as_posix()}/{{x,clients}}/**/*.md"]
    for pattern in patterns:
        ok, _ = gate.decide("Glob", {"pattern": pattern}, root, "build-only", project)
        assert not ok, pattern
    # A brace group that names only folders under project/ still passes.
    ok, _ = gate.decide("Glob", {"pattern": f"{project.as_posix()}/{{src,test}}/**/*.md"}, root, "build-only", project)
    assert ok


# Round 2 (P1/P2): a Glob pattern is one whole pathname. A workspace or client folder
# whose name holds a space keeps its root (the round-1 brace expansion split patterns
# on spaces), nested alternatives are expanded, and an unresolved brace is read
# conservatively as a wildcard.
@pytest.fixture
def spaced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    base = ws.init_workspace(tmp_path / "firm with space", "Firm")
    ws.add_client(base, "Acme")
    base = Path(os.path.realpath(base))
    (base / "project").mkdir(exist_ok=True)
    (base / "clients" / "acme corp").mkdir()
    (base / "clients" / "acme corp" / "notes.md").write_text("x", encoding="utf-8")
    ws.set_ai_access(base, "build-only")
    return base


def test_glob_patterns_keep_their_root_when_a_path_holds_a_space(spaced):
    project = spaced / "project"
    for pattern in (f"{spaced.as_posix()}/clients/**/*.md",
                    f"{spaced.as_posix()}/clients/acme corp/**/*.md",
                    f"{spaced.as_posix()}/{{clients,x}}/**/*.md",
                    f"{spaced.as_posix()}/{{x,cl{{i,j}}ents}}/**/*.md"):
        ok, _ = gate.decide("Glob", {"pattern": pattern, "path": str(project)}, spaced, "build-only", project)
        assert not ok, pattern
    ok, _ = gate.decide("Glob", {"pattern": f"{project.as_posix()}/**/*.md", "path": str(project)},
                        spaced, "build-only", project)
    assert ok


def test_a_client_path_with_a_space_is_client_context_in_bash(spaced):
    ok, _ = gate.decide("Bash", {"command": 'cat "clients/acme corp/notes.md"'}, spaced, "build-only", spaced)
    assert not ok
    ok, _ = gate.decide("Bash", {"command": "grep -r x project"}, spaced, "build-only", spaced)
    assert ok


def test_nested_and_unresolved_brace_globs_are_blocked(root):
    project = root / "project"
    for pattern in (f"{root.as_posix()}/{{x,cl{{i,j}}ents}}/**/*.md",   # the original finding
                    f"{root.as_posix()}/{{clients/**/*.md",              # a brace that never closes
                    f"{root.as_posix()}/{{a,{{b,{{c,clients}}}}}}/**"):   # deeper nesting
        ok, _ = gate.decide("Glob", {"pattern": pattern, "path": str(project)}, root, "build-only", project)
        assert not ok, pattern
    # Alternatives that all stay under project/ pass.
    ok, _ = gate.decide("Glob", {"pattern": f"{project.as_posix()}/{{src,{{test,docs}}}}/**/*.md",
                                 "path": str(project)}, root, "build-only", project)
    assert ok


# Round 2 (P2): find's leading traversal options (-H, -L, -P) come before its paths, so
# `find -H project` is rooted at project/; with no path the current directory still is.
@pytest.mark.parametrize("command", ["find -H project -name x", "find -L project -name x",
                                     "find -P project -type f", "find -H -L project -name x"])
def test_scoped_find_with_traversal_options_passes(root, command):
    assert not blocked(root, command), command


@pytest.mark.parametrize("command", ["find -H -name x", "find -L . -name x", "find -L -type f"])
def test_rootless_find_with_traversal_options_is_still_blocked(root, command):
    assert blocked(root, command), command


# claude-A5#4: the build-only ANSI-C decoder matches bash: a NUL truncates, and a
# trailing invalid escape is kept literal instead of failing open.
def test_ansi_c_matches_bash_including_a_trailing_invalid_escape(root):
    # From the workspace root the decoded path reaches clients/; bash truncates at the
    # NUL and keeps a trailing invalid escape, so all three resolve to the same file.
    for command in (r"cat $'\x63lients/acme/notes.md\0'",
                    r"cat $'\x63lients/acme/notes.md\0\x'",
                    r"cat $'\x63lients/acme/notes.md\0\N'",
                    r"cat $'clients/acme/notes.md'"):
        assert blocked(root, command), command


def test_the_shared_ansi_c_decoder_is_one_function():
    from torque import connected_routes
    assert connected_routes._ansi_c is gate._ansi_c
    # bash reads a NUL as the end of the word, and keeps an unknown trailing escape.
    assert gate._ansi_c(r"a\x62c\0def") == "abc"
    assert gate._ansi_c(r"abc\N") == "abc\\N"


# gemini-code#1: in a client-bound connected session, a variable-built path to another
# client's file from a subfolder, and the tracked-index deny, anchor at the workspace.
def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    import subprocess
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def connected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    base = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(base, "Acme")
    ws.add_client(base, "Beta")
    ws.set_ai_access(base, "connected", approval="required", presence=YES)
    return Path(os.path.realpath(base))


def _decide(root, command, bound="acme", cwd=None):
    return gc.decide_connected("Bash", {"command": command}, root, cwd or root,
                               env={"TORQUE_CLIENT": bound}, permission_mode="default",
                               session_id="s", tool_use_id="t")


def test_variable_built_path_to_another_client_is_denied_from_a_subfolder(connected):
    (connected / "clients" / "beta" / "secret.md").write_text("x", encoding="utf-8")
    (connected / "project").mkdir(exist_ok=True)
    for command in ("R=..; cat $R/clients/beta/secret.md",
                    "cat ${R}/clients/beta/secret.md",
                    "R=..; grep -r x $R/clients"):
        assert _decide(connected, command, cwd=connected / "project").action == "deny", command


def test_tracked_index_deny_holds_in_a_bound_session_without_initiatives(connected):
    (connected / "clients" / "beta" / "secret.md").write_text("x", encoding="utf-8")
    _git(connected, "init", "-q")
    _git(connected, "add", "-f", "clients/beta/secret.md")
    _git(connected, "commit", "-q", "-m", "x")
    assert gate.clients_index_count(connected) == 1
    assert _decide(connected, "git log -p").action == "deny"
