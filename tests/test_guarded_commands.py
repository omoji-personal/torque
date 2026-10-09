"""Guarded reads, the commands: their own checks (with the gate out of the picture),
what they print, and what they record."""
from collections import namedtuple
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from torque import consent, guarded, guarded_core as core, workspace as ws
from torque.presence import Presence

Org = namedtuple("Org", "org_id_18 detected_org_type is_production instance_url", defaults=(None,))
ORGS = {"acme-prod": Org("00D000000000002AAA", "production", True, "https://acme.my.salesforce.com"),
        "acme-dev": Org("00D000000000003AAA", "sandbox", False, "https://acme--dev.sandbox.my.salesforce.com")}
YES = lambda: Presence(True, "")
NO = lambda: Presence(False, "not a terminal")
OK = lambda: True
E = core.GuardedError
ACCOUNT, OPP, OTHER = "001000000000001AAA", "006000000000001AAA", "001000000000002AAA"
SENTINEL = "REAL-VALUE-73482"


def fld(name, type_, **more):
    base = {"name": name, "type": type_, "label": name, "groupable": type_ in ("picklist", "boolean"),
            "filterable": type_ not in ("textarea", "address"), "aggregatable": True, "calculated": False,
            "calculatedFormula": None, "nameField": False, "encrypted": False, "compoundFieldName": None,
            "picklistValues": [{"value": v, "active": True} for v in more.pop("values", [])]}
    base.update(more)
    return base


DESCRIBES = {
    "opportunity": {"name": "Opportunity", "queryable": True, "fields": [
        fld("Id", "id"), fld("Name", "string", nameField=True), fld("AccountId", "reference"),
        fld("StageName", "picklist", values=["Prospecting", "Closed Won"]), fld("IsWon", "boolean"),
        fld("Amount", "currency"), fld("CloseDate", "date"), fld("Email__c", "email"),
        fld("Gender__c", "picklist", values=["F", "M"]),
        fld("Parent_Name__c", "string", calculated=True, calculatedFormula="Account.Name"),
        fld("Total__c", "currency", calculated=True)]},
    "account": {"name": "Account", "queryable": True, "fields": [
        fld("Id", "id"), fld("Name", "string", nameField=True), fld("IsPersonAccount", "boolean"),
        fld("Phone", "phone"), fld("ParentId", "reference"), fld("Rating", "picklist", values=["Hot"])]},
    "trigger_handler__c": {"name": "Trigger_Handler__c", "queryable": True, "fields": [
        fld("Id", "id"), fld("Name", "string", nameField=True), fld("Class__c", "string"),
        fld("Active__c", "boolean"), fld("Owner_Email__c", "email"), fld("Notes__c", "textarea")]},
    "settings__c": {"name": "Settings__c", "queryable": True, "customSetting": True, "fields": [
        fld("Id", "id"), fld("Name", "string", nameField=True), fld("Batch_Size__c", "double"),
        fld("Contact_Email__c", "email")]},
}


class Sf:
    """A stand-in for the Salesforce CLI: describe from DESCRIBES, queries from `answer`."""

    def __init__(self, answer=None, facts=()):
        self.answer, self.facts, self.calls, self.queries = answer or (lambda soql: []), facts, [], []

    def __call__(self, argv, **options):
        self.calls.append(list(argv))
        assert options.get("capture_output") and options.get("stdin") is not None
        if argv[1:3] == ["sobject", "describe"]:
            payload = DESCRIBES.get(argv[argv.index("--sobject") + 1].casefold())
            body = {"status": 0, "result": payload} if payload else {"status": 1, "message": SENTINEL}
        else:
            soql = Path(argv[argv.index("--file") + 1]).read_text(encoding="utf-8")
            self.queries.append(soql)
            if " FROM FieldDefinition " not in soql:
                records = self.answer(soql)
            elif self.facts != ():
                records = self.facts
            else:       # the org's answer when nothing is classified: a row for every field
                name = soql.rsplit("'", 2)[-2].casefold()
                records = [{"QualifiedApiName": f["name"], "DataType": "Text", "ComplianceGroup": None,
                            "SecurityClassification": None} for f in DESCRIBES.get(name, {"fields": []})["fields"]]
            body = records if isinstance(records, dict) else {"status": 0, "result": {"done": True, "records": records}}
        return SimpleNamespace(returncode=0 if body.get("status") == 0 else 1, stdout=json.dumps(body), stderr=SENTINEL)


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for name in ("TORQUE_CLIENT", "TORQUE_LAUNCH", "TORQUE_WORKSPACE", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(consent, "_user", lambda: "consultant")
    # A session is told by its host's markers here, not by what process runs the tests.
    monkeypatch.setattr(guarded, "_agent_reason", lambda env: "an agent session" if "CLAUDECODE" in env else "")
    root = ws.init_workspace(tmp_path / "firm", "Firm")
    ws.add_client(root, "Acme")
    ws.add_client(root, "Other")
    ws.set_ai_access(root, "connected", approval="required", presence=YES)
    ws.set_guarded_reads(root, "on", presence=YES)
    letter = tmp_path / "a.pdf"
    letter.write_bytes(b"agreement")
    consent.record_consent(root, "Acme", "2026-09-30", letter, ["metadata"], ["acme-prod", "acme-dev"], ["Contact"],
                           presence=YES, resolve=ORGS.get,
                           org_data={"acme-prod": ["counts", "config_records", "test_records"]})
    consent.sign_off(root, "Acme", "Reviewer", presence=YES)
    return Path(os.path.realpath(root))


def kw(sf=None, **more):
    return {"resolve": ORGS.get, "run": sf or Sf(), "env": {}, **more}


def admin(**more):
    return {"presence": YES, "confirm": OK, "resolve": ORGS.get, "env": {}, **more}


def release(w, target, level="exact", sf=None, **more):
    return guarded.policy_release(w, "Acme", "acme-prod", target, level, run=sf or Sf(), **admin(**more))


def activity(w):
    folder = w / "clients" / "acme" / "approvals" / guarded.ACTIVITY_DIR
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []


# ---- counts and fill

def test_counts_end_to_end(w):
    release(w, "Opportunity.StageName")
    release(w, "Opportunity.IsWon")
    sf = Sf(lambda soql: [{"attributes": {}, "d0": "Closed Won", "n": 12}, {"attributes": {}, "d0": "Prospecting", "n": 4},
                          {"attributes": {}, "d0": SENTINEL, "n": 9}])
    out = guarded.counts(w, "Acme", "acme-prod", "opportunity", ["StageName"], ["IsWon = true"], **kw(sf))
    assert sf.queries[-1] == ("SELECT StageName d0, COUNT(Id) n FROM Opportunity WHERE IsWon = true "
                              "GROUP BY StageName LIMIT 201")
    assert out["rows"] == [{"values": ["Closed Won"], "count": 12}, {"values": ["<other>"], "count": 9}]
    text = "\n".join(guarded._lines(out))
    assert "Prospecting" not in text and SENTINEL not in text and "smaller than 5" in text
    [entry] = activity(w)
    assert entry["lane"] == "counts" and entry["object"] == "Opportunity" and entry["groups_shown"] == 2
    assert entry["filters"] == ["IsWon = true"] and SENTINEL not in json.dumps(entry)


def test_counts_refuses_what_is_not_released_before_any_data_query(w):
    sf = Sf()
    for group, where, why in ((["StageName"], [], "not released"), ([], ["Name = 'Jane'"], "not released"),
                              ([], ["Gender__c != null"], "gender"), (["Account.Name"], [], "relationship")):
        with pytest.raises(E, match=why):
            guarded.counts(w, "Acme", "acme-prod", "Opportunity", group, where, **kw(sf))
    assert all(" FROM FieldDefinition " in q for q in sf.queries) and activity(w) == []


def test_fill(w):
    sf = Sf(lambda soql: [{"attributes": {}, "n": 100, "f0": 60, "f1": 99}])
    out = guarded.fill(w, "Acme", "acme-prod", "Opportunity", ["Email__c", "Amount"], **kw(sf))
    assert out["total"] == 100 and out["rows"] == [{"field": "Email__c", "filled": 60}, {"field": "Amount", "filled": None}]
    small = guarded.fill(w, "Acme", "acme-prod", "Opportunity", ["Email__c"],
                         **kw(Sf(lambda soql: [{"n": 3, "f0": 1}])))
    empty = guarded.fill(w, "Acme", "acme-prod", "Opportunity", ["Email__c"],
                         **kw(Sf(lambda soql: [{"n": 0, "f0": 0}])))
    assert guarded._lines(small) == guarded._lines(empty) and "not shown" in guarded._lines(small)[0]


# ---- configuration objects

def test_config_shows_only_the_released_fields(w):
    guarded.policy_release_object(w, "Acme", "acme-prod", "Trigger_Handler__c", ["Class__c", "Active__c"],
                                  records=True, run=Sf(), **admin())
    rows = [{"attributes": {}, "Id": "a01000000000001AAA", "Name": SENTINEL, "Class__c": "ACCT_TDTM", "Active__c": True,
             "Owner_Email__c": SENTINEL, "Notes__c": SENTINEL}]
    sf = Sf(lambda soql: rows)
    out = guarded.config(w, "Acme", "acme-prod", "Trigger_Handler__c", **kw(sf))
    assert sf.queries[-1] == "SELECT Id, Class__c, Active__c FROM Trigger_Handler__c ORDER BY Id LIMIT 200"
    assert out["rows"] == [{"Id": "a01000000000001AAA", "Class__c": "ACCT_TDTM", "Active__c": True}]
    out = guarded.config(w, "Acme", "acme-prod", "Trigger_Handler__c", ["Name", "Class__c", "Owner_Email__c", "Notes__c"],
                         **kw(sf))
    assert out["rows"] == [{"Id": "a01000000000001AAA", "Name": "<set>", "Class__c": "ACCT_TDTM",
                            "Owner_Email__c": "<set>", "Notes__c": "<set>"}]
    assert SENTINEL not in "\n".join(guarded._lines(out)) and SENTINEL not in json.dumps(activity(w))
    with pytest.raises(E, match="not released as a configuration object"):
        guarded.config(w, "Acme", "acme-prod", "Settings__c", **kw())


def test_object_release_rules(w):
    go = lambda name, fields=(), **more: guarded.policy_release_object(w, "Acme", "acme-prod", name, fields, run=Sf(),
                                                                       **admin(), **more)
    assert go("Settings__c") == {"released_object": "Settings__c", "fields": ["*"]}
    with pytest.raises(E, match="people"):
        go("Account", ["Rating"], records=True)
    with pytest.raises(E, match="people"):
        go("Opportunity", ["StageName"], records=True)
    with pytest.raises(E, match="acknowledge-records"):
        go("Trigger_Handler__c", ["Class__c"])
    with pytest.raises(E, match="email"):
        go("Trigger_Handler__c", ["Owner_Email__c"], records=True)
    row = [{"Id": "a02000000000001AAA", "Name": "Default", "Batch_Size__c": 200.0, "Contact_Email__c": SENTINEL}]
    out = guarded.config(w, "Acme", "acme-prod", "Settings__c", **kw(Sf(lambda soql: row)))
    assert out["rows"] == [{"Id": "a02000000000001AAA", "Name": "Default", "Batch_Size__c": 200.0,
                            "Contact_Email__c": "<set>"}]


# ---- test records

def register(w, rid=ACCOUNT, name="Account", sf=None, **more):
    sf = sf or Sf(lambda soql: [{"Id": rid}])
    return guarded.test_records_add(w, "Acme", "acme-prod", name, rid, run=sf, out=io.StringIO(), **admin(), **more)


def test_only_a_registered_record_is_read(w):
    with pytest.raises(E, match="not a registered test record"):
        guarded.record(w, "Acme", "acme-prod", "Account", ACCOUNT, every=True, **kw())
    assert register(w) == {"registered": [ACCOUNT], "already": 0}
    assert register(w) == {"registered": [], "already": 1}
    row = {"attributes": {}, "Id": ACCOUNT, "Name": "Test Donor", "IsPersonAccount": False, "Phone": "555-0100",
           "ParentId": OTHER, "Rating": "Hot"}
    sf = Sf(lambda soql: [row])
    out = guarded.record(w, "Acme", "acme-prod", "Account", ACCOUNT[:15], every=True, **kw(sf))
    assert sf.queries[-1].endswith(f"FROM Account WHERE Id = '{ACCOUNT}' LIMIT 1")
    assert out["fields"] == {"Id": ACCOUNT, "Name": "Test Donor", "IsPersonAccount": False, "Phone": "555-0100",
                             "ParentId": "<unregistered 001>", "Rating": "Hot"}
    with pytest.raises(E, match="not a registered test record"):          # the right id, the wrong object
        guarded.record(w, "Acme", "acme-prod", "Opportunity", ACCOUNT, every=True, **kw())
    with pytest.raises(E, match="not a registered"):
        guarded.record(w, "Acme", "acme-prod", "Account", OTHER, every=True, **kw())
    with pytest.raises(E, match="--fields"):
        guarded.record(w, "Acme", "acme-prod", "Account", ACCOUNT, **kw())
    entry = activity(w)[-1]
    assert entry["fields"]["Name"] == "shown" and entry["fields"]["ParentId"] == "masked" and "Test Donor" not in json.dumps(entry)


def test_registration_states_made_up_data_and_names_people_objects(w):
    out = io.StringIO()
    guarded.test_records_add(w, "Acme", "acme-prod", "Account", ACCOUNT, run=Sf(lambda soql: [{"Id": ACCOUNT}]), out=out,
                             **admin())
    assert "made-up data" in out.getvalue() and "holds people's records" in out.getvalue()
    with pytest.raises(E, match="real terminal"):
        guarded.test_records_add(w, "Acme", "acme-prod", "Account", OTHER, run=Sf(), presence=NO, resolve=ORGS.get, env={})
    with pytest.raises(E, match="did not match"):
        guarded.test_records_add(w, "Acme", "acme-prod", "Account", OTHER, run=Sf(lambda soql: [{"Id": OTHER}]),
                                 presence=YES, confirm=lambda: False, resolve=ORGS.get, env={}, out=io.StringIO())
    with pytest.raises(E, match="no record with that id"):
        guarded.test_records_add(w, "Acme", "acme-prod", "Account", OTHER, run=Sf(), out=io.StringIO(), **admin())
    assert [e["id"] for e in guarded.test_records_list(w, "Acme")] == [ACCOUNT]


def test_children_are_registered_one_by_one_and_derived_values_stay_masked(w):
    children = [{"Id": OPP}, {"Id": "006000000000002AAA"}]
    sf = Sf(lambda soql: children if "WHERE AccountId" in soql else [{"Id": ACCOUNT}])
    out = io.StringIO()
    guarded.test_records_add(w, "Acme", "acme-prod", "Account", ACCOUNT, ["Opportunity.AccountId"], run=sf, out=out,
                             **admin())
    assert "2 Opportunity records" in out.getvalue()
    assert len(guarded.test_records_list(w, "Acme")) == 3
    guarded.test_records_remove(w, "Acme", "006000000000002AAA", presence=YES, confirm=OK)
    rows = [{"Id": OPP, "Name": "Test gift", "AccountId": ACCOUNT, "Parent_Name__c": SENTINEL, "Total__c": 73482.19}]
    sf = Sf(lambda soql: children + [{"Id": "006000000000003AAA"}] if soql.startswith("SELECT Id FROM Opportunity WHERE AccountId")
            else rows)
    out = guarded.related(w, "Acme", "acme-prod", ACCOUNT, "Opportunity.AccountId",
                          ["Name", "AccountId", "Parent_Name__c", "Total__c"], **kw(sf))
    assert sf.queries[-1].endswith(f"WHERE Id IN ('{OPP}')")           # only the registered child is fetched
    assert out["rows"] == [{"Id": OPP, "Name": "Test gift", "AccountId": ACCOUNT, "Parent_Name__c": "<set>",
                            "Total__c": "<set>"}] and out["not_shown"] == 2
    assert SENTINEL not in "\n".join(guarded._lines(out)) and "73482" not in "\n".join(guarded._lines(out))
    with pytest.raises(E, match="not a registered test record"):
        guarded.related(w, "Acme", "acme-prod", OTHER, "Opportunity.AccountId", every=True, **kw())


# ---- the command's own checks, one at a time, with no gate

def test_each_precondition_refuses_before_any_org_call(w, tmp_path):
    sf, resolved = Sf(), []
    resolve = lambda alias: resolved.append(alias) or ORGS.get(alias)
    go = lambda **more: guarded.counts(**{"workspace": w, "client": "Acme", "alias": "acme-prod", "sobject": "Opportunity",
                                          "resolve": resolve, "run": sf, "env": {}, **more})
    with pytest.raises(E, match="not in this client's consent"):
        go(alias="elsewhere")
    with pytest.raises(E, match="does not cover counts"):               # acme-dev has metadata only
        go(alias="acme-dev")
    with pytest.raises(E, match="consent is not usable"):
        go(client="Other")
    with pytest.raises(E, match="could not be read"):
        go(client="Nobody")
    with pytest.raises(E, match="could not be read"):
        go(workspace=tmp_path / "nowhere")
    policy = w / "clients" / "acme" / "approvals" / guarded.POLICY_FILE
    policy.parent.mkdir(exist_ok=True)
    policy.write_text("{not json", encoding="utf-8")
    with pytest.raises(E, match="could not be read"):
        go()
    policy.write_text(json.dumps({"schema": core.POLICY_SCHEMA, "counts_min_cell": 1}), encoding="utf-8")
    with pytest.raises(E, match="malformed"):
        go()
    policy.unlink()
    ws.set_guarded_reads(w, "off", presence=YES)
    with pytest.raises(E, match="guarded reads are off"):
        go()
    ws.set_guarded_reads(w, "on", presence=YES)
    ws.set_ai_access(w, "full", presence=YES)
    with pytest.raises(E, match="connected workspace"):
        go()
    assert sf.calls == [] and resolved == [] and activity(w) == []


def test_inside_a_session_only_the_launched_client_is_read(w, monkeypatch):
    from torque import launch
    session = {"CLAUDECODE": "1"}
    with pytest.raises(E, match="inside an AI session"):               # the launch variables were removed
        guarded.counts(w, "Acme", "acme-prod", "Opportunity", resolve=ORGS.get, run=Sf(), env=session)
    monkeypatch.setattr(launch, "session_record", lambda folder, env: {"client": "other", "workspace": str(w)})
    with pytest.raises(E, match="inside an AI session"):
        guarded.counts(w, "Acme", "acme-prod", "Opportunity", resolve=ORGS.get, run=Sf(), env=session)
    monkeypatch.setattr(launch, "session_record", lambda folder, env: {"client": "acme", "workspace": str(w)})
    assert guarded.counts(w, "Acme", "acme-prod", "Opportunity", resolve=ORGS.get,
                          run=Sf(lambda soql: [{"n": 40}]), env=session)["rows"] == [{"values": [], "count": 40}]


def test_the_alias_must_still_be_the_consented_org(w):
    moved = {"acme-prod": Org("00D000000000009AAA", "production", True)}
    sf = Sf()
    with pytest.raises(E, match="does not resolve now"):
        guarded.counts(w, "Acme", "acme-prod", "Opportunity", resolve=moved.get, run=sf, env={})
    with pytest.raises(E, match="does not resolve now"):
        guarded.org_info(w, "Acme", "acme-prod", resolve=lambda alias: None, env={})
    assert sf.calls == []
    assert guarded.org_info(w, "Acme", "acme-prod", resolve=ORGS.get, env={}) == {
        "org": "acme-prod", "org_id_18": "00D000000000002AAA", "kind": "production"}


def test_unreadable_classification_stops_the_lanes_unless_ignored(w):
    release(w, "Opportunity.IsWon")
    broken = Sf(lambda soql: [{"n": 40}], facts={"status": 1, "message": SENTINEL})
    with pytest.raises(E, match="classification could not be read"):
        guarded.counts(w, "Acme", "acme-prod", "Opportunity", **kw(broken))
    guarded.policy_set(w, "Acme", "classification", "ignore", presence=YES, confirm=OK)
    assert guarded.counts(w, "Acme", "acme-prod", "Opportunity", **kw(broken))["rows"] == [{"values": [], "count": 40}]
    classified = Sf(facts=[{"QualifiedApiName": "IsWon", "DataType": "Checkbox", "ComplianceGroup": "PII",
                            "SecurityClassification": None},
                           {"QualifiedApiName": "StageName", "DataType": "Picklist", "ComplianceGroup": None,
                            "SecurityClassification": None}])
    guarded.policy_set(w, "Acme", "classification", "required", presence=YES, confirm=OK)
    with pytest.raises(E, match="not released"):                        # the org now classifies a released field
        guarded.counts(w, "Acme", "acme-prod", "Opportunity", ["IsWon"], **kw(classified))


# ---- nothing from the org leaks through an error or the record

@pytest.mark.parametrize("answer", [
    lambda soql: {"status": 1, "name": "MALFORMED_QUERY", "message": SENTINEL},
    lambda soql: {"status": 0, "result": {"done": False, "records": [{"n": 9, "x": SENTINEL}]}},
    lambda soql: {"status": 0, "result": {"records": SENTINEL}},
    lambda soql: {"status": 0},
    lambda soql: [{"n": SENTINEL}],
    lambda soql: [SENTINEL],
])
def test_a_failed_or_odd_answer_prints_nothing_from_the_org(w, answer, capsys):
    parsed = SimpleNamespace(action="counts", workspace=str(w), client="Acme", target_org="acme-prod",
                             sobject="Opportunity", group_by=[], where=[], json=False)
    out, err = io.StringIO(), io.StringIO()
    import torque.guarded as module
    original = module.counts
    module.counts = lambda *a, **k: original(*a, resolve=ORGS.get, run=Sf(answer), env={}, **k)
    try:
        assert guarded.run(parsed, out, err) == 2
    finally:
        module.counts = original
    assert out.getvalue() == "" and SENTINEL not in err.getvalue() and err.getvalue().startswith("torque guarded: ")
    assert activity(w) == []


def test_an_unexpected_error_prints_its_class_only(w):
    def boom(*a, **k):
        raise RuntimeError(SENTINEL)
    parsed = SimpleNamespace(action="org", workspace=str(w), client="Acme", target_org="acme-prod", json=False)
    out, err = io.StringIO(), io.StringIO()
    import torque.guarded as module
    original, module.org_info = module.org_info, boom
    try:
        assert guarded.run(parsed, out, err) == 2
    finally:
        module.org_info = original
    assert err.getvalue() == "torque guarded: guarded read failed (RuntimeError)\n" and out.getvalue() == ""


def test_nothing_prints_when_the_record_of_the_call_cannot_be_written(w, monkeypatch):
    monkeypatch.setattr(guarded.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(E, match="record of this call could not be written"):
        guarded.counts(w, "Acme", "acme-prod", "Opportunity", **kw(Sf(lambda soql: [{"n": 40}])))


# ---- the consultant's commands need the consultant

def test_policy_and_registry_changes_need_a_person(w):
    for call in (lambda: guarded.policy_release(w, "Acme", "acme-prod", "Opportunity.StageName", "exact", run=Sf(),
                                                presence=NO, resolve=ORGS.get, env={}),
                 lambda: guarded.policy_release_object(w, "Acme", "acme-prod", "Settings__c", run=Sf(), presence=NO,
                                                       resolve=ORGS.get, env={}),
                 lambda: guarded.policy_unrelease(w, "Acme", "Opportunity.StageName", presence=NO),
                 lambda: guarded.policy_set(w, "Acme", "counts_min_cell", 3, presence=NO),
                 lambda: guarded.test_records_remove(w, "Acme", ACCOUNT, presence=NO)):
        with pytest.raises(E, match="real terminal"):
            call()
    assert guarded.policy_show(w, "Acme") == {"schema": core.POLICY_SCHEMA, "fields": {}, "objects": {}}
    with pytest.raises(E, match="malformed"):
        guarded.policy_set(w, "Acme", "counts_min_cell", 2, presence=YES, confirm=OK)
    assert guarded.policy_set(w, "Acme", "counts_min_cell", 3, presence=YES, confirm=OK) == {"counts_min_cell": 3}
    release(w, "Opportunity.StageName")
    with pytest.raises(E, match="exact or not at all"):
        release(w, "Opportunity.StageName", "coarse")
    with pytest.raises(E, match="record's name"):
        release(w, "Opportunity.Name")
    with pytest.raises(E, match="acknowledge-sensitive"):
        release(w, "Opportunity.Gender__c")
    release(w, "opportunity.gender__c", sensitive=True)
    assert guarded.policy_show(w, "Acme")["fields"] == {"Opportunity.StageName": "exact", "Opportunity.Gender__c": "exact"}
    assert guarded.policy_unrelease(w, "Acme", "opportunity.stagename", presence=YES, confirm=OK)
    assert guarded.policy_show(w, "Acme")["fields"] == {"Opportunity.Gender__c": "exact"}


def test_candidates_and_exposure(w):
    release(w, "Opportunity.StageName")
    out = guarded.candidates(w, "Acme", "acme-prod", "Opportunity", **kw())
    by = {row["field"]: row for row in out["fields"]}
    assert by["StageName"]["release"] == "exact" and by["StageName"]["released"] == "exact"
    assert by["Amount"]["release"] == "exact or coarse" and by["Name"]["release"] == "no"
    assert "acknowledge-sensitive" in by["Gender__c"]["note"] and by["Parent_Name__c"]["release"] == "no"
    guarded.counts(w, "Acme", "acme-prod", "Opportunity", ["StageName"], **kw(Sf(lambda soql: [{"d0": "Closed Won", "n": 9}])))
    guarded.fill(w, "Acme", "acme-prod", "Opportunity", ["Email__c"], **kw(Sf(lambda soql: [{"n": 50, "f0": 20}])))
    seen = guarded.exposure(w, "Acme")
    assert seen["calls"] == {"candidates Opportunity": 1, "counts Opportunity": 1, "fill Opportunity": 1}
    assert seen["fields_shown"] == {"Opportunity": ["Email__c", "StageName"]}


# ---- round 4

def test_a_launched_session_is_known_by_its_launch_variables_too(w, monkeypatch):
    # A host that sets no marker of its own (Antigravity) still carries the launch variables.
    from torque import launch
    go = lambda env: guarded.counts(w, "Acme", "acme-prod", "Opportunity", resolve=ORGS.get,
                                    run=Sf(lambda soql: [{"n": 40}]), env=env)
    for env in ({"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": "x"}, {"TORQUE_LAUNCH": "x"}, {"TORQUE_CLIENT": "other"}):
        with pytest.raises(E, match="inside an AI session"):          # no launch record in this workspace
            go(env)
    monkeypatch.setattr(launch, "session_record", lambda folder, env: {"client": "acme", "workspace": str(w)})
    assert go({"TORQUE_CLIENT": "acme", "TORQUE_LAUNCH": "x"})["rows"] == [{"values": [], "count": 40}]
    assert go({})["rows"] == [{"values": [], "count": 40}]                 # the consultant's own terminal


def test_org_and_candidates_are_recorded_and_print_nothing_when_they_cannot_be(w, monkeypatch):
    guarded.org_info(w, "Acme", "acme-prod", resolve=ORGS.get, env={})
    guarded.candidates(w, "Acme", "acme-prod", "Opportunity", **kw())
    assert [entry["lane"] for entry in activity(w)] == ["org", "candidates"]
    assert activity(w)[1]["object"] == "Opportunity"
    monkeypatch.setattr(guarded.os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    for call in (lambda: guarded.org_info(w, "Acme", "acme-prod", resolve=ORGS.get, env={}),
                 lambda: guarded.candidates(w, "Acme", "acme-prod", "Opportunity", **kw())):
        with pytest.raises(E, match="record of this call could not be written"):
            call()


def test_a_wide_object_is_read_in_several_queries_joined_on_id(w, monkeypatch):
    wide = {"name": "Wide__c", "queryable": True, "customSetting": True,
            "fields": [fld("Id", "id"), *[fld(f"Field_{n:03d}__c", "double") for n in range(250)]]}
    monkeypatch.setitem(DESCRIBES, "wide__c", wide)
    guarded.policy_release_object(w, "Acme", "acme-prod", "Wide__c", run=Sf(), **admin())

    def answer(soql):
        names = soql.split(" FROM ")[0].replace("SELECT ", "").split(", ")
        return [{"Id": rid, **{name: float(name[6:9]) for name in names if name != "Id"}}
                for rid in ("a01000000000001AAA", "a01000000000002AAA")]
    sf = Sf(answer)
    out = guarded.config(w, "Acme", "acme-prod", "Wide__c", **kw(sf))
    data = [q for q in sf.queries if " FROM Wide__c " in q]
    assert len(data) == 3 and all(q.startswith("SELECT Id, ") and q.endswith("ORDER BY Id LIMIT 200") for q in data)
    assert all(len(q) < 6000 for q in data)
    assert len(out["rows"]) == 2 and len(out["rows"][0]) == 251
    assert out["rows"][1]["Field_249__c"] == 249.0 and out["rows"][0]["Field_000__c"] == 0.0


def test_registration_reaches_the_org_only_with_a_person_present(w):
    sf, resolved = Sf(), []
    with pytest.raises(E, match="real terminal"):
        guarded.test_records_add(w, "Acme", "acme-prod", "Account", ACCOUNT, run=sf, presence=NO,
                                 resolve=lambda alias: resolved.append(alias) or ORGS.get(alias), env={})
    assert sf.calls == [] and resolved == []


def test_a_child_id_the_org_returns_is_never_repeated_in_an_error(w):
    sf = Sf(lambda soql: [{"Id": SENTINEL}] if "WHERE AccountId" in soql else [{"Id": ACCOUNT}])
    parsed = SimpleNamespace(action="test-records", verb="add", workspace=str(w), client="Acme", target_org="acme-prod",
                             sobject="Account", rid=ACCOUNT, with_children=["Opportunity.AccountId"], note="", json=False)
    import torque.guarded as module
    original = module.test_records_add
    module.test_records_add = lambda *a, **k: original(*a, run=sf, out=io.StringIO(), **admin(), **k)
    out, err = io.StringIO(), io.StringIO()
    try:
        assert guarded.run(parsed, out, err) == 2
    finally:
        module.test_records_add = original
    assert SENTINEL not in err.getvalue() and "nothing was registered" in err.getvalue()
    assert guarded.test_records_list(w, "Acme") == []


def test_the_record_of_a_call_says_how_each_field_printed(w):
    register(w, sf=Sf(lambda soql: [{"Id": OPP}] if "WHERE AccountId" in soql else [{"Id": ACCOUNT}]),
             with_children=["Opportunity.AccountId"])
    rows = [{"Id": OPP, "Name": "Test gift", "Parent_Name__c": SENTINEL, "Total__c": 5.0}]
    sf = Sf(lambda soql: [{"Id": OPP}] if soql.startswith("SELECT Id FROM Opportunity WHERE AccountId") else rows)
    guarded.related(w, "Acme", "acme-prod", ACCOUNT, "Opportunity.AccountId", ["Name", "Parent_Name__c", "Total__c"],
                    **kw(sf))
    entry = activity(w)[-1]
    assert entry["fields"] == {"Id": "shown", "Name": "shown", "Parent_Name__c": "masked", "Total__c": "masked"}
    guarded.fill(w, "Acme", "acme-prod", "Opportunity", ["Email__c", "Amount"],
                 **kw(Sf(lambda soql: [{"n": 100, "f0": 60, "f1": 99}])))
    assert activity(w)[-1]["fields"] == {"Email__c": "shown", "Amount": "suppressed"}
    guarded.fill(w, "Acme", "acme-prod", "Opportunity", ["Email__c"], **kw(Sf(lambda soql: [{"n": 2, "f0": 1}])))
    assert activity(w)[-1]["fields"] == {"Email__c": "suppressed"} and activity(w)[-1]["total_shown"] is False
    seen = guarded.exposure(w, "Acme")["fields_shown"]
    assert seen == {"Opportunity": ["Email__c", "Id", "Name"]}


# ---- round 6

@pytest.mark.parametrize("facts", [
    [], [{"QualifiedApiName": "StageName"}],
    [{"QualifiedApiName": "StageName", "DataType": "Picklist", "ComplianceGroup": ["PII"], "SecurityClassification": None}],
    {"status": 0, "result": {"done": False, "records": []}},
])
def test_an_odd_classification_answer_stops_the_lanes(w, facts):
    with pytest.raises(E, match="classification could not be read"):
        guarded.counts(w, "Acme", "acme-prod", "Opportunity", **kw(Sf(lambda soql: [{"n": 40}], facts=facts)))
    assert activity(w) == []


def test_a_field_without_a_classification_row_is_kept_back(w):
    release(w, "Opportunity.StageName")
    partial = [{"QualifiedApiName": "StageName", "DataType": "Picklist", "ComplianceGroup": None,
                "SecurityClassification": None}]
    sf = Sf(lambda soql: [{"d0": "Closed Won", "n": 9}], facts=partial)
    assert guarded.counts(w, "Acme", "acme-prod", "Opportunity", ["StageName"], **kw(sf))["rows"]
    with pytest.raises(E, match="no row"):
        guarded.fill(w, "Acme", "acme-prod", "Opportunity", ["Amount"], **kw(Sf(facts=partial)))


def test_only_the_rows_that_were_asked_for_are_shown(w):
    register(w, sf=Sf(lambda soql: [{"Id": OPP}] if "WHERE AccountId" in soql else [{"Id": ACCOUNT}]),
             with_children=["Opportunity.AccountId"])
    other_row = [{"Id": OTHER, "Name": SENTINEL}]
    with pytest.raises(E, match="was not found"):                       # the answer holds another record
        guarded.record(w, "Acme", "acme-prod", "Account", ACCOUNT, ["Name"], **kw(Sf(lambda soql: other_row)))
    with pytest.raises(E, match="was not found"):
        guarded.record(w, "Acme", "acme-prod", "Account", ACCOUNT, ["Name"],
                       **kw(Sf(lambda soql: [{"Id": "junk", "Name": SENTINEL}])))
    stray = [{"Id": OPP, "Name": "Test gift"}, {"Id": "006000000000009AAA", "Name": SENTINEL}]
    sf = Sf(lambda soql: [{"Id": OPP}] if soql.startswith("SELECT Id FROM Opportunity WHERE AccountId") else stray)
    with pytest.raises(E, match="did not answer the children"):
        guarded.related(w, "Acme", "acme-prod", ACCOUNT, "Opportunity.AccountId", ["Name"], **kw(sf))
    assert SENTINEL not in json.dumps(activity(w))


# ---- round 6, second pass

def test_every_batch_of_fields_must_answer_with_the_same_rows(w, monkeypatch):
    wide = {"name": "Wide__c", "queryable": True, "customSetting": True,
            "fields": [fld("Id", "id"), *[fld(f"Field_{n:03d}__c", "double") for n in range(150)]]}
    monkeypatch.setitem(DESCRIBES, "wide__c", wide)
    guarded.policy_release_object(w, "Acme", "acme-prod", "Wide__c", run=Sf(), **admin())
    first, second = "a01000000000001AAA", "a01000000000002AAA"
    for later in ([{"Id": second, "Field_100__c": 1.0}],                       # another row
                  [{"Id": first}, {"Id": second, "Field_100__c": 1.0}],        # a row too many
                  [],                                                          # a row too few
                  [{"Id": first}, {"Id": first}]):                             # the same row twice
        answers = iter([[{"Id": first}], later])
        sf = Sf(lambda soql: next(answers))
        with pytest.raises(E, match="the same way twice"):
            guarded.config(w, "Acme", "acme-prod", "Wide__c", **kw(sf))
    assert activity(w) == []


def test_registration_checks_that_the_record_found_is_the_one_asked_for(w):
    with pytest.raises(E, match="no record with that id"):
        guarded.test_records_add(w, "Acme", "acme-prod", "Account", ACCOUNT, run=Sf(lambda soql: [{"Id": OTHER}]),
                                 out=io.StringIO(), **admin())
    with pytest.raises(E, match="no record with that id"):
        guarded.test_records_add(w, "Acme", "acme-prod", "Account", ACCOUNT, run=Sf(lambda soql: [{"Id": SENTINEL}]),
                                 out=io.StringIO(), **admin())
    assert guarded.test_records_list(w, "Acme") == []


def test_wording_of_the_footer_and_of_the_classification_hint(w):
    out = {"lane": "related", "rows": [], "not_shown": 1}
    assert "not registered, or past the limit" in guarded._lines(out)[-1]
    with pytest.raises(E, match="set-classification --value ignore"):
        core.require_facts(core.parse_policy(None), core.parse_facts(None))


def test_a_refusal_is_one_line_whatever_it_holds(w, monkeypatch):
    def refuse(*a, **k):
        raise E("first line\nsecond line\tand\x0bmore")
    monkeypatch.setattr(guarded, "org_info", refuse)
    parsed = SimpleNamespace(action="org", workspace=str(w), client="Acme", target_org="acme-prod", json=False)
    out, err = io.StringIO(), io.StringIO()
    assert guarded.run(parsed, out, err) == 2
    assert err.getvalue() == "torque guarded: first line second line and more\n" and out.getvalue() == ""
