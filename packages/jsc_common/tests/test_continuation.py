"""Offline regressions for migration boundaries, with synthetic identifiers."""
from pathlib import Path
import json
from types import SimpleNamespace

import pytest


def test_sequential_clients_resolve_storage_at_call_time(tmp_path, monkeypatch):
    from jsc_common.workspace import state_dir
    from jsc_revert import bundle, post_deploy_polling
    from jsc_memory import storage
    from jsc_browser_tests import vision
    from jsc_ai_prompt_regression import harness
    monkeypatch.setenv("JSC_REVERT_DIR", str(tmp_path / "stale-client"))
    monkeypatch.setenv("JSC_MEMORY_DIR", str(tmp_path / "stale-client"))
    stale = tmp_path / "old-repo/local/lessons"
    stale.mkdir(parents=True)
    (stale / "lessons.md").write_text("Private stale client context must never be selected")
    monkeypatch.chdir(tmp_path / "old-repo")
    paths=[]
    for client in ("alpha", "beta"):
        root=tmp_path/client
        monkeypatch.setenv("TORQUE_WORKSPACE", str(root))
        paths.append(bundle.revert_dir())
        assert bundle.revert_dir() == root/"state/revert"
        assert storage.memory_root() == root/"state/memory"
        assert storage.active_lessons_path() == root/"state/memory/lessons/lessons.md"
        assert post_deploy_polling._queue_dir() == root/"state/revert/_polling_queue"
        assert harness._stage_prompt_to_workspace("synthetic").is_relative_to(root)
        assert state_dir("vision-staging").is_relative_to(root)
    assert paths[0] != paths[1]
    assert not (tmp_path/"stale-client").exists()


def test_unscoped_state_has_no_home_default(monkeypatch):
    from jsc_common.workspace import state_dir
    monkeypatch.delenv("TORQUE_WORKSPACE", raising=False)
    monkeypatch.delenv("JSC_ROOT", raising=False)
    with pytest.raises(ValueError, match="Select a Torque workspace"):
        state_dir("memory")


def test_credential_scanner_never_echoes_matched_values():
    from jsc_browser_tests.auth import scan_replay_script
    value="SYNTHETIC_SECRET_987654321"
    for snippet in (f"sid={value}",f"Bearer {value}",f"Cookie: sid={value}"):
        result=scan_replay_script(snippet)
        assert result
        assert value not in str(result)


class SyntheticClient:
    def __init__(self): self.calls=[]
    def query(self, org, soql, **kwargs):
        self.calls.append(soql)
        return {"records": []}


def test_user_assignment_scope_and_unknown_evidence_are_honest():
    from jsc_advisory.evidence import build_evidence, NOT_OBSERVED
    client=SyntheticClient()
    result=build_evidence(client,target_org="sample-sandbox",field_name="Account.Example__c",permset="Example_Access",user_id="005000000000001",not_applicable=["field_exists:operator says unrelated"])
    assignments=[q for q in client.calls if "PermissionSetAssignment" in q]
    assert len(assignments)==2
    assert all("AssigneeId='005000000000001'" in q for q in assignments)
    field=result["ledger"][0]
    assert field["outcome"]==NOT_OBSERVED
    assert field["declared_not_applicable"]=="operator says unrelated"
    assert result["complete"] is False
    assert result["effective_user_access_proven"] is False


def test_any_assignee_cannot_be_reported_as_intended_user():
    from jsc_advisory.evidence import build_evidence
    result=build_evidence(SyntheticClient(),target_org="sample-sandbox",field_name="Account.Example__c",permset="Example_Access")
    assert result["assignment_scope"]=="any_assignee_in_org"
    assert result["intended_user_id"] is None
    assert result["effective_user_access_proven"] is False


def test_invalid_user_id_is_rejected_before_query():
    from jsc_advisory.evidence import build_evidence
    client=SyntheticClient()
    with pytest.raises(ValueError,match="--user-id"):
        build_evidence(client,target_org="sample-sandbox",field_name="Account.Example__c",user_id="bad' OR Id!='")
    assert client.calls==[]


def test_generic_router_data_is_inside_importable_package():
    from jsc_qa.router import load_router, DEFAULT_ROUTER_PATH
    data=load_router()
    assert DEFAULT_ROUTER_PATH.parent.name=="data"
    assert len(data["change_types"])==67
    assert "Hook-Gate" not in {name for row in data["change_types"] for name in row["surfaces"]}
    assert not any("sample-sandbox" in str(row) for row in data["change_types"])


def test_explicit_client_browser_flow_preserves_execution_surface(tmp_path,monkeypatch):
    from jsc_browser_tests.suite import discover_flows
    path=tmp_path/"example.py"
    path.write_text('from jsc_browser_tests.runner import BaseFlow, Variation\nfrom jsc_browser_tests.flow_spec import FlowSpec\nclass Example(BaseFlow):\n    name="example_flow"\n    spec=FlowSpec(name="example_flow",workflow="E1",writes=False,variations=[Variation("happy")])\nFLOW=Example()\n')
    monkeypatch.setenv("TORQUE_BROWSER_FLOWS",str(path))
    assert {f.spec.name for f in discover_flows()}=={"smoke_login","example_flow"}
    monkeypatch.setenv("TORQUE_BROWSER_FLOWS",str(tmp_path/"missing.py"))
    with pytest.raises(ValueError,match="does not exist"):
        discover_flows()


def test_no_ambient_token_can_authorize_production_browser_writes():
    from jsc_browser_tests.suite import resolve_write_gate,WriteGateError
    org=SimpleNamespace(detected_org_type="production")
    with pytest.raises(WriteGateError):
        resolve_write_gate(org,has_token=True)
    assert resolve_write_gate(org,allow_production_writes=True)


def test_test_user_path_uses_selected_client_not_alias_guess(tmp_path,monkeypatch):
    from jsc_qa.seed_validator import seed_path_for
    monkeypatch.setenv("TORQUE_WORKSPACE",str(tmp_path/"chosen-client"))
    assert seed_path_for("sf-unrelated-org-prod")==tmp_path/"chosen-client/config/test-users.json"


def test_selected_state_rejects_symlink_escape(tmp_path, monkeypatch):
    from jsc_common.workspace import state_dir, private_config
    client = tmp_path/'alpha'; other=tmp_path/'beta'; client.mkdir(); other.mkdir()
    monkeypatch.setenv('TORQUE_WORKSPACE',str(client))
    for area, lookup in [('state',state_dir),('config',private_config)]:
        (client/area).symlink_to(other, target_is_directory=True)
        with pytest.raises(ValueError,match='escapes'):
            lookup('memory')


@pytest.mark.parametrize('severity,expected',[(1,'FAIL'),(2,'FAIL'),(3,'PASS'),(None,'PASS')])
def test_side_effects_assess_findings_not_analyzer_exit(monkeypatch,severity,expected):
    import sys
    from jsc_qa.dispatcher import dispatch_side_eff
    from jsc_loganalyzer.score import Result, Finding, score_findings
    items=[] if severity is None else [Finding('synthetic',severity,'synthetic finding')]
    evidence={**Result(score_findings(items),items).to_dict(),'logs_analyzed':1}
    def execute(cmd,**kwargs):
        assert cmd[0]==sys.executable and '--json' in cmd
        return SimpleNamespace(returncode=0,stdout=json.dumps(evidence),stderr='')
    monkeypatch.setattr('jsc_qa.dispatcher.subprocess.run',execute)
    result=dispatch_side_eff('sample-sandbox','synthetic change')
    assert result.status==expected
    assert result.metadata['change_execution_proven'] is False


@pytest.mark.parametrize('evidence',[{}, {'findings':[],'summary':{},'logs_analyzed':1}, {'findings':[],'summary':{'p0_count':0,'p1_count':0,'p2_count':0,'total':0},'logs_analyzed':0}])
def test_side_effects_incomplete_evidence_is_error(monkeypatch,evidence):
    from jsc_qa.dispatcher import dispatch_side_eff
    monkeypatch.setattr('jsc_qa.dispatcher.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=json.dumps(evidence),stderr=''))
    assert dispatch_side_eff('sample-sandbox','synthetic').status=='ERROR'


def test_lock_audit_remains_client_private_and_omits_prior_tokens(tmp_path,monkeypatch):
    from jsc_revert.org_sequence import _audit_steal,LockReadResult,LockReadStatus
    monkeypatch.setenv('TORQUE_WORKSPACE',str(tmp_path))
    _audit_steal(tmp_path/'state/revert/synthetic-org/.org_lock.json',LockReadResult(LockReadStatus.VALID,{'owner_token':'SYNTHETIC_SECRET'}))
    text=(tmp_path/'state/audit-logs/revert-lock-events.jsonl').read_text()
    assert 'revert_lock_steal' in text
    assert 'SYNTHETIC_SECRET' not in text


def test_revert_refuses_manifest_for_different_org(tmp_path,monkeypatch):
    from jsc_revert import revert_executor as executor
    monkeypatch.setattr(executor.org_detect,'resolve_org',lambda _:SimpleNamespace(org_id_short='00D000000000001',alias='sample-sandbox'))
    monkeypatch.setattr(executor.mf,'load_by_id',lambda *a:(tmp_path,{'org':{'org_id_18':'00D000000000002AAA'}}))
    monkeypatch.setattr(executor.subprocess,'run',lambda *a,**k:pytest.fail('must not execute'))
    assert executor.execute_revert('synthetic-snapshot','sample-sandbox')==executor.EXIT_ORG_RESOLUTION_FAILED


def test_revert_fallback_uses_current_python(monkeypatch):
    import sys
    from jsc_revert import revert_planner
    monkeypatch.setattr(revert_planner,'_jsc_bin',lambda:'')
    assert revert_planner._jsc_command()==[sys.executable,'-m','jsc_revert.cli']
