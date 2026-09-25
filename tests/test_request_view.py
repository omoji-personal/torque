import hashlib
import json
import os

import pytest

from delegated_helpers import ORGS, delegated_workspace, flow_request
from torque import approval, changes, cli

pytestmark = pytest.mark.skipif(not hasattr(os, "getuid"), reason="tier 2 is POSIX only")
SCOPE = ("operation", "org_alias", "org_id_18", "org_kind", "components", "cwd")


def test_spelling_wrappers_and_non_scope_flags_do_not_change_the_fields(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    spellings = (None,
                 ["sf", "project", "deploy", "start", "-m", "Flow:Case_Escalation", "-o", "acme-dev", "--wait", "10"],
                 ["torque", "deploy", "--metadata", "Flow:Case_Escalation", "--target-org", "acme-dev"])
    views = [approval.request_view(root, "Acme", flow_request(root, argv)["id"], resolve=ORGS.get)
             for argv in spellings]
    assert [{k: v[k] for k in SCOPE} for v in views] == [{k: views[0][k] for k in SCOPE}] * 3
    assert views[0]["operation"] == "deploy" and views[0]["components"] == ["Flow:Case_Escalation"]
    assert views[0]["org_id_18"] == "00D000000000003AAA" and views[0]["org_kind"] == "developer"
    assert len({v["payload"]["digest"] for v in views}) == 1
    content = hashlib.sha256(b"<Flow>v2</Flow>").hexdigest()
    assert views[0]["payload"]["files"] == [
        {"path": "force-app/main/default/flows/Case_Escalation.flow-meta.xml", "sha256": content}]


def test_request_sha256_is_over_the_exact_file(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    path = root / "clients" / "acme" / "approvals" / "requests" / f"{req['id']}.json"
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    assert view["request_sha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    assert view["expires_at"] > view["created_at"]
    assert any("acme-dev" in line for line in view["screen"])


def test_record_writes_normalize_to_object_and_id(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    long = ["sf", "data", "update", "record", "--sobject", "Contact", "--record-id", "003000000000001AAA",
            "--values", "Title=Steward", "--target-org", "acme-dev"]
    short = ["sf", "data", "update", "record", "-s", "Contact", "-i", "003000000000001AAA",
             "-v", "Title=Steward", "-o", "acme-dev"]
    views = [approval.request_view(root, "Acme", flow_request(root, a)["id"], resolve=ORGS.get) for a in (long, short)]
    assert views[0]["operation"] == views[1]["operation"] == "data update"
    assert views[0]["components"] == views[1]["components"] == ["Record:Contact:003000000000001AAA"]


def test_record_upserts_normalize_to_object_and_external_field(tmp_path, monkeypatch):
    """F20: an upsert names an object plus external ID field, not a record ID; both
    the long and short flag spellings of --sobject/--external-id must agree."""
    root = delegated_workspace(tmp_path, monkeypatch)
    long = ["sf", "data", "upsert", "record", "--sobject", "Contact", "--external-id", "Email_Ext_Id__c",
            "--values", "Email_Ext_Id__c=steward@acme.example Title=Steward", "--target-org", "acme-dev"]
    short = ["sf", "data", "upsert", "record", "-s", "Contact", "-i", "Email_Ext_Id__c",
             "-v", "Email_Ext_Id__c=steward@acme.example Title=Steward", "-o", "acme-dev"]
    views = [approval.request_view(root, "Acme", flow_request(root, a)["id"], resolve=ORGS.get) for a in (long, short)]
    assert views[0]["operation"] == views[1]["operation"] == "data upsert"
    assert views[0]["components"] == views[1]["components"] == ["Record:Contact:external:Email_Ext_Id__c"]


def test_browser_request_view(tmp_path, monkeypatch):
    root = delegated_workspace(tmp_path, monkeypatch)
    cid = changes.create_change(root, "Acme", "Check", "Visit saves", [], "acme-dev")["id"]
    req = approval.create_request(root, "Acme", cid, "acme-dev", browser_minutes=20,
                                  purpose="Login As check of the visit form", resolve=ORGS.get)
    view = approval.request_view(root, "Acme", req["id"], resolve=ORGS.get)
    assert (view["operation"], view["components"], view["payload"]["digest"]) == ("browser window", [], None)


def test_cli_show_json(tmp_path, monkeypatch, capsys):
    root = delegated_workspace(tmp_path, monkeypatch)
    req = flow_request(root)
    monkeypatch.setattr(approval, "_resolver", lambda resolve: resolve or ORGS.get)
    assert cli.main(["approval", "show", req["id"], "--workspace", str(root), "--client", "Acme", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == approval.VIEW_SCHEMA
