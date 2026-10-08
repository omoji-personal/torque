"""Launch records name their host, bind only under that host's hook, and a
delegated launch passes each host only the options on that host's own list."""
import json
import os
from pathlib import Path

import pytest

from torque import hosts, launch, workspace as ws
from torque.presence import Presence

YES = lambda: Presence(True, "")


@pytest.fixture
def root(tmp_path):
    made = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(made, "Acme")
    ws.set_ai_access(made, "connected", approval="required", presence=YES)
    return Path(os.path.realpath(made))


def env_for(record, **extra):
    return {"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": record["id"], **extra}


def stored(root, record):
    return root / "clients" / "acme" / "approvals" / "consumed" / f"{record['id']}.launch"


def test_a_record_names_the_host_it_was_written_for(root):
    plain = launch.write_launch_record(root, "Acme", "human")
    agy = launch.write_launch_record(root, "Acme", "human", host="antigravity")
    probe = launch.write_launch_record(root, "Acme", "probe", host=hosts.ANTIGRAVITY)
    assert (plain["host"], agy["host"], probe["host"]) == ("claude", "antigravity", "antigravity")
    assert plain["schema"] == agy["schema"] == launch.LAUNCH_SCHEMA == "torque.launch/1"
    assert json.loads(stored(root, agy).read_text(encoding="utf-8")) == agy


@pytest.mark.parametrize("host", ["agy", "gemini", "Claude", "", None, 5])
def test_a_record_is_written_only_for_a_host_key(root, host):
    with pytest.raises(ws.WorkspaceError, match="claude or antigravity"):
        launch.write_launch_record(root, "Acme", "human", host=host)
    assert not list((root / "clients" / "acme").rglob("*.launch"))


def test_a_record_binds_only_under_the_hook_of_its_host(root):
    claude = launch.write_launch_record(root, "Acme", "human")
    agy = launch.write_launch_record(root, "Acme", "human", host="antigravity")
    stated = {hosts.HOOK_HOST_ENV: "antigravity"}
    # Claude Code's hook sets nothing: an unset (or empty) variable is Claude Code.
    assert launch.verify_launch(root, env_for(claude)) == ("acme", "")
    assert launch.verify_launch(root, env_for(claude, **{hosts.HOOK_HOST_ENV: ""})) == ("acme", "")
    assert launch.verify_launch(root, env_for(claude, **{hosts.HOOK_HOST_ENV: "claude"})) == ("acme", "")
    assert launch.verify_launch(root, env_for(agy, **stated)) == ("acme", "")
    assert launch.verify_launch(root, env_for(agy)) == (
        None, "the launch record was written for Antigravity, not for Claude Code")
    assert launch.verify_launch(root, env_for(claude, **stated)) == (
        None, "the launch record was written for Claude Code, not for Antigravity")
    assert "TORQUE_CLIENT" not in launch.bound_env(env_for(claude, **stated), root)
    assert launch.bound_env(env_for(agy, **stated), root)["TORQUE_CLIENT"] == "acme"


def test_a_record_from_before_the_field_is_claude_codes(root):
    record = launch.write_launch_record(root, "Acme", "human")
    older = {key: value for key, value in record.items() if key != "host"}
    stored(root, record).write_text(json.dumps(older), encoding="utf-8")
    assert launch.verify_launch(root, env_for(record)) == ("acme", "")
    slug, why = launch.verify_launch(root, env_for(record, **{hosts.HOOK_HOST_ENV: "antigravity"}))
    assert slug is None and "written for Claude Code" in why


@pytest.mark.parametrize("host", ["agy", "gemini", "Antigravity", "cursor", "", None, 5, ["claude"]])
def test_a_record_with_a_host_this_version_does_not_know_never_binds(root, host):
    record = launch.write_launch_record(root, "Acme", "human")
    stored(root, record).write_text(json.dumps({**record, "host": host}), encoding="utf-8")
    for stated in ({}, {hosts.HOOK_HOST_ENV: "antigravity"}):
        slug, why = launch.verify_launch(root, env_for(record, **stated))
        assert slug is None and why, (host, stated)


@pytest.mark.parametrize("stated", ["agy", "gemini", "Antigravity", "cursor", " claude"])
def test_a_hook_that_names_an_unknown_host_binds_nothing(root, stated):
    for host in hosts.KEYS:
        record = launch.write_launch_record(root, "Acme", "human", host=host)
        assert launch.verify_launch(root, env_for(record, **{hosts.HOOK_HOST_ENV: stated})) == (
            None, "the hook names a host this version does not know")


def test_the_session_record_is_found_only_in_the_records_own_workspace(root, tmp_path):
    record = launch.write_launch_record(root, "Acme", "human", host="antigravity")
    env = env_for(record)
    assert launch.session_record(root, env) == record == launch.session_record(str(root), env)
    other = Path(os.path.realpath(ws.init_workspace(tmp_path / "other", "Other")))
    ws.add_client(other, "Acme")
    for folder in (other, root / "clients", root / "clients" / "acme", tmp_path, tmp_path / "missing"):
        assert launch.session_record(folder, env) is None, folder
    # A copy of the record in another workspace still names the first one.
    copy = other / "clients" / "acme" / "approvals" / "consumed"
    copy.mkdir(parents=True)
    (copy / f"{record['id']}.launch").write_bytes(stored(root, record).read_bytes())
    assert launch.session_record(other, env) is None
    for bad in ({}, {"TORQUE_LAUNCH": record["id"]}, {"TORQUE_CLIENT": "acme"},
                {"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": "../../x"},
                {"TORQUE_CLIENT": "beta", "TORQUE_LAUNCH": record["id"]},
                {"TORQUE_CLIENT": "../acme", "TORQUE_LAUNCH": record["id"]}):
        assert launch.session_record(root, bad) is None, bad
    for content in (b"", b"not json", b"[]"):
        stored(root, record).write_bytes(content)
        assert launch.session_record(root, env) is None, content


AGY_PASSES = [["-p", "summarize the open pull requests"], ["--print", "hello"], ["-p", "--model", "m"],
              ["--model=m"], ["--add-dir", "/work/shared"], ["--add-dir=/work/shared"], ["--output-format", "json"],
              ["--print-timeout", "600"], ["--print-timeout=600"], ["-p", "--", "plain prompt"], []]
AGY_REFUSED = [["--dangerously-skip-permissions"], ["-p", "--dangerously-skip-permissions"], ["--verbose"],
               ["--permission-mode", "default"], ["--permission-mode=default"], ["--input-format", "stream-json"],
               ["--settings", "s.json"], ["--some-future-flag"], ["-x"], ["-"], ["--print=hello"], ["--model"],
               ["--model", "--add-dir"], ["--add-dir"], ["--add-dir", "-x"], ["--", "--model"],
               ["prompt", "--", "-x"]]


@pytest.mark.parametrize("words", AGY_PASSES)
def test_a_delegated_antigravity_launch_passes_its_short_list(words):
    assert launch.launch_flag_problem(words, hosts.ANTIGRAVITY) == ""


@pytest.mark.parametrize("words", AGY_REFUSED)
def test_a_delegated_antigravity_launch_refuses_everything_else(words):
    assert launch.launch_flag_problem(words, hosts.ANTIGRAVITY)


def test_each_hosts_list_is_its_own():
    # Claude Code's list is the default and is unchanged: it never passed --add-dir.
    assert launch.launch_flag_problem(["--add-dir", "/tmp"]) == "--add-dir"
    assert launch.launch_flag_problem(["--add-dir", "/tmp"], hosts.CLAUDE) == "--add-dir"
    assert launch.launch_flag_problem(["--verbose", "--permission-mode", "default"]) == ""
    assert launch.launch_flag_problem(["--permission-mode", "plan"]) == "--permission-mode plan"
    assert launch.launch_flag_problem(["--verbose"], hosts.ANTIGRAVITY) == "--verbose"
    assert launch.launch_env_problem({"CLAUDE_CODE_SIMPLE": "1"}) == "CLAUDE_CODE_SIMPLE"
    assert launch.launch_env_problem({"CLAUDE_CODE_SIMPLE": "1"}, hosts.CLAUDE) == "CLAUDE_CODE_SIMPLE"
    assert launch.launch_env_problem({"CLAUDE_CODE_SIMPLE": "1"}, hosts.ANTIGRAVITY) == ""
