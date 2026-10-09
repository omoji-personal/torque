"""Data classes per org: an approved org can carry classes beyond the client's list,
and the guarded classes live only there, in a record an older Torque refuses."""
from collections import namedtuple
import json
import os
from pathlib import Path

import pytest

from torque import cli, consent, gate_connected as gc, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-dev": Org("00D000000000003AAA", "sandbox", False, "https://acme--dev.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
GUARDED = ["counts", "config_records", "test_records"]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    (tmp_path / "a.pdf").write_bytes(b"agreement")
    return Path(os.path.realpath(root))


def record(root, data=("metadata",), org_data=None, orgs=("acme-prod", "acme-dev"), **more):
    item = consent.record_consent(root, "Acme", "2026-09-30", root.parent / "a.pdf", list(data), list(orgs),
                                  ["Contact"], presence=YES, resolve=ORGS.get, org_data=org_data, **more)
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return item


def run(root, command, mode=None):
    return gc.decide_connected("Bash", {"command": command}, root, root, env={"TORQUE_CLIENT": "acme"},
                               permission_mode=mode, session_id="s1", tool_use_id="t1")


def test_extra_classes_apply_to_their_org_only(root):
    item = record(root, org_data={"acme-dev": ["records", "debug_logs"]})
    assert item["schema"] == "torque.consent/1"
    assert [o.get("extra_data") for o in item["approved_orgs"]] == [None, ["records", "debug_logs"]]
    stored = consent.load_consent(root, "Acme")
    assert consent.consent_problems(stored, client="acme") == []
    assert consent.data_allowed(stored) == {"metadata"}
    assert consent.data_allowed(stored, "acme-prod") == {"metadata"}
    assert consent.data_allowed(stored, "acme-dev") == {"metadata", "records", "debug_logs"}
    assert consent.data_allowed(stored, "elsewhere") == {"metadata"}
    query = "sf data query -q 'SELECT Id FROM Account' -o "
    assert run(root, query + "acme-dev").action == "allow"
    denied = run(root, query + "acme-prod")
    assert denied.action == "deny" and "record data" in denied.reason
    assert run(root, "sf apex get log -o acme-dev").action == "allow"
    assert run(root, "sf apex get log -o acme-prod").action == "deny"
    assert run(root, "sf sobject describe -s Account -o acme-prod").action == "allow"


def test_an_older_reader_sees_the_narrower_client_list(root):
    record(root, org_data={"acme-dev": ["records"]})
    stored = consent.load_consent(root, "Acme")
    older = {c for c in stored["data_allowed"] if c in consent.DATA_CLASSES}     # what 2.0.0a20 computes
    assert older == {"metadata"} and stored["schema"] == "torque.consent/1"


@pytest.mark.parametrize("org_data,why", [
    ({"elsewhere": ["records"]}, "not one of the approved orgs"),
    ({"acme-dev": ["everything"]}, "unknown data class"),
    ({"acme-dev": []}, "unknown data class"),
    ({"acme-dev": "records"}, "unknown data class"),
])
def test_bad_org_data_is_refused(root, org_data, why):
    with pytest.raises(ws.WorkspaceError, match=why):
        record(root, org_data=org_data)


def test_guarded_classes_need_the_setting_and_live_per_org_in_schema_2(root):
    with pytest.raises(ws.WorkspaceError, match="guarded reads are off"):
        record(root, org_data={"acme-prod": GUARDED})
    with pytest.raises(ws.WorkspaceError, match="unknown data class"):           # never in the client list
        record(root, data=["metadata", "counts"])
    ws.set_guarded_reads(root, "on", presence=YES)
    with pytest.raises(ws.WorkspaceError, match="unknown data class"):
        record(root, data=["metadata", "counts"])
    with pytest.raises(ws.WorkspaceError, match="needs metadata"):
        record(root, data=["local_artifacts"], org_data={"acme-prod": ["counts"]})
    item = record(root, org_data={"acme-prod": GUARDED})
    assert item["schema"] == "torque.consent/2"
    stored = consent.load_consent(root, "Acme")
    assert consent.consent_problems(stored, client="acme") == []
    assert consent.data_allowed(stored, "acme-prod") == {"metadata", *GUARDED}
    assert consent.data_allowed(stored, "acme-dev") == {"metadata"}
    # Recording again without a guarded class goes back to the format every Torque reads.
    assert record(root)["schema"] == "torque.consent/1"


def test_a_hand_edited_record_is_refused(root):
    ws.set_guarded_reads(root, "on", presence=YES)
    record(root, org_data={"acme-prod": GUARDED})
    path = root / "clients" / "acme" / "consent.json"
    stored = json.loads(path.read_text(encoding="utf-8"))

    def problems(change):
        item = json.loads(json.dumps(stored))
        change(item)
        return consent.consent_problems(item, client="acme")
    assert problems(lambda i: i.update(schema="torque.consent/1")) == ["a guarded data class needs the newer "
                                                                       "consent format"]
    assert problems(lambda i: i.update(schema="torque.consent/3")) == ["the consent record has an unknown schema"]
    assert problems(lambda i: i["data_allowed"].append("counts")) == ["the data classes are malformed"]
    assert problems(lambda i: i["approved_orgs"][0].update(extra_data=["everything"])) == [
        "the data classes are malformed"]
    assert problems(lambda i: i["approved_orgs"][0].update(extra_data="counts")) == ["the data classes are malformed"]
    assert problems(lambda i: i.update(data_allowed=["local_artifacts"])) == [
        "a guarded data class needs metadata for the same org"]


def test_the_command_line_takes_org_data(root, capsys, monkeypatch):
    monkeypatch.setattr(consent, "_resolver", lambda: ORGS.get)
    monkeypatch.setattr(consent, "_require_operator", lambda presence, confirm=None: None)
    args = ["client", "consent", "record", "--workspace", str(root), "--client", "Acme", "--agreed-on", "2026-09-30",
            "--evidence", str(root.parent / "a.pdf"), "--data", "metadata", "--org", "acme-prod", "--org", "acme-dev"]
    assert cli.main([*args, "--org-data", "acme-dev=records,debug_logs"]) == 0
    assert "acme-dev (sandbox" in capsys.readouterr().out
    assert consent.load_consent(root, "Acme")["approved_orgs"][1]["extra_data"] == ["records", "debug_logs"]
    assert cli.main([*args, "--org-data", "acme-dev"]) == 2
    assert "ALIAS=class" in capsys.readouterr().err
    assert cli.main(["client", "consent", "show", "--workspace", str(root), "--client", "Acme"]) == 0
    assert "acme-dev: also records, debug_logs" in capsys.readouterr().out
