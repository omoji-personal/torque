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
        delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", presence=NO, geteuid=lambda: ME)
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
    actor = delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, **CLEAN)
    assert actor.as_dict() == {"kind": "ai", "account": ACCOUNT, "uid": ME, "model": "m-1", "via": "delegate"}
    with pytest.raises(delegation.Refusal, match="model"):
        delegation.delegated_actor(root, "setup", model_id=None, require_tier2=False, **CLEAN)
    with pytest.raises(delegation.Refusal) as info:
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, getuid=lambda: ME + 1,
                                   **CLEAN)
    assert info.value.reason_class == "not-delegated"


def test_delegated_actor_needs_a_protected_workspace_file(root):
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=ROOT)
    (root / "workspace.json").chmod(0o666)
    with pytest.raises(delegation.Refusal, match="writable by no one else"):
        delegation.delegated_actor(root, "setup", model_id="m-1", require_tier2=False, **CLEAN)


def test_approver_role_needs_tier2(root):
    delegation.set_delegate(root, "approver", ACCOUNT, ME, "ai", geteuid=ROOT)
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    with pytest.raises(delegation.Refusal) as info:
        delegation.delegated_actor(root, "approver", model_id="m-1", **CLEAN)
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
