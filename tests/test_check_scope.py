"""`torque check metadata` scope: a deploy result, a baseline, --only, files, or everything (said out loud)."""
import json

from check_fixtures import Tree, create_task, element, e10_project, start, USER_ID, DEFAULT
from torque.checks import metadata, report


def hits(r, rule, name=None):
    return [f for f in r["findings"] if f["rule"] == rule and (name is None or f["component"]["name"] == name)]


def result_file(path, files, envelope=True, check_only=True):
    body = {"checkOnly": check_only, "done": True, "id": "0Af000000000001AAA", "status": "Succeeded",
            "success": True, "numberComponentsTotal": len(files), "files": files}
    path.write_text(json.dumps({"status": 0, "result": body} if envelope else body), encoding="utf-8")
    return path


def test_changed_from_checks_only_created_and_changed(tmp_path):
    t = Tree(tmp_path / "p")
    t.field("Shift__c", "Hours__c", type="Number")                # Created, no grant: error
    t.field("Shift__c", "Old__c", desc=None)                       # Unchanged, no description: not reported
    t.field("Shift__c", "Edited__c", desc=None)                    # Changed: description finding, grant not asked
    t.permset("Shift_User", fields=["Shift__c.Old__c"])            # Unchanged but part of the deploy
    t.field("Shift__c", "Outside__c", desc=None)                   # not in the result at all
    res = result_file(tmp_path / "dry-run.json", [
        {"fullName": "Shift__c.Hours__c", "type": "CustomField", "state": "Created",
         "filePath": "C:\\work\\p\\force-app\\main\\default\\objects\\Shift__c\\fields\\Hours__c.field-meta.xml"},
        {"fullName": "Shift__c.Old__c", "type": "CustomField", "state": "Unchanged", "filePath": ""},
        {"fullName": "Shift__c.Edited__c", "type": "CustomField", "state": "Changed", "filePath": ""},
        {"fullName": "Shift_User", "type": "PermissionSet", "state": "Unchanged", "filePath": ""},
        {"fullName": "Shift__c.Gone__c", "type": "CustomField", "state": "Created", "filePath": ""},
        {"fullName": "Shift_Tab", "type": "CustomTab", "state": "Created", "filePath": ""},
        {"fullName": "Retired_Flow", "type": "Flow", "state": "Deleted", "filePath": ""},
    ])
    r = t.check(changed_from=str(res))
    names = {f["component"]["name"] for f in r["findings"]}
    assert "Shift__c.Old__c" not in names and "Shift__c.Outside__c" not in names
    grant = hits(r, "access.field-without-grant")
    assert [f["component"]["name"] for f in grant] == ["Shift__c.Hours__c"]
    assert "Permission sets in this change: Shift_User" in grant[0]["action"]
    assert hits(r, "doc.description-missing", "Shift__c.Edited__c")
    assert not hits(r, "access.field-without-grant", "Shift__c.Edited__c")
    s = r["scope"]
    assert s["mode"] == "changed-from" and s["in_scope"] == 2 and s["unchanged_not_checked"] == 2
    assert s["not_found"] == [{"type": "CustomField", "name": "Shift__c.Gone__c", "state": "Created"}]
    assert s["types_without_rules"] == {"CustomTab": 1} and s["deleted"] == [{"type": "Flow", "name": "Retired_Flow"}]
    assert hits(r, "doc.description-missing", "Shift__c.Edited__c")[0]["inherited"] is None
    first = report.metadata_text(r).splitlines()[0]
    assert "2 component(s) the check-only result" in first and "2 unchanged not checked" in first


def test_changed_from_component_details_without_files_list(tmp_path):
    t = Tree(tmp_path / "p")
    t.field("Shift__c", "Hours__c", type="Number")
    t.field("Shift__c", "Same__c", desc=None)
    body = {"status": 0, "result": {"checkOnly": True, "id": "0Af000000000002AAA", "status": "Succeeded",
            "details": {"componentSuccesses": [
                {"componentType": "CustomField", "fullName": "Shift__c.Hours__c", "created": "true", "changed": "false",
                 "deleted": "false", "fileName": "objects/Shift__c.object"},
                {"componentType": "CustomField", "fullName": "Shift__c.Same__c", "created": "false",
                 "changed": "false", "deleted": "false"},
                {"componentType": "", "fullName": "package.xml", "created": "false", "changed": "true"}]}}}
    res = tmp_path / "r.json"
    res.write_text(json.dumps(body), encoding="utf-8")
    r = t.check(changed_from=str(res))
    assert {f["component"]["name"] for f in r["findings"]} == {"Shift__c.Hours__c"}


def test_changed_from_matches_a_windows_file_path_when_names_differ(tmp_path):
    t = Tree(tmp_path / "p")
    t.add(f"{DEFAULT}/layouts/Shift__c-Shift %28Volunteer%29 Layout.layout-meta.xml",
          '<?xml version="1.0"?><Layout xmlns="http://soap.sforce.com/2006/04/metadata"><layoutSections>'
          "<label>Details</label></layoutSections></Layout>")
    res = result_file(tmp_path / "r.json", [
        {"fullName": "Shift__c-Shift (Volunteer) Layout", "type": "Layout", "state": "Changed",
         "filePath": "C:\\work\\p\\force-app\\main\\default\\layouts\\Shift__c-Shift %28Volunteer%29 Layout.layout-meta.xml"}])
    r = t.check(changed_from=str(res))
    assert r["scope"]["in_scope"] == 1 and not r["scope"]["not_found"]


def test_baseline_labels_inherited_and_counts_new_elements(tmp_path):
    before, after = Tree(tmp_path / "before"), Tree(tmp_path / "after")
    old = start(target="Old_Step") + element("assignments", "Old_Step", desc=None, connector="Do_It") \
        + create_task(desc="Creates the task.")
    new = start(target="Old_Step") + element("assignments", "Old_Step", desc=None, connector="Do_It") \
        + create_task(desc="Creates the task.", connector="Added_Step") + element("assignments", "Added_Step", desc=None) \
        + element("assignments", "Added_Described")
    before.flow("Thank_You", old, desc=None, api="64.0")
    after.flow("Thank_You", new, desc=None, api="63.0")
    before.field("Shift__c", "Same__c", desc=None)
    after.field("Shift__c", "Same__c", desc=None)
    after.field("Shift__c", "Brand_New__c")
    after.permset("Shift_User", fields=["Shift__c.Brand_New__c", "Shift__c.Same__c"])
    r = after.check(baseline=str(before.root))
    s = r["scope"]
    assert s["mode"] == "baseline" and s["by_status"] == {"changed": 1, "new": 2} and s["unchanged_not_checked"] == 1
    fault = hits(r, "flow.no-fault-path", "Thank_You")[0]
    assert fault["inherited"] is True                                # Do_It had no fault path before either
    desc = hits(r, "doc.description-missing", "Thank_You")[0]
    assert desc["inherited"] is True
    elements = hits(r, "doc.flow-elements-undescribed", "Thank_You")[0]
    assert "1 of 2 flow elements new in this change" in elements["message"] and "Added_Step" in elements["message"]
    assert elements["inherited"] is False and "Old_Step" not in elements["message"]
    lowered = hits(r, "flow.api-version-behind", "Thank_You")[0]
    assert lowered["severity"] == "error" and lowered["inherited"] is False
    assert all(f["inherited"] is False for f in r["findings"] if f["component"]["status"] == "new")
    text = report.metadata_text(r)
    assert "[inherited]" in text and "[new in this change]" in text


def test_only_and_unknown_names(tmp_path):
    t = Tree(tmp_path)
    t.field("Shift__c", "Hours__c", desc=None)
    t.field("Shift__c", "Other__c", desc=None)
    t.permset("Shift_User", fields=["Shift__c.Hours__c", "Shift__c.Other__c"])
    r = t.check(only=["CustomField:shift__c.hours__c", "PermissionSet:Shift_User", "Flow:Missing_Flow"])
    assert {f["component"]["name"] for f in r["findings"]} == {"Shift__c.Hours__c"}
    assert r["scope"]["not_found"] == [{"type": "Flow", "name": "Missing_Flow", "state": ""}]
    assert not hits(r, "access.field-without-grant")


def test_explicit_files_are_the_scope_and_the_project_is_context(tmp_path):
    t = Tree(tmp_path)
    picked = t.field("Shift__c", "Hours__c", desc=None)
    t.field("Shift__c", "Other__c", desc=None)
    t.permset("Shift_User", fields=["Shift__c.Hours__c"])
    r = metadata.check([str(picked)], analyzer_mode="off", lessons=False)
    assert r["scope"]["mode"] == "files" and r["scope"]["in_scope"] == 1
    assert {f["component"]["name"] for f in r["findings"]} == {"Shift__c.Hours__c"}
    grant = hits(r, "access.field-without-grant")[0]
    assert "Shift_User under the checked folder grants it but is not in this change" in grant["action"]


def test_a_file_and_a_folder_are_both_in_scope(tmp_path):
    """Review R2-C04: a field file and a flows folder given together; the folder was silently context only."""
    t = Tree(tmp_path)
    picked = t.field("Shift__c", "Hours__c")
    flow = t.flow("Bare", "", desc=None)
    t.field("Shift__c", "Other__c", desc=None)                    # around them: context, not scope
    r = metadata.check([str(picked), str(flow.parent), str(picked)], analyzer_mode="off", lessons=False)
    assert r["scope"]["mode"] == "files" and r["scope"]["in_scope"] == 2
    assert {f["component"]["name"] for f in r["findings"]} == {"Shift__c.Hours__c", "Bare"}
    assert hits(r, "doc.description-missing", "Bare")
    assert r["scope"]["outside_the_change"] == 1
    assert "in the files and folders given" in report.scope_line(r)


def test_everything_says_so_first_and_names_the_options(tmp_path):
    t = Tree(tmp_path)
    t.field("Shift__c", "Hours__c")
    t.permset("Shift_User", fields=["Shift__c.Hours__c"])
    r = t.check()
    first = report.metadata_text(r).splitlines()[0]
    assert first.startswith("Scope: everything under") and "2 component(s) in 2 file(s)" in first
    for option in ("--changed-from", "--baseline", "--only"):
        assert option in first


def test_bad_scope_input_is_an_input_error(tmp_path):
    t = Tree(tmp_path)
    import pytest
    with pytest.raises(metadata.InputError, match="Type:Name"):
        t.check(only=["Hours__c"])
    with pytest.raises(metadata.InputError, match="not a folder"):
        t.check(baseline=str(tmp_path / "nope"))
    with pytest.raises(metadata.InputError, match="no such file"):
        t.check(changed_from=str(tmp_path / "nope.json"))
    with pytest.raises(metadata.InputError, match="no such file or folder"):
        metadata.check([str(tmp_path / "missing")], analyzer_mode="off", lessons=False)


def test_e10_planted_defects(tmp_path):
    """The walkthrough's 11 planted defects: 9 are visible in files; D7 (a threshold the recorded decision says must
    be configurable) and D11 (acceptance criteria with no component) are not."""
    t = e10_project(tmp_path)
    r = t.check()
    got = {(f["rule"], f["component"]["name"]) for f in r["findings"]}
    expected = {
        ("doc.description-missing", "Shift__c.Hours__c"),           # D1
        ("doc.help-text-missing", "Shift__c.Hours__c"),             # D1
        ("access.field-without-grant", "Shift__c.Hours__c"),        # D2
        ("flow.no-fault-path", "Gift_Thank_You_Task"),              # D3
        ("flow.hard-coded-id", "Gift_Thank_You_Task"),              # D4
        ("doc.description-missing", "Gift_Thank_You_Task"),         # D5
        ("doc.flow-elements-undescribed", "Gift_Thank_You_Task"),   # D5
        ("flow.api-version-behind", "Gift_Thank_You_Task"),         # D6
        ("flow.reruns-on-every-update", "Gift_Thank_You_Task"),     # D8
        ("flow.active-in-source", "Gift_Thank_You_Task"),           # D9
        ("doc.description-missing", "Shift__c.Hours_In_Range"),     # D10
        ("doc.description-missing", "Shift_Coordinator"),           # D10
    }
    assert expected <= got
    assert got - expected == set()        # nothing else: the well-built parts raise no finding
    grant = hits(r, "access.field-without-grant")[0]
    assert "Shift_Coordinator" in grant["action"]
    assert USER_ID not in json.dumps(r)
