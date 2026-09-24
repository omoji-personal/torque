"""Alpha 13: regression tests for the routes a fourth review round found.

N3 covers symlinks: making a link (`ln`, `cp -s`, `mklink`, `New-Item`) that points
at or above clients/ is blocked, a path that names a link is resolved, the gate does
no filesystem walk per call, and `torque doctor` reports links that lead out of the
tree. N4 covers
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


# --- N3: links are stopped where they are made; the gate does not walk trees ---

# Recursive tools in a mode that follows links. The gate does not walk their tree
# (a walk per call can be slowed past the hook timeout and misses links made in the
# same command); `torque doctor` reports links that lead out of the tree instead.
FOLLOW_MODES = [
    "rg -L SECRET", "rg --follow SECRET .", "find -L . -name notes.md", "grep -R SECRET .",
    "tar -chf - .", "cp -rL . /tmp/x", "cp -r . /tmp/x", "rsync -aL . /tmp/x", "zip -r o.zip .",
    "fd -L notes", "ls -RL", "tree -l", "grep -rS SECRET .",
]


@pytest.mark.parametrize("command", FOLLOW_MODES)
def test_n3_gate_does_not_walk_the_tree(ws_up, command, monkeypatch):
    """Over a 40-level tree the gate lists at most a couple of single folders (the
    worktrees folder, a shell glob), never the tree."""
    deep = ws_up / "project"
    for n in range(40):
        deep = deep / f"d{n}"
    deep.mkdir(parents=True)
    scanned = []
    real_scandir = os.scandir

    def counting_scandir(path="."):
        scanned.append(str(path))
        return real_scandir(path)

    def no_walk(*args, **kwargs):
        raise AssertionError("the gate walked the filesystem")

    monkeypatch.setattr(os, "scandir", counting_scandir)
    monkeypatch.setattr(os, "walk", no_walk)
    assert _bash_allowed(command, ws_up, ws_up / "project"), command
    assert len(scanned) <= 3, scanned


@pytest.mark.parametrize("command", ["rg SECRET up", "grep -r SECRET up", "cat up/clients/acme/notes.md",
                                     "ls up/clients", "tar -cf - up", "rg -L SECRET up"])
def test_n3_a_path_that_names_a_link_is_still_resolved(ws_up, command):
    assert _bash_blocked(command, ws_up, ws_up / "project"), command


def test_n3_read_through_a_link_is_still_resolved(ws_up):
    assert _blocked("Read", {"file_path": "up/clients/acme/notes.md"}, ws_up, ws_up / "project")


LINK_MAKERS_BLOCK = [
    "cp -s ../clients/acme/notes.md n.md",
    "cp -rs .. mirror",
    "cmd //c mklink //D up ..",
    "cmd /c mklink /D up ..",
    "cmd /c mklink /J up ../..",
    "mklink /D up ..",
    "mklink /H n.md ../clients/acme/notes.md",
    "New-Item -ItemType SymbolicLink -Path up -Target ..",
    "New-Item -ItemType Junction -Path up -Value ..",
    "new-item -type symboliclink -name up -target ..",
]
LINK_MAKERS_ALLOW = [
    "cp -s src/a.py b.py",
    "cmd /c mklink a_link.py src/a.py",
    "mklink /D lib src",
    "New-Item -ItemType SymbolicLink -Path lib -Target src",
    "New-Item -ItemType Directory -Path out",
]


@pytest.mark.parametrize("command", LINK_MAKERS_BLOCK)
def test_n3_other_link_makers_at_or_above_clients_blocked(ws, command):
    assert _bash_blocked(command, ws, ws / "project"), command


@pytest.mark.parametrize("command", LINK_MAKERS_ALLOW)
def test_n3_other_link_makers_inside_project_allowed(ws, command):
    assert _bash_allowed(command, ws, ws / "project"), command


def test_n3_powershell_link_maker_blocked(ws):
    assert _blocked("PowerShell", {"command": "New-Item -ItemType SymbolicLink -Path up -Target .."},
                    ws, ws / "project")


def _doctor_report(root):
    import contextlib
    import io
    from torque import cli
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["doctor", "--workspace", str(root), "--json"])
    return code, json.loads(out.getvalue())


def _doctor_ws(tmp_path):
    from torque import workspace as wsmod
    root = tmp_path / "d"
    wsmod.init_workspace(root, "Example firm", "generic")
    wsmod.set_ai_access(root, "build-only")
    (root / "clients" / "acme").mkdir(parents=True, exist_ok=True)
    (root / "clients" / "acme" / "notes.md").write_text("x", encoding="utf-8")
    (root / "project" / "src").mkdir(parents=True, exist_ok=True)
    return Path(os.path.realpath(str(root)))


def test_n3_doctor_reports_links_that_lead_out_of_the_tree(tmp_path):
    root = _doctor_ws(tmp_path)
    _symlink("..", root / "project" / "up")
    _symlink(str(root / "clients" / "acme"), root / "project" / "src" / "cl")
    code, report = _doctor_report(root)
    links = report["ai_access"]["links_out"]
    assert sorted(links["found"]) == ["project/src/cl", "project/up"], links
    assert report["ready"] is False and code == 3
    assert any("link" in action and "project/up" in action for action in report["next_actions"])


def test_n3_doctor_ignores_links_inside_the_tree_and_skipped_folders(tmp_path):
    root = _doctor_ws(tmp_path)
    _symlink("src", root / "project" / "lib")
    (root / "project" / "node_modules" / ".bin").mkdir(parents=True)
    _symlink("../../..", root / "project" / "node_modules" / ".bin" / "up")
    _symlink("..", root / "clients" / "acme" / "up")
    _, report = _doctor_report(root)
    links = report["ai_access"]["links_out"]
    assert links["found"] == [] and links["complete"] is True, links
    assert not any("link" in action for action in report["next_actions"])


def test_n3_doctor_reports_a_two_hop_link_through_an_outside_folder(tmp_path):
    root = _doctor_ws(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink(str(outside), root / "project" / "ext")
    _, report = _doctor_report(root)
    assert report["ai_access"]["links_out"]["found"] == []
    _symlink(str(root / "clients"), outside / "back")
    code, report = _doctor_report(root)
    found = report["ai_access"]["links_out"]["found"]
    assert len(found) == 1 and found[0].startswith("project/ext"), found
    assert report["ready"] is False and code == 3


def test_n3_doctor_reports_a_link_chain_ending_above_clients(tmp_path):
    root = _doctor_ws(tmp_path)
    _symlink("../..", root / "project" / "src" / "hop2")
    _symlink("src/hop2", root / "project" / "hop1")
    _, report = _doctor_report(root)
    found = report["ai_access"]["links_out"]["found"]
    assert "project/hop1" in found and "project/src/hop2" in found, found


def _hook_settings(root, timeout):
    from torque import gate
    import sys
    entry = {"type": "command", "command": gate.hook_command(sys.executable.replace("\\", "/"))}
    if timeout is not None:
        entry["timeout"] = timeout
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [entry]}]}}), encoding="utf-8")


@pytest.mark.parametrize("timeout,warned", [(None, True), (601, True), (3600, True), (600, False), (120, False)])
def test_doctor_warns_about_the_hook_timeout(tmp_path, timeout, warned):
    root = _doctor_ws(tmp_path)
    _hook_settings(root, timeout)
    _, report = _doctor_report(root)
    hook = report["ai_access"]["hook"]
    assert hook["timeouts"] == [timeout], hook
    assert any("timeout" in action for action in report["next_actions"]) is warned, report["next_actions"]


def test_n3_doctor_link_scan_stops_at_its_cap_and_says_so(tmp_path, monkeypatch):
    from torque import cli
    root = _doctor_ws(tmp_path)
    for n in range(12):
        (root / "project" / "src" / f"m{n}.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "LINK_SCAN_LIMIT", 5)
    code, report = _doctor_report(root)
    assert report["ai_access"]["links_out"]["complete"] is False
    assert report["ready"] is False and code == 3


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


# --- R4-06: patches and archive extraction outside project/ ---

GOOD_PATCH = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -1 +1 @@
-x = 1
+x = 2
"""
BAD_PATCH = GOOD_PATCH.replace("a/src/a.py", "a/../workspace.json").replace("b/src/a.py", "b/../workspace.json")
ABS_PATCH = GOOD_PATCH.replace("a/src/a.py", "/etc/x").replace("b/src/a.py", "/etc/x")


@pytest.fixture
def wsp(wsg):
    """The git workspace with patches in project/: one confined, two that climb or are absolute."""
    (wsg / "project" / "good.patch").write_text(GOOD_PATCH, encoding="utf-8")
    (wsg / "project" / "bad.patch").write_text(BAD_PATCH, encoding="utf-8")
    (wsg / "project" / "abs.patch").write_text(ABS_PATCH, encoding="utf-8")
    return wsg


APPLY_BLOCK = [
    ("git apply /tmp/flip.patch", ""),
    ("git apply --index project/good.patch", ""),
    ("git apply project/good.patch", ""),
    ("git am /tmp/flip.mbox", ""),
    ("git am /tmp/flip.mbox", "project"),
    ("git am --continue", "project"),
    ("git -c core.quotepath=false am good.patch", "project"),
    ("git apply bad.patch", "project"),
    ("git apply abs.patch", "project"),
    ("git apply --unsafe-paths good.patch", "project"),
    ("git apply --directory=.. good.patch", "project"),
    ("git apply --directory=/tmp good.patch", "project"),
    ("git apply --directory=. project/good.patch", ""),
    ("patch -p1 < project/good.patch", ""),
    ("patch -p1 -i project/good.patch", ""),
    ("patch -d .. -p1 < good.patch", "project"),
    ("patch --directory=.. -p1 -i good.patch", "project"),
    ("cat good.patch | patch -p1", "project"),
    ("patch -p1 < bad.patch", "project"),
    ("patch -p1 -i abs.patch", "project"),
    ("patch -p1 <<'EOF'", "project"),
    ("tar -xf a.tar", ""),
    ("tar xf a.tar", ""),
    ("tar -xzf a.tgz -C ..", "project"),
    ("tar -C .. -xf a.tar", "project"),
    ("tar --extract -f a.tar", ""),
    ("bsdtar -xf a.tar", ""),
    ("tar -xPf a.tar", "project"),
    ("tar --absolute-names -xf a.tar", "project"),
    ("tar -xf a.tar --transform=s,^,../,", "project"),
    ("unzip a.zip", ""),
    ("unzip a.zip -d ..", "project"),
    ("unzip -d.. a.zip", "project"),
    ("unzip -: a.zip", "project"),
    ("unzip -od.. a.zip", "project"),
    ("unzip -qod .. a.zip", "project"),
    ("unzip -qo -d.. a.zip", "project"),
    ("unzip -oqd ../clients a.zip", "project"),
    ("ditto -x -k a.zip ..", "project"),
]
APPLY_ALLOW = [
    ("git apply good.patch", "project"),
    ("git apply --index good.patch", "project"),
    ("git apply -R good.patch", "project"),
    ("git apply --check project/good.patch", ""),
    ("git apply --stat project/good.patch", ""),
    ("git diff | git apply -R", "project"),
    ("git apply --directory=project project/good.patch", ""),
    ("git am --abort", ""),
    ("git am --show-current-patch", "project"),
    ("patch -p1 < good.patch", "project"),
    ("patch -p1 -i good.patch", "project"),
    ("patch --dry-run -p1 -i project/good.patch", ""),
    ("tar -xf a.tar", "project"),
    ("tar -xzf a.tgz -C src", "project"),
    ("tar -xf a.tar -C project", ""),
    ("tar -tf a.tar", ""),
    ("tar -czf out.tgz project", ""),
    ("unzip a.zip", "project"),
    ("unzip a.zip -d project/out", ""),
    ("unzip -l a.zip", ""),
    ("unzip -od out a.zip", "project"),
    ("unzip -qod src a.zip", "project"),
    ("unzip -qo -dout a.zip", "project"),
    ("ditto -x -k a.zip out", "project"),
]


@pytest.mark.parametrize("command,sub", APPLY_BLOCK)
def test_r4_06_patch_and_extract_outside_project_blocked(wsp, command, sub):
    assert _bash_blocked(command, wsp, wsp / sub if sub else wsp), (command, sub)


@pytest.mark.parametrize("command,sub", APPLY_ALLOW)
def test_r4_06_patch_and_extract_inside_project_allowed(wsp, command, sub):
    assert _bash_allowed(command, wsp, wsp / sub if sub else wsp), (command, sub)


def test_r4_06_git_am_allowed_when_project_is_its_own_repository(ws):
    project = ws / "project"
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")
    (project / "good.patch").write_text(GOOD_PATCH, encoding="utf-8")
    assert _bash_allowed("git am good.patch", ws, project)
    assert _bash_blocked("git am good.patch", ws, ws)


# --- R4-10 and Kimi R4-07: stale strings ---

def test_r4_10_gate_docstring_names_the_final_rule():
    import inspect
    doc = inspect.getdoc(gate._git_stage_reason)
    assert "status and log" not in doc and "git rm --cached" in doc


@pytest.mark.parametrize("version", ["2.0.0a12", "2.0.0a13"])
def test_kimi_r4_07_changelog_says_tagged_not_unpublished(version):
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = next(line for line in text.splitlines() if line.startswith(f"## {version} "))
    assert "unpublished" not in heading and "not published to a package index" in heading, heading


# --- Spot-check: one time budget and one glob budget per call ---

def test_gate_blocks_when_its_time_budget_runs_out(ws, monkeypatch):
    monkeypatch.setattr(gate, "GATE_TIME_BUDGET", -1.0)
    allowed, reason = gate.decide("Bash", {"command": "ls src/*"}, ws, "build-only", ws / "project")
    assert not allowed and "time budget" in reason, reason


def test_gate_time_budget_is_a_few_seconds():
    assert 1.0 <= gate.GATE_TIME_BUDGET <= 10.0


def test_gate_checks_the_deadline_during_glob_expansion(ws, monkeypatch):
    for n in range(50):
        (ws / "project" / "src" / f"m{n}.py").write_text("", encoding="utf-8")
    clock = {"now": 0.0}

    def monotonic():
        clock["now"] += 0.2
        return clock["now"]

    monkeypatch.setattr(gate.time, "monotonic", monotonic)
    allowed, reason = gate.decide("Bash", {"command": "ls src/*.py"}, ws, "build-only", ws / "project")
    assert not allowed and "time budget" in reason, reason


def test_gate_blocks_above_its_glob_match_budget(ws, monkeypatch):
    for n in range(12):
        (ws / "project" / "src" / f"m{n}.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(gate, "GLOB_MATCH_LIMIT", 5)
    allowed, reason = gate.decide("Bash", {"command": "ls src/*.py"}, ws, "build-only", ws / "project")
    assert not allowed and "glob" in reason, reason
    assert _bash_allowed("ls src/a.py", ws, ws / "project")


def test_gate_budget_is_fresh_for_each_call(ws):
    for _ in range(3):
        assert _bash_allowed("ls src/*.py", ws, ws / "project")


def test_hook_reports_the_budget_and_exits_2(wsg, monkeypatch):
    event = {"tool_name": "Bash", "tool_input": {"command": "ls src/*"}, "cwd": str(wsg / "project")}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(wsg))
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    import sys
    code = "import torque.gate as g, sys; g.GATE_TIME_BUDGET = -1.0; sys.exit(g.main())"
    result = subprocess.run([sys.executable, "-c", code], input=json.dumps(event), capture_output=True, text=True,
                            env=env)
    assert result.returncode == 2 and "time budget" in result.stderr, result.stderr


def _run_main(code, event, project_dir, timeout=60):
    import sys
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    import time
    start = time.monotonic()
    result = subprocess.run([sys.executable, "-c", code], input=json.dumps(event), capture_output=True, text=True,
                            env=env, timeout=timeout)
    return result, time.monotonic() - start


def test_watchdog_blocks_a_call_that_outruns_the_budget(wsg):
    """The budget is a hard limit: a step that never checks it (here a decide()
    that sleeps) is cut off by the watchdog, which blocks with exit 2."""
    code = ("import time, sys, torque.gate as g\n"
            "g.GATE_TIME_BUDGET = 0.5\n"
            "g.decide = lambda *a, **k: (time.sleep(30), (True, ''))[1]\n"
            "sys.exit(g.main())\n")
    event = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(wsg / "project")}
    result, elapsed = _run_main(code, event, wsg)
    assert result.returncode == 2 and "time budget" in result.stderr, result.stderr
    assert elapsed < 15, elapsed


def test_watchdog_stops_an_exponential_glob_through_self_links(wsg):
    lp = wsg / "project" / "lp"
    lp.mkdir()
    _symlink(".", lp / "a")
    _symlink(".", lp / "b")
    code = "import sys, torque.gate as g\ng.GATE_TIME_BUDGET = 1.0\nsys.exit(g.main())\n"
    command = "ls lp/" + "*/" * 22 + "zz; cat ../clients/acme/notes.md"
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(wsg / "project")}
    result, elapsed = _run_main(code, event, wsg)
    assert result.returncode == 2, result.stderr
    assert elapsed < 15, elapsed


def test_watchdog_does_not_fire_on_an_ordinary_call(wsg):
    code = "import sys, torque.gate as g\nsys.exit(g.main())\n"
    event = {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(wsg / "project")}
    result, _ = _run_main(code, event, wsg)
    assert result.returncode == 0 and not result.stderr, result.stderr


def _many_files(folder, count):
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(count):
        (folder / f"f{n:05d}.js").write_bytes(b"")


def test_glob_budget_counts_each_pattern_once_2400_matches_pass(ws):
    _many_files(ws / "project" / "a", 1200)
    _many_files(ws / "project" / "b", 1200)
    assert _bash_allowed("ls a/*.js b/*.js", ws, ws / "project")
    assert _bash_allowed("ls a/*.js b/*.js a/*.js; wc -l b/*.js", ws, ws / "project")


def test_glob_budget_10001_distinct_matches_block(ws):
    for index in range(6):
        _many_files(ws / "project" / f"d{index}", 1667 if index < 5 else 1666)
    command = "ls " + " ".join(f"d{index}/*.js" for index in range(6))
    allowed, reason = gate.decide("Bash", {"command": command}, ws, "build-only", ws / "project")
    assert not allowed and "10000" in reason, reason
    assert _bash_allowed("ls " + " ".join(f"d{index}/*.js" for index in range(5)), ws, ws / "project")


# --- Re-review finding 4: a word longer than the file name limit ---

def _hook(event, project_dir):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    import sys
    return subprocess.run([sys.executable, "-m", "torque.gate"], input=json.dumps(event), capture_output=True,
                          text=True, env=env)


@pytest.mark.parametrize("command", [
    "git commit -m \"" + "word " * 90 + "\"",
    "git commit -m '" + "x" * 400 + "'",
    "echo hi;" * 30,
    " && ".join(f"echo {chr(97 + n % 26)}" for n in range(28)),
    "ls src/a.py ; " * 70,
    "ls src/a.py ; " * 400 + "echo done",
])
def test_long_words_are_not_paths_and_do_not_fail_closed(wsg, command):
    result = _hook({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(wsg / "project")}, wsg)
    assert result.returncode == 0, result.stderr


def test_long_word_naming_client_context_still_blocked(wsg):
    command = "cat ../clients/acme/" + "n" * 300 + ".md; echo " + "x" * 300
    result = _hook({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(wsg / "project")}, wsg)
    assert result.returncode == 2 and "client context" in result.stderr, result.stderr


def test_installation_docs_state_the_hook_timeout():
    text = " ".join((REPO / "docs" / "installation.md").read_text(encoding="utf-8").split())
    assert '"timeout": 600' in text and "timed-out hook" in text and "proceed" in text


def test_ai_access_doc_states_the_gate_budget():
    text = _doc()
    assert "well under a second" not in text
    assert "5-second" in text and "10,000" in text


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
    for phrase in ("ln -s", "cp -s", "mklink", "cp -r", "torque doctor", "-d recurse", "git status --ignored",
                   "git ls-files", "unresolved `$`", "`if`", "timed-out hook"):
        assert phrase in text, phrase
    # The removed per-call walk stopped at 20,000 entries; alpha 14's 20,000-character
    # input limit is a different number with the same value.
    assert "The walk stops at" not in text and "20,000 entries" not in text


def test_alpha12_review_scope_is_no_longer_pending():
    text = (REPO / "docs" / "validation-alpha12.md").read_text(encoding="utf-8")
    scope = text.split("## Review scope", 1)[1].split("##", 1)[0]
    assert "not yet been re-reviewed" not in scope
    assert "d3b0891" in scope and "spot-check" in scope


def test_alpha13_record_exists_and_names_its_python():
    text = (REPO / "docs" / "validation-alpha13.md").read_text(encoding="utf-8")
    assert "Python 3." in text and "## Review scope" in text


def test_alpha13_review_scope_records_the_re_review():
    text = (REPO / "docs" / "validation-alpha13.md").read_text(encoding="utf-8")
    scope = " ".join(text.split("## Review scope", 1)[1].split("##", 1)[0].split())
    assert "a356093" in scope and "fix first" in scope and "spot-check" in scope
