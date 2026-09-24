"""Alpha 11: regression tests for the round-1 audit of de-identified mode.

The *_BLOCK lists hold the bypasses the audit reported plus neighbouring
forms; most were allowed by the 2.0.0a10 gate. Each section names the audit
item (K1..K9) it closes. "acme" is a neutral placeholder client name.
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


@pytest.fixture
def ws(tmp_path):
    """A real build-only workspace on disk, for glob and nesting cases."""
    root = tmp_path / "w"
    (root / "clients" / "acme").mkdir(parents=True)
    (root / "clients" / "acme" / "notes.md").write_text("client notes", encoding="utf-8")
    (root / "project" / "force-app").mkdir(parents=True)
    (root / "project" / "README.md").write_text("readme", encoding="utf-8")
    (root / "project" / "a.xml").write_text("<x/>", encoding="utf-8")
    (root / ".torque").mkdir()
    (root / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    (root / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic",
         "ai_access": "build-only"}), encoding="utf-8")
    return Path(os.path.realpath(str(root)))


# --- K1: cd / pushd earlier in the same command ---

K1_BLOCK = [
    "cd project && cat ../clients/acme/notes.md",
    "cd project && ls ../clients",
    "cd project; cat ../clients/acme/notes.md",
    "pushd project && cat ../clients/acme/notes.md",
    "builtin cd project && cat ../clients/acme/notes.md",
    "cd project/force-app && cat ../../clients/acme/notes.md",
    "cd .. && cat w/clients/acme/notes.md",
    "bash -c 'cd project && cat ../clients/acme/notes.md'",
    "cd project && grep -r secret ..",
]
K1_ALLOW = [
    "cd project && cat ../README.md",
    "cd project && sf project generate --name demo",
    "cd project && git status",
]


@pytest.mark.parametrize("command", K1_BLOCK)
def test_k1_cd_prefix_is_tracked(command):
    assert _blocked("Bash", {"command": command}), command


@pytest.mark.parametrize("command", K1_ALLOW)
def test_k1_cd_prefix_ordinary_work_allowed(command):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), command


# --- K2: input and output redirection ---

K2_BLOCK = [
    "cat <clients/acme/notes.md",
    "cat 0<clients/acme/notes.md",
    "wc -l <clients/acme/notes.md",
    "echo x >clients/acme/out.md",
    "echo x >>clients/acme/out.md",
    "echo x 2>clients/acme/err.log",
    "echo x &>clients/acme/all.log",
    "cat <>clients/acme/notes.md",
    "cd project && cat <../clients/acme/notes.md",
    "grep --file=clients/acme/notes.md x project",
    "CLIENT_DIR=clients/acme; cat \"$CLIENT_DIR/notes.md\"",
    "export CLIENT_DIR=clients/acme",
]
K2_ALLOW = [
    "cat <project/README.md",
    "echo hi >project/out.txt",
    "sf --version 2>/dev/null",
    "git status 2>&1",
    "FOO=bar git status",
]


@pytest.mark.parametrize("command", K2_BLOCK)
def test_k2_redirection_is_parsed(command):
    assert _blocked("Bash", {"command": command}), command


@pytest.mark.parametrize("command", K2_ALLOW)
def test_k2_redirection_ordinary_work_allowed(command):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), command


# --- K3: globs, brace expansion and $PWD ---

K3_BLOCK = [
    "cat c*/acme/notes.md",
    "cat cli?nts/acme/notes.md",
    "cat [c]lients/acme/notes.md",
    "cat */acme/notes.md",
    "head */*/notes.md",
    "cat project/../c*/acme/notes.md",
    "grep -r secret c*",
    "grep -r secret *",
    "cp -r c* /tmp/copy",
    "tar czf /tmp/x.tgz c*",
    "cat {clients,project}/acme/notes.md",
    "cat **/notes.md",
    "cat $PWD/clients/acme/notes.md",
    "cat ${PWD}/clients/acme/notes.md",
    "rm -rf $PWD/.claude",
    "cd project && cat ../c*/acme/notes.md",
]
K3_ALLOW = [
    "ls project/*.xml",
    "cat project/*.md",
    "cat $PWD/project/README.md",
    "rm project/tmp/*.log",
]


@pytest.mark.parametrize("command", K3_BLOCK)
def test_k3_globs_are_expanded(ws, command):
    assert _blocked("Bash", {"command": command}, ws, ws), command


@pytest.mark.parametrize("command", K3_ALLOW)
def test_k3_globs_outside_clients_allowed(ws, command):
    assert gate.decide("Bash", {"command": command}, ws, "build-only", ws) == (True, ""), command


# --- K4: MCP tools with path arguments ---

K4_BLOCK = [
    ("mcp__filesystem__read_file", {"path": "clients/acme/notes.md"}),
    ("mcp__filesystem__read_text_file", {"path": "/w/clients/acme/notes.md"}),
    ("mcp__filesystem__read_multiple_files", {"paths": ["/w/project/a.xml", "/w/clients/acme/notes.md"]}),
    ("mcp__filesystem__directory_tree", {"path": "/w"}),
    ("mcp__filesystem__search_files", {"path": "/w", "pattern": "*.md"}),
    ("mcp__files__open", {"uri": "file:///w/clients/acme/notes.md"}),
    ("mcp__filesystem__write_file", {"path": "/w/workspace.json", "content": "{}"}),
    ("mcp__filesystem__edit_file", {"path": "/w/.claude/settings.json", "edits": []}),
    ("mcp__filesystem__move_file", {"source": "/w/project/a", "destination": "/w/clients/acme/a"}),
    ("mcp__tools__run", {"options": {"nested": {"file": "../w/clients/acme/notes.md"}}}),
]
K4_ALLOW = [
    ("mcp__filesystem__read_file", {"path": "/w/project/README.md"}),
    ("mcp__filesystem__list_directory", {"path": "/w/project"}),
    ("mcp__github__list_issues", {"owner": "example", "repo": "example"}),
    ("mcp__claude_ai_Gmail__search_threads", {"query": "flow fault"}),
]


@pytest.mark.parametrize("tool,inp", K4_BLOCK)
def test_k4_mcp_path_arguments_are_inspected(tool, inp):
    assert _blocked(tool, inp), (tool, inp)
    assert gate.decide(tool, inp, W, "full")[0]


@pytest.mark.parametrize("tool,inp", K4_ALLOW)
def test_k4_mcp_ordinary_calls_allowed(tool, inp):
    assert gate.decide(tool, inp, W, "build-only") == (True, ""), (tool, inp)


# --- K5: git grep over untracked or unindexed files ---

K5_BLOCK = [
    "git grep --untracked secret",
    "git grep --no-index secret",
    "git grep --untracked -e secret",
    "git -C /w grep --untracked secret",
    "cd project && git grep --no-index secret ..",
    "git grep --no-index secret .",
]
K5_ALLOW = [
    "git grep secret",
    "git grep --untracked secret project/",
    "git grep --no-index secret project",
]


@pytest.mark.parametrize("command", K5_BLOCK)
def test_k5_git_grep_untracked_reaching_clients_blocked(command):
    assert _blocked("Bash", {"command": command}), command


@pytest.mark.parametrize("command", K5_ALLOW)
def test_k5_git_grep_scoped_or_tracked_allowed(command):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), command


# --- K6: explicit null, and the strictest ancestor wins ---

def _run_gate(payload, env_extra=None):
    env = dict(os.environ)
    src = str(REPO / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "torque.gate"], input=payload,
                          capture_output=True, text=True, env=env)


@pytest.mark.parametrize("value", [None, "", "null"])
def test_k6_explicit_empty_ai_access_is_build_only(tmp_path, value):
    config = {"schema": "torque.workspace/1", "name": "Example", "profile": "generic", "ai_access": value}
    (tmp_path / "workspace.json").write_text(json.dumps(config), encoding="utf-8")
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload)
    assert result.returncode == 2 and result.stderr.strip()


def test_k6_resolve_ai_access_values():
    assert gate._resolve_ai_access(None) == "build-only"
    assert gate._resolve_ai_access("") == "build-only"
    assert gate._resolve_ai_access("full") == "full"
    assert gate._resolve_ai_access("build-only") == "build-only"


@pytest.mark.parametrize("nested_config", [{}, {"ai_access": "full"}, {"ai_access": None}])
@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": "../clients/acme/notes.md"}),
    ("Bash", {"command": "cat ../clients/acme/notes.md"}),
    ("Bash", {"command": "sf org display --target-org prod"}),
    ("Bash", {"command": "torque workspace ai-access full --path .."}),
])
def test_k6_nested_workspace_cannot_downgrade_its_parent(ws, nested_config, tool, inp):
    nested = ws / "project"
    (nested / "workspace.json").write_text(json.dumps(nested_config), encoding="utf-8")
    payload = json.dumps({"cwd": str(nested), "tool_name": tool, "tool_input": inp})
    result = _run_gate(payload)
    assert result.returncode == 2 and result.stderr.strip(), (nested_config, tool, inp)


def test_k6_nested_workspace_ordinary_work_allowed(ws):
    nested = ws / "project"
    (nested / "workspace.json").write_text("{}", encoding="utf-8")
    payload = json.dumps({"cwd": str(nested), "tool_name": "Read", "tool_input": {"file_path": "README.md"}})
    assert _run_gate(payload).returncode == 0


def test_k6_full_parent_with_build_only_child_still_gates_the_child(tmp_path):
    parent = tmp_path / "parent"
    child = parent / "child"
    (child / "clients" / "acme").mkdir(parents=True)
    (parent / "workspace.json").write_text(json.dumps({"ai_access": "full"}), encoding="utf-8")
    (child / "workspace.json").write_text(json.dumps({"ai_access": "build-only"}), encoding="utf-8")
    payload = json.dumps({"cwd": str(child), "tool_name": "Read",
                          "tool_input": {"file_path": "clients/acme/notes.md"}})
    assert _run_gate(payload).returncode == 2


def test_k6_workspace_mode_reports_strictest(ws):
    nested = ws / "project"
    (nested / "workspace.json").write_text("{}", encoding="utf-8")
    chain = gate._workspace_chain(nested)
    assert [(folder, mode) for folder, mode, _ in chain][:2] == [(nested, "full"), (ws, "build-only")]
    assert gate._workspace_mode(nested)[:2] == (ws, "build-only")


# --- K7: the installed Torque package and its installer ---

PKG = Path(os.path.realpath(str(Path(torque.__file__).parent)))

K7_BLOCK_TOOLS = [
    ("Write", {"file_path": str(PKG / "gate.py"), "content": "def main(): return 0"}),
    ("Edit", {"file_path": str(PKG / "gate.py"), "old_string": "a", "new_string": "b"}),
    ("Edit", {"file_path": str(PKG / "cli.py"), "old_string": "a", "new_string": "b"}),
    ("MultiEdit", {"file_path": str(PKG / "__init__.py"), "edits": []}),
    ("Write", {"file_path": str(PKG / "sitecustomize.py"), "content": ""}),
    ("mcp__filesystem__write_file", {"path": str(PKG / "gate.py"), "content": ""}),
]
K7_BLOCK_BASH = [
    f"sed -i '' 's/return 2/return 0/' {PKG.as_posix()}/gate.py",
    f"echo 'def main(): return 0' > {PKG.as_posix()}/gate.py",
    f"rm -rf {PKG.as_posix()}",
    "pip uninstall -y torque-salesforce",
    "pip install --force-reinstall torque-salesforce==2.0.0a9",
    "pip3 uninstall torque_salesforce",
    "python -m pip install -U torque-salesforce",
    "python3 -m pip uninstall -y torque-salesforce",
    ".venv/bin/pip install torque-salesforce==2.0.0a1",
    "uv pip uninstall torque-salesforce",
    "uv pip install torque-salesforce==2.0.0a9",
    "uv tool uninstall torque-salesforce",
    "pipx uninstall torque-salesforce",
    "pipx reinstall torque-salesforce",
    "rm -rf .venv/lib/python3.12/site-packages/torque_salesforce-2.0.0a10.dist-info",
    "rm .venv/lib/python3.12/site-packages/__editable__.torque_salesforce-2.0.0a10.pth",
]
K7_ALLOW_BASH = [
    "pip install pytest",
    "pip list",
    "pip show torque-salesforce",
    "uv pip install ruff",
    "python -m pip --version",
]


@pytest.mark.parametrize("tool,inp", K7_BLOCK_TOOLS)
def test_k7_writes_into_installed_package_blocked(tool, inp):
    assert _blocked(tool, inp), (tool, inp)


def test_k7_read_of_installed_package_allowed():
    assert gate.decide("Read", {"file_path": str(PKG / "gate.py")}, W, "build-only") == (True, "")


@pytest.mark.parametrize("command", K7_BLOCK_BASH)
def test_k7_bash_self_disable_blocked(command):
    assert _blocked("Bash", {"command": command}), command


@pytest.mark.parametrize("command", K7_ALLOW_BASH)
def test_k7_ordinary_package_work_allowed(command):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), command


# --- K8: a hook shim that fails closed, and doctor's hook check ---

def _run_shim(event, pythonpath):
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    return subprocess.run([sys.executable, "-c", gate.HOOK_SHIM_CODE], input=json.dumps(event),
                          capture_output=True, text=True, env=env)


def test_k8_shim_exits_2_when_torque_gate_cannot_load(tmp_path):
    fake = tmp_path / "fake"
    (fake / "torque").mkdir(parents=True)
    (fake / "torque" / "__init__.py").write_text("", encoding="utf-8")
    (fake / "torque" / "gate.py").write_text("raise ImportError('simulated broken install')\n", encoding="utf-8")
    result = _run_shim({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(tmp_path)},
                       str(fake))
    assert result.returncode == 2
    assert "could not load" in result.stderr


def test_k8_shim_runs_the_real_gate(ws):
    src = str(REPO / "src")
    blocked = _run_shim({"tool_name": "Read", "tool_input": {"file_path": "clients/acme/notes.md"},
                         "cwd": str(ws)}, src)
    assert blocked.returncode == 2 and "client context" in blocked.stderr
    allowed = _run_shim({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(ws)}, src)
    assert allowed.returncode == 0, allowed.stderr


def test_k8_hook_command_embeds_shim():
    command = gate.hook_command("/opt/venv/bin/python")
    assert command.startswith('"/opt/venv/bin/python" -c "')
    assert gate.HOOK_SHIM_CODE in command
    assert '"' not in gate.HOOK_SHIM_CODE and "%" not in gate.HOOK_SHIM_CODE


def test_k8_documented_hook_uses_shim():
    for doc in ("docs/ai-access.md", "docs/installation.md"):
        text = (REPO / doc).read_text(encoding="utf-8")
        assert "sys.excepthook" in text, doc


def _doctor(ws_path):
    from torque import cli
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["doctor", "--workspace", str(ws_path), "--json"])
    return code, json.loads(out.getvalue())


def _init_ws(tmp_path, mode):
    from torque import workspace as wsmod
    root = tmp_path / "w"
    wsmod.init_workspace(root, "Example firm", "generic")
    wsmod.set_ai_access(root, mode)
    return root


def test_k8_doctor_reports_unwired_hook_in_build_only(tmp_path):
    root = _init_ws(tmp_path, "build-only")
    code, report = _doctor(root)
    assert report["ai_access"]["mode"] == "build-only"
    assert report["ai_access"]["hook"]["configured"] is False
    assert report["ai_access"]["hook"]["verified"] is False
    assert report["ready"] is False and code == 3
    assert any("hook" in action for action in report["next_actions"])


@pytest.mark.skipif(os.name == "nt", reason="the probe runs the hook through a POSIX shell")
def test_k8_doctor_verifies_wired_shim(tmp_path):
    root = _init_ws(tmp_path, "build-only")
    (root / ".claude").mkdir(exist_ok=True)
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|mcp__.*",
                                          "hooks": [{"type": "command", "command": gate.hook_command(sys.executable)}]}]}}
    (root / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    code, report = _doctor(root)
    hook = report["ai_access"]["hook"]
    assert hook["configured"] and hook["fail_closed_shim"] and hook["verified"], hook
    assert code == 0


@pytest.mark.skipif(os.name == "nt", reason="the probe runs the hook through a POSIX shell")
def test_k8_doctor_flags_a_hook_that_does_not_block(tmp_path):
    root = _init_ws(tmp_path, "build-only")
    (root / ".claude").mkdir(exist_ok=True)
    broken = f'"{sys.executable}" -c "import sys; sys.exit(1)" # torque.gate'
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash|Read|mcp__.*",
                                          "hooks": [{"type": "command", "command": broken}]}]}}
    (root / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    code, report = _doctor(root)
    hook = report["ai_access"]["hook"]
    assert hook["configured"] and not hook["verified"] and hook["probe_exit"] == 1, hook
    assert code == 3


def test_k8_doctor_full_mode_does_not_require_hook(tmp_path):
    root = _init_ws(tmp_path, "full")
    code, report = _doctor(root)
    assert report["ai_access"]["mode"] == "full"
    assert code == 0


# --- K8 (optional part): offline sf code-analyzer and project convert ---

K8_SF_ALLOW = [
    "cd project && sf code-analyzer run",
    "sf code-analyzer run --workspace project --target project/force-app",
    "sf code-analyzer rules --workspace project",
    "sf project convert source --source-dir project/force-app --output-dir project/mdapi",
    "cd project && sf project convert source --output-dir mdapi",
]
K8_SF_BLOCK = [
    "sf code-analyzer run",
    "sf code-analyzer run --workspace .",
    "sf code-analyzer run --target clients/acme",
    "sf code-analyzer run --workspace project --target-org prod",
    "sf project convert source --source-dir clients/acme",
    "sf project convert source --source-dir project/force-app -o prod",
]


@pytest.mark.parametrize("command", K8_SF_ALLOW)
def test_k8_offline_sf_analysis_allowed(command):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), command


@pytest.mark.parametrize("command", K8_SF_BLOCK)
def test_k8_offline_sf_analysis_reaching_clients_or_org_blocked(command):
    assert _blocked("Bash", {"command": command}), command


# --- K9: demo wording and docs ---

def test_k9_alert_triage_client_note_is_conditional():
    from torque import demo
    walkthrough = demo._SCENARIOS["alert-triage"]["walkthrough.md"]
    note = walkthrough.split("## 7. What to tell the client", 1)[1]
    assert "likely cause" in note.casefold() and "to confirm" in note.casefold()
    assert "Cause: the automation" not in note


def test_k9_demo_doc_names_every_scenario():
    from torque import demo
    text = (REPO / "docs" / "demo.md").read_text(encoding="utf-8")
    for name in demo._SCENARIOS:
        assert name in text, name


def test_ai_access_doc_states_current_limits():
    text = (REPO / "docs" / "ai-access.md").read_text(encoding="utf-8").casefold()
    for phrase in ("no org allowlist", "metadata-only", "not a sandbox", "switching to `full` removes"):
        assert phrase in text, phrase


# --- Archive and recursive copy of a tree containing clients/ ---

ARCHIVE_BLOCK = [
    "tar czf /tmp/w.tgz .",
    "tar -czf /tmp/w.tgz -C /w .",
    "zip -r /tmp/w.zip .",
    "cp -r . /tmp/backup",
    "cp -a /w /tmp/backup",
    "rsync -a ./ /tmp/backup/",
    "cd project && cp -R .. /tmp/backup",
]
ARCHIVE_ALLOW = [
    "tar czf /tmp/src.tgz project",
    "zip -r /tmp/src.zip project/force-app",
    "cp -r project/force-app /tmp/backup",
    "cp README.md /tmp/x",
]


@pytest.mark.parametrize("command", ARCHIVE_BLOCK)
def test_archive_or_recursive_copy_of_clients_blocked(command):
    assert _blocked("Bash", {"command": command}), command


@pytest.mark.parametrize("command", ARCHIVE_ALLOW)
def test_archive_or_copy_scoped_away_from_clients_allowed(command):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), command


def test_cd_and_narrowing_follow_the_separator():
    # After `cd X &&` only X applies; after `;` the cd may have failed.
    assert gate.decide("Bash", {"command": "cd project && rg foo || true"}, W, "build-only") == (True, "")
    assert _blocked("Bash", {"command": "cd project; rg foo"})
    assert _blocked("Bash", {"command": "(cd /tmp && true); cat clients/acme/notes.md"})
    assert _blocked("Bash", {"command": "cd /tmp && true || cat clients/acme/notes.md"})
    assert _blocked("Bash", {"command": 'cd "$D" && cat clients/acme/notes.md'})
