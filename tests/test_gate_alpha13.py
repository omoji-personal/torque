"""Alpha 13: regression tests for the routes a fourth review round found.

N3 covers symlinks: `ln` pointing at or above clients/, and recursive tools in a
mode that follows links (rg -L, find -L, grep -R, tar -h, cp -rL, rsync -L, zip,
fd -L, ls -RL, tree -l) walking into a link that leaves the search root. N4 covers
paths holding an unresolved `$` expansion, a `cd` inside if/then/while/for, and
grep's `-d recurse`. N5 covers git's ignored-file listings and a Glob pattern
that climbs with `..`. Each block has an ordinary build-session counterpart that
must still pass. "acme" is a neutral placeholder client name.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from torque import gate

REPO = Path(__file__).resolve().parents[1]


def _blocked(tool, inp, workspace, cwd):
    allowed, reason = gate.decide(tool, inp, workspace, "build-only", cwd)
    return (not allowed) and bool(reason)


def _allowed(tool, inp, workspace, cwd):
    return gate.decide(tool, inp, workspace, "build-only", cwd) == (True, "")


def _bash_blocked(command, ws, cwd):
    return _blocked("Bash", {"command": command}, ws, cwd)


def _bash_allowed(command, ws, cwd):
    return _allowed("Bash", {"command": command}, ws, cwd)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


def _symlink(target, link, is_dir=True):
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlinks here: {exc}")


def _make_ws(root):
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
    return Path(os.path.realpath(str(root)))


@pytest.fixture
def ws(tmp_path):
    """A build-only workspace on disk, not under git."""
    return _make_ws(tmp_path / "w")


@pytest.fixture
def wsg(tmp_path):
    """A build-only workspace under git, with clients/ ignored as `torque workspace init` leaves it."""
    root = _make_ws(tmp_path / "w")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def ws_up(ws):
    """The workspace with a link in project/ that points back at the workspace root,
    as a project the consultant supplies might already hold."""
    _symlink("..", ws / "project" / "up")
    return ws


@pytest.fixture
def ws_inner(ws):
    """The workspace with links in project/ that stay inside project/."""
    _symlink("src", ws / "project" / "lib")
    _symlink("a.py", ws / "project" / "src" / "b.py", is_dir=False)
    return ws


# --- N3: ln pointing at or above clients/ ---

LN_BLOCK = [
    "ln -s .. up",
    "ln -s ../ up",
    "ln -sf .. up",
    "ln -sfn .. up",
    "ln -s -- .. up",
    "ln -s ../.. up",
    "ln -s / root",
    "ln -s ../clients cl",
    "ln -s ../clients/acme a",
    "ln -s ../../.. src/up",
    "ln .. up",
    "ln -s -t . ..",
    "ln -st src ../..",
    "ln --symbolic --target-directory=. ..",
    "ln -s .. up && rg -L SECRET",
    "/bin/ln -s .. up",
    "command ln -s .. up",
]
LN_ALLOW = [
    "ln -s src/a.py a_link.py",
    "ln -s ../README.md readme",
    "ln -s src lib",
    "ln -sf a.py src/b.py",
    "ln -s .. src/up",
    "ln src/a.py hard.py",
]


@pytest.mark.parametrize("command", LN_BLOCK)
def test_n3_ln_at_or_above_clients_blocked(ws, command):
    assert _bash_blocked(command, ws, ws / "project"), command


def test_n3_ln_to_an_ancestor_of_the_workspace_blocked(ws):
    assert _bash_blocked(f"ln -s {ws.parent.as_posix()} data", ws, ws / "project")
    assert _bash_blocked(f"ln -s {ws.as_posix()} data", ws, ws / "project")


@pytest.mark.parametrize("command", LN_ALLOW)
def test_n3_ln_inside_project_allowed(ws, command):
    assert _bash_allowed(command, ws, ws / "project"), command


def test_n3_ln_at_the_root_into_project_allowed(ws):
    assert _bash_allowed("ln -s project/src s", ws, ws)
    assert _bash_allowed("ln -s project/README.md R.md", ws, ws)


# --- N3: recursive tools that follow a link out of the search root ---

FOLLOW_BLOCK = [
    "rg -L SECRET",
    "rg --follow SECRET .",
    "rg -uuL SECRET",
    "rg -L --files",
    "find -L . -name notes.md",
    "find . -follow -name notes.md",
    "find . -follow -name '*.md' -exec cat {} +",
    "find -H . -name notes.md",
    "grep -R SECRET .",
    "grep -Rn SECRET",
    "grep --dereference-recursive SECRET .",
    "tar -chf - .",
    "tar --dereference -cf - .",
    "tar chf - .",
    "bsdtar -c -L -f - .",
    "cp -rL . /tmp/x",
    "cp -RL . /tmp/x",
    "cp -r -L . /tmp/x",
    "cp -r --dereference . /tmp/x",
    "rsync -aL . /tmp/x",
    "rsync -a --copy-links . /tmp/x",
    "rsync -ak . /tmp/x",
    "rsync -a --copy-unsafe-links . /tmp/x",
    "zip -r o.zip .",
    "fd -L notes",
    "fd --follow notes",
    "ls -RL",
    "ls -R -L .",
    "tree -l",
    "tree -l .",
    "ag -f SECRET",
]
# The same tools without following links: they skip, or store, the link.
NO_FOLLOW_ALLOW = [
    "rg SECRET",
    "rg SECRET .",
    "grep -r SECRET .",
    "find . -name notes.md",
    "tar -cf - .",
    "cp -r . /tmp/x",
    "rsync -a . /tmp/x",
    "zip -ry o.zip .",
    "zip -r --symlinks o.zip .",
    "fd notes",
    "ls -R",
    "tree",
    "rg -L SECRET src",
    "tar -chf - src",
    "zip -r o.zip src",
]


@pytest.mark.parametrize("command", FOLLOW_BLOCK)
def test_n3_follow_mode_through_a_link_to_the_root_blocked(ws_up, command):
    assert _bash_blocked(command, ws_up, ws_up / "project"), command


@pytest.mark.parametrize("command", NO_FOLLOW_ALLOW)
def test_n3_no_follow_or_narrow_root_allowed_with_the_link_present(ws_up, command):
    assert _bash_allowed(command, ws_up, ws_up / "project"), command


@pytest.mark.parametrize("command", FOLLOW_BLOCK)
def test_n3_follow_mode_with_links_inside_project_allowed(ws_inner, command):
    assert _bash_allowed(command, ws_inner, ws_inner / "project"), command


def test_n3_follow_mode_through_a_link_into_clients_blocked(ws):
    _symlink(str(ws / "clients" / "acme"), ws / "project" / "cl")
    assert _bash_blocked("rg -L SECRET", ws, ws / "project")
    assert _bash_blocked("grep -R SECRET .", ws, ws / "project")


def test_n3_follow_mode_through_a_file_link_into_clients_blocked(ws):
    _symlink(str(ws / "clients" / "acme" / "notes.md"), ws / "project" / "n.md", is_dir=False)
    assert _bash_blocked("rg -L SECRET", ws, ws / "project")
    assert _bash_blocked("zip -r o.zip .", ws, ws / "project")


def test_n3_follow_mode_through_an_outside_folder_that_links_back_blocked(ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _symlink(str(other), ws / "project" / "ext")
    assert _bash_allowed("rg -L SECRET", ws, ws / "project")
    _symlink(str(ws), other / "back")
    assert _bash_blocked("rg -L SECRET", ws, ws / "project")


def test_n3_follow_mode_with_a_link_loop_terminates_and_allows(ws):
    _symlink(".", ws / "project" / "src" / "self")
    _symlink("..", ws / "project" / "src" / "parent")
    assert _bash_allowed("rg -L x", ws, ws / "project")
    assert _bash_allowed("find -L . -name a.py", ws, ws / "project")


def test_n3_follow_walk_fails_closed_at_the_entry_cap(ws, monkeypatch):
    for n in range(12):
        (ws / "project" / "src" / f"m{n}.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(gate, "_FOLLOW_WALK_LIMIT", 5)
    assert _bash_blocked("rg -L x", ws, ws / "project")
    assert _bash_allowed("rg x", ws, ws / "project")


def test_n3_follow_walk_fails_closed_at_the_depth_cap(ws, monkeypatch):
    deep = ws / "project" / "d1" / "d2" / "d3" / "d4"
    deep.mkdir(parents=True)
    monkeypatch.setattr(gate, "_FOLLOW_WALK_DEPTH", 2)
    assert _bash_blocked("find -L . -name x", ws, ws / "project")
    assert _bash_allowed("find . -name x", ws, ws / "project")


def test_n3_follow_block_message_names_the_link(ws_up):
    allowed, reason = gate.decide("Bash", {"command": "rg -L SECRET"}, ws_up, "build-only", ws_up / "project")
    assert not allowed and "link" in reason, reason


# --- N4: unresolved $ expansions, cd inside if/while/for, grep -d recurse ---

N4_BLOCK = [
    "R=..; rg SECRET $R",
    'R=..; grep -r SECRET "$R"',
    "R=..; tar -cf - $R",
    'D=..; cat "$D/clients/acme/notes.md"',
    'W="$(dirname "$PWD")"; cat "$W/clients/acme/notes.md"',
    'cat "${PWD%/project}/clients/acme/notes.md"',
    'rg SECRET "${R}"',
    "find $1 -name notes.md",
    'cat "$D"/clients/acme/notes.md',
    "if cd ..; then rg SECRET; fi",
    "if true; then cd ..; fi; cat clients/acme/notes.md",
    "while cd ..; do rg SECRET; break; done",
    "until cd ..; do :; done; rg SECRET",
    "for d in x; do cd ..; done; cat clients/acme/notes.md",
    "if ! cd ..; then :; else rg SECRET; fi",
    "if false; then :; elif cd ..; then rg SECRET; fi",
    "grep -d recurse SECRET ..",
    "grep --directories=recurse SECRET ..",
    "grep --directories recurse SECRET ..",
    "grep -drecurse SECRET ..",
    "grep -nd recurse SECRET ..",
    "grep --dir=recurse SECRET ..",
    "grep --recur SECRET ..",
    "cd .. && grep -d recurse SECRET",
]
N4_ALLOW = [
    "grep -d recurse TODO src",
    "grep --directories=recurse TODO src",
    "grep -d skip TODO README.md",
    'echo "$HOME"',
    "echo $PATH",
    'for f in src/*.py; do python -m py_compile "$f"; done',
    'export PATH="$PWD/.venv/bin:$PATH"',
    'cat "$PWD/src/a.py"',
    "if true; then cd src; fi; ls",
    "if cd src; then rg x; fi",
    "while false; do cd src; done; pytest -q",
    'python -m pytest -k "$K"',
    "awk '{print $1}' README.md",
    "grep -n 'x$' src/a.py",
]


@pytest.mark.parametrize("command", N4_BLOCK)
def test_n4_runtime_paths_and_nested_cd_blocked(ws, command):
    assert _bash_blocked(command, ws, ws / "project"), command


@pytest.mark.parametrize("command", N4_ALLOW)
def test_n4_ordinary_variables_and_nested_cd_allowed(ws, command):
    assert _bash_allowed(command, ws, ws / "project"), command


def test_n4_variable_path_at_the_root_reaches_clients(ws):
    assert _bash_blocked('cat "$D/clients/acme/notes.md"', ws, ws)
    assert _bash_blocked("rg SECRET $R", ws, ws)
    assert _bash_allowed("echo $PATH", ws, ws)


# --- N5: git's ignored-file listings and Glob patterns that climb ---

GIT_LIST_BLOCK = [
    ("git status --ignored", ""),
    ("git status --ignored", "project"),
    ("git status --ignored=matching", "project"),
    ("git status -s --ignored", "project"),
    ("git status --ignored ..", "project"),
    ("git ls-files -o", ""),
    ("git ls-files -o -i --exclude-standard ..", "project"),
    ("git ls-files --others --ignored --exclude-standard ..", "project"),
    ("git ls-files -oi --exclude-standard", ""),
    ("git ls-files --ignored --exclude-standard -o clients", ""),
    ("git ls-files -i -o --exclude-standard", ""),
    ("git ls-files --other", ""),
]
GIT_LIST_ALLOW = [
    ("git status", ""),
    ("git status", "project"),
    ("git status --ignored=no", "project"),
    ("git status --ignored .", "project"),
    ("git status --ignored project", ""),
    ("git ls-files", ""),
    ("git ls-files ..", "project"),
    ("git ls-files -o", "project"),
    ("git ls-files -o --exclude-standard src", "project"),
    ("git ls-files --others --exclude-standard project", ""),
]


@pytest.mark.parametrize("command,sub", GIT_LIST_BLOCK)
def test_n5_git_ignored_listings_reaching_clients_blocked(wsg, command, sub):
    assert _bash_blocked(command, wsg, wsg / sub if sub else wsg), (command, sub)


@pytest.mark.parametrize("command,sub", GIT_LIST_ALLOW)
def test_n5_git_listings_away_from_clients_allowed(wsg, command, sub):
    assert _bash_allowed(command, wsg, wsg / sub if sub else wsg), (command, sub)


GLOB_BLOCK = ["../**/*.md", "../*/acme/*.md", "src/../../**", "./../**/notes.md", "**/../../*"]
GLOB_ALLOW = ["**/*.py", "src/**/*.py", "src/../**/*.md", "*.md"]


@pytest.mark.parametrize("pattern", GLOB_BLOCK)
def test_n5_glob_pattern_climbing_to_the_root_blocked(ws, pattern):
    assert _blocked("Glob", {"pattern": pattern}, ws, ws / "project"), pattern


def test_n5_glob_pattern_with_an_absolute_root_blocked(ws):
    assert _blocked("Glob", {"pattern": f"{ws.as_posix()}/**/*.md"}, ws, ws / "project")
    assert _blocked("Glob", {"pattern": "../../**/*.md", "path": "src"}, ws, ws / "project")


@pytest.mark.parametrize("pattern", GLOB_ALLOW)
def test_n5_glob_pattern_inside_project_allowed(ws, pattern):
    assert _allowed("Glob", {"pattern": pattern}, ws, ws / "project"), pattern


# --- R4-01: short-option clusters with digits or a value option ---

CLUSTER_BLOCK = [
    "grep -rA2 ERROR ..",
    "grep -rnA2 ERROR ..",
    "grep -nrC3 ERROR ..",
    "grep -r2 ERROR ..",
    "egrep -riA2 ERROR ..",
    "zip -9r - ..",
    "zip -r9 - ..",
    "zip -9r out.zip ..",
    "ls -R1 ..",
    "ls -1R ..",
    "diff -rU3 .. /tmp/x",
]
CLUSTER_ALLOW = [
    "grep -rA2 ERROR src",
    "grep -rnA2 ERROR .",
    "grep -nrC3 ERROR src",
    "zip -9r - src",
    "zip -r9 out.zip src",
    "ls -R1 src",
    "diff -rU3 src /tmp/x",
    "grep -A2r ERROR README.md",
    "grep -eERROR README.md",
]


@pytest.mark.parametrize("command", CLUSTER_BLOCK)
def test_r4_01_clusters_with_digits_still_recursive(ws, command):
    assert _bash_blocked(command, ws, ws / "project"), command


@pytest.mark.parametrize("command", CLUSTER_ALLOW)
def test_r4_01_confined_clusters_allowed(ws, command):
    assert _bash_allowed(command, ws, ws / "project"), command


def test_r4_01_zip_to_stdout_still_walks_its_input_for_links(ws_up):
    assert _bash_blocked("zip -9r - .", ws_up, ws_up / "project")
    assert _bash_allowed("zip -9ry - .", ws_up, ws_up / "project")


# --- R4-07: doctor and the alpha 12 record state the final tracked-clients rule ---

def test_r4_07_doctor_names_the_final_rule(tmp_path):
    import contextlib
    import io
    from torque import cli, workspace as wsmod
    root = tmp_path / "w"
    wsmod.init_workspace(root, "Example firm", "generic")
    wsmod.set_ai_access(root, "build-only")
    (root / "clients" / "acme").mkdir(parents=True, exist_ok=True)
    (root / "clients" / "acme" / "notes.md").write_text("x", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-f", "clients/acme/notes.md")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["doctor", "--workspace", str(root), "--json"])
    actions = " ".join(json.loads(out.getvalue())["next_actions"])
    assert "status and log" not in actions
    assert "git status" in actions and "-v" in actions and "git rm --cached" in actions


def test_r4_07_alpha12_record_has_no_status_and_log_rule():
    text = " ".join((REPO / "docs" / "validation-alpha12.md").read_text(encoding="utf-8").split())
    assert "only `git status` and `git log` pass" not in text


# --- Ordinary build session: nothing new blocks it ---

ORDINARY_PROJECT = [
    "git status", "git diff", "git log --oneline -5", "git add src/a.py", "git commit -m 'fix: x'",
    "pytest -q", "python -m pytest -q", "rg TODO", "rg TODO src", "grep -rn TODO .", "grep -rn TODO src",
    "find . -name '*.py'", "tar -czf ../out.tgz --exclude=.git src", "tar -cf - .", "zip -r out.zip src",
    "ln -s src/a.py alias.py", "ls -R src", "tree src", "fd py",
]
ORDINARY_ROOT = ["git status", "git diff", "git log --oneline -5", "pytest -q project", "rg TODO project",
                 "grep -rn TODO project", "tar -czf out.tgz project", "zip -r out.zip project",
                 "ln -s project/src/a.py a.py"]


@pytest.mark.parametrize("command", ORDINARY_PROJECT)
def test_ordinary_build_commands_in_project_allowed(wsg, command):
    _symlink("src", wsg / "project" / "lib")
    assert _bash_allowed(command, wsg, wsg / "project"), command


@pytest.mark.parametrize("command", ORDINARY_ROOT)
def test_ordinary_build_commands_at_the_root_allowed(wsg, command):
    assert _bash_allowed(command, wsg, wsg), command


# --- Docs ---

def _doc():
    return " ".join((REPO / "docs" / "ai-access.md").read_text(encoding="utf-8").split())


def test_ai_access_doc_describes_the_new_checks():
    text = _doc()
    for phrase in ("ln -s", "rg -L", "grep -R", "-d recurse", "git status --ignored", "git ls-files",
                   "unresolved `$`", "`if`"):
        assert phrase in text, phrase


def test_alpha12_review_scope_is_no_longer_pending():
    text = (REPO / "docs" / "validation-alpha12.md").read_text(encoding="utf-8")
    scope = text.split("## Review scope", 1)[1].split("##", 1)[0]
    assert "not yet been re-reviewed" not in scope
    assert "d3b0891" in scope and "spot-check" in scope


def test_alpha13_record_exists_and_names_its_python():
    text = (REPO / "docs" / "validation-alpha13.md").read_text(encoding="utf-8")
    assert "Python 3." in text and "## Review scope" in text
