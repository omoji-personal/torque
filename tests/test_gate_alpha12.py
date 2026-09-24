"""Alpha 12: regression tests for the spot-check of the alpha 11 host-tool change
and the git working-tree routes found in the same review.

G1 covers EnterWorktree and the worktree copies it makes, G2 the LSP tool and N6
`git clean` / `git stash`. "acme" is a neutral placeholder client name.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import torque
from torque import gate

W = Path("/w")
REPO = Path(__file__).resolve().parents[1]


def _blocked(tool, inp, workspace=W, cwd=None):
    allowed, reason = gate.decide(tool, inp, workspace, "build-only", cwd)
    return (not allowed) and bool(reason)


def _allowed(tool, inp, workspace=W, cwd=None):
    return gate.decide(tool, inp, workspace, "build-only", cwd) == (True, "")


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


@pytest.fixture
def ws(tmp_path):
    """A build-only workspace on disk, under git, with clients/ ignored as
    `torque workspace init` leaves it."""
    root = tmp_path / "w"
    (root / "clients" / "acme").mkdir(parents=True)
    (root / "clients" / "acme" / "notes.md").write_text("client notes", encoding="utf-8")
    (root / "project" / "src").mkdir(parents=True)
    (root / "project" / "README.md").write_text("readme", encoding="utf-8")
    (root / "project" / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".torque").mkdir()
    (root / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    (root / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic",
         "ai_access": "build-only"}), encoding="utf-8")
    (root / ".gitignore").write_text("/clients/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return Path(os.path.realpath(str(root)))


@pytest.fixture
def ws_copy(ws):
    """The workspace with a worktree copy at .claude/worktrees/x that holds client files."""
    copy = ws / ".claude" / "worktrees" / "x"
    (copy / "clients" / "acme").mkdir(parents=True)
    (copy / "clients" / "acme" / "notes.md").write_text("client notes", encoding="utf-8")
    (copy / "project").mkdir()
    (copy / "project" / "README.md").write_text("readme", encoding="utf-8")
    return ws


# --- G1: EnterWorktree `path` ---

ENTER_PATH_BLOCK = [
    "clients/acme",
    "/w/clients/acme",
    "clients",
    ".",
    "/w",
    "project",
    "/tmp/elsewhere",
    "..",
    ".claude/worktrees",
    ".claude/worktrees/x/clients",
    ".claude/worktrees/x/clients/acme",
    "/w/.claude/worktrees/../../clients/acme",
]
ENTER_PATH_ALLOW = [
    ".claude/worktrees/x",
    "/w/.claude/worktrees/feature-1",
    ".claude/worktrees/x/project",
]


@pytest.mark.parametrize("path", ENTER_PATH_BLOCK)
def test_g1_enter_worktree_path_outside_worktrees_or_into_clients_blocked(path):
    assert _blocked("EnterWorktree", {"path": path}), path


@pytest.mark.parametrize("path", ENTER_PATH_ALLOW)
def test_g1_enter_worktree_path_to_a_worktree_allowed(path):
    assert _allowed("EnterWorktree", {"path": path}), path


# --- G1: EnterWorktree `name` makes a copy of the tree ---

def test_g1_enter_worktree_name_allowed_when_clients_stay_ignored(ws):
    assert _allowed("EnterWorktree", {"name": "feature"}, ws, ws)
    assert _allowed("EnterWorktree", {}, ws, ws)


def test_g1_enter_worktree_name_blocked_when_clients_are_tracked(ws):
    _git(ws, "add", "-f", "clients/acme/notes.md")
    _git(ws, "commit", "-q", "-m", "oops")
    assert _blocked("EnterWorktree", {"name": "feature"}, ws, ws)
    assert _blocked("EnterWorktree", {}, ws, ws)


@pytest.mark.parametrize("include", ["clients/\n", "/clients\n", "*.md\n", "clients/acme/notes.md\n",
                                     "# copies\nCLIENTS/\n"])
def test_g1_enter_worktree_name_blocked_when_worktreeinclude_names_clients(ws, include):
    (ws / ".worktreeinclude").write_text(include, encoding="utf-8")
    assert _blocked("EnterWorktree", {"name": "feature"}, ws, ws), include


def test_g1_enter_worktree_name_allowed_with_unrelated_worktreeinclude(ws):
    (ws / ".worktreeinclude").write_text(".env\nproject/.env.local\n", encoding="utf-8")
    assert _allowed("EnterWorktree", {"name": "feature"}, ws, ws)


def test_g1_enter_worktree_name_blocked_when_git_cannot_answer(tmp_path):
    root = tmp_path / "plain"
    (root / "clients").mkdir(parents=True)
    root = Path(os.path.realpath(str(root)))
    assert _blocked("EnterWorktree", {"name": "feature"}, root, root)


@pytest.mark.parametrize("tool,inp", [
    ("Write", {"file_path": "/w/.worktreeinclude", "content": "clients/\n"}),
    ("Edit", {"file_path": "/w/.worktreeinclude", "old_string": "a", "new_string": "clients/"}),
    ("MultiEdit", {"file_path": ".worktreeinclude", "edits": []}),
    ("Bash", {"command": "printf 'x\\n' >> .worktreeinclude"}),
    ("Bash", {"command": "cp /tmp/list .worktreeinclude"}),
    ("Bash", {"command": "tee .worktreeinclude </tmp/list"}),
    ("mcp__filesystem__write_file", {"path": "/w/.worktreeinclude", "content": "clients/"}),
])
def test_g1_worktreeinclude_is_a_guarded_file(tool, inp):
    assert _blocked(tool, inp), (tool, inp)


# --- G1: worktree copies of clients/ are guarded like clients/ ---

def test_g1_worktree_copy_of_clients_is_guarded(ws_copy):
    ws = ws_copy
    copy = ws / ".claude" / "worktrees" / "x"
    note = copy / "clients" / "acme" / "notes.md"
    for tool, inp, cwd in [
        ("Read", {"file_path": str(note)}, ws),
        ("Read", {"file_path": "clients/acme/notes.md"}, copy),
        ("Grep", {"pattern": "notes"}, copy),
        ("Glob", {"pattern": "**/*.md"}, copy),
        ("Grep", {"pattern": "x", "path": str(copy)}, ws),
        ("LS", {"path": str(copy / "clients")}, ws),
        ("Edit", {"file_path": str(note), "old_string": "a", "new_string": "b"}, ws),
        ("mcp__filesystem__read_file", {"path": str(note)}, ws),
    ]:
        assert _blocked(tool, inp, ws, cwd), (tool, inp, cwd)


def test_g1_worktree_copy_outside_clients_stays_usable(ws_copy):
    ws = ws_copy
    copy = ws / ".claude" / "worktrees" / "x"
    assert _allowed("Read", {"file_path": str(copy / "project" / "README.md")}, ws, ws)
    assert _allowed("Grep", {"pattern": "x", "path": "project"}, ws, copy)
    assert _allowed("EnterWorktree", {"path": str(copy)}, ws, ws)


def _run_gate(payload, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "torque.gate"], input=payload,
                          capture_output=True, text=True, env=env)


def test_g1_hook_blocks_reads_from_inside_a_worktree_copy(ws_copy):
    ws = ws_copy
    copy = ws / ".claude" / "worktrees" / "x"
    for project_dir in (str(ws), ""):
        event = {"tool_name": "Read", "tool_input": {"file_path": "clients/acme/notes.md"}, "cwd": str(copy)}
        result = _run_gate(json.dumps(event), {"CLAUDE_PROJECT_DIR": project_dir})
        assert result.returncode == 2 and result.stderr.strip(), project_dir


# --- G2: LSP ---

LSP_BLOCK = [
    {"operation": "workspaceSymbol", "filePath": "/w/project/src/a.py", "query": "Acme"},
    {"operation": "workspaceSymbol", "query": "Acme"},
    {"operation": "findReferences", "filePath": "/w/project/src/a.py", "line": 1, "character": 1},
    {"operation": "goToImplementation", "filePath": "/w/project/src/a.py", "line": 1, "character": 1},
    {"operation": "prepareCallHierarchy", "filePath": "/w/project/src/a.py", "line": 1, "character": 1},
    {"operation": "incomingCalls", "filePath": "/w/project/src/a.py", "line": 1, "character": 1},
    {"operation": "outgoingCalls", "filePath": "/w/project/src/a.py", "line": 1, "character": 1},
    {"operation": "someFutureOperation", "filePath": "/w/project/src/a.py"},
    {"filePath": "/w/project/src/a.py", "line": 1, "character": 1},
    {"operation": "hover"},
    {"operation": "hover", "filePath": ""},
    {"operation": "hover", "filePath": None},
    {"operation": "hover", "filePath": ["/w/project/src/a.py"]},
    {"operation": "hover", "filePath": "/w/clients/acme/notes.md", "line": 1, "character": 1},
    {"operation": "documentSymbol", "filePath": "clients/acme/x.cls"},
    {"operation": "hover", "filePath": "file:///w/clients/acme/notes.md", "line": 1, "character": 1},
    {"operation": "hover", "filePath": "FILE:///w/clients/acme/notes.md"},
    {"operation": "hover", "filePath": "file://localhost/w/clients/acme/notes.md"},
    {"operation": "hover", "filePath": "file:///w/cl%69ents/acme/notes.md"},
    {"operation": "goToDefinition", "filePath": "/w/project/src/a.py", "uri": "file:///w/clients/acme/n.md"},
    {"operation": "hover", "filePath": "/w/project/src/a.py", "extra": {"path": "/w/clients/acme"}},
    {"operation": "documentSymbol", "filePath": "/w/.claude/settings.json"},
]
LSP_ALLOW = [
    {"operation": "documentSymbol", "filePath": "/w/project/src/a.py"},
    {"operation": "hover", "filePath": "/w/project/src/a.py", "line": 3, "character": 7},
    {"operation": "goToDefinition", "filePath": "project/src/a.py", "line": 3, "character": 7},
    {"operation": "goToDefinition", "filePath": "file:///w/project/src/a.py", "line": 3, "character": 7},
]


@pytest.mark.parametrize("inp", LSP_BLOCK)
def test_g2_lsp_limited_to_single_file_operations_outside_clients(inp):
    assert _blocked("LSP", inp), inp
    assert gate.decide("LSP", inp, W, "full") == (True, "")


@pytest.mark.parametrize("inp", LSP_ALLOW)
def test_g2_lsp_single_file_operations_allowed(inp):
    assert _allowed("LSP", inp), inp


def test_g2_lsp_block_names_the_allowed_operations():
    allowed, reason = gate.decide("LSP", {"operation": "workspaceSymbol", "query": "x"}, W, "build-only")
    assert not allowed
    for op in ("documentSymbol", "hover", "goToDefinition"):
        assert op in reason, reason


# --- G3 and the docs: what is enforced, and what is a limit ---

def _doc():
    return (REPO / "docs" / "ai-access.md").read_text(encoding="utf-8")


def _cannot_stop_section():
    text = _doc()
    return text.split("## What it cannot stop", 1)[1]


def test_g3_send_message_is_documented_as_a_limit():
    section = _cannot_stop_section()
    assert "SendMessage" in section
    assert "EnterWorktree" not in _doc().split("only these pass unchecked", 1)[1].split(".", 1)[0]


def test_g2_doc_states_lsp_index_covers_clients():
    section = _cannot_stop_section()
    assert "LSP" in section and "clients/" in section
    for op in ("documentSymbol", "hover", "goToDefinition"):
        assert op in _doc(), op


def test_g1_doc_names_worktree_checks():
    text = _doc()
    for phrase in (".worktreeinclude", ".claude/worktrees/", "EnterWorktree"):
        assert phrase in text, phrase


def test_n6_doc_names_git_clean_and_stash():
    text = _doc()
    assert "git clean" in text and "git stash" in text


# --- N6: git clean and git stash of untracked files ---

GIT_BLOCK = [
    "git clean -fdx",
    "git clean -fdX",
    "git clean -fd",
    "git clean -f",
    "git clean -ffdx",
    "git clean --force -d -x",
    "git clean -fdx .",
    "git clean -fdx -- .",
    "git clean -fdx -e keep",
    "git clean -fdx '*.md'",
    "git clean -i",
    "git -C . clean -fdx",
    "git -c color.ui=never clean -fdx",
    "cd project && git clean -fdx :/",
    "cd project && git clean -fdx ..",
    "cd project && GIT_WORK_TREE=.. git clean -fdx",
    "cd project && git --work-tree=.. clean -fdx",
    "git stash -u",
    "git stash --include-untracked",
    "git stash --all",
    "git stash -a",
    "git stash --inc",
    "git stash -ku",
    "git stash push -u",
    "git stash push --all -m wip",
    "git stash push -u -- clients",
    "git stash push -u -- .",
    "git stash save -u wip",
    "cd project && git stash -u",
    "git stash --all && git stash show -p --include-untracked",
    "git stash show -u",
    "git stash show -p -u stash@{0}",
    "git stash show --only-untracked",
    "git show stash^3",
    "git show 'stash@{0}^3:clients/acme/notes.md'",
    "git log -p refs/stash^3",
]
GIT_ALLOW = [
    "git clean -n",
    "git clean -nfdx",
    "git clean -fdxn",
    "git clean --dry-run -fdx",
    "git clean -fdx project",
    "cd project && git clean -fdx",
    "git stash",
    "git stash push -m wip",
    "git stash list",
    "git stash pop",
    "git stash show -p",
    "git stash push -u -- project",
    "git status",
    "git log --oneline -5",
]


@pytest.mark.parametrize("command", GIT_BLOCK)
def test_n6_git_clean_and_untracked_stash_blocked(ws, command):
    assert _blocked("Bash", {"command": command}, ws, ws), command


@pytest.mark.parametrize("command", GIT_ALLOW)
def test_n6_git_ordinary_work_allowed(ws, command):
    assert _allowed("Bash", {"command": command}, ws, ws), command


def test_n6_untracked_stash_in_a_nested_project_repo_allowed(ws):
    _git(ws / "project", "init", "-q")
    project = ws / "project"
    assert _allowed("Bash", {"command": "git stash -u"}, ws, project)
    assert _allowed("Bash", {"command": "git stash show -u"}, ws, project)
    assert _blocked("Bash", {"command": "git clean -fdx .."}, ws, project)


def test_n6_git_clean_or_stash_outside_a_repository_blocked(tmp_path):
    root = tmp_path / "plain"
    (root / "clients").mkdir(parents=True)
    root = Path(os.path.realpath(str(root)))
    for command in ("git clean -fdx", "git stash -u", "git stash show -u"):
        assert _blocked("Bash", {"command": command}, root, root), command


def test_n6_git_clean_over_the_hook_environment_blocked():
    holder = Path(torque.__file__).resolve().parent.parent
    for command in (f"git -C '{holder.as_posix()}' clean -fdx", f"git clean -fdx '{holder.as_posix()}'"):
        assert _blocked("Bash", {"command": command}), command
