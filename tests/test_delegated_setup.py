import json
import os
import shutil

import pytest

from delegated_helpers import (ACCOUNT, CLEAN, FAKE_OWNER, ME, MODEL, ORGS, YES, base_workspace, changed,
                               snapshot, within)
from torque import cli, consent, delegation, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = base_workspace(tmp_path, monkeypatch)
    delegation.set_delegate(root, "setup", ACCOUNT, ME, "ai", geteuid=lambda: 0)
    return root


def switch(root, **extra):
    return ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME,
                            delegated=True, model_id=MODEL, root_owner=FAKE_OWNER, **{**CLEAN, **extra})


def test_delegated_mode_switch_records_the_ai_actor(root):
    before = snapshot(root)
    switch(root)
    config = ws.load_workspace(root)[1]
    assert (config["ai_access"], config["approval_verify"], config["approver_uid"]) == ("connected", "owner-uid", ME)
    assert config["ai_access_changed_by"] == {"kind": "ai", "account": ACCOUNT, "uid": ME, "model": MODEL,
                                              "via": "delegate"}
    assert within(changed(before, snapshot(root)), delegation.SETUP_WRITES["ai-access"])


def test_delegated_mode_switch_is_tier2_only(root):
    with pytest.raises(delegation.Refusal) as info:
        ws.set_ai_access(root, "connected", approval="required", verify="hmac", delegated=True, model_id=MODEL,
                         **CLEAN)
    assert info.value.reason_class == "tier-2-required"


def test_delegated_mode_switch_refuses_an_ai_session(root):
    with pytest.raises(delegation.Refusal) as info:
        switch(root, env={"CLAUDECODE": "1"})
    assert info.value.reason_class == "agent-session"


def test_owner_mode_switch_is_unchanged(root):
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME, presence=YES)
    assert "ai_access_changed_by" not in ws.load_workspace(root)[1]


def test_delegated_consent_record_and_sign_off(root, tmp_path):
    switch(root)
    letter = tmp_path / "agreement.pdf"
    letter.write_bytes(b"synthetic agreement")
    before = snapshot(root)
    item = consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-dev"],
                                  ["Contact"], resolve=ORGS.get, delegated=True, model_id=MODEL,
                                  root_owner=FAKE_OWNER, **CLEAN)
    assert item["recorded_by"] == ACCOUNT and item["recorded_by_actor"]["kind"] == "ai"
    assert within(changed(before, snapshot(root)), delegation.SETUP_WRITES["consent-record"])
    before = snapshot(root)
    signed = consent.sign_off(root, "Acme", "Two model reviewers", delegated=True, model_id=MODEL,
                              root_owner=FAKE_OWNER, **CLEAN)
    assert signed["status"] == "active" and signed["reviewer"]["signed_off_by"]["kind"] == "ai"
    assert within(changed(before, snapshot(root)), delegation.SETUP_WRITES["consent-sign-off"])
    assert not consent.consent_problems(consent.load_consent(root, "Acme"))


def test_owner_consent_path_is_unchanged(root, tmp_path):
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME, presence=YES)
    letter = tmp_path / "agreement.pdf"
    letter.write_bytes(b"synthetic agreement")
    item = consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-dev"], [],
                                  presence=YES, resolve=ORGS.get)
    assert "recorded_by_actor" not in item


def test_delegated_mode_switch_file_mode_is_0644(root):
    switch(root)
    assert (root / "workspace.json").stat().st_mode & 0o777 == 0o644


def test_delegated_consent_file_mode_is_0644(root, tmp_path):
    switch(root)
    letter = tmp_path / "agreement.pdf"
    letter.write_bytes(b"synthetic agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata", "records"], ["acme-dev"], ["Contact"],
                           resolve=ORGS.get, delegated=True, model_id=MODEL, root_owner=FAKE_OWNER, **CLEAN)
    assert (root / "clients" / "acme" / "consent.json").stat().st_mode & 0o777 == 0o644


def test_owner_path_file_modes_are_unchanged(root):
    """F12: the setup delegate's writes relax to 0644; the a15 owner path keeps 0600."""
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME, presence=YES)
    assert (root / "workspace.json").stat().st_mode & 0o777 == 0o600


# --- Fix round 1 ---


def test_delegated_mode_switch_relaxes_the_connected_rule_and_rules_dir_modes(root):
    """Fix round 1 finding 1: the rule file and .claude/rules (which already
    existed at 0700, from _materialize_workflows during init) relax to
    world-readable on the delegated path, so the agent account can load it."""
    switch(root)
    rule = root / ".claude" / "rules" / "production-approval.md"
    assert rule.stat().st_mode & 0o777 == 0o644
    assert (root / ".claude" / "rules").stat().st_mode & 0o777 == 0o755


def test_delegated_mode_switch_relaxes_claude_dir_only_when_created_by_this_step(root):
    """Fix round 1 finding 1: .claude already exists (created during init), so a
    delegated switch must not touch its mode. Remove it first to prove the
    "and .claude if created by this step" half of the fix: when this step is
    the one that creates .claude, it also relaxes that directory's mode."""
    shutil.rmtree(root / ".claude")
    switch(root)
    assert (root / ".claude").stat().st_mode & 0o777 == 0o755
    assert (root / ".claude" / "rules").stat().st_mode & 0o777 == 0o755


def test_owner_mode_switch_leaves_the_connected_rule_and_claude_dir_modes_alone(root):
    """Fix round 1 finding 1: the owner path is unchanged."""
    claude_mode_before = (root / ".claude").stat().st_mode & 0o777
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=ME, presence=YES)
    rule = root / ".claude" / "rules" / "production-approval.md"
    assert rule.stat().st_mode & 0o777 == 0o600
    assert (root / ".claude" / "rules").stat().st_mode & 0o777 == 0o700
    assert (root / ".claude").stat().st_mode & 0o777 == claude_mode_before


def test_cli_ai_access_delegated_refusal_is_exit3_with_json_reason_class(root, capsys):
    """Fix round 1 finding 2: the F37 contract, exercised through cli.main
    directly (not a mocked path): a delegated call that fails tier 2
    validation (--verify hmac) exits 3 and, with --json, prints
    {"reason_class": "tier-2-required", ...}. This refusal fires in
    ws.set_ai_access before delegation.delegated_actor ever runs (verify !=
    "owner-uid" is checked first), so it is reachable even though this test
    process itself is an actual Claude Code agent session."""
    code = cli.main(["workspace", "ai-access", "connected", "--approval", "required", "--verify", "hmac",
                     "--delegated", "--model-id", MODEL, "--path", str(root), "--json"])
    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason_class"] == "tier-2-required"


def test_cli_non_delegated_workspace_error_still_exits_2(tmp_path):
    """Fix round 1 finding 2: the new `except delegation.Refusal` clause in
    cli.main must not swallow or reclassify an ordinary WorkspaceError."""
    code = cli.main(["workspace", "ai-access", "connected", "--path", str(tmp_path / "missing-workspace")])
    assert code == 2


def test_model_id_without_delegated_is_a_usage_error(root, tmp_path):
    """Fix round 1 finding 3: --model-id (model_id=) means nothing without
    --delegated (delegated=True); it must refuse, not be silently ignored."""
    with pytest.raises(ws.WorkspaceError, match="--model-id"):
        ws.set_ai_access(root, "full", model_id=MODEL)
    with pytest.raises(ws.WorkspaceError, match="--model-id"):
        consent.record_consent(root, "Acme", "2026-09-30", tmp_path / "missing.pdf", ["metadata"], ["acme-dev"],
                               [], model_id=MODEL)
    with pytest.raises(ws.WorkspaceError, match="--model-id"):
        consent.sign_off(root, "Acme", "Reviewer", model_id=MODEL)


def test_cli_model_id_without_delegated_is_exit2(root):
    """Fix round 1 finding 3, CLI level."""
    code = cli.main(["workspace", "ai-access", "full", "--model-id", MODEL, "--path", str(root)])
    assert code == 2


def test_delegated_record_consent_requires_tier2(root, tmp_path):
    """Fix round 1 finding 5: a build-only workspace (switch() never ran)
    refuses a delegated consent record with reason class tier-2-required."""
    with pytest.raises(delegation.Refusal) as info:
        consent.record_consent(root, "Acme", "2026-09-30", tmp_path / "missing.pdf", ["metadata"], ["acme-dev"],
                               [], resolve=ORGS.get, delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               **CLEAN)
    assert info.value.reason_class == "tier-2-required"


def test_delegated_sign_off_requires_tier2(root):
    with pytest.raises(delegation.Refusal) as info:
        consent.sign_off(root, "Acme", "Reviewer", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                         **CLEAN)
    assert info.value.reason_class == "tier-2-required"


def test_delegated_record_consent_refuses_an_ai_session(root, tmp_path):
    """Fix round 1 finding 5: agent-session env markers refuse before anything
    about tier 2 or the delegate's identity is even checked."""
    with pytest.raises(delegation.Refusal) as info:
        consent.record_consent(root, "Acme", "2026-09-30", tmp_path / "missing.pdf", ["metadata"], ["acme-dev"],
                               [], resolve=ORGS.get, delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               env={"CLAUDECODE": "1"}, ancestors=lambda: [])
    assert info.value.reason_class == "agent-session"


def test_delegated_sign_off_refuses_an_ai_session(root):
    with pytest.raises(delegation.Refusal) as info:
        consent.sign_off(root, "Acme", "Reviewer", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                         env={"CLAUDECODE": "1"}, ancestors=lambda: [])
    assert info.value.reason_class == "agent-session"


def test_delegated_record_consent_refuses_a_different_delegate_uid(root, tmp_path):
    """Fix round 1 finding 5: the caller's real uid does not match the
    registered setup delegate's uid."""
    with pytest.raises(delegation.Refusal) as info:
        consent.record_consent(root, "Acme", "2026-09-30", tmp_path / "missing.pdf", ["metadata"], ["acme-dev"],
                               [], resolve=ORGS.get, delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                               getuid=lambda: ME + 1, **CLEAN)
    assert info.value.reason_class == "not-delegated"


def test_delegated_sign_off_refuses_a_different_delegate_uid(root):
    with pytest.raises(delegation.Refusal) as info:
        consent.sign_off(root, "Acme", "Reviewer", delegated=True, model_id=MODEL, root_owner=FAKE_OWNER,
                         getuid=lambda: ME + 1, **CLEAN)
    assert info.value.reason_class == "not-delegated"
