"""A maintenance flag pauses every record write; reads keep working."""
from unittest.mock import patch

import pytest

from torque import changes
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    return root


def pause(root):
    (root / ".torque").mkdir(exist_ok=True)
    (root / ws.MAINTENANCE_FLAG).write_text("migration\n")


def test_session_write_refused_during_maintenance(root):
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        ws.add_session(root, "Alpha", "work", "prepared")
    assert ws.list_sessions(root, "Alpha") == []


def test_client_and_change_writes_refused_during_maintenance(root):
    item = changes.create_change(root, "Alpha", "Title", "Outcome")
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        ws.add_client(root, "Beta")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.create_change(root, "Alpha", "Another", "Outcome")
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.add_note(root, "Alpha", item["id"], "decided", "decision")


def test_writes_resume_when_the_flag_is_removed(root):
    pause(root)
    (root / ws.MAINTENANCE_FLAG).unlink()
    assert ws.add_session(root, "Alpha", "work", "prepared")["status"] == "prepared"


# --- Fix round 1 ---


def test_check_evidence_capture_refused_during_maintenance_leaves_no_file(root, tmp_path):
    """Fix round 1 finding 2: require_writable must fire before _capture_file
    copies any bytes, not only afterward in _append. Otherwise a refused
    add_check still leaves a copy of the evidence under the change's
    evidence/ folder."""
    item = changes.create_change(root, "Alpha", "Title", "Outcome", ["Do the thing"])
    evidence = tmp_path / "proof.txt"
    evidence.write_text("proof\n")
    pause(root)
    with pytest.raises(ws.WorkspaceError, match="maintenance"):
        changes.add_check(root, "Alpha", item["id"], "AC1", "pass", "checked", evidence)
    evidence_dir = root / "clients" / "alpha" / "changes" / item["id"] / "evidence"
    assert not evidence_dir.exists() or list(evidence_dir.iterdir()) == []


def test_verify_deploy_refused_during_maintenance_before_dispatch(root):
    """Fix round 1 finding 2: require_writable must fire before verify_deploy
    ever dispatches a live Metadata API call or writes raw evidence."""
    item = changes.create_change(root, "Alpha", "Title", "Outcome")
    pause(root)
    with patch("jsc_qa.dispatcher.dispatch_meta_api") as dispatch:
        with pytest.raises(ws.WorkspaceError, match="maintenance"):
            changes.verify_deploy(root, "Alpha", item["id"], "explicit-dev", "0Af000000000001AAA")
    dispatch.assert_not_called()
