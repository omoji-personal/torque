import json
import os
from pathlib import Path

import pytest

from torque import cli, delegation, presence, workspace as ws
from torque.presence import Presence

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="delegation needs tier 2 (POSIX)")
YES = lambda: Presence(True, "")
NO = lambda: Presence(False, "this needs a real terminal")
ME = os.getuid() if hasattr(os, "getuid") else -1
if hasattr(os, "getuid"):
    import pwd
    ACCOUNT = pwd.getpwuid(ME).pw_name
CLEAN = {"env": {}, "ancestors": lambda: []}
ROOT = lambda: 0
# A fake workspace-directory owner distinct from ME/ACCOUNT, for tests whose
# target behavior sits behind the R41 "delegate != workspace owner" check
# (the fixture's real directory owner is ME, the test process's own uid, so
# without this override every such test would trip that check first).
FAKE_OWNER = lambda p: ME + 1


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return Path(os.path.realpath(ws.init_workspace(tmp_path / "firm", "Firm")))


def test_agent_reason_keeps_operator_present_messages():
    assert presence.agent_reason(env={"CLAUDECODE": "1"}, ancestors=lambda: []).startswith(
        "this looks like an agent session")
    assert "(claude)" in presence.agent_reason(env={}, ancestors=lambda: [(10, "/usr/local/bin/claude")])
    assert presence.agent_reason(env={}, ancestors=lambda: [(-1, "<unknown>")]) == \
        "could not read the process ancestry; refusing"
    assert presence.agent_reason(env={}, ancestors=lambda: [(10, "launchd")]) == ""


def test_set_delegate_needs_presence_or_an_administrator(root):
    with pytest.raises(ws.WorkspaceError, match="real terminal"):
        delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", presence=NO, geteuid=lambda: ME,
                                getuid=lambda: ME + 1)
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)
    config = ws.load_workspace(root)[1]
    assert config["delegates"]["approver"] == {"account": ACCOUNT, "uid": ME, "kind": "ai"}
    assert config["delegates_changed_by"]["via"] == "administrator"


def test_set_delegate_validates_role_kind_and_uid(root):
    with pytest.raises(ws.WorkspaceError, match="has uid"):
        delegation.set_delegate(root, "setup", ACCOUNT, ME + 1, "ai", geteuid=ROOT)
    with pytest.raises(ws.WorkspaceError, match="role"):
        delegation.set_delegate(root, "boss", ACCOUNT, ME, "ai", geteuid=ROOT)
    with pytest.raises(ws.WorkspaceError, match="kind"):
        delegation.set_delegate(root, "setup", ACCOUNT, ME, "robot", geteuid=ROOT)


def test_setup_delegate_is_separate_from_the_grant_approver(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    config = ws.load_workspace(root)[1]
    assert delegation.delegate_for(config, "setup") == {"account": ACCOUNT, "uid": ME, "kind": "ai"}
    assert delegation.delegate_for(config, "approver") is None


def test_delegated_actor_refuses_an_ai_session(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    for env, ancestors in (({"CLAUDE_CODE_ENTRYPOINT": "cli"}, lambda: []),
                           ({}, lambda: [(42, "claude")])):
        with pytest.raises(delegation.Refusal) as info:
            delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, env=env,
                                       ancestors=ancestors)
        assert info.value.reason_class == "agent-session"


def test_delegated_actor_identity_and_model(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    actor = delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, root_owner=FAKE_OWNER,
                                       **CLEAN)
    assert actor.as_dict() == {"kind": "ai", "account": ACCOUNT, "uid": ME, "model": "m-1", "via": "delegate"}
    with pytest.raises(delegation.Refusal, match="model"):
        delegation.delegated_actor(root, "setup", model_id=None, require_tier2=False, root_owner=FAKE_OWNER,
                                   **CLEAN)
    with pytest.raises(delegation.Refusal) as info:
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, getuid=lambda: ME + 1,
                                   root_owner=FAKE_OWNER, **CLEAN)
    assert info.value.reason_class == "not-delegated"


def test_delegated_actor_needs_a_protected_workspace_file(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    (root / "workspace.json").chmod(0o666)
    with pytest.raises(delegation.Refusal, match="writable by no one else"):
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, root_owner=FAKE_OWNER,
                                   **CLEAN)


def test_approver_role_needs_tier2(root):
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    with pytest.raises(delegation.Refusal) as info:
        delegation.delegated_actor(root, "approver", model_id="m-1", root_owner=FAKE_OWNER, **CLEAN)
    assert info.value.reason_class == "tier-2-required"


def test_cli_workspace_delegate(root, monkeypatch, capsys):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert cli.main(["workspace", "delegate", "--path", str(root), "--role", "approver", "--account", ACCOUNT,
                     "--uid", str(ME), "--kind", "ai", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["delegates"]["approver"]["kind"] == "ai"


def test_workspace_delegate_is_admin_for_the_agent():
    from torque import connected_routes as cr
    routes = cr.classify("Bash", {"command": "torque workspace delegate --path . --role approver --account x "
                                             "--uid 1 --kind ai"})
    assert [r.kind for r in routes] == ["admin"]


# --- Fix round 1 ---

# Important 1 (delegation.py:277-289 load-then-stat race): workspace.json's
# owner/mode must be checked from the very same read as its content, not a
# separate later stat call an attacker could race past by swapping the file
# in between.

def test_delegated_actor_stat_is_immune_to_a_file_swap_mid_read(root, monkeypatch):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    cfg = root / "workspace.json"
    cfg.chmod(0o666)  # insecure at open/fstat time
    real_loads = json.loads

    def swap_then_parse(text, *a, **kw):
        # Simulate an attacker (or an unrelated writer) fixing the file's mode
        # in the gap between "content read" and a naive later re-check; the
        # fixed implementation must not see this because it already captured
        # the descriptor's stat before this point.
        cfg.chmod(0o600)
        return real_loads(text, *a, **kw)

    monkeypatch.setattr(delegation.json, "loads", swap_then_parse)
    with pytest.raises(delegation.Refusal, match="writable by no one else"):
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, root_owner=FAKE_OWNER,
                                   **CLEAN)


def test_delegated_actor_refuses_a_symlinked_workspace_json(root, tmp_path):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    cfg = root / "workspace.json"
    decoy = tmp_path / "decoy-workspace.json"
    decoy.write_bytes(cfg.read_bytes())
    cfg.unlink()
    cfg.symlink_to(decoy)
    with pytest.raises(delegation.Refusal, match="symlink"):
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, root_owner=FAKE_OWNER,
                                   **CLEAN)


# Important 2 / controller ruling R41: the delegate must be a separate OS
# account from the one naming it (set_delegate) and from the account that
# owns the workspace directory (delegated_actor). An administrator (euid 0)
# is exempt since it is provisioning the workspace, not acting as either.

def test_set_delegate_refuses_naming_the_caller_as_delegate(root):
    with pytest.raises(delegation.Refusal, match="separate") as info:
        delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", presence=YES, confirm=lambda: True,
                                geteuid=lambda: ME + 1, getuid=lambda: ME)
    assert info.value.reason_class == "not-delegated"
    # the administrator path is exempt from the same-account rule
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)
    config = ws.load_workspace(root)[1]
    assert config["delegates"]["approver"]["uid"] == ME


def test_delegated_actor_refuses_when_the_caller_owns_the_workspace_directory(root):
    # No root_owner override: the fixture's real directory owner is ME, the
    # same account under which this test process (standing in for the AI
    # session) runs, which is exactly the case R41 must catch.
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    with pytest.raises(delegation.Refusal, match="separate") as info:
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, **CLEAN)
    assert info.value.reason_class == "not-delegated"


# Minor 1: a malformed, unreadable or missing workspace.json at the delegated
# check must surface as Refusal("not-delegated", ...), not a bare
# WorkspaceError or OSError.

def test_delegated_actor_refuses_a_malformed_workspace_json(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    (root / "workspace.json").write_text("not json", encoding="utf-8")
    with pytest.raises(delegation.Refusal) as info:
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, **CLEAN)
    assert info.value.reason_class == "not-delegated"


def test_delegated_actor_refuses_a_missing_workspace_json(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    (root / "workspace.json").unlink()
    with pytest.raises(delegation.Refusal) as info:
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, **CLEAN)
    assert info.value.reason_class == "not-delegated"


# Fix round 2, Important: invalid UTF-8 bytes in workspace.json must also
# surface as Refusal("not-delegated", ...), not a raw UnicodeDecodeError.

def test_delegated_actor_refuses_invalid_utf8_workspace_json(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    (root / "workspace.json").write_bytes(b"\xff\xfe\x00not valid utf-8 \x80\x81")
    with pytest.raises(delegation.Refusal) as info:
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, **CLEAN)
    assert info.value.reason_class == "not-delegated"


# Minor 2: an existing but non-dict "delegates" value fails closed with
# WorkspaceError, not a bare TypeError/ValueError from dict(...).

def test_set_delegate_rejects_a_non_dict_delegates_value(root):
    root_path, config = ws.load_workspace(root)
    config["delegates"] = ["not", "a", "dict"]
    ws._atomic_replace_text(root_path / ws.CONFIG, json.dumps(config, indent=2) + "\n")
    with pytest.raises(ws.WorkspaceError):
        delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)


# --- delegation.delegated_tier2 (F26): the shared "tier 2 with a named-delegate
# approver" predicate that D2/D3, D5, D9 and D11 reuse instead of each
# repeating config.get("approval_verify") == "owner-uid" and the matching
# approver delegate.

def test_delegated_tier2_needs_owner_uid_verify_and_a_matching_approver_delegate(root):
    config = ws.load_workspace(root)[1]
    assert delegation.delegated_tier2(config) is False
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME, presence=YES)
    config = ws.load_workspace(root)[1]
    assert delegation.delegated_tier2(config) is False
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)
    config = ws.load_workspace(root)[1]
    assert delegation.delegated_tier2(config) is True


def test_delegated_tier2_false_when_the_delegate_is_not_the_configured_approver(root):
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME + 1, presence=YES)
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)
    config = ws.load_workspace(root)[1]
    assert delegation.delegated_tier2(config) is False


def test_delegated_tier2_false_for_hmac_verification(root):
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)
    config = ws.load_workspace(root)[1]
    assert delegation.delegated_tier2(config) is False
