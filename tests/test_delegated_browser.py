"""A browser window granted by a delegated approver: the gate lets Torque's own browser
route (`torque browser` and `torque qa` with an org) through while the window lasts,
and the browser it starts is headless only."""
import os

import pytest

from delegated_helpers import ORGS, as_agent, delegated_grant, delegated_workspace
from torque import approval, browser_guard, changes, gate_connected as gc

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
BROWSE = "torque browser multiprofile visit --target-org acme-dev --json"
QA = "torque qa run 'Visit saves' --org acme-dev"


def run(root, command):
    return gc.decide_connected("Bash", {"command": command}, root, root, env={"TORQUE_CLIENT": "acme"},
                               permission_mode="default", session_id="s1", tool_use_id="t-b1")


def window(root):
    cid = changes.create_change(root, "Acme", "Login As check", "Visit saves", [], "acme-dev")["id"]
    return approval.create_request(root, "Acme", cid, "acme-dev", browser_minutes=20,
                                   purpose="Login As check of the visit form", resolve=ORGS.get)


def at(monkeypatch, when):
    """Evaluate browser windows at `when`, for the gate's lookup only (time itself is not patched)."""
    real = approval.find_browser_approval
    monkeypatch.setattr(approval, "find_browser_approval",
                        lambda *args, **kwargs: real(*args, **{**kwargs, "now": when}))


@pytest.mark.parametrize("command", [BROWSE, QA])
def test_delegated_browser_window_allows_the_headless_route_then_expires(tmp_path, monkeypatch, command):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = window(root)
    own_uid = os.getuid
    as_agent(monkeypatch)
    assert run(root, command).action == "deny"  # requested, not yet granted
    monkeypatch.setattr(os, "getuid", own_uid)
    record = delegated_grant(root, req)
    assert record["kind"] == "browser" and record["approver_kind"] == "ai" and record["delegated"] is True
    assert approval._epoch(record["expires_at"]) - approval._epoch(record["granted_at"]) <= 20 * 60
    as_agent(monkeypatch)
    decision = run(root, command)
    assert decision.action == "allow" and decision.approved == record["id"]
    at(monkeypatch, approval._epoch(record["expires_at"]) + approval.SKEW + 1)
    assert run(root, command).action == "deny"


def test_delegated_window_marks_the_browser_guard_delegated(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    record = delegated_grant(root, window(root))
    monkeypatch.setenv("TORQUE_WORKSPACE", str(root / "clients" / "acme"))
    as_agent(monkeypatch)
    guard = browser_guard.connected_guard("acme-dev", resolve=ORGS.get)
    assert guard is not None and guard.delegated is True
    assert guard.expires_at == approval._epoch(record["expires_at"])
