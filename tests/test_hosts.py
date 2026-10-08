"""The host registry: what Torque knows about Claude Code and Antigravity, in one place."""
import pytest

from torque import cli_approval, gate_antigravity, hosts, launch, presence


def test_each_host_has_its_binary_playbook_and_hook_file():
    assert [host.key for host in hosts.HOSTS] == ["claude", "antigravity"] and hosts.KEYS == ("claude", "antigravity")
    claude, agy = hosts.CLAUDE, hosts.ANTIGRAVITY
    assert (claude.name, claude.binary, claude.playbook, claude.hook_file) == (
        "Claude Code", "claude", ".claude", ".claude/settings.json")
    assert (agy.name, agy.binary, agy.playbook, agy.hook_file) == (
        "Antigravity", "agy", ".agents", ".agents/hooks.json")
    assert hosts.DEFAULT is claude
    with pytest.raises(Exception):
        claude.binary = "other"  # frozen


def test_markers_are_claude_codes_verified_ones_and_antigravitys_process_names():
    assert hosts.CLAUDE.env_markers == ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
    assert hosts.CLAUDE.process_markers == ("claude",)
    # Not verified yet, so none is listed: nothing is claimed that was not seen.
    assert hosts.ANTIGRAVITY.env_markers == ()
    assert hosts.ANTIGRAVITY.process_markers == ("agy", "antigravity")
    assert hosts.env_markers() == ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
    assert hosts.process_markers() == ("claude", "agy", "antigravity")


@pytest.mark.parametrize("name,key", [("claude", "claude"), ("antigravity", "antigravity"), ("agy", "antigravity"),
                                      ("gemini", "antigravity"), ("Antigravity", "antigravity"),
                                      (" AGY ", "antigravity"), ("CLAUDE", "claude")])
def test_resolve_accepts_keys_and_antigravitys_aliases(name, key):
    assert hosts.resolve(name).key == key


@pytest.mark.parametrize("name", ["", " ", "cursor", "claude-code", "codex", None, 5, ["claude"]])
def test_resolve_refuses_anything_else_and_says_what_is_accepted(name):
    with pytest.raises(hosts.UnknownHost, match="choose claude or antigravity"):
        hosts.resolve(name)
    assert issubclass(hosts.UnknownHost, ValueError)


def test_get_takes_only_an_exact_key():
    assert hosts.get("claude") is hosts.CLAUDE and hosts.get("antigravity") is hosts.ANTIGRAVITY
    for other in ("agy", "gemini", "Claude", "", None, 5):
        assert hosts.get(other) is None


def test_a_workspace_names_its_host_or_gets_claude_code():
    assert hosts.for_workspace({}) is hosts.CLAUDE
    assert hosts.for_workspace({"host": "claude"}) is hosts.CLAUDE
    assert hosts.for_workspace({"host": "antigravity"}) is hosts.ANTIGRAVITY
    assert hosts.for_workspace({"host": "agy"}) is hosts.ANTIGRAVITY
    for bad in ("", None, "cursor", 5):
        with pytest.raises(hosts.UnknownHost, match="workspace.json"):
            hosts.for_workspace({"host": bad})


def test_claude_codes_delegated_launch_lists_moved_unchanged():
    """The lists launch.py held before the registry existed, word for word."""
    assert hosts.CLAUDE.allowed_flags == ("-p", "--print", "--include-partial-messages", "--replay-user-messages",
                                          "--verbose", "--no-session-persistence")
    assert hosts.CLAUDE.allowed_value_options == (
        "--input-format", "--output-format", "--model", "--fallback-model", "--effort", "--append-system-prompt",
        "--max-budget-usd", "--json-schema", "--session-id", "--name")
    assert hosts.CLAUDE.pinned_options == (("--permission-mode", "default"),)
    assert hosts.CLAUDE.refused_env == ("CLAUDE_CODE_SIMPLE",)


def test_antigravitys_delegated_launch_list_is_the_minimum():
    assert hosts.ANTIGRAVITY.allowed_flags == ("-p", "--print")
    assert hosts.ANTIGRAVITY.allowed_value_options == ("--model", "--add-dir", "--output-format", "--print-timeout")
    assert hosts.ANTIGRAVITY.pinned_options == () and hosts.ANTIGRAVITY.refused_env == ()


def test_no_other_module_keeps_its_own_copy_of_a_hosts_names():
    assert presence.AGENT_ENV == hosts.env_markers()
    assert presence.AGENT_PROCESS_MARKERS == hosts.process_markers()
    for module, names in ((launch, ("ALLOWED_FLAGS", "ALLOWED_VALUE_OPTIONS", "REFUSED_ENV")),
                          (cli_approval, ("AGENT_BINARY",)), (gate_antigravity, ("_TOOL_HINT",))):
        for name in names:
            assert not hasattr(module, name), (module.__name__, name)
