"""Offline tests for the model-neutral Salesforce advisory package.

No test in this module invokes ``sf`` or contacts an org. Live-facing behavior
is exercised through deterministic fake clients or a mocked subprocess call.
"""

from __future__ import annotations

import ast
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from jsc_advisory import cli
from jsc_advisory.catalogue import closure_report, entries, platform_notes, provenance
from jsc_advisory.evidence import NOT_CHECKED, NOT_OBSERVED, UNKNOWN, build_evidence
from jsc_advisory.flow import verify_flow
from jsc_advisory.impact import (
    DEFAULT_RELATIONSHIP_QUERY_BUDGET,
    ImpactRequest,
    build_impact,
)
from jsc_advisory.receipt import build_receipt
from jsc_advisory.sf import AdvisorySafetyError, SfClient, Unknown


class FakeClient:
    """Minimal in-memory Salesforce client used by all behavior tests."""

    def __init__(self, *, query_handler=None, descriptions=None):
        self.query_handler = query_handler or (lambda _soql, _tooling: {
            "records": [], "totalSize": 0,
        })
        self.descriptions = descriptions or {}
        self.calls = []

    def query(self, target_org, soql, *, tooling=False, timeout=120):
        self.calls.append(("query", target_org, soql, tooling, timeout))
        answer = self.query_handler(soql, tooling)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def describe(self, target_org, sobject, *, timeout=120):
        self.calls.append(("describe", target_org, sobject, False, timeout))
        answer = self.descriptions.get(sobject, {"fields": [], "childRelationships": []})
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_catalogue_is_complete_ranked_and_flow_guidance_is_consistent():
    catalogue = entries()
    assert len(catalogue) == 46
    assert len({item["id"] for item in catalogue}) == len(catalogue)

    flow_activation = next(item for item in catalogue
                           if item["id"] == "flow-activation-on-deploy")
    assert "FlowDefinition.ActiveVersionId" in flow_activation["remedy"]
    assert "Do not infer activation" in flow_activation["remedy"]

    source = provenance()
    assert len(source["source_sha256"]) == 64
    assert source["adaptations"][0]["entry"] == "flow-activation-on-deploy"

    notes = platform_notes(
        "sf project deploy start --metadata Flow:Example_Flow --target-org dev",
        limit=5,
    )
    assert notes
    assert any(item["id"] == "flow-activation-on-deploy" for item in notes)


def test_closure_report_distinguishes_no_match_from_no_recorded_requirement():
    no_match = closure_report("definitely-not-a-salesforce-command")
    assert no_match == {"requirements": [], "matched": [], "unrecorded": []}

    matched = closure_report("sf project retrieve start --metadata Flow:Example")
    assert matched["matched"]
    assert set(matched["unrecorded"]).issubset(set(matched["matched"]))


def test_catalogue_reads_are_safe_for_concurrent_agent_processes():
    command = "sf project deploy start --metadata Flow:Example --target-org dev"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: platform_notes(command, 3), range(64)))
    first_ids = [item["id"] for item in results[0]]
    assert first_ids
    assert all([item["id"] for item in result] == first_ids for result in results)


def test_sf_boundary_refuses_every_write_shape_before_process_launch(monkeypatch):
    launched = False

    def forbidden_launch(*_args, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("subprocess must not launch")

    monkeypatch.setattr("jsc_advisory.sf.subprocess.run", forbidden_launch)
    client = SfClient()

    for argv in (
        ["data", "update", "record", "--sobject", "Account"],
        ["project", "deploy", "start"],
        ["apex", "run"],
        ["org", "delete", "scratch"],
        ["data", "query", "--target-org", "fake-org", "--json", "--query",
         "SELECT Id FROM Account", "--output-file", "/tmp/result.json"],
    ):
        with pytest.raises(AdvisorySafetyError):
            client.run(argv)

    assert launched is False
    SfClient._assert_read_only([
        "data", "query", "--target-org", "fake-org", "--json", "--query",
        "SELECT Id FROM Account",
    ])
    SfClient._assert_read_only([
        "data", "query", "--target-org", "fake-org", "--json", "--query",
        "SELECT Id FROM ApexClass", "--use-tooling-api",
    ])
    SfClient._assert_read_only([
        "sobject", "describe", "--target-org", "fake-org", "--sobject", "Account", "--json",
    ])


def test_flow_verifier_uses_tooling_definition_and_standard_view():
    def answer(soql, tooling):
        if "FROM FlowDefinition WHERE" in soql:
            assert tooling is True
            return {"records": [{
                "DeveloperName": "Example_Flow",
                "ActiveVersionId": "301-active",
                "LatestVersionId": "301-draft",
            }], "totalSize": 1}
        if "FROM FlowDefinitionView WHERE" in soql:
            assert tooling is False
            return {"records": [{
                "ApiName": "Example_Flow", "IsActive": True,
                "ProcessType": "AutoLaunchedFlow", "TriggerType": "RecordAfterSave",
            }], "totalSize": 1}
        raise AssertionError(soql)

    client = FakeClient(query_handler=answer)
    report = verify_flow(client, "fake-org", "Example_Flow")

    assert report["status"] == "ACTIVE"
    assert report["complete"] is True
    assert [call[3] for call in client.calls] == [True, False]
    assert all(call[0] == "query" for call in client.calls)


def test_flow_verifier_preserves_source_disagreement():
    def answer(soql, _tooling):
        if "FROM FlowDefinition WHERE" in soql:
            return {"records": [{"ActiveVersionId": "301-active"}], "totalSize": 1}
        return {"records": [{"IsActive": False}], "totalSize": 1}

    report = verify_flow(FakeClient(query_handler=answer), "fake-org", "Example_Flow")
    assert report["status"] == "INCONSISTENT"
    assert report["complete"] is False
    assert report["undetermined"]


def test_flow_verifier_does_not_ignore_presence_disagreement():
    def answer(soql, _tooling):
        if "FROM FlowDefinition WHERE" in soql:
            return {"records": [{"ActiveVersionId": "301-active"}], "totalSize": 1}
        return {"records": [], "totalSize": 0}

    report = verify_flow(FakeClient(query_handler=answer), "fake-org", "Example_Flow")
    assert report["status"] == "INCONSISTENT"
    assert report["complete"] is False
    assert "disagree about whether" in report["undetermined"][0]


def test_impact_reports_only_known_coverage_and_names_omissions():
    def answer(soql, _tooling):
        if soql.startswith("SELECT COUNT() FROM Account"):
            return {"records": [], "totalSize": 2}
        if "FROM ApexTrigger" in soql:
            return {"records": [{
                "Name": "AccountTrigger", "Status": "Active",
                "UsageBeforeUpdate": True,
            }], "totalSize": 1}
        if "FROM FlowDefinitionView" in soql:
            return {"records": [{
                "ApiName": "Account_After_Save", "Label": "Account After Save",
                "IsActive": True, "TriggerType": "RecordAfterSave",
                "RecordTriggerType": "CreateAndUpdate",
            }], "totalSize": 1}
        if "FROM ValidationRule" in soql:
            return {"records": [{"ValidationName": "Require_Type", "Active": True}],
                    "totalSize": 1}
        if "FROM WorkflowRule" in soql:
            return {"records": [], "totalSize": 0}
        raise AssertionError(soql)

    report = build_impact(
        FakeClient(query_handler=answer),
        ImpactRequest("fake-org", "Account", "update", "Type='Prospect'"),
    )

    assert report["scope"] == 2
    assert report["triggers"] == ["AccountTrigger"]
    assert report["flows"] == ["Account After Save [RecordAfterSave]"]
    assert report["validation_rules"] == ["Require_Type"]
    assert report["complete_within_covered_surfaces"] is True
    assert "external side effects such as callouts, email, and async work" in report["not_covered"]
    assert report["advisory"] is True


def test_impact_does_not_convert_unavailable_source_to_empty():
    def answer(soql, _tooling):
        if "FROM FlowDefinitionView" in soql:
            return Unknown("simulated API outage")
        return {"records": [], "totalSize": 0}

    report = build_impact(
        FakeClient(query_handler=answer),
        ImpactRequest("fake-org", "Account", "update"),
    )
    assert report["flows"] is None
    assert report["complete_within_covered_surfaces"] is False
    assert any(item.startswith("flows:") for item in report["undetermined"])


def test_impact_caps_are_reported_as_incomplete_not_silently_truncated():
    workflow_rows = [{"Id": f"01Q{i:03d}", "Name": f"Rule_{i}"} for i in range(26)]

    def answer(soql, _tooling):
        if "SELECT Id, Name FROM WorkflowRule" in soql:
            return {"records": workflow_rows, "totalSize": len(workflow_rows)}
        if "SELECT Metadata FROM WorkflowRule" in soql:
            return {"records": [{"Metadata": {"active": False}}], "totalSize": 1}
        return {"records": [], "totalSize": 0}

    report = build_impact(
        FakeClient(query_handler=answer),
        ImpactRequest("fake-org", "Account", "update"),
    )
    assert "UNDETERMINED — cap reached" in report["workflow_rules"][0]
    assert report["complete_within_covered_surfaces"] is False


def test_delete_relationship_fanout_has_an_explicit_read_budget():
    relationships = [
        {"childSObject": f"Cascade_{i}__c", "field": "Account__c",
         "cascadeDelete": True, "restrictedDelete": False}
        for i in range(5)
    ] + [
        {"childSObject": "Restricted__c", "field": "Account__c",
         "cascadeDelete": False, "restrictedDelete": True},
        {"childSObject": "Lookup__c", "field": "Account__c",
         "cascadeDelete": False, "restrictedDelete": False},
    ]

    def answer(soql, _tooling):
        if soql.startswith("SELECT COUNT() FROM Account"):
            return {"records": [], "totalSize": 1}
        return {"records": [], "totalSize": 0}

    client = FakeClient(
        query_handler=answer,
        descriptions={"Account": {"fields": [], "childRelationships": relationships}},
    )
    report = build_impact(
        client,
        ImpactRequest("fake-org", "Account", "delete", "", relationship_query_budget=2),
    )
    rel = report["delete_relationships"]
    relationship_queries = [call for call in client.calls
                            if call[0] == "query" and " IN (SELECT Id FROM Account" in call[2]]

    assert len(relationship_queries) == 2
    assert rel["query_budget"] == {
        "limit": 2, "used": 2, "exhaustive_requested": False, "exhausted": True,
    }
    assert any("budget of 2 exhausted" in item for item in report["undetermined"])
    assert report["complete_within_covered_surfaces"] is False


def test_zero_scope_delete_skips_relationship_count_queries():
    client = FakeClient(
        descriptions={"Account": {
            "fields": [],
            "childRelationships": [{
                "childSObject": "Contact", "field": "AccountId",
                "cascadeDelete": True, "restrictedDelete": False,
            }],
        }},
    )
    report = build_impact(
        client,
        ImpactRequest("fake-org", "Account", "delete", "Name='no match'"),
    )

    assert report["scope"] == 0
    assert report["delete_relationships"]["state"] == "empty-scope"
    assert report["delete_relationships"]["query_budget"]["used"] == 0
    assert not any(" IN (SELECT Id FROM Account" in call[2]
                   for call in client.calls if call[0] == "query")
    assert report["relationship_query_budget"] == DEFAULT_RELATIONSHIP_QUERY_BUDGET


def test_evidence_keeps_not_observed_unknown_and_not_checked_distinct():
    def answer(soql, _tooling):
        if "FROM FieldDefinition" in soql:
            return {"records": [], "totalSize": 0}
        if "FROM FieldPermissions" in soql:
            return Unknown("simulated permissions API outage")
        raise AssertionError(soql)

    report = build_evidence(
        FakeClient(query_handler=answer),
        target_org="fake-org",
        field_name="Account.Tier__c",
    )
    outcomes = {item["layer"]: item["outcome"] for item in report["ledger"]}

    assert outcomes["field_exists"] == NOT_OBSERVED
    assert outcomes["fls"] == UNKNOWN
    assert outcomes["assignment"] == NOT_CHECKED
    assert outcomes["profile_render"] == NOT_CHECKED
    assert report["complete"] is False


def test_receipt_is_an_assessment_not_execution_or_authorization():
    report = build_receipt(
        FakeClient(), target_org="fake-org", sobject="Account", operation="update",
    )
    encoded = json.dumps(report).lower()
    assert report["schema"] == "jsc.advisory.receipt/1"
    assert report["execution_proven"] is False
    assert report["advisory"] is True
    assert "authorization" not in encoded
    assert "approval" not in encoded


def test_default_cli_semantics_never_block_on_incomplete_evidence(monkeypatch, capsys):
    class UnavailableClient(FakeClient):
        def query(self, *_args, **_kwargs):
            raise Unknown("offline test source unavailable")

    monkeypatch.setattr(cli, "SfClient", UnavailableClient)
    base = ["flow", "--target-org", "fake-org", "--api-name", "Example_Flow", "--json"]

    assert cli.main(base) == 0
    default_payload = json.loads(capsys.readouterr().out)
    assert default_payload["complete"] is False
    assert default_payload["status"] == "UNKNOWN"

    assert cli.main([*base, "--strict"]) == 3
    capsys.readouterr()


def test_runtime_package_has_no_filesystem_write_calls():
    package = Path(__file__).resolve().parents[1] / "jsc_advisory"
    prohibited_attributes = {"write_text", "write_bytes", "unlink", "mkdir", "touch"}
    findings = []

    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in prohibited_attributes:
                    findings.append(f"{path.name}:{node.lineno}:{node.func.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "open" and len(node.args) > 1:
                    mode = node.args[1]
                    if isinstance(mode, ast.Constant) and any(flag in str(mode.value)
                                                              for flag in "wax+"):
                        findings.append(f"{path.name}:{node.lineno}:open-{mode.value}")

    assert findings == []


def test_advisory_remains_model_and_hook_independent():
    root = Path(cli.__file__).parent
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name.startswith(("anthropic", "openai", "packages.hooks")) for name in imported)


