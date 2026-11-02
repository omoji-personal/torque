"""One safe org identity check (audit item N13).

The Organization query answers "which org is this alias?" without printing a
credential. It reads the org's own identity, which the client's consent already
records for each approved org, so connected mode runs it under metadata consent.
Only that exact shape counts; every other query of Organization reads records. The
credential refusal names the query only where the session may run it, and no hint
sends anyone to `sf org display`, which printed the access token before Salesforce
CLI 2.136.8."""
from collections import namedtuple
import io
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from torque import approval, changes, consent, gate_connected as gc, workspace as ws
from torque import connected_routes as cr
from torque.presence import Presence

REPO = Path(__file__).resolve().parents[1]
Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-sbx": Org("00D000000000001AAA", "sandbox", False, "https://acme--sbx.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
SOQL = "SELECT Id, Name, IsSandbox, OrganizationType FROM Organization"
IDENTITY = gc.ORG_IDENTITY_QUERY.replace("ALIAS", "acme-sbx")


def routes(command, tool="Bash"):
    return [(r.kind, r.org, r.data) for r in cr.classify(tool, {"command": command})]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A connected workspace for Acme; call it with the data classes the consent covers."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for name in cr.SF_CONTAINER_VARS:
        monkeypatch.delenv(name, raising=False)
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)

    def with_consent(data, resolve=ORGS.get):
        letter = tmp_path / "a.pdf"
        letter.write_bytes(b"agreement")
        consent.record_consent(root, "Acme", "2026-09-30", letter, data, ["acme-prod", "acme-sbx"], ["Contact"],
                               presence=YES, resolve=resolve)
        consent.sign_off(root, "Acme", "Reviewer", presence=YES)
        return Path(os.path.realpath(root))
    return with_consent


def run(root, command, tool="Bash", env=None):
    return gc.decide_connected(tool, {"command": command}, root, root,
                               env={"TORQUE_CLIENT": "acme"} if env is None else env,
                               permission_mode="default", session_id="s1", tool_use_id="t1")


def test_the_query_the_gate_names_is_the_canonical_one():
    assert gc.ORG_IDENTITY_QUERY == f'sf data query --query "{SOQL}" --target-org ALIAS'


@pytest.mark.parametrize("command", [
    IDENTITY,
    f'sf data query --target-org acme-sbx -q "{SOQL}" --json',
    'sf data query --target-org acme-sbx -q "SELECT Id, Name, IsSandbox FROM Organization"',
    'sf data query -q "select id, organizationtype from organization" -o acme-sbx',
    'sf data query -q "SELECT InstanceName, Id FROM Organization" -o acme-sbx -r csv',
    'sf data query --query="SELECT Id,IsSandbox FROM Organization" --target-org=acme-sbx --result-format json',
    "sf query data -q 'SELECT Id, Name FROM Organization' -o acme-sbx",
    'sfdx force:data:soql:query -q "SELECT Id, Name FROM Organization" -u acme-sbx',
])
def test_the_organization_identity_query_has_a_class_of_its_own(command):
    assert routes(command) == [("read", "acme-sbx", cr.ORG_IDENTITY)], command


def test_the_powershell_form_is_the_same_read():
    assert set(routes(IDENTITY, tool="PowerShell")) == {("read", "acme-sbx", cr.ORG_IDENTITY)}


@pytest.mark.parametrize("command", [
    'sf data query -q "SELECT Id, Name, Address FROM Organization" -o acme-sbx',
    'sf data query -q "SELECT Id, PrimaryContact FROM Organization" -o acme-sbx',
    'sf data query -q "SELECT Id FROM Organization WHERE Name = \'x\'" -o acme-sbx',
    'sf data query -q "SELECT Id FROM Organization LIMIT 1" -o acme-sbx',
    'sf data query -q "SELECT Id, Id FROM Organization" -o acme-sbx',
    'sf data query -q "SELECT FIELDS(ALL) FROM Organization" -o acme-sbx',
    'sf data query -q "SELECT Id, (SELECT Id FROM Users) FROM Organization" -o acme-sbx',
    'sf data query -q "SELECT Id FROM Account" -o acme-sbx',
    'sf data query -q "SELECT Id FROM Organization" -o acme-sbx --use-tooling-api',
    'sf data query -q "SELECT Id FROM Organization" -o acme-sbx --all-rows',
    'sf data query -q "SELECT Id FROM Organization" -o acme-sbx --output-file org.csv -r csv',
    'sf data query -q "SELECT Id FROM Organization" -o acme-sbx --flags-dir flags',
    'sf data query -q "SELECT Id FROM Organization" -q "SELECT Name FROM Contact" -o acme-sbx',
    'sf data query --file org.soql -o acme-sbx',
    'sf data query -q "SELECT Id FROM Organization" -o acme-sbx -r xml',
    'sf data query -q "$QUERY" -o acme-sbx',
    'sf data query -q "SELECT Id FROM Organization$MORE" -o acme-sbx',
])
def test_any_other_query_of_organization_still_reads_records(command):
    found = routes(command)
    assert ("read", "acme-sbx", "records") in found and not any(data == cr.ORG_IDENTITY for *_, data in found), (
        command, found)


def test_the_identity_query_names_exactly_one_org():
    # The CLI's default org, which can point at another client's org, is never read.
    assert routes(f'sf data query -q "{SOQL}"') == [("no_org", None, cr.ORG_IDENTITY)]
    assert [kind for kind, *_ in routes(f'sf data query -q "{SOQL}" -o acme-sbx -o acme-prod')] == ["no_org"]


@pytest.mark.parametrize("data", [["metadata"], ["records"], ["metadata", "records"], ["metadata", "debug_logs"]])
def test_metadata_or_record_consent_covers_the_identity_query(workspace, data):
    root = workspace(data)
    assert run(root, IDENTITY).action == "allow", data
    assert run(root, IDENTITY, tool="PowerShell").action == "allow", data


def test_the_identity_query_stays_inside_the_consent(workspace):
    root = workspace(["metadata"])
    assert run(root, gc.ORG_IDENTITY_QUERY.replace("ALIAS", "other-org")).action == "deny"
    told = run(root, f'sf data query -q "{SOQL}"')
    assert told.action == "deny" and "the default org is never used" in told.reason
    told = run(root, 'sf data query -q "SELECT Id, Address FROM Organization" -o acme-sbx')
    assert told.action == "deny" and "record data" in told.reason
    assert run(root, IDENTITY, env={}).action == "deny"
    root = workspace(["debug_logs"])
    told = run(root, IDENTITY)
    assert told.action == "deny" and "does not cover metadata" in told.reason


def test_the_credential_refusal_names_the_query_only_where_the_session_may_run_it(workspace):
    root = workspace(["metadata"])
    told = run(root, "sf org display -o acme-sbx")
    assert told.action == "deny" and gc.ORG_IDENTITY_QUERY in told.reason
    assert "torque guarded org" not in told.reason and "record data" not in told.reason
    # What it names is allowed, so the refusal does not lead into another one.
    assert run(root, IDENTITY).action == "allow"
    for env in ({}, {"TORQUE_CLIENT": "nobody"}):
        told = run(root, "sf org display -o acme-sbx", env=env)
        assert told.action == "deny" and gc.ORG_IDENTITY_QUERY not in told.reason, env
        assert "ask the consultant" in told.reason and "no client is bound" not in told.reason, env
    root = workspace(["debug_logs"])
    told = run(root, "sf org display -o acme-sbx")
    assert gc.ORG_IDENTITY_QUERY not in told.reason and "ask the consultant" in told.reason


@pytest.mark.parametrize("command", [
    "sf org auth show-access-token -o acme-sbx", "sf org auth show-access-token -o acme-sbx --no-prompt --json",
    "sf org auth show-sfdx-auth-url -o acme-sbx", "sf org auth show-user-password -o acme-sbx",
    # Shortened, as the CLI completes them when only one command fits.
    "sf show-access-token -o acme-sbx", "sf auth show-access-token -o acme-sbx",
    "sf org show-sfdx-auth-url -o acme-sbx", "sf show-user-password -o acme-sbx",
])
def test_the_commands_that_print_secrets_on_current_clis_are_refused(command):
    assert routes(command) == [("credential", None, None)], command


# ---------------------------------------------------------------- the hints

def test_consent_recording_names_the_identity_query_for_an_alias_that_does_not_resolve(workspace):
    with pytest.raises(ws.WorkspaceError) as caught:
        workspace(["metadata"], resolve=lambda alias: None)
    assert gc.ORG_IDENTITY_QUERY.replace("ALIAS", "acme-prod") in str(caught.value)
    assert "org display" not in str(caught.value)


def test_the_package_hints_name_the_identity_query(monkeypatch, capsys):
    from jsc_qa import cli as qa_cli
    from jsc_revert import cli as revert_cli
    monkeypatch.delenv("JSC_QA_TOKEN_ALLOW_PLACEHOLDER", raising=False)
    monkeypatch.setattr(revert_cli.org_detect, "resolve_org", lambda alias: None)
    assert revert_cli.cmd_revert_token_grant(SimpleNamespace(org="acme-sbx")) == 1
    monkeypatch.setattr(qa_cli, "_get_org_id_18", lambda alias: None)
    assert qa_cli.cmd_token_grant(SimpleNamespace(org="acme-sbx")) == 1
    told = capsys.readouterr().err
    assert told.count(IDENTITY) == 2 and "org display" not in told


def test_the_advisory_catalogue_detects_identity_with_the_query():
    from jsc_advisory.catalogue import entries
    detects = {item["id"]: item.get("detect", "") for item in entries()}
    assert not [name for name, text in detects.items() if "org display" in text]
    for name in ("developer-edition-is-not-sandbox", "sandbox-refresh-changes-orgid"):
        assert SOQL in detects[name], name


@pytest.mark.parametrize("path", ["workflows/status.md", "workflows/connect-org.md", "workflows/preflight-org.md",
                                  ".claude/rules/tools-and-artifacts.md",
                                  "src/torque/data/rules/tools-and-artifacts.md",
                                  "src/torque/data/commands/status.md"])
def test_the_recipes_and_the_standing_rule_name_the_safe_check(path):
    text = " ".join((REPO / path).read_text(encoding="utf-8").split())
    assert SOQL in text, path
    assert "--json` into chat" not in text, path


def test_the_connected_rule_names_the_query_and_no_second_route():
    rule = " ".join((REPO / "src/torque/data/connected/production-approval.md").read_text(encoding="utf-8").split())
    assert gc.ORG_IDENTITY_QUERY in rule and "torque guarded org" not in rule
    assert "a record read" not in rule


# ------------------------------------------- an alias that points at another org

ORG_A, ORG_B = "00D000000000001AAA", "00D000000000005AAA"


def stub_cli(monkeypatch, orgs):
    """Answer the two calls the live resolver makes (org display, then the Organization
    query) for an alias mapped to an org ID; an alias mapped to None has an expired
    login. Both orgs are logged in, so a re-pointed alias resolves cleanly."""
    def run_cli(argv, **kwargs):
        alias = argv[argv.index("--target-org") + 1]
        org = orgs.get(alias)
        if org is None:
            return SimpleNamespace(returncode=1, stdout=json.dumps(
                {"status": 1, "name": "RefreshTokenAuthError", "message": "expired access/refresh token"}))
        if argv[1:3] == ["org", "display"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"status": 0, "result": {
                "id": org, "instanceUrl": "https://example.invalid", "accessToken": "[REDACTED]"}}))
        assert argv[1:3] == ["data", "query"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status": 0, "result": {
            "done": True, "totalSize": 1, "records": [{"Id": org, "IsSandbox": True,
                                                       "OrganizationType": "Enterprise Edition"}]}}))
    monkeypatch.setattr(subprocess, "run", run_cli)


def test_a_write_stops_when_the_alias_now_points_at_another_org_and_names_both(workspace, monkeypatch):
    orgs = {"acme-sbx": ORG_A, "acme-prod": "00D000000000002AAA", "other-client": ORG_B}
    stub_cli(monkeypatch, orgs)
    root = workspace(["metadata"], resolve=None)
    cid = changes.create_change(root, "Acme", "Layout", "Cases show Tier", [], "acme-sbx")["id"]
    window = dict(browser_minutes=20, purpose="Add Tier to the Case layout")
    req = approval.create_request(root, "Acme", cid, "acme-sbx", **window)
    orgs["acme-sbx"] = ORG_B        # the alias was logged in again, to another client's org
    for attempt in (lambda: approval.create_request(root, "Acme", cid, "acme-sbx", **window),
                    lambda: approval.grant(root, "Acme", req["id"], presence=YES, confirm=lambda: True,
                                           out=io.StringIO())):
        with pytest.raises(ws.WorkspaceError) as caught:
            attempt()
        assert ORG_A in str(caught.value) and ORG_B in str(caught.value)
    assert [e["kind"] for e in changes.get_change(root, "Acme", cid)["events"]].count("approval_request") == 1
    assert not approval.active_browser_approval(root, "Acme", "acme-sbx", config=ws.load_workspace(root)[1])
    # The session's own check reads the alias it names, never the CLI's default org.
    assert run(root, 'sf project deploy start -m "Layout:Case-Case Layout"').action == "deny"


def test_an_expired_login_reads_not_authenticated(workspace, monkeypatch):
    orgs = {"acme-sbx": ORG_A, "acme-prod": "00D000000000002AAA"}
    stub_cli(monkeypatch, orgs)
    root = workspace(["metadata"], resolve=None)
    cid = changes.create_change(root, "Acme", "Layout", "Cases show Tier", [], "acme-sbx")["id"]
    orgs["acme-sbx"] = None
    with pytest.raises(ws.WorkspaceError, match="not authenticated") as caught:
        approval.create_request(root, "Acme", cid, "acme-sbx", browser_minutes=20,
                                purpose="Add Tier to the Case layout")
    assert "nothing was recorded" in str(caught.value)
    assert not [e for e in changes.get_change(root, "Acme", cid)["events"] if e["kind"] == "approval_request"]
