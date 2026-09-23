import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from torque import gate

W = Path("/w")
BLOCK = [
    ("Bash", {"command": "sf data query -q 'select Id from Account' -o prod"}),
    ("Bash", {"command": "sf project retrieve start -o prod"}),
    ("Bash", {"command": "sf org display --target-org prod"}),
    ("Bash", {"command": "sfdx force:source:deploy -u prod"}),
    ("Bash", {"command": "torque data update --workspace . --client acme"}),
    ("Bash", {"command": "torque context --workspace . --client acme"}),
    ("Bash", {"command": "cd x && torque deploy start"}),
    ("Read", {"file_path": "/w/clients/acme/context/discovery.md"}),
    ("Grep", {"pattern": "x", "path": "/w/clients"}),
    ("Bash", {"command": "torque workspace ai-access full"}),
    ("Write", {"file_path": "/w/workspace.json"}),
    ("Edit", {"file_path": "/w/workspace.json"}),
]
ALLOW = [
    ("Bash", {"command": "torque demo /tmp/d"}),
    ("Bash", {"command": "torque workflows show diagnose"}),
    ("Bash", {"command": "sf project generate --name demo"}),
    ("Bash", {"command": "sf --version"}),
    ("Bash", {"command": "git status"}),
    ("Read", {"file_path": "/w/project/force-app/main/default/flows/x.flow-meta.xml"}),
]


@pytest.mark.parametrize("tool,inp", BLOCK)
def test_build_only_blocks_org_and_client_access(tool, inp):
    allowed, reason = gate.decide(tool, inp, W, "build-only")
    assert not allowed and reason


@pytest.mark.parametrize("tool,inp", ALLOW)
def test_build_only_allows_build_work(tool, inp):
    assert gate.decide(tool, inp, W, "build-only") == (True, "")


@pytest.mark.parametrize("tool,inp", BLOCK)
def test_full_mode_allows_everything(tool, inp):
    assert gate.decide(tool, inp, W, "full")[0]


def test_ai_access_action_sets_mode(tmp_path):
    from torque import cli, workspace as ws
    ws.init_workspace(tmp_path / "w", "Example firm", "generic")
    assert cli.main(["workspace", "ai-access", "build-only", "--path", str(tmp_path / "w")]) == 0
    assert json.loads((tmp_path / "w/workspace.json").read_text(encoding="utf-8"))["ai_access"] == "build-only"


# --- Fix round 1: regression tests for review-demonstrated bypasses ---
# Each case below was an ALLOW under the brief's gate.py that should have been a
# BLOCK, per the review at task-T3-review.md (4911c30..52606aa). "acme" is a
# neutral placeholder client name, matching the original brief's own fixtures.

BASH_BYPASS_BLOCK = [
    # C1: Bash reaching client context was never checked at all.
    ("cat clients/acme/context.md", "cat via relative clients/ path"),
    ("grep -r foo clients/", "grep -r over clients/"),
    # C2: python -m torque bypassed every torque check.
    ("python -m torque data update --workspace . --client acme", "python -m torque"),
    ("python3 -m torque context --workspace . --client acme", "python3 -m torque"),
    (".venv/bin/python -m torque context --workspace . --client acme", "a venv python -m torque"),
    # C3: the session could switch the mode off via Bash.
    ("sed -i '' 's/build-only/full/' workspace.json", "sed -i on workspace.json"),
    ("echo '{}' > workspace.json", "redirect over workspace.json"),
    ("python -c \"import json; json.dump({}, open('workspace.json','w'))\"", "python -c rewriting workspace.json"),
    ("python -m torque workspace ai-access full", "python -m torque workspace ai-access"),
    ("rm workspace.json", "rm workspace.json"),
    ("mv workspace.json x.json", "mv workspace.json"),
    ("cp /tmp/x workspace.json", "cp onto workspace.json"),
    ("tee workspace.json </tmp/x", "tee onto workspace.json"),
    ("sed -i '' 's/x/y/' .claude/settings.json", "sed -i on .claude/settings.json"),
    # C4: an org flag on an otherwise-local-looking sf subcommand.
    ("sf project generate manifest --from-org prod", "sf project generate --from-org"),
    # Important-2: command-position bypasses (env prefix, wrappers, grouping,
    # substitution, bash -c).
    ("FOO=1 sf data query -o prod", "leading env-var assignment"),
    ("SF_TARGET_ORG=prod sf data query -o prod", "leading env-var assignment (named like an org var)"),
    ("npx @salesforce/cli data query -o prod", "npx @salesforce/cli"),
    ("npx sf data query -o prod", "npx sf"),
    ("time sf data query -o prod", "time wrapper"),
    ("env sf data query -o prod", "env wrapper"),
    ("command sf data query -o prod", "command wrapper"),
    ("nice sf data query -o prod", "nice wrapper"),
    ("nohup sf data query -o prod &", "nohup wrapper"),
    ("sudo sf data query -o prod", "sudo wrapper"),
    ("xargs sf data query -o prod", "xargs wrapper"),
    ("(sf data query -o prod)", "subshell grouping"),
    ("{ sf data query -o prod; }", "brace grouping"),
    ("if true; then sf data query -o prod; fi", "command after then"),
    ("echo $(sf data query -o prod)", "$(...) command substitution"),
    ("echo `sf data query -o prod`", "backtick command substitution"),
    ('bash -c "sf data query -o prod"', "bash -c string argument"),
    ("sh -c 'torque context --workspace . --client acme'", "sh -c string argument"),
    ("zsh -c 'sf org display --target-org prod'", "zsh -c string argument"),
]


@pytest.mark.parametrize("command,label", BASH_BYPASS_BLOCK, ids=[label for _, label in BASH_BYPASS_BLOCK])
def test_build_only_blocks_bash_bypasses(command, label):
    allowed, reason = gate.decide("Bash", {"command": command}, W, "build-only")
    assert not allowed and reason, f"{label!r} should be blocked: {command!r}"


BASH_STILL_ALLOWED = [
    ("torque demo /tmp/d", "demo stays allowed"),
    ("sf project generate --name demo", "local sf generator stays allowed"),
    ("sf --version", "sf --version stays allowed"),
    ("sf plugins", "bare sf plugins stays allowed"),
    ("git status", "git stays allowed"),
    ('echo "hello world"', "plain echo stays allowed"),
]


@pytest.mark.parametrize("command,label", BASH_STILL_ALLOWED, ids=[label for _, label in BASH_STILL_ALLOWED])
def test_build_only_still_allows_build_work_after_hardening(command, label):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), label


def test_sf_plugins_install_is_blocked():
    # Minor 1: a bare "plugins" prefix whitelisted every "plugins install <name>".
    allowed, reason = gate.decide("Bash", {"command": "sf plugins install evil"}, W, "build-only")
    assert not allowed and reason


PATH_BYPASS_BLOCK = [
    ("Read", {"file_path": "clients/acme/x.md"}, "relative path into clients/"),
    ("Read", {"file_path": "/w/project/../clients/acme/x.md"}, "'..' traversal into clients/"),
    ("Write", {"file_path": "/w/.claude/settings.json"}, "Write on .claude/settings.json"),
    ("Write", {"file_path": "/w/.claude/settings.local.json"}, "Write on .claude/settings.local.json"),
    ("Edit", {"file_path": "/w/.claude/settings.json"}, "Edit on .claude/settings.json"),
    ("MultiEdit", {"file_path": "/w/.claude/settings.json"}, "MultiEdit on .claude/settings.json"),
    ("Write", {"file_path": "/w/WORKSPACE.JSON"}, "case-varied workspace.json"),
    ("Grep", {"pattern": "x"}, "Grep with no path defaults to cwd"),
    ("Grep", {"pattern": "x", "glob": "clients/**"}, "Grep glob containing clients"),
    ("Glob", {"pattern": "**/clients/**/*.md", "path": "/w"}, "Glob rooted above clients"),
    ("Glob", {"pattern": "*.md"}, "Glob with no path defaults to cwd"),
]


@pytest.mark.parametrize("tool,inp,label", PATH_BYPASS_BLOCK, ids=[label for _, _, label in PATH_BYPASS_BLOCK])
def test_build_only_blocks_path_bypasses(tool, inp, label):
    allowed, reason = gate.decide(tool, inp, W, "build-only")
    assert not allowed and reason, label


def test_grep_with_explicit_path_outside_clients_still_allowed():
    # The Grep/Glob hardening must not nuke ordinary scoped search.
    assert gate.decide("Grep", {"pattern": "x", "path": "/w/project"}, W, "build-only") == (True, "")


def test_symlinked_workspace_client_path_is_blocked(tmp_path):
    # C1a: the workspace root itself may be reached through a symlink, as it is
    # for some private deployments of Torque; comparisons must resolve first.
    real = tmp_path / "real-w"
    (real / "clients" / "acme").mkdir(parents=True)
    (real / "clients" / "acme" / "context.md").write_text("secret", encoding="utf-8")
    link = tmp_path / "link-w"
    link.symlink_to(real)
    allowed, reason = gate.decide(
        "Read", {"file_path": str(link / "clients" / "acme" / "context.md")}, link, "build-only")
    assert not allowed and reason


# --- main(): fail-closed behaviour ---

def _run_gate(payload, home=None):
    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run([sys.executable, "-m", "torque.gate"], input=payload,
                          capture_output=True, text=True, env=env)


def test_main_fails_closed_on_malformed_stdin():
    result = _run_gate("not json")
    assert result.returncode == 2 and result.stderr.strip()


def test_main_fails_closed_on_missing_tool_name(tmp_path):
    payload = json.dumps({"cwd": str(tmp_path), "tool_input": {}})
    result = _run_gate(payload)
    assert result.returncode == 2 and result.stderr.strip()


def test_main_fails_closed_on_non_dict_tool_input(tmp_path):
    (tmp_path / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic", "ai_access": "build-only"}), encoding="utf-8")
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": "oops"})
    result = _run_gate(payload)
    assert result.returncode == 2 and result.stderr.strip()


def test_main_treats_unreadable_workspace_json_as_build_only(tmp_path):
    if os.name == "nt":
        pytest.skip("chmod(0o000) does not deny the owner read access on Windows; "
                    "there is no POSIX-permission equivalent to simulate there")
    config = tmp_path / "workspace.json"
    config.write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic", "ai_access": "full"}), encoding="utf-8")
    config.chmod(0o000)
    try:
        payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash",
                              "tool_input": {"command": "sf org display --target-org prod"}})
        result = _run_gate(payload)
        assert result.returncode == 2 and result.stderr.strip()
    finally:
        config.chmod(0o600)


def test_main_treats_malformed_workspace_json_as_build_only(tmp_path):
    (tmp_path / "workspace.json").write_text("{not valid json", encoding="utf-8")
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload)
    assert result.returncode == 2 and result.stderr.strip()


def test_main_fails_closed_on_typo_ai_access_value(tmp_path):
    (tmp_path / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic", "ai_access": "Build-Only"}), encoding="utf-8")
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload)
    assert result.returncode == 2 and result.stderr.strip()


def test_main_allows_ordinary_work_with_no_workspace_json_anywhere(tmp_path):
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "git status"}})
    result = _run_gate(payload)
    assert result.returncode == 0


def test_main_full_mode_allows_org_calls(tmp_path):
    (tmp_path / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic", "ai_access": "full"}), encoding="utf-8")
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload)
    assert result.returncode == 0


def test_main_build_only_allows_ordinary_command(tmp_path):
    (tmp_path / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic", "ai_access": "build-only"}), encoding="utf-8")
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "git status"}})
    result = _run_gate(payload)
    assert result.returncode == 0


# --- Fix round 2: regression tests for the re-review's open findings ---
# task-T3-rereview-1.md (52606aa..12f9be0): C1 part (b) (Grep/Glob search-root
# and Bash recursive-search bypasses), C3 (.claude directory removal and a
# workspace-root glob deletion of workspace.json), and a new Important (a
# missing ai_access key must mean full, not build-only).

WRAPPER_NO_ORG_FLAG_BLOCK = [
    # Every case here blocks purely via the torque-subcommand rule (no org
    # flag anywhere), so it only passes if wrapper/position handling still
    # finds the "torque" token; it cannot pass through the org-flag route.
    ("FOO=1 torque context --workspace . --client acme", "leading env-var assignment"),
    ("npx torque context --workspace . --client acme", "npx wrapper"),
    ("time torque context --workspace . --client acme", "time wrapper"),
    ("env torque context --workspace . --client acme", "env wrapper"),
    ("command torque context --workspace . --client acme", "command wrapper"),
    ("sudo torque context --workspace . --client acme", "sudo wrapper"),
    ("xargs torque context --workspace . --client acme", "xargs wrapper"),
    ("nice torque context --workspace . --client acme", "nice wrapper"),
    ("(torque context --workspace . --client acme)", "subshell grouping"),
    ("{ torque context --workspace . --client acme; }", "brace grouping"),
    ("echo $(torque context --workspace . --client acme)", "$(...) substitution"),
    ("echo `torque context --workspace . --client acme`", "backtick substitution"),
    ('bash -c "torque context --workspace . --client acme"', "bash -c string argument"),
]


@pytest.mark.parametrize("command,label", WRAPPER_NO_ORG_FLAG_BLOCK,
                         ids=[label for _, label in WRAPPER_NO_ORG_FLAG_BLOCK])
def test_wrapper_handling_blocks_without_relying_on_an_org_flag(command, label):
    allowed, reason = gate.decide("Bash", {"command": command}, W, "build-only")
    assert not allowed and reason, f"{label!r} should be blocked without an org flag: {command!r}"


GREP_GLOB_SEARCH_ROOT_BLOCK = [
    ("Grep", {"pattern": "x", "path": "/w"}, "Grep rooted at the workspace root"),
    ("Grep", {"pattern": "x", "path": "."}, "Grep rooted at '.' (cwd)"),
    ("Glob", {"pattern": "**/*.md", "path": "/w"}, "Glob rooted at the workspace root"),
    ("Grep", {"pattern": "x", "path": "/w", "glob": "clients/**"}, "Grep glob field naming clients"),
]


@pytest.mark.parametrize("tool,inp,label", GREP_GLOB_SEARCH_ROOT_BLOCK,
                         ids=[label for _, _, label in GREP_GLOB_SEARCH_ROOT_BLOCK])
def test_grep_glob_search_root_reaching_clients_is_blocked(tool, inp, label):
    allowed, reason = gate.decide(tool, inp, W, "build-only")
    assert not allowed and reason, label
    assert "narrower path" in reason


def test_grep_glob_scoped_to_a_subdirectory_still_allowed():
    # The search-root hardening must not nuke ordinary scoped search.
    assert gate.decide("Grep", {"pattern": "x", "path": "/w/project"}, W, "build-only") == (True, "")
    assert gate.decide("Glob", {"pattern": "*.md", "path": "/w/project"}, W, "build-only") == (True, "")


RECURSIVE_TOOL_BLOCK = [
    ("grep -r foo .", "grep -r defaulting to '.'"),
    ("grep -R foo clients", "grep -R with an explicit clients arg"),
    ("rg foo", "rg with no path (defaults to cwd)"),
    ("ag foo", "ag with no path (defaults to cwd)"),
    ("find . -name x", "find rooted at '.'"),
    ("tree", "bare tree (defaults to cwd)"),
    ("ls -R", "ls -R with no path (defaults to cwd)"),
]


@pytest.mark.parametrize("command,label", RECURSIVE_TOOL_BLOCK, ids=[label for _, label in RECURSIVE_TOOL_BLOCK])
def test_recursive_search_tools_reaching_clients_are_blocked(command, label):
    allowed, reason = gate.decide("Bash", {"command": command}, W, "build-only")
    assert not allowed and reason, label


RECURSIVE_TOOL_SCOPED_ALLOW = [
    ("rg foo project/", "rg scoped to project/"),
    ("grep -r foo project", "grep -r scoped to a bare directory name"),
    ("find project -name x", "find scoped to a bare directory name"),
    ("ls -R project/", "ls -R scoped to project/"),
]


@pytest.mark.parametrize("command,label", RECURSIVE_TOOL_SCOPED_ALLOW,
                         ids=[label for _, label in RECURSIVE_TOOL_SCOPED_ALLOW])
def test_recursive_search_tools_scoped_outside_clients_still_allowed(command, label):
    assert gate.decide("Bash", {"command": command}, W, "build-only") == (True, ""), label


def test_home_and_tilde_expand_before_path_resolution(monkeypatch):
    # W = Path("/w") is drive-relative on Windows (str(W) == "\\w"), which
    # pathlib does NOT consider is_absolute(); _resolve() then joins it onto
    # cwd instead of treating it as rooted, unlike a real Windows HOME (always
    # drive-qualified, e.g. C:\Users\...). Match gate.py's own realpath so the
    # HOME value here resolves onto the actual current drive, same as W does.
    home_value = str(W) if os.name != "nt" else os.path.realpath(str(W))
    monkeypatch.setenv("HOME", home_value)
    for command in ("cat ~/clients/acme/x.md", "cat $HOME/clients/acme/x.md", "cat ${HOME}/clients/acme/x.md"):
        allowed, reason = gate.decide("Bash", {"command": command}, W, "build-only")
        assert not allowed and reason, command


CLAUDE_DIR_AND_ROOT_GLOB_BLOCK = [
    ("rm -rf .claude", "rm -rf .claude"),
    ("mv .claude .claude.bak", "mv .claude aside"),
    ("mv .claude x", "mv .claude to x"),
    ("rm *.json", "rm with a bare workspace-root glob"),
    ("rm works*.json", "rm with a narrower workspace-root glob"),
]


@pytest.mark.parametrize("command,label", CLAUDE_DIR_AND_ROOT_GLOB_BLOCK,
                         ids=[label for _, label in CLAUDE_DIR_AND_ROOT_GLOB_BLOCK])
def test_claude_directory_and_root_glob_deletion_are_blocked(command, label):
    allowed, reason = gate.decide("Bash", {"command": command}, W, "build-only")
    assert not allowed and reason, label


def test_glob_deletion_of_a_subdirectory_still_allowed():
    # The workspace-root glob rule must not block an ordinary subdirectory clean.
    assert gate.decide("Bash", {"command": "rm project/tmp/*.log"}, W, "build-only") == (True, "")
    assert gate.decide("Bash", {"command": "rm -rf node_modules"}, W, "build-only") == (True, "")


def test_main_treats_missing_ai_access_key_as_full(tmp_path):
    # New Important: a missing key must mean full (the documented default),
    # not build-only, so an unwired workspace never silently gates org work.
    (tmp_path / "clients").mkdir()
    (tmp_path / "workspace.json").write_text(json.dumps(
        {"schema": "torque.workspace/1", "name": "Example", "profile": "generic"}), encoding="utf-8")
    payload = json.dumps({"cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": "sf org list"}})
    result = _run_gate(payload)
    assert result.returncode == 0


def test_main_fails_closed_when_workspace_json_is_deleted_but_marker_remains(tmp_path):
    # C3: rm workspace.json (or rm *.json) must not silently fall back to full
    # just because clients/ is still present. A real marker requires BOTH
    # clients/ AND .torque/templates.json (what `torque workspace init`
    # writes) so this uses a fake home outside tmp_path, per fix round 3.
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "elsewhere" / "w"
    workspace.mkdir(parents=True)
    (workspace / "clients").mkdir()
    (workspace / ".torque").mkdir()
    (workspace / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    payload = json.dumps({"cwd": str(workspace), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload, home=home)
    assert result.returncode == 2 and result.stderr.strip()
    # Build-only's own allowances still work under this fail-closed default.
    ok = json.dumps({"cwd": str(workspace), "tool_name": "Bash", "tool_input": {"command": "git status"}})
    assert _run_gate(ok, home=home).returncode == 0


def test_main_allows_ordinary_work_under_a_bare_torque_dir_in_home(tmp_path):
    # Fix round 3: older torque tooling can leave a bare .torque/ directly
    # under $HOME (prod-sessions, tokens, maintainer.grant, etc.), with no
    # templates.json and no clients/. That alone must never make every
    # project under home look like a workspace with a deleted config.
    home = tmp_path / "home"
    (home / ".torque").mkdir(parents=True)
    (home / ".torque" / "prod-sessions").mkdir()
    project = home / "Desktop" / "some-project"
    project.mkdir(parents=True)
    for command in ("sf org list", "torque context --workspace . --client acme"):
        payload = json.dumps({"cwd": str(project), "tool_name": "Bash", "tool_input": {"command": command}})
        result = _run_gate(payload, home=home)
        assert result.returncode == 0, f"{command!r} should be allowed: {result.stderr}"


def test_main_allows_a_plain_repo_with_only_clients_directory(tmp_path):
    # A bare clients/ folder alone (any plain repo can have one; it is not
    # exclusive to Torque) must not fail closed without .torque/templates.json.
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "elsewhere" / "repo"
    repo.mkdir(parents=True)
    (repo / "clients").mkdir()
    payload = json.dumps({"cwd": str(repo), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload, home=home)
    assert result.returncode == 0


def test_main_never_treats_home_itself_or_above_as_a_workspace(tmp_path):
    # Even a fully-formed marker directly at $HOME must not count: the search
    # stops before home (exclusive), never checking home or its ancestors.
    home = tmp_path / "home"
    (home / "clients").mkdir(parents=True)
    (home / ".torque").mkdir()
    (home / ".torque" / "templates.json").write_text("{}", encoding="utf-8")
    payload = json.dumps({"cwd": str(home), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload, home=home)
    assert result.returncode == 0


def test_main_allows_ordinary_work_with_no_workspace_marker_at_all(tmp_path):
    # No workspace.json AND no real clients/+.torque/templates.json marker:
    # genuinely outside any Torque workspace, so this stays full.
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "elsewhere" / "project"
    project.mkdir(parents=True)
    payload = json.dumps({"cwd": str(project), "tool_name": "Bash",
                          "tool_input": {"command": "sf org display --target-org prod"}})
    result = _run_gate(payload, home=home)
    assert result.returncode == 0
