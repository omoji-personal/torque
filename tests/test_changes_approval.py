import json

import pytest

from torque import changes, workspace as ws


@pytest.fixture
def change(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic consultants")
    ws.add_client(root, "Acme")
    item = changes.create_change(root, "Acme", "Escalation flow", "Cases escalate", ["Escalates"], "acme-prod")
    return root, item["id"]


def test_approval_events_round_trip_and_render(change):
    root, cid = change
    changes.append_approval_event(root, "Acme", cid, "approval_request",
                                  {"request_id": "req-000000000001", "command": "sf project deploy start -o acme-prod",
                                   "org_alias": "acme-prod", "org_kind": "production"})
    changes.append_approval_event(root, "Acme", cid, "approval_grant",
                                  {"request_id": "req-000000000001", "approval_id": "apr-000000000001",
                                   "approver": "consultant", "expires_at": "2026-10-01T15:19:05+00:00"})
    kinds = [e["kind"] for e in changes.get_change(root, "Acme", cid)["events"]]
    assert kinds[-2:] == ["approval_request", "approval_grant"]
    text = changes.render_change(root, "Acme", cid)
    assert "## Approvals" in text and "apr-000000000001" in text and "acme-prod" in text


def test_unknown_approval_kind_refused(change):
    root, cid = change
    with pytest.raises(ws.WorkspaceError):
        changes.append_approval_event(root, "Acme", cid, "approval_forged", {})


def test_fields_cannot_override_event_identity(change):
    root, cid = change
    event = changes.append_approval_event(root, "Acme", cid, "approval_deny",
                                          {"request_id": "req-000000000001", "reason": "not now",
                                           "kind": "check", "basis": "operator_reported", "id": "x"})
    assert event["kind"] == "approval_deny" and event["basis"] == "torque_approval"
    changes.get_change(root, "Acme", cid)


def test_approval_event_with_wrong_basis_is_invalid(change):
    root, cid = change
    event = changes.append_approval_event(root, "Acme", cid, "approval_request", {"request_id": "req-000000000001"})
    path = root / "clients" / "acme" / "changes" / cid / "events" / f"{event['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["basis"] = "operator_reported"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ws.WorkspaceError):
        changes.get_change(root, "Acme", cid)
