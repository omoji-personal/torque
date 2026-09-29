"""A maintenance flag pauses every record write; reads keep working."""
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
