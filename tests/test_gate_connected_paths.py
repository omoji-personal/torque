import os
from pathlib import Path

import pytest

from torque import gate

W = Path(os.path.realpath(os.sep)) / "w"
OTHER = [W / "clients" / "beta"]


def d(tool, inp):
    return gate._decide(tool, inp, W, W / "project", org_rules=False, guarded=OTHER)


def test_org_calls_pass_path_scan_when_org_rules_off():
    assert d("Bash", {"command": "sf data query -q 'x' -o acme-prod"}) == (True, "")
    assert d("Bash", {"command": "torque context --workspace /w --client acme"}) == (True, "")
    assert d("Bash", {"command": "python3 -m torque deploy -o acme-prod"}) == (True, "")
    assert d("Bash", {"command": "jsc deploy -o acme-prod"}) == (True, "")
    assert d("mcp__salesforce__run_soql_query", {"query": "x"}) == (True, "")


def test_other_client_folder_blocked_own_allowed():
    assert not d("Read", {"file_path": str(W / "clients" / "beta" / "context.md")})[0]
    assert d("Read", {"file_path": str(W / "clients" / "acme" / "context.md")})[0]
    assert not d("Bash", {"command": f"cat {(W / 'clients' / 'beta' / 'context.md').as_posix()}"})[0]
    assert d("Bash", {"command": f"cat {(W / 'clients' / 'acme' / 'context.md').as_posix()}"})[0]
    assert not d("Grep", {"pattern": "x", "path": str(W / "clients")})[0]
    assert d("Grep", {"pattern": "x", "path": str(W / "clients" / "acme")})[0]
    assert not d("Bash", {"command": f"torque context --workspace {W.as_posix()} --client x && cat ../clients/beta/a"})[0]


def test_approval_and_consent_files_guarded_for_writes():
    assert not d("Write", {"file_path": str(W / "clients" / "acme" / "consent.json")})[0]
    assert not d("Edit", {"file_path": str(W / "clients" / "acme" / "approvals" / "granted" / "apr-1.json")})[0]
    assert not d("Write", {"file_path": str(W / "clients" / "acme" / "consent-evidence" / "x.pdf")})[0]
    assert not d("Bash", {"command": f"echo x > {(W / 'clients' / 'acme' / 'approvals' / 'consumed' / 'apr-1').as_posix()}"})[0]
    assert not d("Bash", {"command": f"rm -rf {(W / 'clients' / 'acme' / 'approvals').as_posix()}"})[0]
    assert not d("Bash", {"command": "cat ~/.config/torque/approval.key"})[0]
    assert not d("Write", {"file_path": str(Path.home() / ".config" / "torque" / "approval.key")})[0]
    assert d("Read", {"file_path": str(W / "clients" / "acme" / "approvals" / "requests" / "req-1.json")})[0]


def test_workspace_and_settings_still_guarded():
    assert not d("Write", {"file_path": str(W / "workspace.json")})[0]
    assert not d("Edit", {"file_path": str(W / ".claude" / "settings.json")})[0]
    assert not d("Bash", {"command": f"rm {(W / '.claude' / 'rules' / 'production-approval.md').as_posix()}"})[0]
    assert not d("UnknownTool", {"x": 1})[0]


def test_unbound_guards_every_client():
    everything = [W / "clients"]
    assert not gate._decide("Read", {"file_path": str(W / "clients" / "acme" / "context.md")}, W, W,
                            org_rules=False, guarded=everything)[0]


def test_no_other_clients_still_runs_integrity_checks():
    assert not gate._decide("Write", {"file_path": str(W / "workspace.json")}, W, W, org_rules=False, guarded=[])[0]
    assert gate._decide("Read", {"file_path": str(W / "notes.md")}, W, W, org_rules=False, guarded=[])[0]


def test_build_only_unchanged():
    assert not gate.decide("Bash", {"command": "sf data query -q x -o p"}, W, "build-only")[0]
    assert not gate.decide("Bash", {"command": "torque context --workspace . --client acme"}, W, "build-only")[0]
    assert not gate.decide("mcp__salesforce__run_soql_query", {}, W, "build-only")[0]
    assert not gate.decide("Read", {"file_path": str(W / "clients" / "acme" / "x")}, W, "build-only")[0]


@pytest.mark.parametrize("text,expected", [
    ("/w/clients/acme/consent.json", True), ("/w/clients/acme/consent-evidence/a.pdf", True),
    ("/w/clients/acme/approvals", True), ("/w/clients/acme/approvals/granted/x", True),
    ("C:\\w\\clients\\acme\\approvals\\x", True), ("/home/u/.config/torque/approval.key", True),
    ("/w/clients/acme/context.md", False), ("/w/project/approvals/x", False), ("/w/consent.json", False),
])
def test_guarded_patterns(text, expected):
    assert gate._targets_guarded_file(text) is expected
