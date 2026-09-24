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


# --- Round 3, R3-01: MCP tools that run a command get the Bash scan ---

EXEC_BLOCK = [
    "cat clients/acme/notes.md",
    "sf data query --target-org sample --query 'SELECT Id FROM Account'",
    "cd project && cat ../clients/acme/notes.md",
    "rg secret",
    "git clean -fdx",
]
EXEC_ALLOW = [
    "ls project",
    "git status",
    "sf project generate --name demo",
]


@pytest.mark.parametrize("command", EXEC_BLOCK)
@pytest.mark.parametrize("key", ["command", "cmd", "script"])
def test_r3_01_mcp_command_is_scanned_like_bash(ws, command, key):
    assert _blocked("Bash", {"command": command}, ws, ws), command
    assert _blocked("mcp__desktop_commander__start_process", {key: command}, ws, ws), (key, command)
    assert _blocked("mcp__shell__run", {"input": {key: command}}, ws, ws), (key, command)


@pytest.mark.parametrize("command", EXEC_ALLOW)
def test_r3_01_mcp_command_ordinary_work_allowed(ws, command):
    assert _allowed("Bash", {"command": command}, ws, ws), command
    assert _allowed("mcp__desktop_commander__start_process", {"command": command}, ws, ws), command


def test_r3_01_other_tools_with_cmd_or_script_are_scanned(ws):
    assert _blocked("SomeShell", {"cmd": "cat clients/acme/notes.md"}, ws, ws)
    assert _blocked("SomeShell", {"script": "cat clients/acme/notes.md"}, ws, ws)


# --- Round 3, R3-02: search option forms, from a project subdirectory ---

SEARCH_BLOCK = [
    "rg -uuu -eERROR ..",
    "grep -R -eERROR ..",
    "grep -rneERROR ..",
    "grep -R -fpatterns.txt ..",
    "grep -R --regexp=ERROR ..",
    "grep -R --reg=ERROR ..",
    "grep -R --reg ERROR ..",
    "rg --files --hidden --no-ignore ..",
    "rg --files ..",
    "ack -f ..",
    "ag -g notes ..",
    "ack -g notes ..",
    "rg -A2 ERROR ..",
    "rg -gnotes.md ERROR ..",
]
SEARCH_ALLOW = [
    "rg -eERROR .",
    "grep -R -eERROR .",
    "rg --files",
    "rg --files --hidden src",
    "grep -rneERROR src",
    "ack -f .",
    "rg -A2 ERROR .",
]


@pytest.mark.parametrize("command", SEARCH_BLOCK)
def test_r3_02_search_forms_from_project_blocked(ws, command):
    assert _blocked("Bash", {"command": command}, ws, ws / "project"), command


@pytest.mark.parametrize("command", SEARCH_ALLOW)
def test_r3_02_search_confined_to_project_allowed(ws, command):
    if command == "rg --files":
        # From the workspace root, rg --files lists clients/.
        assert _blocked("Bash", {"command": command}, ws, ws)
    assert _allowed("Bash", {"command": command}, ws, ws / "project"), command


# --- Round 3, R3-03: tar's directory changes ---

TAR_BLOCK = [
    "tar -C.. -cf - .",
    "tar -C .. -cf - .",
    "tar --directory=.. -cf - .",
    "tar --directory .. -cf - .",
    "tar -cf - -C .. .",
    "tar -cf /tmp/x.tar -C /tmp -C .. .",
    "tar -cf - -C .. clients",
    "tar -cC .. -f - .",
    "tar -C src -C ../.. -cf - .",
    "tar -C \"$DIR\" -cf - .",
]
TAR_ALLOW = [
    "tar -cf - .",
    "tar -C .. -cf - project",
    "tar -C src -cf - .",
    "tar --directory=src -czf /tmp/src.tgz .",
]


@pytest.mark.parametrize("command", TAR_BLOCK)
def test_r3_03_tar_directory_changes_tracked(ws, command):
    assert _blocked("Bash", {"command": command}, ws, ws / "project"), command


@pytest.mark.parametrize("command", TAR_ALLOW)
def test_r3_03_tar_in_project_allowed(ws, command):
    assert _allowed("Bash", {"command": command}, ws, ws / "project"), command


# --- Round 3, R3-04: git clean over an in-workspace hook environment ---

@pytest.fixture
def ws_venv(ws, monkeypatch):
    """The workspace with the hook's interpreter modelled at .venv/."""
    venv = ws / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "lib" / "site-packages").mkdir(parents=True)
    (ws / ".gitignore").write_text("/clients/\n/.venv/\n", encoding="utf-8")
    monkeypatch.setattr(gate, "_interpreter_paths",
                        lambda: ((venv / "lib" / "site-packages", venv / "pyvenv.cfg"), (venv / "bin" / "python", venv / "bin")))
    return ws


@pytest.mark.parametrize("command", ["git clean -fdx .venv", "git clean -fdx", "git clean -fdX", "git clean -fx .venv/bin",
                                     "git clean -fdx -- .venv", "git stash -a -- .venv", "rm -rf .venv"])
def test_r3_04_git_clean_of_the_hook_environment_blocked(ws_venv, command):
    assert _blocked("Bash", {"command": command}, ws_venv, ws_venv), command


@pytest.mark.parametrize("command", ["git clean -n .venv", "git clean -ndx", "git clean -fdx project/build",
                                     "git clean -fdX project"])
def test_r3_04_dry_runs_and_build_output_cleans_allowed(ws_venv, command):
    assert _allowed("Bash", {"command": command}, ws_venv, ws_venv), command


# --- Round 3, R3-05: abbreviated options ---

@pytest.mark.parametrize("command", [
    "torque doctor --workspace . --clie example",
    "torque doctor --cl example",
    "torque doctor --clien=example",
    "python -m torque doctor --clie example",
])
def test_r3_05_abbreviated_client_flag_blocked(command):
    assert _blocked("Bash", {"command": command}), command


def test_r3_05_doctor_without_client_allowed():
    assert _allowed("Bash", {"command": "torque doctor --workspace . --json"})


@pytest.mark.parametrize("argv", [
    ["doctor", "--clie", "example"],
    ["doctor", "--work", "."],
    ["context", "--workspace", ".", "--client", "example", "--js"],
    ["workflows", "list", "--js"],
])
def test_r3_05_cli_rejects_abbreviated_options(argv, capsys):
    from torque import cli
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_r3_05_every_torque_parser_disables_abbreviation():
    import argparse
    from torque import cli
    seen = []

    def walk(parser):
        seen.append(parser)
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    walk(child)
    walk(cli.build_parser())
    assert len(seen) > 10
    assert all(p.allow_abbrev is False for p in seen), [p.prog for p in seen if p.allow_abbrev]


# --- Round 3, NEW-1: file:// URIs with a host ---

@pytest.mark.parametrize("uri", [
    "file://localhost/w/clients/acme/notes.md",
    "FILE://LOCALHOST/w/clients/acme/notes.md",
    "file:/w/clients/acme/notes.md",
    "file:///w/cl%69ents/acme/notes.md",
    "file://localhost/w/workspace.json",
])
def test_new1_file_uri_with_host_is_parsed(uri):
    assert _blocked("mcp__filesystem__read_file", {"path": uri}), uri
    assert _blocked("ReadMcpResourceTool", {"server": "files", "uri": uri}), uri
    assert _blocked("LSP", {"operation": "hover", "filePath": uri}), uri


def test_new1_uri_path_helper():
    assert gate._uri_path("file://localhost/w/a.py") == "/w/a.py"
    assert gate._uri_path("file:///C:/w/a.py") == "C:/w/a.py"
    assert gate._uri_path("file:///w/a%20b.py") == "/w/a b.py"
    assert gate._uri_path("/w/a.py") == "/w/a.py"


# --- The mode's name, and playbooks in build-only mode ---

def test_block_messages_use_the_build_only_name():
    allowed, reason = gate.decide("Read", {"file_path": "/w/clients/acme/notes.md"}, W, "build-only")
    assert not allowed and reason.startswith("Build-only mode:"), reason
    assert "Build-only mode" in gate.HOOK_SHIM_CODE


@pytest.mark.parametrize("rel", ["README.md", "docs/ai-access.md", "docs/installation.md", "src/torque/gate.py",
                                 "src/torque/cli.py", "docs/validation-alpha12.md"])
def test_user_facing_text_does_not_call_the_mode_de_identified(rel):
    text = (REPO / rel).read_text(encoding="utf-8").casefold()
    assert "de-identified mode" not in text and "de-identified-mode" not in text, rel


def test_ai_access_doc_says_the_mode_redacts_nothing():
    text = " ".join(_doc().split())
    assert "Build-only mode redacts nothing" in text
    assert "removing client details" in text


DEMO_PLAYBOOKS = ["triage-alert", "gift-payments", "grants-outbound-funds", "requirements-to-build"]


@pytest.mark.parametrize("name", DEMO_PLAYBOOKS)
def test_demo_playbooks_have_a_build_only_section(name):
    for path in (REPO / "workflows" / f"{name}.md", REPO / "src" / "torque" / "data" / "commands" / f"{name}.md"):
        text = path.read_text(encoding="utf-8")
        assert "## In build-only mode" in text, path
        section = " ".join(text.split("## In build-only mode", 1)[1].split())
        for phrase in ("names, IDs and values removed", "hand-off", "consultant"):
            assert phrase in section, (path, phrase)


def test_round3_docs_name_the_new_checks():
    text = _doc()
    for phrase in ("`cmd`", "`script`", "`--files`", "`-C`", "--directory", "abbreviat", "file://localhost"):
        assert phrase in text, phrase


def test_readme_explains_the_legacy_qa_token_entries():
    text = " ".join((REPO / "README.md").read_text(encoding="utf-8").split())
    assert "qa-token-" in text and "legacy" in text


# --- CI finding: a recursive root at the filesystem root ---

@pytest.mark.parametrize("command", ["grep -r secret /", "rg secret /", "tar -cf - -C / .", "find / -name notes.md",
                                     "cd / && rg secret"])
def test_filesystem_root_reaches_clients(ws, command):
    assert _blocked("Bash", {"command": command}, ws, ws / "project"), command


def test_reaches_and_within_handle_a_root_path():
    root = Path(os.path.abspath(os.sep))
    assert gate._reaches(root, root / "w" / "clients")
    assert gate._is_within(root / "w", root)


# --- Scoped re-review: tar key bundles and tar variants (NB-2) ---

TAR2_BLOCK = [
    "tar cCf .. - .",
    "tar cCf .. /dev/stdout .",
    "tar cfC - .. .",
    "tar czCf .. /tmp/x.tgz .",
    "bsdtar -C.. -cf - .",
    "bsdtar -cf - ..",
    "/usr/bin/bsdtar -C .. -cf - .",
    "gtar -C.. -cf - .",
    "gnutar -cf - ..",
]
TAR2_ALLOW = [
    "tar cCf . - src",
    "tar cf - src",
    "bsdtar -cf - src",
    "gtar -C src -cf - .",
]


@pytest.mark.parametrize("command", TAR2_BLOCK)
def test_rr_tar_key_bundles_and_variants_blocked(ws, command):
    assert _blocked("Bash", {"command": command}, ws, ws / "project"), command


@pytest.mark.parametrize("command", TAR2_ALLOW)
def test_rr_tar_key_bundles_confined_allowed(ws, command):
    assert _allowed("Bash", {"command": command}, ws, ws / "project"), command


# --- Scoped re-review: git add -f and reading staged or stashed client files (NB-1, NB-3) ---

ADD_BLOCK_ROOT = [
    "git add -f .",
    "git add -A -f",
    "git add --force --all",
    "git add --forc -A",
    "git add -fA",
    "git add -f -- .",
    "git add -f .claude",
    "git add -f '*'",
    "git add -f :/",
    "git add -f --pathspec-from-file=list.txt",
    "git add .",
    "git add -A",
    "git add --all",
    "git add -u .",
    "git add -u",
    "git stage .",
    "git stage -f -A",
    "git update-index --add clients/acme/notes.md",
    "git update-index --add --cacheinfo 100644,e69de29bb2d1d6434b8b29ae775ad8c2e48c5391,clients/acme/n.md",
    "git update-index --add --cacheinfo 100644 e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 clients/acme/n.md",
    "git update-index --force-remove .claude/settings.json",
    "git update-index --add --stdin",
    "git update-index --index-info",
    "git hash-object -w clients/acme/notes.md",
    "git hash-object -w --stdin-paths",
]
ADD_ALLOW_ROOT = [
    "git add project/README.md",
    "git add -f project/README.md",
    "git stage project",
    "git commit -am wip",
    "git diff --cached",
    "git show :project/README.md",
    "git log -p -3",
    "git stash show -p",
    "git update-index --refresh",
    "git update-index --add project/README.md",
    "git hash-object project/README.md",
    "git hash-object -w project/README.md",
]


@pytest.mark.parametrize("command", ADD_BLOCK_ROOT)
def test_rr_add_or_index_write_reaching_protected_blocked(ws, command):
    assert _blocked("Bash", {"command": command}, ws, ws), command


def test_rr_add_from_project(ws):
    assert _blocked("Bash", {"command": "git add -f .."}, ws, ws / "project")
    assert _blocked("Bash", {"command": "git add -A"}, ws, ws / "project")
    assert _allowed("Bash", {"command": "git add -f ."}, ws, ws / "project")
    assert _allowed("Bash", {"command": "git add ."}, ws, ws / "project")


@pytest.mark.parametrize("command", ADD_ALLOW_ROOT)
def test_rr_ordinary_add_and_reads_allowed(ws, command):
    assert _allowed("Bash", {"command": command}, ws, ws), command


TRACKED_BLOCK = [
    "git diff --cached",
    "git diff --staged",
    "git diff",
    "git show :clients/acme/notes.md",
    "git show :0:clients/acme/notes.md",
    "git show HEAD",
    "git log -p",
    "git log --all -p",
    "git log --patch -1",
    "git cat-file -p :clients/acme/notes.md",
    "git grep --cached SECRET",
    "git grep x",
    "git ls-files",
    "git archive HEAD",
    "git -c stash.showIncludeUntracked=true stash show -p",
    "cd project && git diff --cached",
]
TRACKED_ALLOW = [
    "git status",
    "git status --short",
    "git log --oneline -5",
    "git log",
]


@pytest.mark.parametrize("command", TRACKED_BLOCK)
def test_rr_git_blocked_while_clients_are_in_the_index(ws, command):
    _git(ws, "add", "-f", "clients/acme/notes.md")
    assert _blocked("Bash", {"command": command}, ws, ws), command


@pytest.mark.parametrize("command", TRACKED_ALLOW)
def test_rr_status_and_log_allowed_while_clients_are_in_the_index(ws, command):
    _git(ws, "add", "-f", "clients/acme/notes.md")
    assert _allowed("Bash", {"command": command}, ws, ws), command


def test_rr_committed_clients_also_block(ws):
    _git(ws, "add", "-f", "clients/acme/notes.md")
    _git(ws, "commit", "-q", "-m", "oops")
    assert _blocked("Bash", {"command": "git show HEAD"}, ws, ws)
    assert _allowed("Bash", {"command": "git status"}, ws, ws)


def test_rr_doctor_reports_tracked_clients(tmp_path):
    from torque import cli, workspace as wsmod
    import contextlib
    import io
    root = tmp_path / "w"
    wsmod.init_workspace(root, "Example firm", "generic")
    wsmod.set_ai_access(root, "build-only")
    (root / "clients" / "acme").mkdir(parents=True, exist_ok=True)
    (root / "clients" / "acme" / "notes.md").write_text("x", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-f", "clients/acme/notes.md")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["doctor", "--workspace", str(root), "--json"])
    report = json.loads(out.getvalue())
    assert code == 3 and report["ready"] is False
    assert report["ai_access"]["clients_in_git"] == 1
    assert any("git rm -r --cached clients" in action for action in report["next_actions"])


# --- Scoped re-review minors ---

def test_rr_enter_existing_worktree_by_path_in_a_tracked_workspace(ws):
    _git(ws, "worktree", "add", "-q", str(ws / ".claude" / "worktrees" / "feat"))
    feat = ws / ".claude" / "worktrees" / "feat"
    assert (feat / "workspace.json").is_file()
    event = {"tool_name": "EnterWorktree", "tool_input": {"path": ".claude/worktrees/feat"}, "cwd": str(ws)}
    result = subprocess.run([sys.executable, "-m", "torque.gate"], input=json.dumps(event), capture_output=True,
                            text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": str(ws),
                                            "PYTHONPATH": str(REPO / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")})
    assert result.returncode == 0, result.stderr
    assert _blocked("EnterWorktree", {"path": str(feat / "clients")}, feat, ws)
    assert _blocked("EnterWorktree", {"path": str(ws / "project")}, feat, ws)


def test_rr_interpreter_message_wording():
    import inspect
    assert "runs the build-only mode hook" in inspect.getsource(gate)
