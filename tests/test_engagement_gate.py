"""On a laptop the agent shares the owner's account, so the gate's matchers are
the protection for engagement bindings and client control records."""
from pathlib import Path

from torque import engagements as eng
from torque import gate
from torque import gate_connected
from torque import workspace as ws


def setup(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic firm")
    ws.add_client(root, "Alpha")
    eng.add_initiative(root, "Plan")
    return root


def test_binding_file_is_protected_in_every_mode(tmp_path):
    root = setup(tmp_path)
    binding = root / "initiatives" / "plan" / "binding.json"
    assert gate._protected_record(binding)
    assert gate._approval_file_reason("Write", {"file_path": str(binding)}, root)
    assert gate._approval_file_reason("Edit", {"file_path": "initiatives/plan/binding.json"}, root)


def test_removing_or_moving_an_initiative_folder_is_refused(tmp_path):
    root = setup(tmp_path)
    for command in ("rm -rf initiatives/plan", "mv initiatives/plan /tmp/x", "rm initiatives/plan/binding.json",
                    "cd initiatives && rm -rf plan"):
        assert gate._approval_file_reason("Bash", {"command": command}, root), command


def test_state_and_records_stay_writable(tmp_path):
    root = setup(tmp_path)
    for rel in ("initiatives/plan/state/engagement.json", "initiatives/plan/context.md",
                "initiatives/plan/sessions/x.json"):
        assert not gate._approval_file_reason("Write", {"file_path": rel}, root), rel


def test_future_client_control_folders_are_protected(tmp_path):
    root = setup(tmp_path)
    for name in ("control/consent.json", "requests/r.json", "claims/c.consumed", "binding.json"):
        assert gate._protected_record(root / "clients" / "alpha" / name), name


def test_connected_guard_covers_initiatives(tmp_path):
    root = setup(tmp_path)
    guarded = gate_connected._guarded(root, "alpha")
    assert root / "initiatives" in guarded
    assert all(p.name != "alpha" for p in guarded)
