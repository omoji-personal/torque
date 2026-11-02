"""N6: build-only runs the Salesforce CLI's local-only commands through the one sf
classifier both modes share (gate.SF_LOCAL_COMMANDS = connected_routes.SF_LOCAL), and
a bare `sf`/`sfdx` word that is another command's argument is not read as an org call.
Org access stays blocked. Synthetic workspace only."""
import os
from pathlib import Path

import pytest

from torque import gate, workspace as ws
from torque.connected_routes import SF_LOCAL, classify


@pytest.fixture
def root(tmp_path):
    base = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(base, "Acme")
    (Path(base) / "project" / "force-app").mkdir(parents=True)
    ws.set_ai_access(base, "build-only")
    return Path(os.path.realpath(base))


def allowed(root, command, cwd=None):
    ok, _ = gate.decide("Bash", {"command": command}, root, "build-only", cwd or root)
    return ok


# Local generators and informational commands the shared classifier allows. Run from
# project/ so the tree readers' default (cwd) does not reach clients/.
LOCAL_ALLOWED = [
    "sf template generate apex class --name ShiftService --output-dir force-app/main/default/classes",
    "sf template generate lightning component",
    "sf schema generate field",
    "sf cmdt generate object",
    "sf apex generate class --name A --output-dir force-app",
    "sf lightning generate component --name c",
    # The template generators' own aliases (round 2): the brief's exact spelling first.
    "sf static-resource generate --name A",
    "sf visualforce generate page --name P --label P",
    "sf analytics generate template --name T",
    "sf generate metadata field",
    "sf project convert source --source-dir force-app --output-dir mdapi",
    "sf project list ignored",
    "sf --help",
    "sf help",
    "sf help project deploy start",
    "sf project deploy start --help",
    "sf code-analyzer run --help",
    "sf which project deploy",
    "sf --version",
    "sf version",
    "sf info",
    "sf doctor",
    "sf plugins",
    "sf plugins inspect",
    "sf code-analyzer run -v detail -o out.json --workspace force-app",
]


@pytest.mark.parametrize("command", LOCAL_ALLOWED)
def test_local_sf_commands_are_allowed_in_project(root, command):
    assert allowed(root, command, root / "project"), command


# Org access, record export and plugin installs stay blocked.
ORG_BLOCKED = [
    "sf project deploy start -d force-app -o dev",
    "sf data query -q 'SELECT Id FROM Account' --target-org dev",
    "sf cmdt generate fromorg --target-org dev",
    "sf plugins install some-plugin",
    "sf org list",
    "sf code-analyzer run --workspace force-app --target-org prod",
    # Round 2 (P1): a command's own --version is an operand, not the global flag. This
    # creates a package version through the configured default org.
    "sf package1 version create --package-id 033000000000001 --name Synthetic --version 1.0",
    "sf org list --version",
    # Round 2: commands the base also blocked, which can reach an org without an org flag:
    # doctor runs the command it is given; agent generate agent-spec defaults to the
    # configured org.
    "sf doctor --command 'data query -q x -o dev'",
    "sf agent generate agent-spec --type customer",
    # Help after `--` is an operand: the query still runs.
    "sf data query -q x -- --help",
]


@pytest.mark.parametrize("command", ORG_BLOCKED)
def test_org_access_stays_blocked(root, command):
    assert not allowed(root, command, root / "project"), command


# Round 2 (P2): a genuine help invocation (oclif shows help, and runs nothing, when -h or
# --help appears before `--`) and the global version flag (argv[0]) pass before the org
# and tree-read checks, also from the workspace root.
HELP_FROM_ROOT = [
    "sf code-analyzer run --help",
    "sf project deploy start --target-org dev --help",
    "sf data query -q x -o dev -h",
    "sf --help",
    "sf --version",
    "sf -v",
]


@pytest.mark.parametrize("command", HELP_FROM_ROOT)
def test_help_and_version_pass_before_the_org_and_tree_checks(root, command):
    assert allowed(root, command, root), command


# A bare sf/sfdx/@salesforce/cli word that is another command's argument is not an
# sf call (checking the CLI is installed, searching for the word).
WORD_NOT_A_CALL = [
    "which sf",
    "which sfdx",
    "where.exe sf",
    "command -v sf",
    "type sf",
    "npm ls -g @salesforce/cli",
    "rg -n sfdx force-app",
    "grep -r sf force-app",
    "echo sf is installed",
    "cat force-app/notes.txt | grep sfdx",
    "find force-app -name sf",
    "fd sf force-app",
]


@pytest.mark.parametrize("command", WORD_NOT_A_CALL)
def test_a_bare_sf_word_as_an_argument_is_not_a_call(root, command):
    assert allowed(root, command, root / "project"), command


# A real sf call behind a wrapper, a runner, a shell keyword or an env assignment is
# still caught (the gate reads sf as a call everywhere except as a data command's
# argument).
WRAPPED_ORG = [
    "time sf data query -q x --target-org dev",
    "env SF_X=1 sf data query -q x --target-org dev",
    "sudo sf project deploy start -o dev",
    "nice sf org list",
    "SF_TOKEN=x sf data query -q y -o dev",
    "if true; then sf data query -o dev; fi",
    "node /usr/lib/node_modules/@salesforce/cli/bin/run.js data query -o dev",
    # A search command that runs a program for each match is not merely data.
    "find . -name x -exec sf data query -o dev \\;",
    "fd -x sf data query -o dev",
    "rg --pre sf x force-app",
]


@pytest.mark.parametrize("command", WRAPPED_ORG)
def test_an_sf_call_behind_a_wrapper_is_still_blocked(root, command):
    assert not allowed(root, command, root / "project"), command


def test_the_classifier_is_shared_with_connected_mode():
    # Both modes read the same local set.
    assert SF_LOCAL is gate.SF_LOCAL_COMMANDS
    # A local generator classifies as local in connected mode too (no org work).
    routes = classify("Bash", {"command": "sf template generate apex class --name A"})
    assert all(r.kind == "local" for r in routes), routes
    # A command's --help is local in connected mode (not a no_org denial), also with an
    # org flag on the line, while a credential command stays refused whatever its flags.
    routes = classify("Bash", {"command": "sf project deploy start --help"})
    assert all(r.kind == "local" for r in routes), routes
    routes = classify("Bash", {"command": "sf project deploy start --target-org dev --help"})
    assert all(r.kind == "local" for r in routes), routes
    routes = classify("Bash", {"command": "sf org display --help"})
    assert any(r.kind == "credential" for r in routes), routes
    # The template alias is local in connected mode too.
    routes = classify("Bash", {"command": "sf static-resource generate --name A"})
    assert all(r.kind == "local" for r in routes), routes
    # Round 2b: sf doctor --command X is judged as X, so it carries X's org and data.
    query = "sf data query --query 'SELECT Id FROM Account' --target-org acme"
    assert (set((r.kind, r.org, r.data) for r in classify("Bash", {"command": f'sf doctor --command "{query}"'}))
            == set((r.kind, r.org, r.data) for r in classify("Bash", {"command": query})))
    # Round 2b: agent generate reaches the configured org, so it is not local.
    routes = classify("Bash", {"command": "sf agent generate agent-spec --type customer"})
    assert not any(r.kind == "local" for r in routes), routes


# Round 2b: connected-mode decisions for sf doctor --command and sf agent generate.
def _connected_fixture(tmp_path, monkeypatch, data):
    from torque import consent, workspace as wsmod
    from torque.presence import Presence
    yes = lambda: Presence(True, "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = wsmod.init_workspace(tmp_path / "firm", "Firm")
    wsmod.add_client(root, "Acme")
    wsmod.set_ai_access(root, "connected", approval="required", presence=yes)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    orgs = {"acme-prod": type("O", (), {"org_id_18": "00D000000000002AAA", "detected_org_type": "production",
                                        "is_production": True, "instance_url": "https://acme.my.salesforce.com"})()}
    consent.record_consent(root, "Acme", "2026-09-30", letter, data, ["acme-prod"], ["Contact"],
                           presence=yes, resolve=orgs.get)
    consent.sign_off(root, "Acme", "Reviewer", presence=yes)
    return Path(os.path.realpath(root))


def _connected_decide(root, command):
    from torque import gate_connected as gc
    return gc.decide_connected("Bash", {"command": command}, root, root, env={"TORQUE_CLIENT": "acme"},
                               permission_mode="default", session_id="s", tool_use_id="t")


def test_connected_doctor_command_needs_the_same_consent_as_the_inner_command(tmp_path, monkeypatch):
    query = "sf data query --query 'SELECT Id FROM Account' --target-org acme-prod"
    wrapped = f'sf doctor --command "{query}"'
    # With records consent, the inner query is a read; doctor --command gets the same answer.
    root = _connected_fixture(tmp_path, monkeypatch, ["metadata", "records"])
    assert _connected_decide(root, query).action == _connected_decide(root, wrapped).action == "allow"


def test_connected_doctor_command_without_records_consent_is_refused(tmp_path, monkeypatch):
    query = "sf data query --query 'SELECT Id FROM Account' --target-org acme-prod"
    wrapped = f'sf doctor --command "{query}"'
    root = _connected_fixture(tmp_path, monkeypatch, ["metadata"])   # no records consent
    assert _connected_decide(root, query).action == "deny"
    assert _connected_decide(root, wrapped).action == "deny"


def test_connected_agent_generate_is_refused(tmp_path, monkeypatch):
    root = _connected_fixture(tmp_path, monkeypatch, ["metadata", "records"])
    # No org flag: connected never uses the default org.
    assert _connected_decide(root, "sf agent generate agent-spec --type customer").action == "deny"


def test_code_analyzer_short_flags_are_not_org_flags():
    # -o is --output-file and -v is --view for code-analyzer, so neither reads as an org.
    assert not gate._sf_has_org_flag(["code-analyzer", "run", "-v", "detail", "-o", "out.json"],
                                     ("code-analyzer", "run"))
    # A long org flag still counts even on code-analyzer.
    assert gate._sf_has_org_flag(["code-analyzer", "run", "--target-org", "prod"], ("code-analyzer", "run"))
