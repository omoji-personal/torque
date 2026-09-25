import json
import os
import subprocess

import pytest

from torque import before_state as bs, changes, workspace as ws


@pytest.fixture
def change(tmp_path):
    root = ws.init_workspace(tmp_path / "firm", "Synthetic consultants")
    ws.add_client(root, "Acme")
    ws.add_client(root, "Beta")
    return root, changes.create_change(root, "Acme", "Flow fix", "Cases escalate", [], "acme-prod")["id"]


def retrieved(tmp_path):
    src = tmp_path / "retrieved" / "force-app" / "main" / "default"
    (src / "flows").mkdir(parents=True)
    (src / "flows" / "Case_Escalation.flow-meta.xml").write_text("<Flow/>", encoding="utf-8")
    (src / "objects" / "Case" / "fields").mkdir(parents=True)
    (src / "objects" / "Case" / "fields" / "Tier__c.field-meta.xml").write_text("<x/>", encoding="utf-8")
    return tmp_path / "retrieved"


def test_import_hashes_every_file(change, tmp_path):
    root, cid = change
    item = bs.import_before_state(root, "Acme", cid, retrieved(tmp_path))
    assert {f["path"].split("/")[-1] for f in item["files"]} == {"Case_Escalation.flow-meta.xml",
                                                                 "Tier__c.field-meta.xml"}
    assert bs.load_before_state(root, "Acme", cid, item["event_id"])["sha256"] == item["sha256"]
    assert "before_state" in [e["kind"] for e in changes.get_change(root, "Acme", cid)["events"]]


def test_changed_before_state_is_refused(change, tmp_path):
    root, cid = change
    item = bs.import_before_state(root, "Acme", cid, retrieved(tmp_path))
    stored = root / "clients" / "acme" / "changes" / cid / item["files"][0]["path"]
    stored.write_text("<edited/>", encoding="utf-8")
    with pytest.raises(ws.WorkspaceError, match="changed"):
        bs.load_before_state(root, "Acme", cid, item["event_id"])


def test_other_client_folder_refused(change, tmp_path):
    root, cid = change
    other = root / "clients" / "beta" / "artifacts" / "x"
    other.mkdir(parents=True)
    (other / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ws.WorkspaceError, match="different client"):
        bs.import_before_state(root, "Acme", cid, other)


def test_empty_directory_refused(change, tmp_path):
    root, cid = change
    (tmp_path / "empty").mkdir()
    with pytest.raises(ws.WorkspaceError, match="no files"):
        bs.import_before_state(root, "Acme", cid, tmp_path / "empty")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_links_refused(change, tmp_path):
    root, cid = change
    src = retrieved(tmp_path)
    (src / "link").symlink_to(root / "clients" / "beta" / "context.md")
    with pytest.raises(ws.WorkspaceError, match="links"):
        bs.import_before_state(root, "Acme", cid, src)


def test_coverage_by_component(change, tmp_path):
    root, cid = change
    item = bs.import_before_state(root, "Acme", cid, retrieved(tmp_path))
    assert bs.coverage(["Flow:Case_Escalation", "CustomField:Case.Tier__c"], item) == []
    assert bs.coverage(["ApexClass:CaseRouter", "ApexClass:*"], item) == ["ApexClass:CaseRouter", "ApexClass:*"]


def test_coverage_metadata_format():
    before = {"files": [{"path": "evidence/before-1/unpackaged/flows/Case_Escalation.flow"},
                        {"path": "evidence/before-1/unpackaged/objects/Case.object"}]}
    assert bs.coverage(["Flow:Case_Escalation", "CustomField:Case.Tier__c", "CustomObject:Case"], before) == []


def test_components_from_command(tmp_path):
    argv = ["sf", "project", "deploy", "start", "-m", "Flow:Case_Escalation", "--metadata=ApexClass:A", "-o", "x"]
    assert bs.deploy_components(argv, tmp_path) == ["Flow:Case_Escalation", "ApexClass:A"]
    manifest = tmp_path / "package.xml"
    manifest.write_text("<Package><types><members>X</members><name>ApexClass</name></types></Package>",
                        encoding="utf-8")
    assert bs.deploy_components(["sf", "project", "deploy", "start", "-x", str(manifest)], tmp_path) == ["ApexClass:X"]
    with pytest.raises(ws.WorkspaceError):
        bs.deploy_components(["-x", "missing.xml"], tmp_path)


def test_capture_records_runs_one_read_per_record(change):
    root, cid = change
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": 0, "result": {"Id": "001x", "Name": "A"}}), "")

    item = bs.capture_records(root, "Acme", cid, "acme-prod", ["Account:001x"], run=fake)
    assert calls[0][:4] == ["sf", "data", "get", "record"] and "acme-prod" in calls[0]
    assert bs.coverage(["Record:Account:001x"], item) == []
    with pytest.raises(ws.WorkspaceError, match="Object:Id"):
        bs.capture_records(root, "Acme", cid, "acme-prod", ["Account;rm:001x"], run=fake)


def test_capture_metadata_records_job(change):
    root, cid = change

    def fake(cmd, **kw):
        out = cmd[cmd.index("--target-metadata-dir") + 1]
        folder = os.path.join(out, "unpackaged", "flows")
        os.makedirs(folder)
        with open(os.path.join(folder, "Case_Escalation.flow"), "w", encoding="utf-8") as handle:
            handle.write("<Flow/>")
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"status": 0, "result": {"id": "09S000000000001"}}), "")

    item = bs.capture_metadata(root, "Acme", cid, "acme-prod", ["Flow:Case_Escalation"], run=fake)
    assert item["job"] == "09S000000000001" and bs.coverage(["Flow:Case_Escalation"], item) == []


def test_failed_capture_records_nothing(change):
    root, cid = change

    def fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, json.dumps({"status": 1, "message": "no such org"}), "")

    with pytest.raises(ws.WorkspaceError, match="no before-state"):
        bs.capture_metadata(root, "Acme", cid, "acme-prod", ["Flow:X"], run=fake)
    assert "before_state" not in [e["kind"] for e in changes.get_change(root, "Acme", cid)["events"]]


# R44 (D4 fix round 1): write_components must stay exactly a15's behavior for an
# upsert (empty for both flag spellings; grant()'s production before-state check,
# via _derive_command, reads this function's output, not the view-only
# Record:Object:external:Field normalization added in approval.view_components).


def test_write_components_upsert_matches_a15_both_spellings():
    long = ["sf", "data", "upsert", "record", "--sobject", "Contact", "--external-id", "Email_Ext_Id__c",
            "--values", "Email_Ext_Id__c=steward@acme.example Title=Steward", "--target-org", "acme-dev"]
    assert bs.write_components(long, None) == []
    # a15's pre-existing quirk (unchanged here): the short -i flag collides with
    # --record-id's short spelling, so a short-flag upsert is read as a (bogus)
    # record ID rather than empty. Recorded here so a future change to that
    # quirk is a deliberate, tested decision, not an accidental side effect.
    short = ["sf", "data", "upsert", "record", "-s", "Contact", "-i", "Email_Ext_Id__c",
             "-v", "Email_Ext_Id__c=steward@acme.example Title=Steward", "-o", "acme-dev"]
    assert bs.write_components(short, None) == ["Record:Contact:Email_Ext_Id__c"]
