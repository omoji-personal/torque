"""Made-up Salesforce source trees for the `torque check` tests. Every name is invented."""
from __future__ import annotations

import json
from pathlib import Path

from torque.checks import metadata

NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'
USER_ID = "005" + "0" * 12 + "1AB"          # a made-up 18 character User Id
USER_ID_15 = "005" + "0" * 9 + "1AB"         # the same shape in 15 characters
DEFAULT = "force-app/main/default"


def xml(root: str, body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{root} {NS}>\n{body}\n</{root}>\n'


def tag(name: str, value) -> str:
    return "" if value is None else f"    <{name}>{value}</{name}>\n"


class Tree:
    def __init__(self, root: Path, api: str = "65.0", namespace: str = ""):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sfdx-project.json").write_text(json.dumps(
            {"packageDirectories": [{"path": "force-app", "default": True}], "namespace": namespace,
             "sourceApiVersion": api}), encoding="utf-8")

    def add(self, rel: str, text: str, encoding: str = "utf-8") -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
        return path

    def obj(self, name, desc="Made-up object for tests.", extra=""):
        body = (tag("deploymentStatus", "Deployed") + tag("description", desc) + tag("label", name.replace("__c", ""))
                + tag("pluralLabel", name.replace("__c", "") + "s") + tag("sharingModel", "ReadWrite") + extra)
        return self.add(f"{DEFAULT}/objects/{name}/{name}.object-meta.xml", xml("CustomObject", body))

    def field(self, obj, name, type="Text", desc="Why it exists and what writes it.", help="What to enter.",
              required=None, formula=None, extra=""):
        body = (tag("fullName", name) + tag("description", desc) + tag("formula", formula)
                + tag("inlineHelpText", help) + tag("label", name.replace("__c", "").replace("_", " "))
                + tag("required", required) + tag("type", type) + extra)
        return self.add(f"{DEFAULT}/objects/{obj}/fields/{name}.field-meta.xml", xml("CustomField", body))

    def permset(self, name, fields=(), desc="Who gets it and what it does.", extra="", editable=False):
        grants = "".join(f"    <fieldPermissions>\n        <editable>{str(editable).lower()}</editable>\n"
                         f"        <field>{f}</field>\n        <readable>true</readable>\n    </fieldPermissions>\n"
                         for f in fields)
        body = grants + tag("description", desc) + tag("hasActivationRequired", "false") + tag("label", name) + extra
        return self.add(f"{DEFAULT}/permissionsets/{name}.permissionset-meta.xml", xml("PermissionSet", body))

    def profile(self, name, fields=()):
        grants = "".join(f"    <fieldPermissions>\n        <editable>false</editable>\n        <field>{f}</field>\n"
                         f"        <readable>true</readable>\n    </fieldPermissions>\n" for f in fields)
        return self.add(f"{DEFAULT}/profiles/{name}.profile-meta.xml", xml("Profile", grants + tag("custom", "true")))

    def rule(self, obj, name, formula="ISBLANK(Name)", msg="Fill in the name.", desc="Why the rule exists."):
        body = (tag("fullName", name) + tag("active", "true") + tag("description", desc)
                + tag("errorConditionFormula", formula) + tag("errorMessage", msg))
        return self.add(f"{DEFAULT}/objects/{obj}/validationRules/{name}.validationRule-meta.xml",
                        xml("ValidationRule", body))

    def flow(self, name, body, api="65.0", status="Active", desc="What starts it, what it changes and why.",
             process="AutoLaunchedFlow", extra=""):
        text = (tag("apiVersion", api) + tag("description", desc) + tag("label", name.replace("_", " "))
                + tag("processType", process) + body + tag("status", status) + extra)
        return self.add(f"{DEFAULT}/flows/{name}.flow-meta.xml", xml("Flow", text))

    def apex(self, name, src):
        self.add(f"{DEFAULT}/classes/{name}.cls-meta.xml",
                 xml("ApexClass", tag("apiVersion", "65.0") + tag("status", "Active")))
        return self.add(f"{DEFAULT}/classes/{name}.cls", src)

    def check(self, *paths, **kw):
        kw.setdefault("analyzer_mode", "off")
        kw.setdefault("lessons", False)
        return metadata.check([str(self.root / p) for p in paths] or [str(self.root)], **kw)


def start(obj="Opportunity", trigger="RecordAfterSave", when="CreateAndUpdate", filters=True, target="Do_It",
          extra=""):
    f = ("        <filterLogic>and</filterLogic>\n        <filters>\n            <field>StageName</field>\n"
         "            <operator>EqualTo</operator>\n            <value><stringValue>Closed Won</stringValue></value>\n"
         "        </filters>\n") if filters else ""
    return (f"    <start>\n        <locationX>50</locationX>\n        <locationY>0</locationY>\n"
            f"        <connector><targetReference>{target}</targetReference></connector>\n{f}"
            f"        <object>{obj}</object>\n        <recordTriggerType>{when}</recordTriggerType>\n"
            f"        <triggerType>{trigger}</triggerType>\n{extra}    </start>\n")


def element(kind, name, body="", desc="What this element does.", fault=None, connector=None):
    return (f"    <{kind}>\n        <name>{name}</name>\n" + (f"        <description>{desc}</description>\n" if desc else "")
            + f"        <label>{name.replace('_', ' ')}</label>\n        <locationX>0</locationX>\n"
            f"        <locationY>0</locationY>\n" + body
            + (f"        <connector><targetReference>{connector}</targetReference></connector>\n" if connector else "")
            + (f"        <faultConnector><targetReference>{fault}</targetReference></faultConnector>\n" if fault else "")
            + f"    </{kind}>\n")


def create_task(name="Do_It", owner=None, fault=None, desc="Creates the follow-up task.", connector=None):
    owner_value = (f"        <inputAssignments><field>OwnerId</field><value><stringValue>{owner}</stringValue></value>"
                   "</inputAssignments>\n") if owner else ""
    return element("recordCreates", name, owner_value + "        <object>Task</object>\n", desc=desc, fault=fault)


def e10_project(root: Path) -> Tree:
    """The eleven planted defects of the walkthrough, rebuilt with made-up names.
    D1 field without description or help text; D2 permission set grants the object and another field but not that
    one; D3 Create Records without fault path; D4 hard-coded User Id as owner; D5 flow and element without
    description; D6 apiVersion 49.0 in a 65.0 project; D7 threshold as a literal; D8 runs on every update of a
    qualifying record; D9 Active in source; D10 validation rule and permission set without description; D11 two
    acceptance criteria with no component at all."""
    t = Tree(root, api="65.0")
    t.obj("Shift__c")
    t.field("Shift__c", "Hours__c", type="Number", desc=None, help=None,
            extra="    <precision>4</precision>\n    <scale>2</scale>\n")                       # D1
    t.field("Shift__c", "Stage__c", type="Picklist")
    t.permset("Shift_Coordinator", fields=["Shift__c.Stage__c"], desc=None,                     # D2, D10
              extra="    <objectPermissions>\n        <allowCreate>true</allowCreate>\n        <allowDelete>false"
                    "</allowDelete>\n        <allowEdit>true</allowEdit>\n        <allowRead>true</allowRead>\n"
                    "        <modifyAllRecords>false</modifyAllRecords>\n        <object>Shift__c</object>\n"
                    "        <viewAllRecords>false</viewAllRecords>\n    </objectPermissions>\n")
    t.rule("Shift__c", "Hours_In_Range", formula="OR(Hours__c &lt;= 0, Hours__c &gt; 24)",
           msg="Hours must be more than 0 and no more than 24.", desc=None)                    # D10
    amount = ("        <filters>\n            <field>Amount</field>\n            <operator>GreaterThanOrEqualTo"
              "</operator>\n            <value><numberValue>1000.0</numberValue></value>\n        </filters>\n")  # D7
    body = (create_task("Create_Thank_You_Task", owner=USER_ID, desc=None)                     # D3, D4, D5
            + start(target="Create_Thank_You_Task", extra=amount))                             # D8
    t.flow("Gift_Thank_You_Task", body, api="49.0", status="Active", desc=None)                # D5, D6, D9
    return t


# The live pair
# The live pair: the made-up source deployed to a Developer Edition practice org on 2026-11-14 (a custom object
# with two fields and a validation rule, a permission set, a flow), and what that org returned for the same
# three members, in metadata format, byte for byte. Every name is invented; no record data. It must read "same".
LIVE_PAIR = {
    'source/force-app/main/default/flows/Torque_Check_Set_Note.flow-meta.xml': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Flow xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>62.0</apiVersion>\n'
        '    <assignments>\n'
        '        <name>Set_Note</name>\n'
        '        <label>Set Note</label>\n'
        '        <locationX>176</locationX>\n'
        '        <locationY>287</locationY>\n'
        '        <assignmentItems>\n'
        '            <assignToReference>$Record.Note__c</assignToReference>\n'
        '            <operator>Assign</operator>\n'
        '            <value>\n'
        '                <stringValue>Set by flow</stringValue>\n'
        '            </value>\n'
        '        </assignmentItems>\n'
        '    </assignments>\n'
        '    <description>Made-up flow for a live run of the Torque checks: writes Note before save when a record is created.</description>\n'
        '    <environments>Default</environments>\n'
        '    <interviewLabel>Torque Check Set Note {!$Flow.CurrentDateTime}</interviewLabel>\n'
        '    <label>Torque Check Set Note</label>\n'
        '    <processType>AutoLaunchedFlow</processType>\n'
        '    <start>\n'
        '        <locationX>50</locationX>\n'
        '        <locationY>0</locationY>\n'
        '        <connector>\n'
        '            <targetReference>Set_Note</targetReference>\n'
        '        </connector>\n'
        '        <object>Torque_Check__c</object>\n'
        '        <recordTriggerType>Create</recordTriggerType>\n'
        '        <triggerType>RecordBeforeSave</triggerType>\n'
        '    </start>\n'
        '    <status>Active</status>\n'
        '</Flow>\n'
    ),
    'source/force-app/main/default/objects/Torque_Check__c/fields/Amount__c.field-meta.xml': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <fullName>Amount__c</fullName>\n'
        '    <label>Amount</label>\n'
        '    <precision>10</precision>\n'
        '    <required>false</required>\n'
        '    <scale>2</scale>\n'
        '    <type>Number</type>\n'
        '</CustomField>\n'
    ),
    'source/force-app/main/default/objects/Torque_Check__c/fields/Note__c.field-meta.xml': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <fullName>Note__c</fullName>\n'
        '    <description>Made-up text field; the flow writes it before save. Granted in the permission set.</description>\n'
        '    <inlineHelpText>Leave blank; automation fills this in.</inlineHelpText>\n'
        '    <label>Note</label>\n'
        '    <length>80</length>\n'
        '    <required>false</required>\n'
        '    <type>Text</type>\n'
        '</CustomField>\n'
    ),
    'source/force-app/main/default/objects/Torque_Check__c/Torque_Check__c.object-meta.xml': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <deploymentStatus>Deployed</deploymentStatus>\n'
        '    <description>Made-up object for a live run of the Torque checks. Safe to delete.</description>\n'
        '    <label>Torque Check</label>\n'
        '    <nameField>\n'
        '        <label>Torque Check Name</label>\n'
        '        <type>Text</type>\n'
        '    </nameField>\n'
        '    <pluralLabel>Torque Checks</pluralLabel>\n'
        '    <sharingModel>ReadWrite</sharingModel>\n'
        '</CustomObject>\n'
    ),
    'source/force-app/main/default/objects/Torque_Check__c/validationRules/Amount_Not_Negative.validationRule-meta.xml': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ValidationRule xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <fullName>Amount_Not_Negative</fullName>\n'
        '    <active>true</active>\n'
        '    <description>Made-up rule: the amount cannot be below zero.</description>\n'
        '    <errorConditionFormula>Amount__c &lt; 0</errorConditionFormula>\n'
        '    <errorDisplayField>Amount__c</errorDisplayField>\n'
        '    <errorMessage>Enter an amount of zero or more.</errorMessage>\n'
        '</ValidationRule>\n'
    ),
    'source/force-app/main/default/permissionsets/Torque_Check_Access.permissionset-meta.xml': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <description>Made-up permission set for a live run of the Torque checks. Grants Note only, on purpose.</description>\n'
        '    <fieldPermissions>\n'
        '        <editable>true</editable>\n'
        '        <field>Torque_Check__c.Note__c</field>\n'
        '        <readable>true</readable>\n'
        '    </fieldPermissions>\n'
        '    <hasActivationRequired>false</hasActivationRequired>\n'
        '    <label>Torque Check Access</label>\n'
        '    <objectPermissions>\n'
        '        <allowCreate>true</allowCreate>\n'
        '        <allowDelete>true</allowDelete>\n'
        '        <allowEdit>true</allowEdit>\n'
        '        <allowRead>true</allowRead>\n'
        '        <modifyAllRecords>false</modifyAllRecords>\n'
        '        <object>Torque_Check__c</object>\n'
        '        <viewAllRecords>false</viewAllRecords>\n'
        '    </objectPermissions>\n'
        '</PermissionSet>\n'
    ),
    'org/unpackaged/flows/Torque_Check_Set_Note.flow': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Flow xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <apiVersion>62.0</apiVersion>\n'
        '    <assignments>\n'
        '        <name>Set_Note</name>\n'
        '        <label>Set Note</label>\n'
        '        <locationX>176</locationX>\n'
        '        <locationY>287</locationY>\n'
        '        <assignmentItems>\n'
        '            <assignToReference>$Record.Note__c</assignToReference>\n'
        '            <operator>Assign</operator>\n'
        '            <value>\n'
        '                <stringValue>Set by flow</stringValue>\n'
        '            </value>\n'
        '        </assignmentItems>\n'
        '    </assignments>\n'
        '    <description>Made-up flow for a live run of the Torque checks: writes Note before save when a record is created.</description>\n'
        '    <environments>Default</environments>\n'
        '    <interviewLabel>Torque Check Set Note {!$Flow.CurrentDateTime}</interviewLabel>\n'
        '    <label>Torque Check Set Note</label>\n'
        '    <processType>AutoLaunchedFlow</processType>\n'
        '    <start>\n'
        '        <locationX>50</locationX>\n'
        '        <locationY>0</locationY>\n'
        '        <connector>\n'
        '            <targetReference>Set_Note</targetReference>\n'
        '        </connector>\n'
        '        <object>Torque_Check__c</object>\n'
        '        <recordTriggerType>Create</recordTriggerType>\n'
        '        <triggerType>RecordBeforeSave</triggerType>\n'
        '    </start>\n'
        '    <status>Active</status>\n'
        '</Flow>\n'
    ),
    'org/unpackaged/objects/Torque_Check__c.object': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <actionOverrides>\n'
        '        <actionName>Accept</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Accept</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Accept</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>CancelEdit</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>CancelEdit</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>CancelEdit</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Clone</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Clone</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Clone</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Delete</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Delete</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Delete</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Edit</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Edit</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Edit</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>List</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>List</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>List</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>New</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>New</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>New</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>SaveEdit</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>SaveEdit</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>SaveEdit</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Tab</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Tab</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>Tab</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>View</actionName>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>View</actionName>\n'
        '        <formFactor>Large</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <actionOverrides>\n'
        '        <actionName>View</actionName>\n'
        '        <formFactor>Small</formFactor>\n'
        '        <type>Default</type>\n'
        '    </actionOverrides>\n'
        '    <allowInChatterGroups>false</allowInChatterGroups>\n'
        '    <compactLayoutAssignment>SYSTEM</compactLayoutAssignment>\n'
        '    <deploymentStatus>Deployed</deploymentStatus>\n'
        '    <description>Made-up object for a live run of the Torque checks. Safe to delete.</description>\n'
        '    <enableActivities>false</enableActivities>\n'
        '    <enableBulkApi>true</enableBulkApi>\n'
        '    <enableFeeds>false</enableFeeds>\n'
        '    <enableHistory>false</enableHistory>\n'
        '    <enableLicensing>false</enableLicensing>\n'
        '    <enableReports>false</enableReports>\n'
        '    <enableSearch>false</enableSearch>\n'
        '    <enableSharing>true</enableSharing>\n'
        '    <enableStreamingApi>true</enableStreamingApi>\n'
        '    <externalSharingModel>Private</externalSharingModel>\n'
        '    <fields>\n'
        '        <fullName>Amount__c</fullName>\n'
        '        <externalId>false</externalId>\n'
        '        <label>Amount</label>\n'
        '        <precision>10</precision>\n'
        '        <required>false</required>\n'
        '        <scale>2</scale>\n'
        '        <trackTrending>false</trackTrending>\n'
        '        <type>Number</type>\n'
        '        <unique>false</unique>\n'
        '    </fields>\n'
        '    <fields>\n'
        '        <fullName>Note__c</fullName>\n'
        '        <description>Made-up text field; the flow writes it before save. Granted in the permission set.</description>\n'
        '        <externalId>false</externalId>\n'
        '        <inlineHelpText>Leave blank; automation fills this in.</inlineHelpText>\n'
        '        <label>Note</label>\n'
        '        <length>80</length>\n'
        '        <required>false</required>\n'
        '        <trackTrending>false</trackTrending>\n'
        '        <type>Text</type>\n'
        '        <unique>false</unique>\n'
        '    </fields>\n'
        '    <label>Torque Check</label>\n'
        '    <nameField>\n'
        '        <label>Torque Check Name</label>\n'
        '        <type>Text</type>\n'
        '    </nameField>\n'
        '    <pluralLabel>Torque Checks</pluralLabel>\n'
        '    <searchLayouts/>\n'
        '    <sharingModel>ReadWrite</sharingModel>\n'
        '    <validationRules>\n'
        '        <fullName>Amount_Not_Negative</fullName>\n'
        '        <active>true</active>\n'
        '        <description>Made-up rule: the amount cannot be below zero.</description>\n'
        '        <errorConditionFormula>Amount__c &lt; 0</errorConditionFormula>\n'
        '        <errorDisplayField>Amount__c</errorDisplayField>\n'
        '        <errorMessage>Enter an amount of zero or more.</errorMessage>\n'
        '    </validationRules>\n'
        '    <visibility>Public</visibility>\n'
        '</CustomObject>\n'
    ),
    'org/unpackaged/package.xml': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <types>\n'
        '        <members>Torque_Check__c</members>\n'
        '        <name>CustomObject</name>\n'
        '    </types>\n'
        '    <types>\n'
        '        <members>Torque_Check_Set_Note</members>\n'
        '        <name>Flow</name>\n'
        '    </types>\n'
        '    <types>\n'
        '        <members>Torque_Check_Access</members>\n'
        '        <name>PermissionSet</name>\n'
        '    </types>\n'
        '    <version>62.0</version>\n'
        '</Package>\n'
    ),
    'org/unpackaged/permissionsets/Torque_Check_Access.permissionset': (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <description>Made-up permission set for a live run of the Torque checks. Grants Note only, on purpose.</description>\n'
        '    <fieldPermissions>\n'
        '        <editable>true</editable>\n'
        '        <field>Torque_Check__c.Note__c</field>\n'
        '        <readable>true</readable>\n'
        '    </fieldPermissions>\n'
        '    <hasActivationRequired>false</hasActivationRequired>\n'
        '    <label>Torque Check Access</label>\n'
        '    <objectPermissions>\n'
        '        <allowCreate>true</allowCreate>\n'
        '        <allowDelete>true</allowDelete>\n'
        '        <allowEdit>true</allowEdit>\n'
        '        <allowRead>true</allowRead>\n'
        '        <modifyAllRecords>false</modifyAllRecords>\n'
        '        <object>Torque_Check__c</object>\n'
        '        <viewAllRecords>false</viewAllRecords>\n'
        '    </objectPermissions>\n'
        '</PermissionSet>\n'
    ),
}


def live_pair(root: Path) -> tuple[Path, Path]:
    """(project folder, retrieve folder) holding the live pair."""
    root = Path(root)
    for rel, body in LIVE_PAIR.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="")
    (root / "source/sfdx-project.json").write_text(json.dumps(
        {"packageDirectories": [{"path": "force-app", "default": True}], "sourceApiVersion": "62.0"}),
        encoding="utf-8")
    return root / "source", root / "org" / "unpackaged"
