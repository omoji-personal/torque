"""The comparison mutation corpus: one table, read by `check deployed`'s comparison and by the baseline scope.

Each row is a submitted file and an org copy (or, for the baseline, the copy retrieved before the change) and the
status the comparison must give. The table in docs/checks.md is this table in short form. Every name is invented.
"""
from __future__ import annotations

import pytest

from check_fixtures import NS
from torque.checks import compare, components, scope

DEFAULT = "force-app/main/default"
PATHS = {"Flow": "flows/Route_Request.flow-meta.xml", "CustomObject": "objects/Thing__c/Thing__c.object-meta.xml",
         "CustomField": "objects/Thing__c/fields/Kind__c.field-meta.xml",
         "ValidationRule": "objects/Thing__c/validationRules/Amount_Not_Negative.validationRule-meta.xml",
         "PermissionSet": "permissionsets/Thing_Access.permissionset-meta.xml",
         "Profile": "profiles/Thing_Profile.profile-meta.xml", "ApexClass": "classes/Greeting.cls"}
ROOTS = {"Flow": "Flow", "CustomObject": "CustomObject", "CustomField": "CustomField",
         "ValidationRule": "ValidationRule", "PermissionSet": "PermissionSet", "Profile": "Profile"}
# What a retrieve of these members returns entries for (the Metadata API returns access entries only for them).
RETRIEVED = {("CustomField", "thing__c.kind__c"), ("CustomField", "thing__c.other__c"), ("CustomObject", "thing__c"),
             ("ApexClass", "greeting"), ("CustomTab", "thing__c"), ("RecordType", "thing__c.special"),
             ("CustomPermission", "approve_refunds")}


def x(typ: str, body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{ROOTS[typ]} {NS}>\n{body}</{ROOTS[typ]}>\n'


# -- flows ---------------------------------------------------------------------------------------------------------
def outcome(name, level="High"):
    return (f"        <rules>\n            <name>{name}</name>\n            <conditionLogic>and</conditionLogic>\n"
            "            <conditions>\n                <leftValueReference>$Record.Priority</leftValueReference>\n"
            "                <operator>EqualTo</operator>\n"
            f"                <rightValue><stringValue>{level}</stringValue></rightValue>\n"
            "            </conditions>\n            <connector><targetReference>Accept</targetReference></connector>\n"
            f"            <label>{name}</label>\n        </rules>\n")


def update(name, value):
    return (f"    <recordUpdates>\n        <name>{name}</name>\n        <label>{name}</label>\n"
            "        <inputAssignments>\n            <field>Description</field>\n"
            f"            <value><stringValue>{value}</stringValue></value>\n        </inputAssignments>\n"
            "        <inputReference>$Record</inputReference>\n    </recordUpdates>\n")


LEVELS = {"Approve": "High", "Reject": "Low", "Old_Outcome": "High"}   # each outcome keeps its own condition


def flow(rules=("Approve", "Reject"), value="Approved by flow", extra="", start_first=False):
    head = "    <apiVersion>62.0</apiVersion>\n    <label>Route Request</label>\n"
    decision = ("    <decisions>\n        <name>Route</name>\n        <label>Route</label>\n"
                "        <defaultConnectorLabel>Otherwise</defaultConnectorLabel>\n"
                + "".join(outcome(r, LEVELS[r]) for r in rules) + "    </decisions>\n")
    start = ("    <start>\n        <connector><targetReference>Route</targetReference></connector>\n"
             "        <object>Thing__c</object>\n        <recordTriggerType>Update</recordTriggerType>\n"
             "        <triggerType>RecordAfterSave</triggerType>\n    </start>\n")
    tail = "    <processType>AutoLaunchedFlow</processType>\n    <status>Active</status>\n"
    parts = [head, decision, update("Accept", value), start, tail, extra]
    if start_first:
        parts = [start, tail, extra, update("Accept", value), decision, head]
    return x("Flow", "".join(parts))


# -- a custom object (source format) -------------------------------------------------------------------------------
OBJ = ("    <deploymentStatus>Deployed</deploymentStatus>\n    <description>Made-up object.</description>\n"
       "    <label>Thing</label>\n    <nameField>\n        <label>Thing Name</label>\n        <type>Text</type>\n"
       "    </nameField>\n    <pluralLabel>Things</pluralLabel>\n    <sharingModel>ReadWrite</sharingModel>\n")
ACTIONS = ("Accept", "CancelEdit", "Clone", "Delete", "Edit", "List", "New", "SaveEdit", "Tab", "View")
# What the live retrieve of 2026-11-14 returned beyond the source for a new object (compare.SERVER_ADDITIONS).
SERVER_DEFAULTS = ("".join(f"    <actionOverrides>\n        <actionName>{a}</actionName>\n{ff}        <type>Default</type>\n"
                           "    </actionOverrides>\n"
                           for a in ACTIONS for ff in ("", "        <formFactor>Large</formFactor>\n",
                                                       "        <formFactor>Small</formFactor>\n"))
                   + "    <allowInChatterGroups>false</allowInChatterGroups>\n"
                   "    <compactLayoutAssignment>SYSTEM</compactLayoutAssignment>\n"
                   "    <enableActivities>false</enableActivities>\n    <enableBulkApi>true</enableBulkApi>\n"
                   "    <enableFeeds>false</enableFeeds>\n    <enableHistory>false</enableHistory>\n"
                   "    <enableLicensing>false</enableLicensing>\n    <enableReports>false</enableReports>\n"
                   "    <enableSearch>false</enableSearch>\n    <enableSharing>true</enableSharing>\n"
                   "    <enableStreamingApi>true</enableStreamingApi>\n"
                   "    <externalSharingModel>Private</externalSharingModel>\n    <searchLayouts/>\n"
                   "    <visibility>Public</visibility>\n")


def obj(body=OBJ, extra=""):
    return x("CustomObject", body + extra)


# -- a picklist field --------------------------------------------------------------------------------------------
def field(values=("Small", "Large"), label="Kind", desc="Pick a kind of thing.", extra=""):
    items = "".join(f"            <value>\n                <fullName>{v}</fullName>\n"
                    f"                <default>false</default>\n                <label>{v}</label>\n"
                    "            </value>\n" for v in values)
    return x("CustomField", f"    <fullName>Kind__c</fullName>\n    <description>{desc}</description>\n"
                            f"    <label>{label}</label>\n    <type>Picklist</type>\n"
                            f"    <valueSet>\n        <valueSetDefinition>\n{items}"
                            "        </valueSetDefinition>\n    </valueSet>\n" + extra)


# -- a validation rule ---------------------------------------------------------------------------------------------
def vrule(formula="Amount__c &lt; 0", active="true", extra="", reorder=False):
    parts = [f"    <fullName>Amount_Not_Negative</fullName>\n", f"    <active>{active}</active>\n",
             "    <description>The amount cannot be below zero.</description>\n",
             f"    <errorConditionFormula>{formula}</errorConditionFormula>\n",
             "    <errorMessage>Enter an amount of zero or more.</errorMessage>\n", extra]
    return x("ValidationRule", "".join(reversed(parts) if reorder else parts))


# -- permission sets and profiles ----------------------------------------------------------------------------------
def fp(f, read, edit):
    return (f"    <fieldPermissions>\n        <editable>{edit}</editable>\n        <field>{f}</field>\n"
            f"        <readable>{read}</readable>\n    </fieldPermissions>\n")


def op(read, extra=""):
    return (f"    <objectPermissions>\n        <allowCreate>{read}</allowCreate>\n        <allowDelete>false</allowDelete>\n"
            f"        <allowEdit>{read}</allowEdit>\n        <allowRead>{read}</allowRead>\n"
            f"        <modifyAllRecords>false</modifyAllRecords>\n        <object>Thing__c</object>\n{extra}"
            "    </objectPermissions>\n")


def ca(on):
    return f"    <classAccesses>\n        <apexClass>Greeting</apexClass>\n        <enabled>{on}</enabled>\n    </classAccesses>\n"


def tab(vis, tag="tabSettings"):
    return f"    <{tag}>\n        <tab>Thing__c</tab>\n        <visibility>{vis}</visibility>\n    </{tag}>\n"


def rtv(on):
    return (f"    <recordTypeVisibilities>\n        <recordType>Thing__c.Special</recordType>\n"
            f"        <visible>{on}</visible>\n    </recordTypeVisibilities>\n")


def cp(on):
    return f"    <customPermissions>\n        <enabled>{on}</enabled>\n        <name>Approve_Refunds</name>\n    </customPermissions>\n"


def up(on, name="ViewSetup"):
    return f"    <userPermissions>\n        <enabled>{on}</enabled>\n        <name>{name}</name>\n    </userPermissions>\n"


def pset(*entries, label="Thing Access"):
    return x("PermissionSet", "    <description>Made-up access.</description>\n"
                              f"    <label>{label}</label>\n" + "".join(entries))


def profile(*entries):
    return x("Profile", "    <custom>true</custom>\n" + "".join(entries))


# -- Apex ----------------------------------------------------------------------------------------------------------
CLS = ("public with sharing class Greeting {\n    public static String text() {\n        // the greeting\n"
       "        return 'Hello world';\n    }\n}\n")

# (id, type, submitted file, org copy, status check deployed must give, text one of the differences must contain)
CASES = [
    # flows: replace semantics everywhere
    ("flow-unchanged", "Flow", flow(), flow(), "same", None),
    ("flow-outcome-removed-in-source-org-keeps-it", "Flow", flow(("Approve",)), flow(("Old_Outcome", "Approve")),
     "different", "decisions Route.rules Old_Outcome: only in org"),
    ("flow-element-only-in-org", "Flow", flow(), flow(extra=update("Reject", "Rejected")), "different",
     "recordUpdates Reject: only in org"),
    ("flow-org-has-a-property-the-source-has-not", "Flow", flow(),
     flow(extra="    <runInMode>SystemModeWithoutSharing</runInMode>\n"), "different", "runInMode: only in org"),
    ("flow-org-default-leaf", "Flow", flow(),
     flow(extra="    <areMetricsLoggedToDataCloud>false</areMetricsLoggedToDataCloud>\n"), "same", None),
    ("flow-literal-changed", "Flow", flow(value="Approved"), flow(value="Accepted"), "different",
     "source 'Approved', org 'Accepted'"),
    ("flow-space-inside-a-string", "Flow", flow(value="Approved by flow"), flow(value="Approved  by flow"),
     "different", "stringValue"),
    ("flow-outcomes-reordered", "Flow", flow(("Approve", "Reject")), flow(("Reject", "Approve")), "different",
     "order differs"),
    ("flow-top-level-reordered", "Flow", flow(), flow(start_first=True), "same", None),
    ("flow-outcome-only-in-source", "Flow", flow(("Approve", "Reject")), flow(("Approve",)), "different",
     "rules Reject: only in source"),
    # documented limit: leading and trailing whitespace of an XML value is not compared
    ("flow-edge-whitespace-of-a-value-limit", "Flow", flow(value="Approved"), flow(value=" Approved "), "same", None),
    # a custom object: known server additions, otherwise visible
    ("object-new-with-the-server-defaults-seen-live", "CustomObject", obj(), obj(extra=SERVER_DEFAULTS), "same", None),
    ("object-unknown-property-in-org", "CustomObject", obj(),
     obj(extra=SERVER_DEFAULTS + "    <enableEnhancedLookup>true</enableEnhancedLookup>\n"),
     "unexplained-additions", None),
    ("object-known-switch-with-a-value-not-seen", "CustomObject", obj(),
     obj(extra="    <enableSearch>true</enableSearch>\n"), "unexplained-additions", None),
    ("object-custom-action-override-in-org", "CustomObject", obj(),
     obj(extra="    <actionOverrides>\n        <actionName>View</actionName>\n        <content>Thing_Page</content>\n"
               "        <type>Flexipage</type>\n    </actionOverrides>\n"), "unexplained-additions", None),
    ("object-property-removed-in-source-org-keeps-it", "CustomObject", obj(),
     obj(extra="    <enableHistory>true</enableHistory>\n"), "unexplained-additions", None),
    ("object-literal-changed", "CustomObject", obj(), obj(OBJ.replace("<label>Thing</label>", "<label>Item</label>")),
     "different", "label: source 'Thing', org 'Item'"),
    ("object-addition-inside-an-element", "CustomObject", obj(),
     obj(OBJ.replace("<type>Text</type>", "<type>Text</type>\n        <trackHistory>true</trackHistory>")),
     "different", "nameField.trackHistory: only in org"),
    ("object-top-level-reordered", "CustomObject", obj(),
     obj(OBJ[OBJ.index("    <label>"):] + OBJ[:OBJ.index("    <label>")]), "same", None),
    # a picklist field
    ("field-value-removed-in-source-org-keeps-it", "CustomField", field(("Small", "Large")),
     field(("Small", "Large", "Retired")), "different", "value Retired: only in org"),
    ("field-value-added-in-source-missing-in-org", "CustomField", field(("Small", "Large", "Huge")),
     field(("Small", "Large")), "different", "value Huge: only in source"),
    ("field-values-reordered", "CustomField", field(("Small", "Large")), field(("Large", "Small")), "different",
     "order differs"),
    ("field-literal-changed", "CustomField", field(label="Kind"), field(label="Sort"), "different",
     "label: source 'Kind', org 'Sort'"),
    ("field-space-inside-a-string", "CustomField", field(desc="Pick a kind of thing."),
     field(desc="Pick a kind  of thing."), "different", "description"),
    ("field-org-default-leaves", "CustomField", field(),
     field(extra="    <externalId>false</externalId>\n    <trackTrending>false</trackTrending>\n"), "same", None),
    ("field-property-removed-in-source-org-keeps-it", "CustomField", field(),
     field(extra="    <inlineHelpText>Choose one.</inlineHelpText>\n"), "unexplained-additions", None),
    # a validation rule
    ("rule-literal-changed", "ValidationRule", vrule("Amount__c &lt; 0"), vrule("Amount__c &lt; 10"), "different",
     "errorConditionFormula"),
    ("rule-space-inside-the-formula", "ValidationRule", vrule("Amount__c &lt; 0"), vrule("Amount__c  &lt; 0"),
     "different", "errorConditionFormula"),
    ("rule-deactivated-in-org", "ValidationRule", vrule(), vrule(active="false"), "different",
     "active: source 'true', org 'false'"),
    ("rule-property-removed-in-source-org-keeps-it", "ValidationRule", vrule(),
     vrule(extra="    <errorDisplayField>Amount__c</errorDisplayField>\n"), "unexplained-additions", None),
    ("rule-top-level-reordered", "ValidationRule", vrule(), vrule(reorder=True), "same", None),
    # permission sets: merge semantics; a revoke is an expectation
    ("pset-field-revoked-org-keeps-it", "PermissionSet", pset(fp("Thing__c.Kind__c", "false", "false")),
     pset(fp("Thing__c.Kind__c", "true", "false")), "different", "source revokes it; the org still grants readable"),
    ("pset-field-revoked-org-dropped-it", "PermissionSet", pset(fp("Thing__c.Kind__c", "false", "false")), pset(),
     "same", None),
    ("pset-field-revoked-org-all-false", "PermissionSet", pset(fp("Thing__c.Kind__c", "false", "false")),
     pset(fp("Thing__c.Kind__c", "false", "false")), "same", None),
    ("pset-object-revoked-org-keeps-it", "PermissionSet", pset(op("false")), pset(op("true")), "different",
     "objectPermissions Thing__c: source revokes it"),
    ("pset-class-revoked-org-keeps-it", "PermissionSet", pset(ca("false")), pset(ca("true")), "different",
     "classAccesses Greeting: source revokes it"),
    ("pset-tab-revoked-org-keeps-it", "PermissionSet", pset(tab("None")), pset(tab("Visible")), "different",
     "tabSettings Thing__c: source revokes it; the org still grants visibility Visible"),
    ("pset-record-type-revoked-org-keeps-it", "PermissionSet", pset(rtv("false")), pset(rtv("true")), "different",
     "recordTypeVisibilities Thing__c.Special: source revokes it"),
    ("pset-custom-permission-revoked-org-keeps-it", "PermissionSet", pset(cp("false")), pset(cp("true")),
     "different", "customPermissions Approve_Refunds: source revokes it"),
    ("pset-user-permission-revoked-org-keeps-it", "PermissionSet", pset(up("false")), pset(up("true")), "different",
     "userPermissions ViewSetup: source revokes it"),
    ("pset-user-permission-revoked-org-dropped-it", "PermissionSet", pset(up("false")), pset(), "same", None),
    ("pset-org-has-entries-the-file-does-not-name", "PermissionSet", pset(fp("Thing__c.Kind__c", "true", "false")),
     pset(fp("Thing__c.Kind__c", "true", "false"), fp("Thing__c.Other__c", "true", "true"), up("true", "ApiEnabled")),
     "same", None),
    ("pset-grant-lowered-in-org", "PermissionSet", pset(fp("Thing__c.Kind__c", "true", "true")),
     pset(fp("Thing__c.Kind__c", "true", "false")), "different", "editable: source 'true', org 'false'"),
    ("pset-org-grants-more-inside-an-entry", "PermissionSet", pset(op("true")),
     pset(op("true", "        <viewAllRecords>true</viewAllRecords>\n")), "different", "viewAllRecords: only in org"),
    ("pset-grant-only-in-source", "PermissionSet", pset(fp("Thing__c.Kind__c", "true", "false")), pset(),
     "different", "fieldPermissions Thing__c.Kind__c: only in source"),
    ("pset-entries-reordered", "PermissionSet", pset(ca("true"), op("true")), pset(op("true"), ca("true")), "same",
     None),
    ("pset-label-changed", "PermissionSet", pset(label="Thing Access"), pset(label="Thing Access 2"), "different",
     "label"),
    # profiles: the same merge semantics
    ("profile-field-revoked-org-keeps-it", "Profile", profile(fp("Thing__c.Kind__c", "false", "false")),
     profile(fp("Thing__c.Kind__c", "true", "true")), "different", "source revokes it; the org still grants"),
    ("profile-tab-hidden-org-shows-it", "Profile", profile(tab("Hidden", "tabVisibilities")),
     profile(tab("DefaultOn", "tabVisibilities")), "different", "tabVisibilities Thing__c: source revokes it"),
    ("profile-org-has-entries-the-file-does-not-name", "Profile", profile(ca("true")),
     profile(ca("true"), up("true", "ApiEnabled")), "same", None),
    # Apex: exact after line endings and trailing whitespace
    ("class-space-inside-a-string", "ApexClass", CLS, CLS.replace("'Hello world'", "'Helloworld'"), "different",
     "code differs from line 4"),
    ("class-line-comment-newline-removed", "ApexClass", CLS,
     CLS.replace("// the greeting\n        return", "// the greeting return"), "different", "code differs"),
    ("class-indentation-changed", "ApexClass", CLS, CLS.replace("        return", "    return"), "different",
     "code differs"),
    ("class-line-endings-and-trailing-whitespace", "ApexClass", CLS, CLS.replace("\n", "   \r\n") + "\n\n", "same",
     None),
    ("class-literal-changed", "ApexClass", CLS, CLS.replace("Hello world", "Hello there"), "different",
     "code differs"),
    ("class-method-only-in-org", "ApexClass", CLS,
     CLS.replace("}\n}\n", "}\n    public static void old() {}\n}\n"), "different", "code differs"),
]
# Rows whose two files the baseline fingerprint must call unchanged; every other row is a change.
BASELINE_UNCHANGED = {"flow-unchanged", "flow-edge-whitespace-of-a-value-limit",
                      "class-line-endings-and-trailing-whitespace", "pset-field-revoked-org-all-false"}


def component(tmp_path, side: str, typ: str, text: str):
    path = tmp_path / side / DEFAULT / PATHS[typ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return components.read_file(str(path))[0]


def status_of(outcome):
    return outcome.status if hasattr(outcome, "status") else outcome[0]


def differences_of(outcome):
    return outcome.differences if hasattr(outcome, "differences") else outcome[1]


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_check_deployed_comparison(tmp_path, case):
    name, typ, source, org, expected, text = case
    a, b = component(tmp_path, "source", typ, source), component(tmp_path, "org", typ, org)
    assert a.error is None and b.error is None
    outcome = compare.compare(a, b, RETRIEVED)
    assert status_of(outcome) == expected, differences_of(outcome)
    if text:
        assert any(text in d for d in differences_of(outcome)), differences_of(outcome)
    if expected != "different":
        assert not differences_of(outcome)


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_baseline_scope(tmp_path, case):
    """The same pairs through `torque check metadata --baseline`: the org copy as the baseline, the file as the
    change. A pair the read-back calls anything but "same" must never be "unchanged" here."""
    name, typ, source, org, expected, _text = case
    component(tmp_path, "now", typ, source)
    component(tmp_path, "before", typ, org)
    sc = scope.with_baseline([str(tmp_path / "now")], str(tmp_path / "before"))
    [c] = sc.everything
    assert c.status == ("unchanged" if name in BASELINE_UNCHANGED else "changed")
    if expected != "same":
        assert c.status == "changed"


def test_the_corpus_covers_each_mutation_for_each_type():
    names = [c[0] for c in CASES]
    assert len(names) == len(set(names))
    for typ in ("Flow", "CustomObject", "CustomField", "ValidationRule", "PermissionSet", "ApexClass"):
        statuses = {c[4] for c in CASES if c[1] == typ}
        assert "different" in statuses and "same" in statuses, typ


def test_known_server_additions_are_reported_as_such(tmp_path):
    a = component(tmp_path, "source", "CustomObject", obj())
    b = component(tmp_path, "org", "CustomObject", obj(extra=SERVER_DEFAULTS))
    o = compare.compare(a, b, RETRIEVED)
    assert (o.status, o.unexplained) == ("same", [])
    # 30 action overrides; compactLayoutAssignment, enableBulkApi, enableSharing, enableStreamingApi,
    # externalSharingModel and visibility; the false switches and the empty searchLayouts are default leaves
    assert len(o.known_additions) == 30 + 6
    p = compare.compare(component(tmp_path, "s2", "PermissionSet", pset(ca("true"))),
                        component(tmp_path, "o2", "PermissionSet", pset(ca("true"), up("true", "ApiEnabled"))),
                        RETRIEVED)
    assert (p.status, p.other_entries) == ("same", 1)
    u = compare.compare(a, component(tmp_path, "o3", "CustomObject",
                                     obj(extra="    <enableEnhancedLookup>true</enableEnhancedLookup>\n")), RETRIEVED)
    assert (u.status, u.unexplained, u.differences) == ("unexplained-additions", ["enableEnhancedLookup"], [])


def test_a_revoke_of_a_component_not_retrieved_is_counted_not_compared(tmp_path):
    a = component(tmp_path, "source", "PermissionSet", pset(fp("Account.Region__c", "false", "false")))
    b = component(tmp_path, "org", "PermissionSet", pset())
    o = compare.compare(a, b, RETRIEVED)
    assert (o.status, o.not_compared) == ("same", 1)
