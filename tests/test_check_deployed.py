"""`torque check deployed` against a stubbed Salesforce CLI: comparison, flow activation, field access, rules."""
import contextlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from check_fixtures import DEFAULT, e10_project, live_pair, LIVE_PAIR, Tree, NS, xml
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
    # the org runs version 2 and the deploy made version 3: the retrieve's copy may be either, so no verdict
    # (Gemini F-10); what it showed is kept
    assert flow["comparison"] == "could-not-compare" and flow["differences"] == ["status: source 'Active', org 'Draft'"]
    assert "a retrieve returns one version without saying which" in flow["reason"]
    assert flow["reason"].endswith("(against that copy: different)")
    assert flow["compared_version"] == "one of version 2 (active) and version 3 (latest)"
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
    rep = json.loads(out)
    assert rep["flows"][0]["latest_is_active"] is True
    [row] = rep["components"]
    assert row["comparison"] == "different" and row["compared_version"] == "version 2, the latest and the active one"
    queries = [a[a.index("--query") + 1] for a in fake.calls if a[1:3] == ["data", "query"]]
    assert any("FROM FlowDefinition WHERE DeveloperName = 'Gift_Thank_You_Task' AND NamespacePrefix = null" in q
               for q in queries)


def test_a_profile_is_retrieved_with_the_components_its_entries_name(world):
    """Gemini F-08: a profile retrieve returns access entries only for components in the same request. A
    permission set retrieve returns all its entries (seen live 2026-11-14), so it is retrieved alone."""
    t, fake, result = world
    t.profile("Shift_Profile", fields=["Shift__c.Stage__c", "Account.Region__c", "Task.Call__c"])
    t.permset("Shift_Coordinator", fields=["Shift__c.Stage__c", "Account.Region__c"])
    run("check", "deployed", "--target-org", "sandbox", "--only", "Profile:Shift_Profile", "--only",
        "PermissionSet:Shift_Coordinator", "--source-dir", str(t.root), "--json")
    [argv] = [a for a in fake.calls if a[1:4] == ["project", "retrieve", "start"]]
    members = [argv[i + 1] for i, a in enumerate(argv) if a == "--metadata"]
    assert members == ["PermissionSet:Shift_Coordinator", "Profile:Shift_Profile", "CustomField:Account.Region__c",
                       "CustomField:Activity.Call__c", "CustomField:Shift__c.Stage__c"]


def test_org_that_cannot_be_read_exits_3(world):
    t, fake, result = world
    fake.logged_in = False
    code, out, err = run("check", "deployed", "--target-org", "sandbox", "--changed-from", str(result),
                         "--source-dir", str(t.root))
    assert code == 3 and "No authorization information found" in err
    assert "could-not-compare" in out and ": 0 same, 0 as submitted plus content only the org has (look at it)," in out


def test_input_errors(tmp_path):
    assert run("check", "deployed", "--target-org", "sandbox")[0] == 2
    assert run("check", "deployed", "--target-org", "sandbox", "--source-dir", str(tmp_path / "none"))[0] == 2


def test_help_states_the_proof_level(capsys):
    with pytest.raises(SystemExit):
        cli.main(["check", "deployed", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "stubbed CLI" in text
    assert "re-run live on 2026-11-14" in text
    assert "the permission-set comparison changed after the last live run" in text


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
    assert compare.compare(a, b, set()).status == "different"      # display order of picklist values is meaningful
    assert compare.compare(a, c, set()).status == "same"           # the order of top-level elements is not


def test_known_server_additions_on_a_new_object_read_same(tmp_path):
    """From the first live run: a new object read "different" only because Salesforce adds its own properties on
    save. Those seen there are accepted with the values seen; anything else stays visible."""
    source = comp(tmp_path, "a", mdapi("CustomObject", "<label>Shift</label><pluralLabel>Shifts</pluralLabel>"
                                       "<sharingModel>ReadWrite</sharingModel>"), ".object-meta.xml")
    org = comp(tmp_path, "b", mdapi("CustomObject", "<enableBulkApi>true</enableBulkApi><label>Shift</label>"
                                    "<pluralLabel>Shifts</pluralLabel><sharingModel>ReadWrite</sharingModel>"
                                    "<externalSharingModel>Private</externalSharingModel>"
                                    "<enableHistory>false</enableHistory>"), ".object-meta.xml")
    o = compare.compare(source, org, set())
    assert (o.status, o.differences, o.unexplained) == ("same", [], [])
    assert o.known_additions == ["enableBulkApi", "externalSharingModel"]     # a default-valued extra is not listed
    unseen = comp(tmp_path, "e", mdapi("CustomObject", "<label>Shift</label><pluralLabel>Shifts</pluralLabel>"
                                       "<sharingModel>ReadWrite</sharingModel>"
                                       "<externalSharingModel>Public</externalSharingModel>"), ".object-meta.xml")
    o = compare.compare(source, unseen, set())
    assert (o.status, o.unexplained) == ("unexplained-additions", ["externalSharingModel"])
    changed = comp(tmp_path, "c", mdapi("CustomObject", "<enableBulkApi>true</enableBulkApi><label>Shifts!</label>"
                                        "<pluralLabel>Shifts</pluralLabel><sharingModel>ReadWrite</sharingModel>"),
                   ".object-meta.xml")
    o = compare.compare(source, changed, set())
    assert o.status == "different" and o.differences == ["label: source 'Shift', org 'Shifts!'"]
    missing = comp(tmp_path, "d", mdapi("CustomObject", "<label>Shift</label><pluralLabel>Shifts</pluralLabel>"),
                   ".object-meta.xml")
    o = compare.compare(source, missing, set())
    assert o.status == "different" and o.differences == ["sharingModel: only in source"]


def test_a_grant_only_the_org_has_inside_a_named_entry_is_a_difference(tmp_path):
    """The file names the entry, so the deploy sets its flags; a flag the org grants beyond the file differs."""
    grant = ("<objectPermissions><allowCreate>true</allowCreate><allowRead>true</allowRead>"
             "<object>Shift__c</object>{extra}</objectPermissions>")
    source = comp(tmp_path, "a", mdapi("PermissionSet", "<label>Access</label>" + grant.format(extra="")),
                  ".permissionset-meta.xml")
    org = comp(tmp_path, "b", mdapi("PermissionSet", "<label>Access</label>"
                                    + grant.format(extra="<viewAllFields>true</viewAllFields>")),
               ".permissionset-meta.xml")
    retrieved = {("CustomObject", "shift__c")}
    o = compare.compare(source, org, retrieved)
    assert (o.status, o.differences) == ("different", ["objectPermissions Shift__c.viewAllFields: only in org ('true')"])
    lowered = comp(tmp_path, "c", mdapi("PermissionSet", "<label>Access</label>"
                                        + grant.replace("<allowCreate>true", "<allowCreate>false").format(extra="")),
                   ".permissionset-meta.xml")
    o = compare.compare(source, lowered, retrieved)
    assert o.status == "different" and o.differences == ["objectPermissions Shift__c.allowCreate: source 'true', org 'false'"]
    default_leaf = comp(tmp_path, "d", mdapi("PermissionSet", "<label>Access</label>"
                                             + grant.format(extra="<viewAllFields>false</viewAllFields>")),
                        ".permissionset-meta.xml")
    assert compare.compare(source, default_leaf, retrieved).status == "same"


def test_compare_never_calls_unreadable_same(tmp_path):
    a = comp(tmp_path, "a", "<CustomField><label>X</CustomField>")
    b = comp(tmp_path, "b", mdapi("CustomField", "<label>X</label>"))
    assert compare.compare(a, b, set()).status == "could-not-compare"


class CopyOrg:
    """A retrieve that returns a prepared folder as the org's copy; every query returns no rows."""

    def __init__(self, org: Path):
        self.org, self.calls = Path(org), []

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if argv[1:4] == ["project", "retrieve", "start"]:
            shutil.copytree(self.org, Path(argv[argv.index("--target-metadata-dir") + 1]) / "unpackaged",
                            dirs_exist_ok=True)
            body = {"done": True, "success": True, "status": "Succeeded", "messages": []}
        elif argv[1:3] == ["data", "query"]:
            body = {"records": [], "totalSize": 0, "done": True}
        else:
            raise AssertionError(f"unexpected sf call: {argv}")
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"status": 0, "result": body}))


def read_back(monkeypatch, project, org, *extra):
    fake = CopyOrg(org)
    monkeypatch.setattr(deployed, "run_tool", fake)
    monkeypatch.setattr("jsc_advisory.sf.run_tool", fake)
    code, out, err = run("check", "deployed", "--target-org", "practice", "--source-dir", str(project), *extra)
    assert code == 0, err
    return out


def test_the_live_pair_reads_same(tmp_path, monkeypatch):
    """The source deployed to a practice org and what that org returned, byte for byte: all same, nothing hidden."""
    project, org = live_pair(tmp_path)
    rep = json.loads(read_back(monkeypatch, project, org, "--json"))
    assert {(r["type"], r["name"]): r["comparison"] for r in rep["components"]} == {
        ("CustomObject", "Torque_Check__c"): "same", ("CustomField", "Torque_Check__c.Amount__c"): "same",
        ("CustomField", "Torque_Check__c.Note__c"): "same",
        ("ValidationRule", "Torque_Check__c.Amount_Not_Negative"): "same",
        ("PermissionSet", "Torque_Check_Access"): "same", ("Flow", "Torque_Check_Set_Note"): "same"}
    obj = next(r for r in rep["components"] if r["type"] == "CustomObject")
    assert obj["known_server_additions"] == 36 and obj["unexplained_additions"] == []
    assert rep["totals"]["same"] == 6 and rep["totals"]["unexplained-additions"] == 0
    out = read_back(monkeypatch, project, org)
    assert "CustomObject Torque_Check__c: same (the org copy also has 36 properties Salesforce is known to add on " \
           "save)" in out
    assert ": 6 same, 0 as submitted plus content only the org has (look at it), 0 partly compared, 0 different," in out


def test_the_live_pair_with_an_unlisted_addition_is_not_same(tmp_path, monkeypatch):
    project, org = live_pair(tmp_path)
    path = org / "objects/Torque_Check__c.object"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "    <enableSearch>false</enableSearch>", "    <enableSearch>true</enableSearch>"), encoding="utf-8")
    out = read_back(monkeypatch, project, org)
    assert ("CustomObject Torque_Check__c: as submitted, plus content only the org has (look at it): enableSearch"
            in out)
    assert ": 5 same, 1 as submitted plus content only the org has (look at it), 0 partly compared, 0 different," in out


def test_review_r2_c01_a_space_inside_an_apex_string(tmp_path, monkeypatch):
    t = Tree(tmp_path / "p")
    t.apex("Greeting", "public with sharing class Greeting { public static String text() { return 'Hello world'; } }")
    org = tmp_path / "org"
    (org / "classes").mkdir(parents=True)
    (org / "classes/Greeting.cls").write_text(
        "public with sharing class Greeting { public static String text() { return 'Helloworld'; } }", encoding="utf-8")
    rep = json.loads(read_back(monkeypatch, t.root, org, "--json"))
    [row] = rep["components"]
    assert row["comparison"] == "different" and row["differences"] == ["code differs from line 1 (source 1 lines, "
                                                                        "org 1)"]


def test_review_r2_c02_a_revoke_the_org_still_grants(tmp_path, monkeypatch):
    t = Tree(tmp_path / "p")
    t.field("Task", "Flag__c", type="Checkbox")
    t.add(f"{DEFAULT}/permissionsets/Staff.permissionset-meta.xml", xml("PermissionSet", (
        "    <label>Staff</label>\n    <fieldPermissions>\n        <editable>false</editable>\n"
        "        <field>Task.Flag__c</field>\n        <readable>false</readable>\n    </fieldPermissions>\n")))
    org = tmp_path / "org"
    (org / "objects").mkdir(parents=True)
    (org / "permissionsets").mkdir()
    field = components.read_file(str(t.root / DEFAULT / "objects/Task/fields/Flag__c.field-meta.xml"))[0]
    body = "".join(f"<{c.tag}>{c.text}</{c.tag}>" for c in field.node.children)
    (org / "objects/Task.object").write_text(mdapi("CustomObject", f"<fields>{body}</fields>"), encoding="utf-8")
    (org / "permissionsets/Staff.permissionset").write_text(mdapi("PermissionSet", (
        "<label>Staff</label><fieldPermissions><editable>false</editable><field>Task.Flag__c</field>"
        "<readable>true</readable></fieldPermissions>")), encoding="utf-8")
    rep = json.loads(read_back(monkeypatch, t.root, org, "--json"))
    perm = next(r for r in rep["components"] if r["type"] == "PermissionSet")
    assert perm["comparison"] == "different"
    assert perm["differences"] == ["fieldPermissions Task.Flag__c: source revokes it; the org still grants readable"]


def test_review_r2_c03_a_decision_outcome_the_org_keeps(tmp_path, monkeypatch):
    def route(*names):
        rules = "".join(f"<rules><name>{n}</name><conditionLogic>and</conditionLogic><conditions>"
                        "<leftValueReference>$Record.Priority</leftValueReference><operator>EqualTo</operator>"
                        "<rightValue><stringValue>High</stringValue></rightValue></conditions>"
                        f"<connector><targetReference>{n}_Update</targetReference></connector><label>{n}</label>"
                        "</rules>" for n in names)
        return mdapi("Flow", "<apiVersion>62.0</apiVersion><decisions><name>Route</name><label>Route</label>"
                             f"<defaultConnectorLabel>Otherwise</defaultConnectorLabel>{rules}</decisions>"
                             "<label>Route Request</label><processType>AutoLaunchedFlow</processType>"
                             "<status>Active</status>")
    t = Tree(tmp_path / "p")
    t.add(f"{DEFAULT}/flows/Route_Request.flow-meta.xml", route("Approve"))
    org = tmp_path / "org"
    (org / "flows").mkdir(parents=True)
    (org / "flows/Route_Request.flow").write_text(route("Old_First_Outcome", "Approve"), encoding="utf-8")
    rep = json.loads(read_back(monkeypatch, t.root, org, "--json"))
    [row] = rep["components"]
    assert row["comparison"] == "different"
    assert row["differences"] == ["decisions Route.rules Old_First_Outcome: only in org"]
    assert rep["totals"]["different"] == 1 and rep["totals"]["same"] == 0


def test_a_standard_object_is_compared_not_dropped(tmp_path, monkeypatch):
    """Gemini F-10: a retrieved standard object defines no label of its own and was dropped (could not compare)."""
    t = Tree(tmp_path / "p")
    t.add(f"{DEFAULT}/objects/Account/Account.object-meta.xml",
          xml("CustomObject", "    <enableHistory>true</enableHistory>\n"))
    org = tmp_path / "org"
    (org / "objects").mkdir(parents=True)
    (org / "objects/Account.object").write_text(mdapi("CustomObject", "<enableHistory>false</enableHistory>"),
                                                encoding="utf-8")
    rep = json.loads(read_back(monkeypatch, t.root, org, "--json"))
    [row] = rep["components"]
    assert row["comparison"] == "different" and row["differences"] == ["enableHistory: source 'true', org 'false'"]


def test_an_activity_field_is_found_granted_on_task(tmp_path, monkeypatch):
    """Gemini F-11: FieldPermissions rows for an Activity field name Task and Event."""
    class Granted(CopyOrg):
        def __call__(self, argv, **kw):
            argv = [str(a) for a in argv]
            if argv[1:3] == ["data", "query"] and "FROM FieldPermissions" in argv[argv.index("--query") + 1]:
                self.calls.append(argv)
                q = argv[argv.index("--query") + 1]
                assert "'Task.Call__c'" in q and "'Event.Call__c'" in q and "'Activity.Call__c'" in q
                body = {"records": [{"Field": "Task.Call__c", "PermissionsRead": True, "PermissionsEdit": True,
                                     "Parent": {"Name": "Caller", "IsOwnedByProfile": False}}], "done": True}
                return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"status": 0, "result": body}))
            return super().__call__(argv, **kw)
    t = Tree(tmp_path / "p")
    t.field("Activity", "Call__c", type="Checkbox")
    org = tmp_path / "org"
    org.mkdir()
    fake = Granted(org)
    monkeypatch.setattr(deployed, "run_tool", fake)
    monkeypatch.setattr("jsc_advisory.sf.run_tool", fake)
    code, out, err = run("check", "deployed", "--target-org", "practice", "--source-dir", str(t.root), "--json")
    [a] = json.loads(out)["fields"]
    assert a["rows"] == 1 and a["message"] == "granted by Caller (read and edit)"


ALONE = "org/unpackaged/permissionsets/Torque_Check_Access.permissionset"


@pytest.mark.parametrize("names_the_field,expected", [(False, "different"), (True, "same")])
def test_a_permission_set_retrieved_alone_returns_all_its_entries(tmp_path, monkeypatch, names_the_field, expected):
    """Live 2026-11-14: `sf project retrieve start --metadata PermissionSet:Torque_Check_Access`, nothing else in
    the request, returned the set with its fieldPermissions and objectPermissions entries; that file is byte for
    byte the live pair's copy. So an entry only the org has is a difference even when its field was not
    retrieved."""
    project, _org = live_pair(tmp_path)
    org = tmp_path / "alone"
    (org / "permissionsets").mkdir(parents=True)
    (org / "permissionsets/Torque_Check_Access.permissionset").write_text(LIVE_PAIR[ALONE], encoding="utf-8",
                                                                          newline="")
    path = project / "force-app/main/default/permissionsets/Torque_Check_Access.permissionset-meta.xml"
    if not names_the_field:
        text = path.read_text(encoding="utf-8")
        start, end = text.index("    <fieldPermissions>"), text.index("</fieldPermissions>\n") + 20
        path.write_text(text[:start] + text[end:], encoding="utf-8")
    rep = json.loads(read_back(monkeypatch, project, org, "--only", "PermissionSet:Torque_Check_Access", "--json"))
    [row] = rep["components"]
    assert (row["comparison"], row["not_compared_entries"]) == (expected, 0)
    if expected == "different":
        assert row["differences"] == ["fieldPermissions Torque_Check__c.Note__c: only in org: a permission set deploy "
                                      "replaces the set, so this entry was added after the deploy, or the file is "
                                      "not what was deployed"]
    assert rep["api_version_note"] is None             # the live project is at 62.0


def test_a_project_below_api_40_says_so(tmp_path, monkeypatch):
    t = Tree(tmp_path / "p", api="39.0")
    t.permset("Old_Set")
    org = tmp_path / "org"
    (org / "permissionsets").mkdir(parents=True)
    (org / "permissionsets/Old_Set.permissionset").write_text(mdapi("PermissionSet", "<label>Old_Set</label>"),
                                                              encoding="utf-8")
    out = read_back(monkeypatch, t.root, org)
    assert "Note: the project's sourceApiVersion 39.0 is below 40.0: a permission set retrieve may then return" in out
