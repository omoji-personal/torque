import json
import os

import pytest

from delegated_helpers import FAKE_OWNER, MODEL, ORGS, WRITE, control_owner, delegated_grant, delegated_workspace, \
    flow_request
from torque import approval, cli, delegation, presence, workspace as ws

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
KEY = "r1:ax-01:1:0123abcd"


def granted_files(root):
    return sorted((root / "clients/acme/approvals/granted").glob("apr-*.json"))


def test_repeat_with_the_same_key_returns_the_same_approval(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    first = delegated_grant(root, req, idempotency_key=KEY)
    second = delegated_grant(root, req, idempotency_key=KEY)
    assert first["id"] == second["id"] and len(granted_files(root)) == 1


def test_lookup_by_key(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root), idempotency_key=KEY)
    assert approval.find_by_idempotency_key(root, "Acme", KEY)["id"] == record["id"]
    assert approval.find_by_idempotency_key(root, "Acme", "r1:none:1:ffffffff") is None
    base = ["approval", "lookup", "--workspace", str(root), "--client", "Acme", "--json", "--idempotency-key"]
    assert cli.main([*base, KEY]) == 0 and json.loads(capsys.readouterr().out)["id"] == record["id"]
    assert cli.main([*base, "r1:none:1:ffffffff"]) == 1 and capsys.readouterr().out == ""


def test_a_reservation_without_its_approval_is_completed_with_the_same_id(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    dirs = approval._dirs(root, "Acme", create=False)
    approval._reserve_key(dirs, KEY, "apr-0123456789ab", req["id"])
    assert delegated_grant(root, req, idempotency_key=KEY)["id"] == "apr-0123456789ab"
    assert [p.name for p in granted_files(root)] == ["apr-0123456789ab.json"]


def test_a_key_belongs_to_one_request(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    delegated_grant(root, flow_request(root), idempotency_key=KEY)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, flow_request(root), idempotency_key=KEY)
    assert info.value.reason_class == "idempotency-conflict"


def test_key_format(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    with pytest.raises(ws.WorkspaceError, match="idempotency key"):
        delegated_grant(root, flow_request(root), idempotency_key="bad key")


# --- D7 brief item 5: an idempotent re-grant must never bypass a D5/D6 check. ---
# Only an exact repeat (same request, same reviewed hash, same payload digest)
# returns the existing grant; every other mismatch, or a fresh call that would be
# refused anyway, still refuses even though the key was used before.

def test_retry_with_a_different_reviewed_hash_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    first = delegated_grant(root, req, idempotency_key=KEY)
    bogus = "sha256:" + "0" * 64
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, idempotency_key=KEY, request_sha256=bogus)
    assert info.value.reason_class == "idempotency-conflict"
    assert [p.name for p in granted_files(root)] == [f"{first['id']}.json"]


def test_retry_with_a_changed_payload_digest_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    first = delegated_grant(root, req, idempotency_key=KEY)
    bogus = "sha256:" + "1" * 64
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, idempotency_key=KEY, payload_digest=bogus)
    assert info.value.reason_class == "idempotency-conflict"
    assert [p.name for p in granted_files(root)] == [f"{first['id']}.json"]


def test_idempotency_key_does_not_bypass_an_expired_request(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    late = approval._epoch(req["created_at"]) + approval.REQUEST_TTL + approval.SKEW + 1
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, idempotency_key=KEY, now=late)
    assert info.value.reason_class == "request-expired"
    assert granted_files(root) == []
    assert approval.find_by_idempotency_key(root, "Acme", KEY) is None


def test_idempotency_key_does_not_bypass_a_production_org(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch, orgs=("acme-dev", "acme-prod"))
    argv = [*WRITE[:-1], "acme-prod"]
    req = flow_request(root, argv, org="acme-prod")
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, idempotency_key=KEY)
    assert info.value.reason_class == "org-production-or-unknown"
    assert granted_files(root) == []
    assert approval.find_by_idempotency_key(root, "Acme", KEY) is None


def test_idempotency_key_does_not_bypass_an_agent_session(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, idempotency_key=KEY, env={"CLAUDECODE": "1"})
    assert info.value.reason_class == "agent-session"
    assert granted_files(root) == []
    assert approval.find_by_idempotency_key(root, "Acme", KEY) is None


def test_idempotency_key_does_not_bypass_r46_control_ownership(tmp_path, monkeypatch):
    """R46 keeps running for every delegated grant, including a repeat with the same
    idempotency key: an earlier grant under this key already exists, but once
    workspace.json belongs to the approver account, a retry must still refuse
    instead of the idempotency lookup silently returning that earlier grant."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    first = delegated_grant(root, req, idempotency_key=KEY)
    control_owner(monkeypatch, owner=os.getuid(), only="workspace.json")
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req, idempotency_key=KEY)
    assert info.value.reason_class == "not-delegated"
    assert [p.name for p in granted_files(root)] == [f"{first['id']}.json"]


def test_publish_grant_race_returns_the_winners_file(tmp_path, monkeypatch):
    """F49: two grants racing to publish the same reserved id (idempotency key)
    both try to write it; the loser reads the winner's file back instead of
    raising, and the file on disk is the winner's."""
    root = delegated_workspace(tmp_path, monkeypatch)
    dirs = approval._dirs(root, "Acme", create=False)
    winner = {"id": "apr-aaaaaaaaaaaa", "idempotency_key": KEY, "marker": "winner"}
    approval._publish_grant(root, "Acme", winner)
    loser = {"id": "apr-aaaaaaaaaaaa", "idempotency_key": KEY, "marker": "loser"}
    result = approval._publish_grant(root, "Acme", loser)
    assert result["marker"] == "winner"
    stored = json.loads((dirs["granted"] / "apr-aaaaaaaaaaaa.json").read_text())
    assert stored["marker"] == "winner"


def test_publish_grant_reraises_a_genuine_collision_without_an_idempotency_key(tmp_path, monkeypatch):
    """A collision that is not the same idempotent grant (no idempotency_key, or a
    different one) is a real bug, not a race to paper over: it must raise."""
    root = delegated_workspace(tmp_path, monkeypatch)
    approval._publish_grant(root, "Acme", {"id": "apr-bbbbbbbbbbbb", "idempotency_key": None, "marker": "first"})
    with pytest.raises(ws.WorkspaceError):
        approval._publish_grant(root, "Acme", {"id": "apr-bbbbbbbbbbbb", "idempotency_key": None, "marker": "second"})


def _cli_grant(root, req, view, *extra):
    return cli.main(["approval", "grant", req["id"], "--workspace", str(root), "--client", "Acme", "--delegated",
                     "--model-id", MODEL, "--request-sha256", view["request_sha256"], "--payload-digest",
                     view["payload"]["digest"] or "none", *extra])


def test_cli_grant_idempotency_key_returns_the_same_record(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    monkeypatch.setattr(presence, "agent_reason", lambda env=None, ancestors=None: "")
    proof = delegation._delegated_proof
    monkeypatch.setattr(delegation, "_delegated_proof",
                        lambda *a, **k: proof(*a, **{**k, "root_owner": FAKE_OWNER}))
    monkeypatch.setattr(approval, "_resolver", lambda resolve: resolve or ORGS.get)
    capsys.readouterr()
    assert _cli_grant(root, req, view, "--idempotency-key", KEY, "--json") == 0
    first = json.loads(capsys.readouterr().out)
    assert _cli_grant(root, req, view, "--idempotency-key", KEY, "--json") == 0
    second = json.loads(capsys.readouterr().out)
    assert first["id"] == second["id"] and len(granted_files(root)) == 1
