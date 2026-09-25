from collections import namedtuple
import json

import pytest

from torque import consent, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type")
YES = lambda: Presence(True, "")
NO = lambda: Presence(False, "needs a real terminal")
RESOLVE = {"acme-sbx": Org("00D000000000001AAA", "sandbox"), "acme-prod": Org("00D000000000002AAA", "production")}.get


@pytest.fixture
def firm(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic consultants")
    ws.add_client(root, "Acme")
    letter = tmp_path / "agreement.pdf"
    letter.write_bytes(b"%PDF synthetic agreement")
    return root, letter


def record(root, letter, **kw):
    args = dict(agreed_on="2026-09-30", evidence=letter, data_allowed=["metadata", "records"],
                orgs=["acme-sbx", "acme-prod"], suspend_contacts=["Named contact"], presence=YES, resolve=RESOLVE)
    args.update(kw)
    return consent.record_consent(root, "Acme", **args)


def test_record_is_pending_until_sign_off(firm):
    root, letter = firm
    item = record(root, letter)
    assert item["status"] == "pending"
    assert consent.consent_problems(consent.load_consent(root, "Acme")) == ["no second-reviewer sign-off"]
    consent.sign_off(root, "Acme", "Second reviewer", presence=YES)
    assert consent.consent_problems(consent.load_consent(root, "Acme")) == []


def test_orgs_are_resolved_live_and_unknown_org_refused(firm):
    root, letter = firm
    item = record(root, letter)
    assert consent.approved_org(item, "acme-prod") == {"alias": "acme-prod", "org_id_18": "00D000000000002AAA",
                                                        "kind": "production"}
    assert consent.approved_org(item, "other") is None
    with pytest.raises(ws.WorkspaceError, match="could not resolve"):
        record(root, letter, orgs=["missing"])


def test_evidence_is_captured_with_hash(firm):
    root, letter = firm
    item = record(root, letter)
    client = root / "clients" / "acme"
    assert (client / item["evidence"]["path"]).read_bytes() == letter.read_bytes()
    assert len(item["evidence"]["sha256"]) == 64


def test_requires_operator(firm):
    root, letter = firm
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        record(root, letter, presence=NO)
    assert consent.load_consent(root, "Acme") is None
    record(root, letter)
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        consent.sign_off(root, "Acme", "Second reviewer", presence=NO)
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        consent.suspend(root, "Acme", presence=NO)


def test_suspend_and_missing(firm):
    root, letter = firm
    assert consent.consent_problems(None) == ["no consent record"]
    record(root, letter)
    consent.sign_off(root, "Acme", "Second reviewer", presence=YES)
    consent.suspend(root, "Acme", presence=YES)
    assert "consent is suspended" in consent.consent_problems(consent.load_consent(root, "Acme"))
    consent.sign_off(root, "Acme", "Second reviewer", presence=YES)
    assert "consent is suspended" in consent.consent_problems(consent.load_consent(root, "Acme"))


def test_rerecording_replaces_and_needs_a_new_sign_off(firm):
    root, letter = firm
    record(root, letter)
    consent.sign_off(root, "Acme", "Second reviewer", presence=YES)
    record(root, letter, orgs=["acme-sbx"])
    item = consent.load_consent(root, "Acme")
    assert [o["alias"] for o in item["approved_orgs"]] == ["acme-sbx"]
    assert consent.consent_problems(item) == ["no second-reviewer sign-off"]


def test_unknown_data_class_refused(firm):
    root, letter = firm
    with pytest.raises(ws.WorkspaceError, match="data class"):
        record(root, letter, data_allowed=["everything"])


def test_bad_date_and_no_orgs_refused(firm):
    root, letter = firm
    with pytest.raises(ws.WorkspaceError, match="YYYY-MM-DD"):
        record(root, letter, agreed_on="30/09/2026")
    with pytest.raises(ws.WorkspaceError, match="org"):
        record(root, letter, orgs=[])


def test_hand_edited_record_is_not_usable(firm):
    root, letter = firm
    record(root, letter)
    consent.sign_off(root, "Acme", "Second reviewer", presence=YES)
    path = root / "clients" / "acme" / "consent.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["approved_orgs"] = "everything"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert consent.consent_problems(consent.load_consent(root, "Acme"))
    assert consent.approved_org(consent.load_consent(root, "Acme"), "everything") is None


def test_file_is_private(firm):
    import os
    root, letter = firm
    record(root, letter)
    if os.name != "nt":
        assert (root / "clients" / "acme" / "consent.json").stat().st_mode & 0o077 == 0
