import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsc_browser_tests.runner import FlowResult, BaseFlow, Variation
from jsc_browser_tests.flow_spec import FlowSpec
from jsc_browser_tests.report import score_run, exit_code
from jsc_browser_tests.sf_client import FakeSfClient
from jsc_browser_tests.suite import run_suite


@pytest.mark.parametrize("statuses", [[], ["SKIP"], ["NOT_APPLICABLE"], ["SKIP", "NOT_APPLICABLE"]])
def test_no_observed_browser_coverage_cannot_pass(statuses):
    cells=[FlowResult(flow_name="synthetic",profile="admin",target_org="sample-sandbox",overall_status=s) for s in statuses]
    score=score_run(cells)
    assert score["coverage_status"]=="NOT_CHECKED"
    assert score["score"]==0
    assert exit_code(score,True,False)==2


@pytest.mark.parametrize("registry", [False, True])
def test_missing_or_failed_cleanup_is_explicit_incomplete(tmp_path,registry):
    class Flow(BaseFlow):
        name="synthetic_mutation"
        spec=FlowSpec(name=name,workflow="E1",writes=True,objects=["Account"],variations=[Variation("happy")])
    class Client(FakeSfClient):
        def query(self,*args,**kwargs): raise RuntimeError("synthetic query failure")
    async def execute(cell, config):
        return FlowResult(flow_name=cell.flow.name,profile="admin",target_org="sample-sandbox",overall_status="PASS")
    async def preflight(config): return {}
    config={"sf":Client(),"target_org":"sample-sandbox","org_info":SimpleNamespace(detected_org_type="sandbox"),"flows":[Flow()],"cell_executor":execute,"preflight":preflight,"run_dir":str(tmp_path),"manifest_path":str(tmp_path/"manifest.json"),"audit_log":str(tmp_path/"audit.log")}
    if registry:
        path=tmp_path/"registry.yaml"
        path.write_text("objects:\n  Account:\n    test_record_carrier: true\n")
        config["registry_path"]=path
    assert asyncio.run(run_suite(config))==4
    report=json.loads((tmp_path/"manifest.json").read_text())
    assert report["cleanup_status"]==("UNKNOWN" if registry else "NOT_CHECKED")
