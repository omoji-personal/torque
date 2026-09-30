"""Initiatives keep change records; client-only operations are refused."""
import pytest

from torque import changes
from torque import engagements as eng
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    eng.add_initiative(root, "Plan")
    return root


def test_initiative_change_lifecycle(root):
    item = changes.create_change(root, "Plan", "Pilot VM", "Work stays off laptops", ["Controls pass"],
                                 engagement_kind="initiative")
    assert item["initiative"] == "plan" and "client" not in item
    changes.add_note(root, "Plan", item["id"], "use one team VM", "decision", engagement_kind="initiative")
    detail = changes.get_change(root, "Plan", item["id"], engagement_kind="initiative")
    assert detail["events"][-1]["initiative"] == "plan"
    assert [c["id"] for c in changes.list_changes(root, "Plan", engagement_kind="initiative")] == [item["id"]]
    assert changes.list_changes(root, "Alpha") == []
    assert "Pilot VM" in changes.render_change(root, "Plan", item["id"], engagement_kind="initiative")


def test_verify_deploy_and_approvals_are_client_only(root):
    item = changes.create_change(root, "Plan", "T", "O", engagement_kind="initiative")
    with pytest.raises(ws.WorkspaceError, match="clients only"):
        changes.verify_deploy(root, "Plan", item["id"], "some-org", "0Af000000000001AAA", [], None,
                              engagement_kind="initiative")
    with pytest.raises(ws.WorkspaceError, match="clients only"):
        changes.append_approval_event(root, "Plan", item["id"], "approval_request", {}, engagement_kind="initiative")


def test_a_client_change_is_unchanged(root):
    item = changes.create_change(root, "Alpha", "T", "O")
    assert item["client"] == "alpha" and "initiative" not in item


# --- Final review ---

ARCHIVED = "this engagement is archived; reopen it before recording new work"


def test_archived_initiative_refuses_notes_and_checks_until_reopened(root, tmp_path):
    item = changes.create_change(root, "Plan", "T", "O", ["Works"], engagement_kind="initiative")
    evidence = tmp_path / "proof.txt"
    evidence.write_text("proof\n")
    eng.set_state(root, "Plan", "archived")
    events = root / "initiatives" / "plan" / "changes" / item["id"] / "events"
    before = sorted(p.name for p in events.iterdir()) if events.exists() else []
    with pytest.raises(ws.WorkspaceError, match=ARCHIVED):
        changes.add_note(root, "Plan", item["id"], "late", engagement_kind="initiative")
    with pytest.raises(ws.WorkspaceError, match=ARCHIVED):
        changes.add_check(root, "Plan", item["id"], "AC1", "pass", "checked", evidence, engagement_kind="initiative")
    assert (sorted(p.name for p in events.iterdir()) if events.exists() else []) == before
    evidence_dir = root / "initiatives" / "plan" / "changes" / item["id"] / "evidence"
    assert not evidence_dir.exists() or list(evidence_dir.iterdir()) == []
    eng.set_state(root, "Plan", "active")
    changes.add_note(root, "Plan", item["id"], "back", engagement_kind="initiative")
    changes.add_check(root, "Plan", item["id"], "AC1", "pass", "checked", evidence, engagement_kind="initiative")


def test_initiative_change_cannot_plan_an_org(root):
    with pytest.raises(ws.WorkspaceError, match="clients only"):
        changes.create_change(root, "Plan", "T", "O", [], "some-org", engagement_kind="initiative")
    assert changes.create_change(root, "Alpha", "T", "O", [], "some-org")["planned_org"] == "some-org"
