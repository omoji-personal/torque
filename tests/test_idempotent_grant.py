import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from delegated_helpers import FAKE_OWNER, MODEL, ORGS, WRITE, YES, as_agent, control_owner, delegated_grant, \
    delegated_workspace, flow_request
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


# --- Fix round 1, finding 1: F28's shared _approver_owned must not change the two ---
# a15 gate messages; _problem still emits each verbatim (git show dea2041:src/torque/approval.py).

def test_gate_owner_uid_file_message_matches_a15_exactly(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    path = root / "clients/acme/approvals/granted" / f"{record['id']}.json"
    config = {**ws.load_workspace(root)[1], "approver_uid": os.getuid() + 1}
    assert approval._problem(record, path, config, "acme", approval._epoch(record["granted_at"])) == \
        "the approval file's owner is not the approver account, or others can write it"


def test_gate_owner_uid_folder_message_matches_a15_exactly(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root))
    path = root / "clients/acme/approvals/granted" / f"{record['id']}.json"
    config = ws.load_workspace(root)[1]
    real_stat = Path.stat

    def fake_stat(self, *a, **k):
        result = real_stat(self, *a, **k)
        if self == path.parent:
            return SimpleNamespace(st_mode=result.st_mode, st_uid=config["approver_uid"] + 1)
        return result
    monkeypatch.setattr(Path, "stat", fake_stat)
    as_agent(monkeypatch)
    assert approval._problem(record, path, config, "acme", approval._epoch(record["granted_at"])) == \
        "approvals/granted must be owned by the approver account and writable only by it"


# --- Fix round 1, finding 2: idempotency_key is delegated-only, beside --model-id. ---

def test_owner_grant_rejects_idempotency_key(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    req = flow_request(root)
    with pytest.raises(ws.WorkspaceError,
                       match=r"--idempotency-key applies only to a delegated grant \(--delegated\)"):
        approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True, out=io.StringIO(),
                       resolve=ORGS.get, idempotency_key=KEY)


def test_cli_grant_idempotency_key_without_delegated_exits_2(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch, kind="human")
    req = flow_request(root)
    assert cli.main(["approval", "grant", req["id"], "--workspace", str(root), "--client", "Acme",
                     "--idempotency-key", KEY]) == 2
    assert "--idempotency-key" in capsys.readouterr().err


# V1 invariant gaps (a16 closure), invariant 7: one approval per key.

def test_a_key_reserved_for_another_request_is_refused_before_publish(tmp_path, monkeypatch):
    """G2: a key reserved for request A whose grant was never published cannot
    serve request B. The lookup finds no completed grant, so only the
    reservation's own request_id stops B from taking A's approval id."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req_a, req_b = flow_request(root), flow_request(root)
    dirs = approval._dirs(root, "Acme", create=False)
    approval._reserve_key(dirs, KEY, "apr-aaaaaaaaaaaa", req_a["id"])
    with pytest.raises(delegation.Refusal) as info:
        delegated_grant(root, req_b, idempotency_key=KEY)
    assert info.value.reason_class == "idempotency-conflict"
    assert granted_files(root) == []
    assert not (dirs["granted"] / f"decision-{req_b['id']}.json").exists()


def test_lookup_needs_the_approval_to_carry_the_same_key(tmp_path, monkeypatch):
    """G8: a reservation that points at an approval granted under another key
    is not a grant for this key."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    other = "r1:ax-02:1:4567cdef"
    record = delegated_grant(root, req, idempotency_key=other)
    approval._reserve_key(approval._dirs(root, "Acme", create=False), KEY, record["id"], req["id"])
    assert approval.find_by_idempotency_key(root, "Acme", other)["id"] == record["id"]
    assert approval.find_by_idempotency_key(root, "Acme", KEY) is None


def test_lookup_ignores_a_reservation_others_can_write(tmp_path, monkeypatch):
    """G8: the reservation file must be the approver account's alone; a marker
    others can write is not trusted, even when its approval file is."""
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, flow_request(root), idempotency_key=KEY)
    assert approval.find_by_idempotency_key(root, "Acme", KEY)["id"] == record["id"]
    approval._key_marker(approval._dirs(root, "Acme", create=False), KEY).chmod(0o664)
    assert approval.find_by_idempotency_key(root, "Acme", KEY) is None


# --- V2 I2: an idempotent retry revalidates the current request, payload, consent ---
# and org exactly like a fresh grant before it returns the earlier approval, and the
# stored approval it returns is validated in full (not only its owner and key).

def _granted_once(root):
    req = flow_request(root)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    first = delegated_grant(root, req, idempotency_key=KEY)
    return req, view, first


def _retry(root, req, view, **extra):
    """The grant call itself, with the earlier view's review (no fresh view)."""
    kwargs = {"delegated": True, "model_id": MODEL, "request_sha256": view["request_sha256"],
              "payload_digest": view["payload"]["digest"] or "none", "out": io.StringIO(),
              "resolve": ORGS.get, "root_owner": FAKE_OWNER, "control_stat": approval._control_stat,
              "env": {}, "ancestors": lambda: [], "idempotency_key": KEY, **extra}
    return approval.grant(root, "Acme", req["id"], **kwargs)


def test_retry_after_the_request_file_changed_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req, view, first = _granted_once(root)
    path = root / "clients/acme/approvals/requests" / f"{req['id']}.json"
    path.write_text(json.dumps(json.loads(path.read_text()), indent=4), encoding="utf-8")
    with pytest.raises(delegation.Refusal) as info:
        _retry(root, req, view)
    assert info.value.reason_class == "request-changed"


def test_retry_after_the_payload_changed_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req, view, first = _granted_once(root)
    flow = root / "force-app/main/default/flows/Case_Escalation.flow-meta.xml"
    flow.write_text("<Flow>v3</Flow>", encoding="utf-8")
    with pytest.raises(delegation.Refusal) as info:
        _retry(root, req, view)
    assert info.value.reason_class == "payload-changed"


def test_retry_after_consent_became_unusable_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req, view, first = _granted_once(root)

    def unusable(workspace, client):
        raise ws.WorkspaceError("consent was withdrawn")
    monkeypatch.setattr(approval, "_usable_consent", unusable)
    with pytest.raises(delegation.Refusal) as info:
        _retry(root, req, view)
    assert info.value.reason_class == "consent-unusable"


def test_retry_when_the_org_now_reads_as_production_is_refused(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req, view, first = _granted_once(root)
    dev = ORGS["acme-dev"]
    now_prod = {"acme-dev": dev._replace(detected_org_type="production", is_production=True)}
    with pytest.raises(delegation.Refusal) as info:
        _retry(root, req, view, resolve=now_prod.get)
    assert info.value.reason_class == "org-production-or-unknown"


def test_an_exact_retry_rechecks_the_review_before_returning(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req, view, first = _granted_once(root)
    calls = []
    real = approval._check_review
    monkeypatch.setattr(approval, "_check_review", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert _retry(root, req, view)["id"] == first["id"] and calls


def _stored(root, record):
    return root / "clients/acme/approvals/granted" / f"{record['id']}.json"


@pytest.mark.parametrize("field, value", [("approver_kind", "robot"), ("delegated", "yes"),
                                          ("schema", "other/1"), ("client", "other"),
                                          ("request_id", "req-000000000000"), ("org_kind", "production"),
                                          ("expires_at", "not a time")])
def test_lookup_refuses_an_invalid_stored_approval(tmp_path, monkeypatch, field, value):
    root = delegated_workspace(tmp_path, monkeypatch)
    req, view, first = _granted_once(root)
    path = _stored(root, first)
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    assert approval.find_by_idempotency_key(root, "Acme", KEY) is None


def test_retry_never_returns_an_invalid_stored_approval(tmp_path, monkeypatch):
    """A lookup that rejects the stored record must not let the publish-race path
    hand the same tampered file back as the retry's result."""
    root = delegated_workspace(tmp_path, monkeypatch)
    req, view, first = _granted_once(root)
    path = _stored(root, first)
    record = json.loads(path.read_text())
    record["approver_kind"] = "robot"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises((delegation.Refusal, ws.WorkspaceError)):
        _retry(root, req, view)
