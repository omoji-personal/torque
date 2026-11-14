"""`torque check metadata` rules: each fires on a made-up positive case and stays quiet on a similar negative."""
import json

from check_fixtures import Tree, create_task, element, start, USER_ID, USER_ID_15, DEFAULT, xml, tag
from torque.checks import metadata


def hits(report, rule, name=None):
    return [f for f in report["findings"] if f["rule"] == rule and (name is None or f["component"]["name"] == name)]


def test_field_without_grant_and_its_exemptions(tmp_path):
    t = Tree(tmp_path)
    t.obj("Shift__c")
    t.obj("Prefs__c", extra="    <customSettingsType>Hierarchy</customSettingsType>\n")
    t.field("Shift__c", "Hours__c", type="Number")
    t.field("Shift__c", "Granted__c")
    t.field("Shift__c", "Must__c", required="true")
    t.field("Shift__c", "Parent__c", type="MasterDetail")
    t.field("Switch__mdt", "On__c", type="Checkbox")
    t.field("Prefs__c", "Limit__c", type="Number")
    t.field("Account", "Industry", type="Picklist")           # a standard field takes no new grant
    t.permset("Shift_User", fields=["Shift__c.Granted__c"])
    r = t.check()
    names = {f["component"]["name"] for f in hits(r, "access.field-without-grant")}
    assert names == {"Shift__c.Hours__c"}
    f = hits(r, "access.field-without-grant")[0]
    assert f["severity"] == "warning" and "Shift_User" in f["action"] and f["line"] == 2   # no scope given


def test_grant_found_elsewhere_is_named(tmp_path):
    t = Tree(tmp_path)
    t.field("Shift__c", "Hours__c", type="Number")
    t.permset("Shift_User", fields=["Shift__c.Hours__c"])
    r = t.check(only=["CustomField:Shift__c.Hours__c"])
    f = hits(r, "access.field-without-grant")[0]
    assert f["severity"] == "note"            # the project grants it; --only did not name the permission set
    assert "PermissionSet Shift_User under the checked folder grants it" in f["action"]
    assert "Permission sets in this change: none" in f["action"]


def test_grant_only_in_profile(tmp_path):
    t = Tree(tmp_path)
    t.field("Shift__c", "Hours__c", type="Number")
    t.field("Shift__c", "Both__c")
    t.profile("Volunteer", fields=["Shift__c.Hours__c", "Shift__c.Both__c"])
    t.permset("Shift_User", fields=["Shift__c.Both__c"])
    r = t.check()
    assert [f["component"]["name"] for f in hits(r, "access.grant-only-in-profile")] == ["Shift__c.Hours__c"]
    assert not hits(r, "access.field-without-grant")


def test_formula_field_editable(tmp_path):
    t = Tree(tmp_path)
    t.field("Shift__c", "Total__c", type="Number", formula="Hours__c * 2", help=None)
    t.field("Shift__c", "Hours__c", type="Number")
    t.permset("Editors", fields=["Shift__c.Total__c", "Shift__c.Hours__c"], editable=True)
    t.permset("Readers", fields=["Shift__c.Total__c"], editable=False)
    r = t.check()
    found = hits(r, "access.formula-field-editable")
    assert len(found) == 1 and found[0]["component"]["name"] == "Editors" and "Shift__c.Total__c" in found[0]["message"]


def test_profile_in_change_and_powerful_permission(tmp_path):
    t = Tree(tmp_path)
    t.profile("Volunteer")
    t.permset("Admin_Like", extra="    <userPermissions>\n        <enabled>true</enabled>\n"
              "        <name>ModifyAllData</name>\n    </userPermissions>\n    <userPermissions>\n"
              "        <enabled>false</enabled>\n        <name>ViewAllData</name>\n    </userPermissions>\n"
              "    <objectPermissions>\n        <allowRead>true</allowRead>\n        <modifyAllRecords>true"
              "</modifyAllRecords>\n        <object>Shift__c</object>\n        <viewAllRecords>false</viewAllRecords>\n"
              "    </objectPermissions>\n")
    t.permset("Plain")
    r = t.check()
    assert [f["component"]["name"] for f in hits(r, "access.profile-in-change")] == ["Volunteer"]
    powerful = hits(r, "access.powerful-permission")
    assert {f["component"]["name"] for f in powerful} == {"Admin_Like"}
    assert sorted(f["message"] for f in powerful) == ["The permission set enables Modify All Data.",
                                                      "The permission set grants Modify All on Shift__c."]


def test_powerful_permissions_in_a_profile_and_the_wider_list(tmp_path):
    """Gemini F-14: profiles were not inspected; Author Apex and permission management were not listed."""
    t = Tree(tmp_path)
    up = "    <userPermissions>\n        <enabled>{on}</enabled>\n        <name>{name}</name>\n    </userPermissions>\n"
    t.add("force-app/main/default/profiles/Ops.profile-meta.xml", xml("Profile", up.format(on="true", name="ModifyAllData")
          + up.format(on="true", name="AssignPermissionSets") + up.format(on="true", name="ApiEnabled")
          + "    <custom>true</custom>\n"))
    t.profile("Quiet")
    t.permset("Coder", extra=up.format(on="true", name="AuthorApex"))
    r = t.check()
    powerful = {f["component"]["name"]: f["message"] for f in hits(r, "access.powerful-permission")}
    assert powerful == {"Ops": "The profile enables Modify All Data, Assign Permission Sets.",
                        "Coder": "The permission set enables Author Apex."}


def test_grants_the_deploy_rejects(tmp_path):
    """Gemini F-13: an editable auto-number field, edit without read, and a formula field on Activity."""
    t = Tree(tmp_path)
    t.field("Shift__c", "Ref__c", type="AutoNumber", help=None)
    t.field("Activity", "Age__c", type="Number", formula="TODAY() - DATEVALUE(CreatedDate)", help=None)
    t.field("Shift__c", "Hours__c", type="Number")
    t.add("force-app/main/default/permissionsets/Bad.permissionset-meta.xml", xml("PermissionSet", "".join(
        f"    <fieldPermissions>\n        <editable>true</editable>\n        <field>{f}</field>\n"
        f"        <readable>{r}</readable>\n    </fieldPermissions>\n"
        for f, r in (("Shift__c.Ref__c", "true"), ("Task.Age__c", "true"), ("Shift__c.Hours__c", "false")))
        + "    <label>Bad</label>\n"))
    r = t.check()
    got = sorted(f["message"] for f in hits(r, "access.formula-field-editable"))
    assert got == ["Shift__c.Hours__c is granted editable but not readable; the deploy fails on it (edit needs read).",
                   "Shift__c.Ref__c is an auto-number field granted as editable; the deploy fails on it.",
                   "Task.Age__c is a formula field granted as editable; the deploy fails on it."]


def test_descriptions_missing_and_placeholder(tmp_path):
    t = Tree(tmp_path)
    t.obj("Shift__c", desc=None)
    t.field("Shift__c", "Hours__c", desc="")
    t.field("Shift__c", "Notes__c", desc="Notes")
    t.field("Shift__c", "Todo__c", desc="TODO")
    t.field("Shift__c", "Good__c", desc="Why the field exists and what writes it.")
    t.field("Account", "Industry", desc=None, help=None, type="Picklist")       # standard: not ours to describe
    t.field("Account", "npsp__Managed__c", desc=None)                         # managed: reported once, as managed
    t.permset("Shift_User", desc=None, fields=["Shift__c.Hours__c", "Shift__c.Notes__c", "Shift__c.Todo__c",
                                               "Shift__c.Good__c"])
    t.rule("Shift__c", "Needs_Name", desc=None)
    t.add(f"{DEFAULT}/customPermissions/Bypass_Rules.customPermission-meta.xml",
          xml("CustomPermission", tag("label", "Bypass Rules")))
    t.add(f"{DEFAULT}/objects/Shift__c/recordTypes/Weekend.recordType-meta.xml",
          xml("RecordType", tag("fullName", "Weekend") + tag("active", "true") + tag("description", "Weekend shifts.")
              + tag("label", "Weekend")))
    r = t.check()
    missing = {f["component"]["name"] for f in hits(r, "doc.description-missing")}
    assert missing == {"Shift__c", "Shift__c.Hours__c", "Shift_User", "Shift__c.Needs_Name", "Bypass_Rules"}
    assert {f["component"]["name"] for f in hits(r, "doc.description-placeholder")} == {"Shift__c.Notes__c",
                                                                                         "Shift__c.Todo__c"}
    assert hits(r, "managed.component-in-change", "Account.npsp__Managed__c")


def test_help_text_only_where_a_user_fills_in(tmp_path):
    t = Tree(tmp_path)
    t.field("Shift__c", "Hours__c", type="Number", help=None)
    t.field("Shift__c", "Total__c", type="Number", formula="Hours__c * 2", help=None)
    t.field("Shift__c", "Count__c", type="Summary", help=None)
    t.field("Shift__c", "Number__c", type="AutoNumber", help=None)
    t.field("Switch__mdt", "On__c", type="Checkbox", help=None)
    t.field("Shift__c", "Helped__c")
    r = t.check()
    assert {f["component"]["name"] for f in hits(r, "doc.help-text-missing")} == {"Shift__c.Hours__c"}


def test_too_long(tmp_path):
    t = Tree(tmp_path)
    t.permset("Long", desc="x" * 256)
    t.permset("Fits", desc="x" * 255)
    t.field("Shift__c", "Hours__c", help="y" * 511)
    t.permset("Shift_User", fields=["Shift__c.Hours__c"])
    r = t.check()
    found = {(f["component"]["name"], f["line"] is not None) for f in hits(r, "doc.too-long")}
    assert found == {("Long", True), ("Shift__c.Hours__c", True)}


def test_flow_element_descriptions_and_version_in_description(tmp_path):
    t = Tree(tmp_path)
    t.flow("Described", start(target="Do_It") + create_task(fault="Do_It"), desc="Creates the follow-up task.")
    t.flow("Bare", start(target="A") + element("assignments", "A", desc=None) + element("decisions", "B", desc=None),
           desc="Version 3 of the follow-up, see v2 notes.")
    t.flow("Api_Mention", start(target="Do_It") + create_task(fault="Do_It"), desc="Runs on API version 62.0 rules.")
    r = t.check()
    undescribed = hits(r, "doc.flow-elements-undescribed")
    assert [f["component"]["name"] for f in undescribed] == ["Bare"] and "2 of 2" in undescribed[0]["message"]
    assert [f["component"]["name"] for f in hits(r, "doc.description-names-version")] == ["Bare"]


def test_apex_header_sharing_asserts_see_all_data(tmp_path):
    t = Tree(tmp_path)
    t.apex("NoHeader", "public class NoHeader {\n    public static Integer one() { return 1; }\n}\n")
    t.apex("WithHeader", "/** Adds numbers for the shift totals. */\npublic with sharing class WithHeader {}\n")
    t.apex("Quiet", "// Shared helpers. The word class in a comment and 'with sharing' in text do not count.\n"
                    "public class Quiet { String s = 'without sharing'; }\n")
    t.apex("NoAssertTest", "@isTest\nprivate class NoAssertTest {\n    @isTest static void runs() { "
                           "NoHeader.one(); }\n}\n")
    t.apex("GoodTest", "@isTest\nprivate class GoodTest {\n    @isTest static void adds() { "
                       "Assert.areEqual(1, NoHeader.one()); }\n}\n")
    t.apex("DataFactory", "@isTest\npublic class DataFactory {\n    public static Account make() { return new "
                          "Account(Name='x'); }\n}\n")
    t.apex("OldTest", "@isTest(SeeAllData=true)\nprivate class OldTest {\n    static testMethod void reads() { "
                      "System.assertEquals(1, 1); }\n}\n")
    r = t.check()
    assert {f["component"]["name"] for f in hits(r, "doc.apex-header-missing")} == {"NoHeader"}
    assert {f["component"]["name"] for f in hits(r, "code.no-sharing-declaration")} == {"NoHeader", "Quiet"}
    assert {f["component"]["name"] for f in hits(r, "code.test-without-assert")} == {"NoAssertTest"}
    assert {f["component"]["name"] for f in hits(r, "code.see-all-data")} == {"OldTest"}
    assert hits(r, "code.see-all-data")[0]["line"] == 1


def test_fault_paths(tmp_path):
    t = Tree(tmp_path)
    lookup = element("recordLookups", "Find_It", "        <object>Account</object>\n", connector="Do_It")
    sub = element("subflows", "Call_It", "        <flowName>Other</flowName>\n")
    t.flow("No_Fault", start(target="Find_It") + lookup + create_task(connector="Call_It") + sub)
    t.flow("With_Fault", start(target="Do_It") + create_task(fault="Log_It") + element("assignments", "Log_It"))
    before = element("recordUpdates", "Set_It", "        <inputReference>$Record</inputReference>\n")
    t.flow("Before_Save", start(trigger="RecordBeforeSave", target="Set_It") + before)
    r = t.check()
    got = {(f["component"]["name"], f["severity"], f["message"].split(" ")[0]) for f in hits(r, "flow.no-fault-path")}
    assert got == {("No_Fault", "note", "Find_It"), ("No_Fault", "warning", "Do_It"), ("No_Fault", "note", "Call_It")}


def loop_flow():
    loop = ("    <loops>\n        <name>Each</name>\n        <label>Each</label>\n        <description>Each shift."
            "</description>\n        <collectionReference>Shifts</collectionReference>\n"
            "        <nextValueConnector><targetReference>Check</targetReference></nextValueConnector>\n"
            "        <noMoreValuesConnector><targetReference>Update_After</targetReference></noMoreValuesConnector>\n"
            "    </loops>\n")
    decision = ("    <decisions>\n        <name>Check</name>\n        <label>Check</label>\n"
                "        <description>Stops on a bad row.</description>\n"
                "        <defaultConnector><targetReference>Update_In_Loop</targetReference></defaultConnector>\n"
                "        <rules>\n            <name>Bad</name>\n            <label>Bad</label>\n"
                "            <connector><targetReference>Delete_And_Leave</targetReference></connector>\n"
                "        </rules>\n    </decisions>\n")
    return (start(target="Each", trigger="RecordAfterSave") + loop + decision
            + element("recordUpdates", "Update_In_Loop", connector="Each", fault="Each")
            + element("recordDeletes", "Delete_And_Leave", fault="Update_After")
            + element("recordUpdates", "Update_After", fault="Each_Done")
            + element("assignments", "Each_Done"))


def test_work_in_loop_follows_only_the_cycle(tmp_path):
    t = Tree(tmp_path)
    t.flow("Loop_Dml", loop_flow())
    r = t.check()
    names = [f["message"].split(" ")[0] for f in hits(r, "flow.work-in-loop")]
    # The update on the loop's cycle counts; the delete on a path that leaves the loop for good and the update after
    # "no more values" do not. (Update_After's fault path to Each_Done does not return to the loop.)
    assert names == ["Update_In_Loop"]


def test_entry_conditions_reruns_and_same_record(tmp_path):
    t = Tree(tmp_path)
    t.flow("No_Entry", start(filters=False, target="Do_It") + create_task(fault="Do_It"))
    t.flow("Reruns", start(target="Do_It") + create_task(fault="Do_It"))
    t.flow("Once", start(target="Do_It", extra="        <doesRequireRecordChangedToMeetCriteria>true"
                                               "</doesRequireRecordChangedToMeetCriteria>\n") + create_task(fault="Do_It"))
    was = ("    <formulas><name>Was</name><dataType>Boolean</dataType><expression>{!$Record__Prior.IsClosed}"
           "</expression></formulas>\n")

    def decide(left, operator="EqualTo", right="<booleanValue>false</booleanValue>"):
        return element("decisions", "Check", "        <rules><name>Go</name><conditionLogic>and</conditionLogic>"
                       f"<conditions><leftValueReference>{left}</leftValueReference><operator>{operator}</operator>"
                       f"<rightValue>{right}</rightValue></conditions><connector><targetReference>Do_It"
                       "</targetReference></connector><label>Go</label></rules>\n")
    t.flow("Prior_Through_Formula", start(target="Check") + decide("Was") + create_task(fault="Do_It") + was)
    t.flow("Prior_In_Decision", start(target="Check") + decide("$Record__Prior.StageName", "NotEqualTo",
                                                                "<elementReference>$Record.StageName</elementReference>")
           + create_task(fault="Do_It"))
    t.flow("Prior_On_The_Right", start(target="Check") + decide("$Record.StageName", "NotEqualTo",
                                                                 "<elementReference>$Record__Prior.StageName"
                                                                 "</elementReference>") + create_task(fault="Do_It"))
    t.flow("Is_Changed_Filter", start(target="Do_It", extra="        <filters><field>Amount</field><operator>IsChanged"
                                                            "</operator><value><booleanValue>true</booleanValue>"
                                                            "</value></filters>\n") + create_task(fault="Do_It"))
    t.flow("Prior_Unused", start(target="Do_It") + create_task(fault="Do_It") + was)       # a resource no one reads
    t.flow("Prior_In_Words", start(target="Do_It") + create_task(fault="Do_It"),
           desc="Creates a task on every qualifying save; consider $Record__Prior.")    # review R2-C05
    t.flow("Prior_In_A_Label", start(target="Check") + decide("$Record.StageName").replace(
        "<label>Go</label>", "<label>Unlike $Record__Prior</label>") + create_task(fault="Do_It"))
    t.flow("Create_Only", start(target="Do_It", when="Create") + create_task(fault="Do_It"))
    same = element("recordUpdates", "Touch", "        <inputReference>$Record</inputReference>\n", fault="Touch_Log")
    t.flow("Same_Record", start(target="Touch") + same + element("assignments", "Touch_Log"))
    t.flow("Same_And_Task", start(target="Touch") + same.replace("Touch_Log", "Do_It") + create_task(fault="Do_It"))
    r = t.check()
    assert [f["component"]["name"] for f in hits(r, "flow.no-entry-conditions")] == ["No_Entry"]
    assert sorted(f["component"]["name"] for f in hits(r, "flow.reruns-on-every-update")) == [
        "Prior_In_A_Label", "Prior_In_Words", "Prior_Unused", "Reruns", "Same_And_Task"]
    assert [f["component"]["name"] for f in hits(r, "flow.after-save-same-record")] == ["Same_Record"]


def test_hard_coded_ids_in_flows_and_rules(tmp_path):
    t = Tree(tmp_path)
    t.flow("Owner_Id", start(target="Do_It") + create_task(owner=USER_ID, fault="Do_It"))
    t.flow("No_Id", start(target="Do_It") + create_task(owner="Closed_Won_2024_Q1", fault="Do_It")
           + "    <processMetadataValues><name>Origin</name><value><stringValue>" + USER_ID
           + "</stringValue></value></processMetadataValues>\n")
    t.rule("Shift__c", "Owner_Only", formula=f"OwnerId &lt;&gt; '{USER_ID_15}'")
    t.rule("Shift__c", "Name_Needed", formula="AND(ISBLANK(Name), Region_Code_2024__c = 'NE12345')")
    r = t.check()
    flow_hits = hits(r, "flow.hard-coded-id")
    assert [f["component"]["name"] for f in flow_hits] == ["Owner_Id"]
    assert "User Id (prefix 005)" in flow_hits[0]["message"] and USER_ID not in flow_hits[0]["message"]
    assert [f["component"]["name"] for f in hits(r, "rule.hard-coded-id")] == ["Shift__c.Owner_Only"]


def test_api_version_behind(tmp_path):
    t = Tree(tmp_path, api="62.0")
    t.flow("Old", start(target="Do_It") + create_task(fault="Do_It"), api="60.0")
    t.flow("New", start(target="Do_It") + create_task(fault="Do_It"), api="64.0")
    t.flow("Held", start(target="Do_It") + create_task(fault="Do_It"), api="63.0")   # above the project: silent
    r = t.check()
    found = hits(r, "flow.api-version-behind")
    assert [(f["component"]["name"], f["severity"]) for f in found] == [("Old", "note")]
    assert "below 62.0, the project's sourceApiVersion" in found[0]["message"]


def test_screen_action_without_trigger(tmp_path):
    action = ("        <actions>\n            <name>Find_It</name>\n            <actionName>Search</actionName>\n"
              "            <actionType>flow</actionType>\n            <label>Find</label>\n        </actions>\n")
    trigger = ("        <triggers>\n            <eventName>{event}</eventName>\n            <eventSource>S</eventSource>\n"
               "            <handlers><screenActionName>Find_It</screenActionName></handlers>\n        </triggers>\n")
    t = Tree(tmp_path)
    t.flow("No_Trigger", element("screens", "S", action), process="Flow")
    t.flow("Triggered", element("screens", "S", action + trigger.format(event="flow__screeninit")), process="Flow")
    t.flow("Odd_Event", element("screens", "S", action + trigger.format(event="flow__onload")), process="Flow")
    r = t.check()
    got = {(f["component"]["name"], f["severity"]) for f in hits(r, "flow.screen-action-without-trigger")}
    assert got == {("No_Trigger", "warning"), ("Odd_Event", "note")}


def test_default_names_status_and_run_mode(tmp_path):
    t = Tree(tmp_path)
    t.flow("Copied", start(target="Decision_1") + element("decisions", "Decision_1")
           + element("assignments", "Copy_1_of_Set_Owner"), status="Draft")
    t.flow("Named", start(target="Check_Amount") + element("decisions", "Check_Amount")
           + element("assignments", "Set_Decision_Owner"), status="Active",
           extra=tag("runInMode", "SystemModeWithoutSharing"))
    t.flow("Explained", start(target="Check_Amount") + element("decisions", "Check_Amount"),
           desc="Runs in system mode without sharing because volunteers cannot see donor records.",
           extra=tag("runInMode", "SystemModeWithoutSharing"))
    r = t.check()
    names = hits(r, "flow.default-names")
    assert [f["component"]["name"] for f in names] == ["Copied"] and "2 name(s)" in names[0]["message"]
    # one note for the report, not one per flow, and none for a Draft (Gemini F-18)
    assert [f["component"]["name"] for f in hits(r, "flow.active-in-source")] == ["Explained"]
    assert "This and any other flow file here that asks for status Active" in hits(r, "flow.active-in-source")[0][
        "message"]
    assert [f["component"]["name"] for f in hits(r, "flow.system-mode-without-sharing")] == ["Named"]


def test_choice_field_compared_to_literal(tmp_path):
    field = ("        <fields>\n            <name>Pick</name>\n            <choiceReferences>Email_Choice"
             "</choiceReferences>\n            <fieldType>RadioButtons</fieldType>\n        </fields>\n"
             "        <fields>\n            <name>Free_Text</name>\n            <fieldType>InputField</fieldType>\n"
             "        </fields>\n")
    t = Tree(tmp_path)
    t.flow("Choice", element("screens", "S", field)
           + "    <formulas><name>Is_Email</name><dataType>Boolean</dataType><expression>{!Pick} = 'EMAIL'"
             "</expression></formulas>\n"
           + "    <formulas><name>Is_Typed</name><dataType>Boolean</dataType><expression>{!Free_Text} = 'EMAIL'"
             "</expression></formulas>\n", process="Flow")
    r = t.check()
    found = hits(r, "flow.choice-compared-to-literal")
    assert len(found) == 1 and "Is_Email" in found[0]["message"]


def test_lookup_store_output_needs_first_record_only(tmp_path):
    t = Tree(tmp_path)
    auto = "        <object>Account</object>\n        <storeOutputAutomatically>true</storeOutputAutomatically>\n"
    t.flow("Missing", start(target="Find") + element("recordLookups", "Find", auto, fault="Find"))
    t.flow("Set", start(target="Find") + element("recordLookups", "Find", auto
           + "        <getFirstRecordOnly>true</getFirstRecordOnly>\n", fault="Find"))
    r = t.check()
    assert [f["component"]["name"] for f in hits(r, "flow.lookup-missing-first-record-only")] == ["Missing"]


def test_validation_rule_literals_and_message(tmp_path):
    t = Tree(tmp_path)
    t.rule("Shift__c", "Profile_Bypass", formula="AND($Profile.Name &lt;&gt; 'System Administrator', ISBLANK(Name))")
    t.rule("Shift__c", "Permission_Bypass", formula="AND(NOT($Permission.Bypass_Rules), ISBLANK(Name))")
    t.rule("Shift__c", "Owner_Check", formula="AND(OwnerId &lt;&gt; $User.Id, ISCHANGED(Hours__c))")
    t.rule("Shift__c", "No_Message", msg=None)
    r = t.check()
    assert [f["component"]["name"] for f in hits(r, "rule.profile-or-user-literal")] == ["Shift__c.Profile_Bypass"]
    assert [f["component"]["name"] for f in hits(r, "rule.no-error-message")] == ["Shift__c.No_Message"]


def test_page_sections_lost_needs_a_baseline(tmp_path):
    def page(labels):
        sections = "".join(
            "        <itemInstances><componentInstance><componentInstanceProperties><name>label</name>"
            f"<value>{label}</value></componentInstanceProperties><componentName>flexipage:fieldSection"
            "</componentName></componentInstance></itemInstances>\n" for label in labels)
        tabset = ("        <itemInstances><componentInstance><componentInstanceProperties><name>label</name>"
                  "<value>Tabs</value></componentInstanceProperties><componentName>flexipage:tabset</componentName>"
                  "</componentInstance></itemInstances>\n")
        return xml("FlexiPage", f"    <flexiPageRegions>\n{sections}{tabset}    </flexiPageRegions>\n"
                   + tag("masterLabel", "Shift Page") + tag("type", "RecordPage"))
    before, after = Tree(tmp_path / "before"), Tree(tmp_path / "after")
    before.add(f"{DEFAULT}/flexipages/Shift_Page.flexipage-meta.xml", page(["Details", "Hours", "Approval"]))
    after.add(f"{DEFAULT}/flexipages/Shift_Page.flexipage-meta.xml", page(["Details"]))
    before.add(f"{DEFAULT}/flexipages/Kept.flexipage-meta.xml", page(["Details", "Hours"]))
    after.add(f"{DEFAULT}/flexipages/Kept.flexipage-meta.xml", page(["Hours", "Details"]))
    r = after.check(baseline=str(before.root))
    found = hits(r, "page.sections-lost")
    assert [f["component"]["name"] for f in found] == ["Shift_Page"]
    assert "Approval, Hours" in found[0]["message"]
    assert not hits(after.check(), "page.sections-lost")


def test_case_collisions(tmp_path):
    t = Tree(tmp_path)
    t.flow("Amounts", start(target="Do_It") + create_task(fault="Do_It")
           + "    <variables><name>amt_1</name><dataType>Number</dataType></variables>\n"
           + "    <variables><name>Amt_1</name><dataType>Number</dataType></variables>\n")
    t.flow("Distinct", start(target="Do_It") + create_task(fault="Do_It")
           + "    <variables><name>amt_1</name><dataType>Number</dataType></variables>\n"
           + "    <variables><name>amt_2</name><dataType>Number</dataType></variables>\n")
    second = Tree(tmp_path / "other")
    second.flow("amounts", start(target="Do_It") + create_task(fault="Do_It"))
    r = metadata.check([str(t.root / "force-app"), str(second.root / "force-app")], analyzer_mode="off",
                       lessons=False)
    found = hits(r, "name.case-collision")
    assert {(f["component"]["name"], "flow-name" if "Flow names" in f["message"] else "component") for f in found} \
        == {("Amounts", "flow-name"), ("Amounts", "component")}


def test_managed_component_respects_project_namespace(tmp_path):
    t = Tree(tmp_path, namespace="own")
    t.field("npsp__Allocation__c", "Region__c")                 # our field on a managed object: fine
    t.field("npsp__Allocation__c", "npsp__Amount__c")           # the package's field
    t.field("Shift__c", "own__Mine__c")                         # the project's own namespace
    t.permset("Shift_User", fields=["npsp__Allocation__c.Region__c", "Shift__c.own__Mine__c"])
    r = t.check()
    assert {f["component"]["name"] for f in hits(r, "managed.component-in-change")} == {
        "npsp__Allocation__c.npsp__Amount__c"}


def test_secret_looking_values_report_key_only(tmp_path):
    t = Tree(tmp_path)
    secret = "q8Zr" + "T2mW9xK4vB7nL1pD"
    t.add(f"{DEFAULT}/customMetadata/Service.Main.md-meta.xml", xml("CustomMetadata", (
        "    <label>Main</label>\n    <values>\n        <field>Client_Secret__c</field>\n"
        f"        <value>{secret}</value>\n    </values>\n    <values>\n        <field>Endpoint__c</field>\n"
        "        <value>https://api.example.com/v2</value>\n    </values>\n    <values>\n"
        "        <field>Token_Label__c</field>\n        <value>Main token</value>\n    </values>\n")))
    t.add(f"{DEFAULT}/settings/Security.settings-meta.xml", xml("SecuritySettings", (
        "    <passwordPolicies>\n        <minimumPasswordLength>12</minimumPasswordLength>\n"
        "        <passwordExpiration>NinetyDays</passwordExpiration>\n    </passwordPolicies>\n")))
    r = t.check()
    found = hits(r, "secret.looks-like-secret")
    assert [(f["component"]["name"], f["message"].split(" ")[0]) for f in found] == [("Service.Main", "Client_Secret__c")]
    assert secret not in json.dumps(r)


def test_malformed_xml_is_one_finding_not_a_crash(tmp_path):
    t = Tree(tmp_path)
    t.add(f"{DEFAULT}/objects/Shift__c/fields/Broken__c.field-meta.xml", "<CustomField><label>Broken</CustomField>")
    t.add(f"{DEFAULT}/flows/Doctype.flow-meta.xml", '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Flow/>')
    t.field("Shift__c", "Fine__c")
    t.permset("Shift_User", fields=["Shift__c.Fine__c"])
    r = t.check()
    broken = hits(r, "file.unreadable")
    assert {f["component"]["name"] for f in broken} == {"Shift__c.Broken__c", "Doctype"}
    assert all(f["severity"] == "error" for f in broken)
    assert not [f for f in r["findings"] if f["component"]["name"] == "Shift__c.Broken__c" and f["rule"] != "file.unreadable"]
    assert r["scope"]["unreadable_files"] == 2


def test_metadata_api_format_object_file(tmp_path):
    t = Tree(tmp_path)
    t.add("mdapi/objects/Gizmo__c.object", xml("CustomObject", (
        "    <deploymentStatus>Deployed</deploymentStatus>\n    <description>A made-up gizmo.</description>\n"
        "    <fields>\n        <fullName>Size__c</fullName>\n        <label>Size</label>\n"
        "        <type>Number</type>\n    </fields>\n    <label>Gizmo</label>\n"
        "    <validationRules>\n        <fullName>Size_Positive</fullName>\n        <active>true</active>\n"
        f"        <errorConditionFormula>Size__c &lt; 0 &amp;&amp; OwnerId = '{USER_ID}'</errorConditionFormula>\n"
        "        <errorMessage>Size must be positive.</errorMessage>\n    </validationRules>\n")))
    r = t.check("mdapi")
    rules = {(f["rule"], f["component"]["name"]) for f in r["findings"]}
    assert ("access.field-without-grant", "Gizmo__c.Size__c") in rules
    assert ("doc.description-missing", "Gizmo__c.Size__c") in rules
    assert ("rule.hard-coded-id", "Gizmo__c.Size_Positive") in rules
    assert ("doc.description-missing", "Gizmo__c") not in rules
