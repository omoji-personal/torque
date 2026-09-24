import json

import pytest

from torque import gate, workspace as ws
from torque.presence import Presence


def present():
    return Presence(True, "")


@pytest.mark.parametrize("value,approval,expected", [
    ("full", None, "full"),
    ("build-only", None, "build-only"),
    ("connected", "required", "connected"),
    ("connected", None, "build-only"),
    ("connected", "optional", "build-only"),
    ("connected", "Required", "build-only"),
    ("Connected", "required", "build-only"),
    ("full", "required", "full"),
    (None, None, "build-only"),
    (["connected"], "required", "build-only"),
])
def test_resolve_ai_access(value, approval, expected):
    assert gate._resolve_ai_access(value, approval) == expected


def test_resolve_ai_access_one_argument_form_unchanged():
    assert gate._resolve_ai_access("full") == "full"
    assert gate._resolve_ai_access("connected") == "build-only"


def test_set_connected_needs_approval_required(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Example firm")
    with pytest.raises(ws.WorkspaceError, match="approval required"):
        ws.set_ai_access(root, "connected", presence=present)
    ws.set_ai_access(root, "connected", approval="required", presence=present)
    data = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    assert data["ai_access"] == "connected" and data["approval"] == "required"
    assert data["approval_verify"] == "hmac"


def test_set_connected_owner_uid_tier(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Example firm")
    with pytest.raises(ws.WorkspaceError, match="approver"):
        ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", presence=present)
    ws.set_ai_access(root, "connected", approval="required", verify="owner-uid", approver_uid=502,
                     presence=present)
    data = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    assert data["approval_verify"] == "owner-uid" and data["approver_uid"] == 502


def test_approval_only_with_connected(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Example firm")
    with pytest.raises(ws.WorkspaceError):
        ws.set_ai_access(root, "full", approval="required")


def test_set_connected_refuses_without_operator(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Example firm")
    with pytest.raises(ws.WorkspaceError, match="terminal"):
        ws.set_ai_access(root, "connected", approval="required",
                         presence=lambda: Presence(False, "needs a real terminal"))
    data = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    assert "ai_access" not in data


def test_leaving_connected_removes_approval_keys(tmp_path):
    root = ws.init_workspace(tmp_path / "w", "Example firm")
    ws.set_ai_access(root, "connected", approval="required", presence=present)
    ws.set_ai_access(root, "full")
    data = json.loads((root / "workspace.json").read_text(encoding="utf-8"))
    assert data["ai_access"] == "full"
    assert not {"approval", "approval_verify", "approver_uid"} & set(data)


def test_build_only_above_connected_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / "workspace.json").write_text(json.dumps({"ai_access": "build-only"}), encoding="utf-8")
    (inner / "workspace.json").write_text(json.dumps({"ai_access": "connected", "approval": "required"}),
                                          encoding="utf-8")
    assert gate._workspace_mode(inner)[1] == "build-only"


def test_connected_above_full_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    (outer / "workspace.json").write_text(json.dumps({"ai_access": "connected", "approval": "required"}),
                                          encoding="utf-8")
    (inner / "workspace.json").write_text(json.dumps({"ai_access": "full"}), encoding="utf-8")
    assert gate._workspace_mode(inner) == (outer, "connected", True)


def test_connected_chain_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    w = tmp_path / "w"
    w.mkdir()
    (w / "workspace.json").write_text(json.dumps({"ai_access": "connected", "approval": "required"}),
                                      encoding="utf-8")
    assert gate._workspace_chain(w)[0][1] == "connected"
    (w / "workspace.json").write_text(json.dumps({"ai_access": "connected"}), encoding="utf-8")
    assert gate._workspace_chain(w)[0][1] == "build-only"


def test_decide_leaves_connected_calls_to_the_connected_path(tmp_path):
    # decide() only runs the build-only checks; connected mode has its own entry point.
    assert gate.decide("Bash", {"command": "sf data query -q x -o p"}, tmp_path, "connected") == (True, "")
