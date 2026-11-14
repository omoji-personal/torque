"""`torque check deployed` against a stubbed Salesforce CLI: comparison, flow activation, field access, rules."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from check_fixtures import e10_project, Tree, NS
from torque import cli
from torque.checks import compare, components, deployed


def mdapi(root, body):
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{root} {NS}>\n{body}\n</{root}>\n'


class FakeSf:
    """Answers the retrieve and the queries like the CLI would, and records every argv."""

    def __init__(self, project: Path, logged_in=True, flow_active_is_latest=False, stage_label="Stage"):
        self.project, self.logged_in = project, logged_in
        self.flow_active_is_latest, self.stage_label = flow_active_is_latest, stage_label
        self.calls = []

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if not self.logged_in:
            return SimpleNamespace(returncode=1, stderr="", stdout=json.dumps(
                {"status": 1, "name": "NoOrgFound", "message": "No authorization information found for sandbox."}))
        if argv[1:4] == ["project", "retrieve", "start"]:
            return self.retrieve(argv)
        if argv[1:3] == ["data", "query"]:
            return self.query(argv[argv.index("--query") + 1])
        raise AssertionError(f"unexpected sf call: {argv}")

    def retrieve(self, argv):
        out = Path(argv[argv.index("--target-metadata-dir") + 1]) / "unpackaged"
        src = self.project / "force-app/main/default"
        hours = components.read_file(str(src / "objects/Shift__c/fields/Hours__c.field-meta.xml"))[0]
        assert hours.node is not None
        (out / "objects").mkdir(parents=True)
        (out / "objects/Shift__c.object").write_text(mdapi("CustomObject", (
            "    <deploymentStatus>Deployed</deploymentStatus>\n"
            "    <description>Made-up object for tests.</description>\n"
            "    <enableHistory>false</enableHistory>\n"                        # an org default the source omits
            "    <fields>\n        <fullName>Hours__c</fullName>\n        <externalId>false</externalId>\n"
            "        <label>Hours</label>\n        <precision>4</precision>\n        <required>false</required>\n"
            "        <scale>2</scale>\n        <type>Number</type>\n    </fields>\n"
            "    <fields>\n        <fullName>Stage__c</fullName>\n"
            "        <description>Why it exists and what writes it.</description>\n"
            "        <inlineHelpText>What to enter.</inlineHelpText>\n"
            f"        <label>{self.stage_label}</label>\n        <type>Picklist</type>\n    </fields>\n"
            "    <label>Shift</label>\n    <pluralLabel>Shifts</pluralLabel>\n    <sharingModel>ReadWrite</sharingModel>\n"
            "    <listViews>\n        <fullName>All</fullName>\n        <filterScope>Everything</filterScope>\n"
            "        <label>All</label>\n    </listViews>\n")), encoding="utf-8")
        (out / "flows").mkdir()
        flow = (src / "flows/Gift_Thank_You_Task.flow-meta.xml").read_text(encoding="utf-8")
        (out / "flows/Gift_Thank_You_Task.flow").write_text(flow.replace("<status>Active</status>",
                                                                         "<status>Draft</status>"), encoding="utf-8")
        (out / "permissionsets").mkdir()
        (out / "permissionsets/Shift_Coordinator.permissionset").write_text(mdapi("PermissionSet", (
            "    <fieldPermissions>\n        <editable>false</editable>\n        <field>Shift__c.Hours__c</field>\n"
            "        <readable>false</readable>\n    </fieldPermissions>\n"
            "    <fieldPermissions>\n        <editable>false</editable>\n        <field>Shift__c.Stage__c</field>\n"
            "        <readable>true</readable>\n    </fieldPermissions>\n"
            "    <hasActivationRequired>false</hasActivationRequired>\n    <label>Shift_Coordinator</label>\n"
            "    <objectPermissions>\n        <allowCreate>true</allowCreate>\n        <allowDelete>false</allowDelete>\n"
            "        <allowEdit>true</allowEdit>\n        <allowRead>true</allowRead>\n"
            "        <modifyAllRecords>false</modifyAllRecords>\n        <object>Shift__c</object>\n"
            "        <viewAllRecords>false</viewAllRecords>\n    </objectPermissions>\n")), encoding="utf-8")
        result = {"status": 0, "result": {"done": True, "success": True, "status": "Succeeded", "messages": [
            {"fileName": "unpackaged/package.xml",
             "problem": "Entity of type 'ValidationRule' named 'Shift__c.Hours_In_Range' cannot be found"}]}}
        return SimpleNamespace(returncode=0, stdout=" ›   Warning: update available\n" + json.dumps(result), stderr="")

    def query(self, soql):
        def answer(records):
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                {"status": 0, "result": {"records": records, "totalSize": len(records), "done": True}}))
        if "FROM FlowDefinitionView" in soql:
            return answer([{"ApiName": "Gift_Thank_You_Task", "IsActive": True, "ProcessType": "AutoLaunchedFlow",
                            "TriggerType": "RecordAfterSave"}])
        if "ActiveVersion.VersionNumber" in soql:
            return answer([{"DeveloperName": "Gift_Thank_You_Task", "ActiveVersion": {"VersionNumber": 2},
                            "LatestVersion": {"VersionNumber": 2 if self.flow_active_is_latest else 3}}])
        if "FROM FlowDefinition" in soql:
            latest = "301000000000002AAA" if self.flow_active_is_latest else "301000000000003AAA"
            return answer([{"DeveloperName": "Gift_Thank_You_Task", "ActiveVersionId": "301000000000002AAA",
                            "LatestVersionId": latest}])
        if "FROM FieldPermissions" in soql:
            return answer([{"Field": "Shift__c.Stage__c", "PermissionsRead": True, "PermissionsEdit": False,
                            "Parent": {"Name": "Shift_Coordinator", "IsOwnedByProfile": False}}])
        if "FROM ValidationRule" in soql:
            return answer([])
        raise AssertionError(soql)


def deploy_result(path, names):
    files = [{"fullName": n, "type": t, "state": "Created", "filePath": ""} for t, n in names]
    path.write_text(json.dumps({"status": 0, "result": {"checkOnly": False, "id": "0Af000000000009AAA",
                                                        "status": "Succeeded", "files": files}}), encoding="utf-8")
    return path


COMPONENTS = [("CustomObject", "Shift__c"), ("CustomField", "Shift__c.Hours__c"), ("CustomField", "Shift__c.Stage__c"),
              ("Flow", "Gift_Thank_You_Task"), ("PermissionSet", "Shift_Coordinator"),
              ("ValidationRule", "Shift__c.Hours_In_Range")]


@pytest.fixture
def world(tmp_path, monkeypatch):
    t = e10_project(tmp_path / "p")
    fake = FakeSf(t.root)
    monkeypatch.setattr(deployed, "run_tool", fake)
    monkeypatch.setattr("jsc_advisory.sf.run_tool", fake)
    return t, fake, deploy_result(tmp_path / "deploy.json", COMPONENTS)


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


def test_report_on_a_stubbed_org(world):
    t, fake, result = world
    code, out, err = run("check", "deployed", "--target-org", "sandbox", "--changed-from", str(result),
                         "--source-dir", str(t.root), "--as-permission-set", "Shift_Coordinator", "--json")
    assert code == 0, err
    rep = json.loads(out)
    by = {(r["type"], r["name"]): r for r in rep["components"]}
    assert by[("CustomField", "Shift__c.Hours__c")]["comparison"] == "same"       # externalId/required defaults
    assert by[("CustomField", "Shift__c.Stage__c")]["comparison"] == "same"
    assert by[("CustomObject", "Shift__c")]["comparison"] == "same"               # fields and list views ignored
    flow = by[("Flow", "Gift_Thank_You_Task")]
    assert flow["comparison"] == "different" and flow["differences"] == ["status: source 'Active', org 'Draft'"]
    pset = by[("PermissionSet", "Shift_Coordinator")]
    assert pset["comparison"] == "same"            # a grant-nothing entry for Hours__c equals no entry
    assert by[("ValidationRule", "Shift__c.Hours_In_Range")]["comparison"] == "not-in-org"
    f = rep["flows"][0]
    assert f["latest_is_active"] is False and f["latest_version"] == 3 and f["active_version"] == 2
    assert "is not the active one; the org runs version 2 (the file asked for Active)" in f["message"]
    access = {a["name"]: a for a in rep["fields"]}
    assert access["Shift__c.Hours__c"]["rows"] == 0 and "no permission set or profile grants it" in \
        access["Shift__c.Hours__c"]["message"]
    assert access["Shift__c.Hours__c"]["as_permission_sets"] == {"Shift_Coordinator": "none"}
    assert access["Shift__c.Stage__c"]["as_permission_sets"] == {"Shift_Coordinator": "read"}
    assert rep["validation_rules"] == [{"name": "Shift__c.Hours_In_Range", "active": None,
                                        "message": "not found in the org"}]
    assert rep["totals"]["flows_latest_not_active"] == 1 and rep["totals"]["fields_without_grants"] == 1
    assert rep["label"] == deployed.LABEL


def test_every_call_is_a_read_naming_the_org(world):
    t, fake, result = world
    run("check", "deployed", "--target-org", "sandbox", "--changed-from", str(result), "--source-dir", str(t.root))
    assert fake.calls
    for argv in fake.calls:
        assert argv[0] == "sf" and argv[argv.index("--target-org") + 1] == "sandbox"
        assert argv[1:3] in (["project", "retrieve"], ["data", "query"])
        assert "display" not in argv and "deploy" not in argv[1:3]


def test_different_component_names_the_element(world):
    t, fake, result = world
    fake.stage_label = "Stage Name"
    code, out, _ = run("check", "deployed", "--target-org", "sandbox", "--changed-from", str(result),
                       "--source-dir", str(t.root))
    assert code == 0
    assert "CustomField Shift__c.Stage__c: different: label: source 'Stage', org 'Stage Name'" in out
    assert out.splitlines()[0] == f"torque check deployed: org sandbox ({deployed.LABEL})."


def test_active_latest_flow_is_reported_active(world):
    t, fake, result = world
    fake.flow_active_is_latest = True
    code, out, _ = run("check", "deployed", "--target-org", "sandbox", "--only", "Flow:Gift_Thank_You_Task",
                       "--source-dir", str(t.root), "--json")
    assert json.loads(out)["flows"][0]["latest_is_active"] is True


def test_org_that_cannot_be_read_exits_3(world):
    t, fake, result = world
    fake.logged_in = False
    code, out, err = run("check", "deployed", "--target-org", "sandbox", "--changed-from", str(result),
                         "--source-dir", str(t.root))
    assert code == 3 and "No authorization information found" in err
    assert "could-not-compare" in out and ": 0 as submitted (0 of them with more in the org)," in out


def test_input_errors(tmp_path):
    assert run("check", "deployed", "--target-org", "sandbox")[0] == 2
    assert run("check", "deployed", "--target-org", "sandbox", "--source-dir", str(tmp_path / "none"))[0] == 2


def test_help_states_the_proof_level(capsys):
    with pytest.raises(SystemExit):
        cli.main(["check", "deployed", "--help"])
    assert "stubbed CLI" in capsys.readouterr().out.replace("\n", " ")


def comp(tmp_path, name, text, suffix=".field-meta.xml"):
    path = tmp_path / name / f"objects/Shift__c/fields/X{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return components.read_file(str(path))[0]


def test_compare_keeps_order_inside_elements(tmp_path):
    def picklist(values):
        items = "".join(f"<value><fullName>{v}</fullName><default>false</default><label>{v}</label></value>"
                        for v in values)
        return mdapi("CustomField", f"<fullName>X</fullName><label>X</label><type>Picklist</type>"
                                    f"<valueSet><valueSetDefinition>{items}</valueSetDefinition></valueSet>")
    a = comp(tmp_path, "a", picklist(["Planned", "Worked"]))
    b = comp(tmp_path, "b", picklist(["Worked", "Planned"]))
    c = comp(tmp_path, "c", mdapi("CustomField", "<label>X</label><fullName>X</fullName><type>Picklist</type>"
                                  "<valueSet><valueSetDefinition>"
                                  "<value><fullName>Planned</fullName><default>false</default><label>Planned</label>"
                                  "</value><value><fullName>Worked</fullName><default>false</default>"
                                  "<label>Worked</label></value></valueSetDefinition></valueSet>"))
    assert compare.compare(a, b, set())[0] == "different"      # display order of picklist values is meaningful
    assert compare.compare(a, c, set())[0] == "same"           # the order of top-level elements is not


def test_what_only_the_org_has_is_not_a_difference(tmp_path):
    """From the first live run: a new object and its permission set read as "different" only because
    Salesforce adds its own properties on save. The question is whether what was submitted is in the org."""
    source = comp(tmp_path, "a", mdapi("CustomObject", "<label>Shift</label><pluralLabel>Shifts</pluralLabel>"
                                       "<sharingModel>ReadWrite</sharingModel>"), ".object-meta.xml")
    org = comp(tmp_path, "b", mdapi("CustomObject", "<enableBulkApi>true</enableBulkApi><label>Shift</label>"
                                    "<pluralLabel>Shifts</pluralLabel><sharingModel>ReadWrite</sharingModel>"
                                    "<externalSharingModel>Private</externalSharingModel>"
                                    "<enableHistory>false</enableHistory>"), ".object-meta.xml")
    status, diffs, _skipped, org_only = compare.compare(source, org, set())
    assert (status, diffs) == ("same-org-has-more", [])
    assert org_only == ["enableBulkApi", "externalSharingModel"]          # a default-valued extra is not listed
    changed = comp(tmp_path, "c", mdapi("CustomObject", "<enableBulkApi>true</enableBulkApi><label>Shifts!</label>"
                                        "<pluralLabel>Shifts</pluralLabel><sharingModel>ReadWrite</sharingModel>"),
                   ".object-meta.xml")
    status, diffs, _skipped, _only = compare.compare(source, changed, set())
    assert status == "different" and diffs == ["label: source 'Shift', org 'Shifts!'"]
    missing = comp(tmp_path, "d", mdapi("CustomObject", "<label>Shift</label><pluralLabel>Shifts</pluralLabel>"),
                   ".object-meta.xml")
    status, diffs, _skipped, _only = compare.compare(source, missing, set())
    assert status == "different" and diffs == ["sharingModel: only in source"]


def test_an_extra_leaf_inside_a_keyed_entry_is_not_a_difference(tmp_path):
    grant = ("<objectPermissions><allowCreate>true</allowCreate><allowRead>true</allowRead>"
             "<object>Shift__c</object>{extra}</objectPermissions>")
    source = comp(tmp_path, "a", mdapi("PermissionSet", "<label>Access</label>" + grant.format(extra="")),
                  ".permissionset-meta.xml")
    org = comp(tmp_path, "b", mdapi("PermissionSet", "<label>Access</label>"
                                    + grant.format(extra="<viewAllFields>true</viewAllFields>")),
               ".permissionset-meta.xml")
    retrieved = {("CustomObject", "shift__c")}
    status, diffs, _skipped, org_only = compare.compare(source, org, retrieved)
    assert (status, diffs, org_only) == ("same-org-has-more", [], ["objectPermissions.viewAllFields"])
    lowered = comp(tmp_path, "c", mdapi("PermissionSet", "<label>Access</label>"
                                        + grant.replace("<allowCreate>true", "<allowCreate>false").format(extra="")),
                   ".permissionset-meta.xml")
    status, diffs, _skipped, _only = compare.compare(source, lowered, retrieved)
    assert status == "different" and diffs == ["objectPermissions.allowCreate: source 'true', org 'false'"]


def test_compare_never_calls_unreadable_same(tmp_path):
    a = comp(tmp_path, "a", "<CustomField><label>X</CustomField>")
    b = comp(tmp_path, "b", mdapi("CustomField", "<label>X</label>"))
    assert compare.compare(a, b, set())[0] == "could-not-compare"
