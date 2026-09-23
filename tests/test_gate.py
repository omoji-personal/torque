from pathlib import Path
import pytest
from torque import gate

W = Path("/w")
BLOCK = [
    ("Bash", {"command": "sf data query -q 'select Id from Account' -o prod"}),
    ("Bash", {"command": "sf project retrieve start -o prod"}),
    ("Bash", {"command": "sf org display --target-org prod"}),
    ("Bash", {"command": "sfdx force:source:deploy -u prod"}),
    ("Bash", {"command": "torque data update --workspace . --client acme"}),
    ("Bash", {"command": "torque context --workspace . --client acme"}),
    ("Bash", {"command": "cd x && torque deploy start"}),
    ("Read", {"file_path": "/w/clients/acme/context/discovery.md"}),
    ("Grep", {"pattern": "x", "path": "/w/clients"}),
    ("Bash", {"command": "torque workspace ai-access full"}),
    ("Write", {"file_path": "/w/workspace.json"}),
    ("Edit", {"file_path": "/w/workspace.json"}),
]
ALLOW = [
    ("Bash", {"command": "torque demo /tmp/d"}),
    ("Bash", {"command": "torque workflows show diagnose"}),
    ("Bash", {"command": "sf project generate --name demo"}),
    ("Bash", {"command": "sf --version"}),
    ("Bash", {"command": "git status"}),
    ("Read", {"file_path": "/w/project/force-app/main/default/flows/x.flow-meta.xml"}),
]


@pytest.mark.parametrize("tool,inp", BLOCK)
def test_build_only_blocks_org_and_client_access(tool, inp):
    allowed, reason = gate.decide(tool, inp, W, "build-only")
    assert not allowed and reason


@pytest.mark.parametrize("tool,inp", ALLOW)
def test_build_only_allows_build_work(tool, inp):
    assert gate.decide(tool, inp, W, "build-only") == (True, "")


@pytest.mark.parametrize("tool,inp", BLOCK)
def test_full_mode_allows_everything(tool, inp):
    assert gate.decide(tool, inp, W, "full")[0]


def test_ai_access_action_sets_mode(tmp_path):
    from torque import cli, workspace as ws
    ws.init_workspace(tmp_path / "w", "Example firm", "generic")
    assert cli.main(["workspace", "ai-access", "build-only", "--path", str(tmp_path / "w")]) == 0
    import json
    assert json.loads((tmp_path / "w/workspace.json").read_text())["ai_access"] == "build-only"
