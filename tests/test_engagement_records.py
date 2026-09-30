"""Initiatives record sessions, context and handoffs exactly like clients."""
import json

import pytest

from torque import engagements as eng
from torque import workspace as ws


@pytest.fixture
def root(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    eng.add_initiative(root, "Plan")
    return root


def test_initiative_sessions_are_kept_apart_from_clients(root):
    entry = ws.add_session(root, "Plan", "drafted the plan", "prepared", kind="initiative")
    assert entry["initiative"] == "plan" and "client" not in entry
    assert [e["id"] for e in ws.list_sessions(root, "Plan", kind="initiative")] == [entry["id"]]
    assert ws.list_sessions(root, "Alpha") == []


def test_context_and_handoff_for_an_initiative(root):
    ws.add_session(root, "Plan", "drafted the plan", "prepared", kind="initiative")
    context = ws.get_context(root, "Plan", kind="initiative")
    assert context["client"]["kind"] == "initiative" and context["client_root"].endswith("initiatives/plan")
    assert "drafted the plan" in ws.render_handoff(root, "Plan", kind="initiative")


def test_archived_initiative_refuses_new_sessions(root):
    eng.set_state(root, "Plan", "archived")
    with pytest.raises(ws.WorkspaceError, match="archived"):
        ws.add_session(root, "Plan", "late note", "prepared", kind="initiative")


def test_client_session_json_is_unchanged(root):
    entry = ws.add_session(root, "Alpha", "client work", "prepared")
    assert entry["client"] == "alpha" and "initiative" not in entry and entry["schema"] == "torque.session/1"
