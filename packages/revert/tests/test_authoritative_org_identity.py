"""Hermetic identity and policy regressions; every subprocess is a fake."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from jsc_revert import org_detect as od, manifest

ID15 = "00D000000000001"
ID18 = od._pad_to_18(ID15)
OTHER = od._pad_to_18("00D000000000002")


@pytest.fixture(autouse=True)
def block_real_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected real subprocess in offline org test")
    monkeypatch.setattr(subprocess, "run", forbidden)


def envelope(result):
    return {"status": 0, "result": result}


def authority(*, sandbox=False, edition="Developer Edition", identifier=ID18):
    return envelope({"done": True, "totalSize": 1, "records": [
        {"Id": identifier, "IsSandbox": sandbox, "OrganizationType": edition}]})


def fake_commands(monkeypatch, query, *, display=None, code=0):
    calls = []
    display = envelope({"id": ID18, "instanceUrl": "https://org-dev-ed.my.salesforce.com",
                        "accessToken": "DO_NOT_RETURN_AUTH"}) if display is None else display
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[argv.index("--target-org") + 1] == "client-test"
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["timeout"] == 3
        if argv[1:3] == ["org", "display"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(display))
        assert argv[1:3] == ["data", "query"]
        assert argv[argv.index("--query") + 1] == "SELECT Id, IsSandbox, OrganizationType FROM Organization"
        if isinstance(query, BaseException):
            raise query
        return SimpleNamespace(returncode=code, stdout=json.dumps(query) if not isinstance(query, str) else query)
    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize("sandbox,edition,kind", [(False, "Developer Edition", "developer"),
    (True, "Enterprise Edition", "sandbox"), (False, "Enterprise Edition", "production")])
def test_authority_overrides_alias_url_and_environment(monkeypatch, sandbox, edition, kind):
    monkeypatch.setenv("JSC_ORG_TYPE", "sandbox")
    monkeypatch.setenv("SALESFORCE_LOGIN_URL", "https://test.salesforce.com")
    calls = fake_commands(monkeypatch, authority(sandbox=sandbox, edition=edition))
    info = od.resolve_org("client-test", 3)
    assert info.is_sandbox is sandbox
    assert info.organization_type == edition and info.detected_org_type == kind
    assert info.is_nonproduction is (kind != "production")
    assert info.is_production is (kind == "production")
    assert info.identity_source == "Organization query" and len(calls) == 2
    assert "DO_NOT_RETURN_AUTH" not in repr(info)


def test_display_15_and_query_18_match_and_manifest_keeps_actual_properties(monkeypatch):
    fake_commands(monkeypatch, authority(), display=envelope({"id": ID15, "instanceUrl": "https://example.invalid"}))
    info = od.resolve_org("client-test", 3)
    result = manifest.build_envelope(snapshot_id="synthetic", wrapper_command="offline",
        operation_type="data_record_update", org_info=info, operator="offline")
    assert result["org"]["org_id_18"] == ID18
    assert result["org"]["is_sandbox"] is False
    assert result["org"]["detected_org_type"] == "developer"
    assert result["org"]["organization_type"] == "Developer Edition"
    assert result["org"]["identity_source"] == "Organization query"
    assert "accessToken" not in json.dumps(result)


@pytest.mark.parametrize("query", [None, [], "malformed", {"status": 1, "result": {}},
    {"status": True, "result": {}}, envelope(None), envelope({}),
    envelope({"done": False, "totalSize": 1, "records": []}),
    envelope({"done": True, "totalSize": 0, "records": []}),
    envelope({"done": True, "totalSize": 2, "records": [{}, {}]}),
    authority(identifier=OTHER), authority(identifier="001000000000001AAA"),
    authority(sandbox="false"), authority(sandbox=None), authority(edition=""),
    authority(edition=[]), subprocess.TimeoutExpired("offline", 3), FileNotFoundError("offline")])
def test_unavailable_malformed_or_conflicting_authority_never_uses_alias(monkeypatch, query):
    monkeypatch.setenv("JSC_ORG_TYPE", "sandbox")
    fake_commands(monkeypatch, query)
    assert od.resolve_org("client-test", 3) is None


def test_nonzero_query_exit_is_unknown(monkeypatch):
    fake_commands(monkeypatch, authority(), code=1)
    assert od.resolve_org("client-test", 3) is None


@pytest.mark.parametrize("alias", [None, "", "--help", "bad\nname"])
def test_invalid_explicit_target_never_launches_process(alias):
    assert od.resolve_org(alias) is None


@pytest.mark.parametrize("display", [None, [], {"status": 0}, envelope({"id": OTHER[:5]}),
    envelope({"id": "001000000000001AAA"})])
def test_bad_display_identity_never_queries_authority(monkeypatch, display):
    calls=[]
    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=json.dumps(display))
    monkeypatch.setattr(subprocess, "run", run)
    assert od.resolve_org("client-test", 3) is None and len(calls) == 1


def test_unresolved_identity_stops_wrapper_before_lock_or_dml(monkeypatch):
    from jsc_revert.wrappers import data_update
    fake_commands(monkeypatch, authority(identifier=OTHER))
    monkeypatch.setattr(data_update.c.WrapperContext, "acquire_org_lock",
        lambda self: pytest.fail("unverified org reached lock acquisition"))
    args=SimpleNamespace(target_org="client-test", sobject="Synthetic__c", record_id="a00000000000001AAA", values="Name=example")
    # Wrapper uses its normal timeout; adapt only the resolver timeout, not its result.
    resolver=od.resolve_org
    monkeypatch.setattr(od, "resolve_org", lambda target: resolver(target, 3))
    assert data_update.run(args) != 0


@pytest.mark.parametrize("kind,sandbox,expected_submits", [("developer", False, 1),
    ("sandbox", True, 1), ("production", False, 0)])
def test_capture_policy_uses_nonproduction_not_is_sandbox(monkeypatch, tmp_path, kind, sandbox, expected_submits):
    from jsc_revert.wrappers import data_update
    submits=[]
    class Context:
        def __init__(self, **kwargs):
            self.org=od.OrgInfo("client-test", ID18, ID15, sandbox, "", "", kind)
            self.snap_dir=tmp_path;self.manifest={}
        def resolve_org(self):return 0
        def acquire_org_lock(self):return 0
        def init_snapshot_dir(self):pass
        def set_revert_capabilities(self):pass
        def update_phase(self,*a,**k):pass
        def save(self):pass
        def release_lock(self):pass
    monkeypatch.setattr(data_update.c, "WrapperContext", Context)
    monkeypatch.setattr(data_update, "_query_record", lambda *a: None)
    def submit(argv, **kwargs):
        submits.append(argv)
        return 0, '{"status":0,"result":{"success":true}}', ''
    monkeypatch.setattr(data_update.c,"run_sf_subprocess",submit)
    args=SimpleNamespace(target_org="client-test",sobject="Synthetic__c",record_id="a00000000000001AAA",values="Name=example")
    data_update.run(args)
    assert len(submits)==expected_submits


def test_legacy_classifier_never_treats_name_url_or_env_as_authority(monkeypatch):
    monkeypatch.setenv("JSC_ORG_TYPE", "sandbox")
    assert od.is_production_org("client-test", "https://org-dev-ed.my.salesforce.com", "https://test.salesforce.com")
    assert od.classify_org_type("x", org_type="Developer Edition", is_sandbox=False)=="developer"
    assert od.classify_org_type("x", org_type="Developer Edition", is_sandbox="false")=="production"
    assert od.classify_org_type("x", org_type="Developer Edition", is_sandbox=None)=="production"
    assert od.classify_org_type("x", org_type="Base Edition", is_sandbox=False)=="production"


def test_base_edition_authority_does_not_establish_developer(monkeypatch):
    fake_commands(monkeypatch, authority(edition="Base Edition"))
    info=od.resolve_org("client-test",3)
    assert info.organization_type=="Base Edition"
    assert info.is_sandbox is False and info.is_production is True
