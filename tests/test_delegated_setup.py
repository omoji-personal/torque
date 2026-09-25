import os

import pytest

from delegated_helpers import (ACCOUNT, CLEAN, FAKE_OWNER, ME, MODEL, ORGS, YES, base_workspace, changed,
                               snapshot, within)
from torque import consent, delegation, workspace as ws

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
