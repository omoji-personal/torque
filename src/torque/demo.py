"""A synthetic consulting journey that needs only the installed local package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from . import workspace as ws

CLIENT = "synthetic-community-center"
OBJECT = "Demo_Service_Request__c"
FIELD = "Preferred_Contact_Method__c"
PERMSET = "Demo_Service_Request_Access"
_XML = '<?xml version="1.0" encoding="UTF-8"?>\n'
_NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'

# Every example is authored for this demo; no customer fixtures or org identifiers.
_FILES = {
    "context/discovery.md": """# Discovery — synthetic example

A fictional community center records service requests. Staff currently put the
requester's preferred contact method in free-text notes, so colleagues miss it.
The proposed change makes that preference visible as a structured field.

This scenario and every stakeholder statement below are invented for learning.
The sample coordinator asks for Phone and Email choices. The sample consultant
asks whether the field should be mandatory and who may edit it. Those decisions
remain open; the starter leaves the field optional and proposes a narrow
permission set. No real client has approved this design.

Next: read requirements.md, then solution.md.
""",
    "context/requirements.md": """# Requirement R1 — synthetic example

Make a requester's preferred contact method easy for service staff to record
consistently and find again. This is a sample requirement, not client evidence.

| Criterion | Expected behavior | Current evidence |
| --- | --- | --- |
| AC1 | A staff user can select Phone or Email on a service request. | NOT_RUN: no org or browser selected. |
| AC2 | The value persists after save and reopening the same request. | NOT_RUN: no record has been created. |
| AC3 | Only the intended staff permission assignment grants the proposed edit access. | NOT_RUN: no users or permission assignments tested. |

Open decisions: required or optional; intended staff role; placement on the real
record page; treatment of existing free-text notes; wording of staff guidance.
""",
    "context/solution.md": """# Proposed change — synthetic example

The starter adds Preferred_Contact_Method__c, an optional restricted picklist
with Phone and Email, to a new Demo_Service_Request__c object. The narrow
Demo_Service_Request_Access permission set proposes object read/create/edit and
field read/edit. The object is new so the example does not assume a customer's
existing schema. Adapt names and design after real discovery.

Prepared files are in ../project/. Their XML can be parsed locally; Salesforce
has not validated or deployed them. No Lightning page/layout or user assignment
is included, so the starter alone does not demonstrate AC1 or AC3.

Review the proposed source against R1, resolve the open decisions, and work
through ../artifacts/qa-plan.md. Keep actual deployment IDs and observations in
a separate real engagement's evidence; never relabel this sample as live proof.
""",
    "artifacts/qa-plan.md": """# QA plan — synthetic example, live checks NOT_RUN

| Step | Evidence to collect during a later real test | Current result |
| --- | --- | --- |
| Validate the exact proposed components in the chosen development org. | Exact validation request ID, component list and terminal report. | NOT_RUN |
| Deploy and independently observe the field. | Separate deployment ID and actual field metadata. | NOT_RUN |
| Test AC1 and AC2 as the intended staff user. | User/context, selected value, saved record ID and reopened value. | NOT_RUN |
| Test AC3 with the agreed permission model. | Actual assignment, effective access and a negative access case. | NOT_RUN |
| Restore or remove only the test's own artifacts. | Before-state/recovery evidence and exact cleanup observations. | NOT_RUN |

Offline review: compare requirements.md with the field and permission-set XML,
then inspect sample-requests.json and evidence.json. The JSON examples demonstrate
allowed values only; they are not query results, UAT, screenshots or saved records.

When exploring the real workflow recipes, read `torque workflows show
validate-change` and `torque workflows show qa`. Reading a recipe runs nothing.
Selecting an actual client/org and performing live work is a separate step.
""",
    "artifacts/sample-requests.json": json.dumps({
        "synthetic": True, "origin": "authored offline example; not Salesforce records",
        "requests": [{"example_id": "SYN-001", FIELD: "Phone"},
                     {"example_id": "SYN-002", FIELD: "Email"}],
    }, indent=2) + "\n",
    "project/sfdx-project.json": json.dumps({
        "packageDirectories": [{"path": "force-app", "default": True}],
        "namespace": "", "sourceApiVersion": "65.0",
    }, indent=2) + "\n",
    f"project/force-app/main/default/objects/{OBJECT}/{OBJECT}.object-meta.xml": _XML + f"""<CustomObject {_NS}>
    <deploymentStatus>Deployed</deploymentStatus>
    <description>Synthetic Torque demo source; not deployed by demo creation.</description>
    <label>Demo Service Request</label>
    <nameField><label>Request Name</label><type>Text</type></nameField>
    <pluralLabel>Demo Service Requests</pluralLabel>
    <sharingModel>Private</sharingModel>
</CustomObject>
""",
    f"project/force-app/main/default/objects/{OBJECT}/fields/{FIELD}.field-meta.xml": _XML + f"""<CustomField {_NS}>
    <fullName>{FIELD}</fullName>
    <description>Synthetic example for requirement R1; no live verification.</description>
    <label>Preferred Contact Method</label>
    <required>false</required>
    <type>Picklist</type>
    <valueSet><restricted>true</restricted><valueSetDefinition>
        <sorted>false</sorted>
        <value><fullName>Phone</fullName><default>false</default><label>Phone</label></value>
        <value><fullName>Email</fullName><default>false</default><label>Email</label></value>
    </valueSetDefinition></valueSet>
</CustomField>
""",
    f"project/force-app/main/default/permissionsets/{PERMSET}.permissionset-meta.xml": _XML + f"""<PermissionSet {_NS}>
    <description>Synthetic proposed access; no user assignment is created.</description>
    <fieldPermissions><editable>true</editable><field>{OBJECT}.{FIELD}</field><readable>true</readable></fieldPermissions>
    <label>Demo Service Request Access</label>
    <objectPermissions><allowCreate>true</allowCreate><allowDelete>false</allowDelete>
        <allowEdit>true</allowEdit><allowRead>true</allowRead><modifyAllRecords>false</modifyAllRecords>
        <object>{OBJECT}</object><viewAllRecords>false</viewAllRecords></objectPermissions>
</PermissionSet>
""",
    "project/manifest/package.xml": _XML + f"""<Package {_NS}>
    <types><members>{OBJECT}</members><name>CustomObject</name></types>
    <types><members>{OBJECT}.{FIELD}</members><name>CustomField</name></types>
    <types><members>{PERMSET}</members><name>PermissionSet</name></types>
    <version>65.0</version>
</Package>
""",
}


def _evidence(client: Path) -> dict:
    """Actually inspect the generated local files, without evaluating live acceptance."""
    project = client / "project"
    xml_paths = sorted(project.rglob("*.xml"))
    for path in xml_paths:
        ET.parse(path)
    ET_ns = {"m": "http://soap.sforce.com/2006/04/metadata"}
    field_path = project / f"force-app/main/default/objects/{OBJECT}/fields/{FIELD}.field-meta.xml"
    allowed = {item.text for item in ET.parse(field_path).findall(".//m:value/m:fullName", ET_ns)}
    sample = json.loads((client / "artifacts/sample-requests.json").read_text())
    if not all(row[FIELD] in allowed for row in sample["requests"]):
        raise ws.WorkspaceError("synthetic demo values do not match the prepared picklist")
    return {
        "schema": "torque.demo-evidence/1", "synthetic": True, "scope": "local files only",
        "observations": [
            {"status": "OBSERVED_LOCAL", "claim": "Generated XML parses locally; this is not Salesforce schema validation.", "xml_file_count": len(xml_paths)},
            {"status": "OBSERVED_LOCAL", "claim": "Synthetic values occur in the prepared picklist.", "sample_count": len(sample["requests"])},
        ],
        "acceptance": [{"criterion": item, "status": "NOT_RUN", "basis": "No selected org, user, browser or live record."} for item in ("AC1", "AC2", "AC3")],
        "deployment": {"status": "NOT_RUN", "job_id": None},
        "files": [{"path": str(path.relative_to(client)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in xml_paths],
    }


def create_demo(destination: Path) -> dict:
    """Create a NEW private demo directory; roll back our new directory on failure.

    Existing files/directories (including empty ones and symlinks) are refused.
    The parent must exist. No environment variables, auth or global settings change.
    """
    target = Path(destination).expanduser()
    if target.exists() or target.is_symlink():
        raise ws.WorkspaceError(f"demo destination already exists; choose a new directory: {target}")
    root = target.resolve()
    if not root.parent.is_dir():
        raise ws.WorkspaceError("demo destination parent must already exist")
    source = ws._source_checkout()
    if ((source and (root == source.resolve() or source.resolve() in root.parents))
            or ws._checkout_containing(root)):
        raise ws.WorkspaceError("choose a private demo directory outside the Torque source checkout")
    root.mkdir(mode=0o700)  # Exclusive reservation: never merge into another directory.
    try:
        ws.init_workspace(root, "Synthetic Consulting Demo", "generic")
        client = ws.add_client(root, "Synthetic Community Center", org=None)
        for name, contents in _FILES.items():
            path = client / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            ws.atomic_write_new(path, contents)
        evidence = client / "artifacts/evidence.json"
        ws.atomic_write_new(evidence, json.dumps(_evidence(client), indent=2) + "\n")
        ws.add_session(root, CLIENT, "Synthetic offline demo prepared: discovery, R1/AC1–AC3, proposed metadata, sample values and a QA plan. Only local XML/value checks ran.", "prepared", evidence)
        ws.add_session(root, CLIENT, "Synthetic demo next step: resolve the sample design questions. Deployment, intended-user access, saved-record behavior and live cleanup remain NOT_RUN; no org is configured.", "incomplete")
        from .changes import create_change, add_note
        change = create_change(root, CLIENT, "Record preferred contact method",
                               "Service staff can reliably record and find a requester's contact preference.",
                               ["A staff user can select Phone or Email on a service request.",
                                "The selected value persists after saving and reopening the request.",
                                "Only the intended permission assignment grants the proposed edit access."])
        add_note(root, CLIENT, change["id"], "Synthetic proposed design: optional restricted picklist, with a narrow staff permission set. Intended staff role is unresolved.", "decision")
        add_note(root, CLIENT, change["id"], "Resolve the sample design questions. All live acceptance remains NOT_RUN; no org is configured.", "next_step")
        handoff = client / "artifacts/handoff.md"
        ws.atomic_write_new(handoff, ws.render_handoff(root, CLIENT))
        start = root / "START-HERE.md"
        ws.atomic_write_new(start, f"""# Torque offline consulting demo

Everything here is synthetic. No Salesforce account, authentication, provider or
paid service was used. You have a prepared consulting example and source files;
no deployment, browser action or live verification has happened.

1. Read `clients/{CLIENT}/context/discovery.md` and `requirements.md`.
2. Review `context/solution.md` and the Salesforce starter in `project/`.
3. Compare `artifacts/sample-requests.json` with `artifacts/qa-plan.md` and
   `artifacts/evidence.json`. Local observations and unrun acceptance checks differ.
4. Read `artifacts/handoff.md`, then record your own next decision in the journal.

Run these local commands from this directory:

```sh
torque context --workspace . --client {CLIENT}
torque workflows show discovery
torque workflows show prep-changeset
torque workflows show qa
torque session list --workspace . --client {CLIENT}
torque change show {change['id']} --workspace . --client {CLIENT}
torque handoff --workspace . --client {CLIENT}
```

For a real engagement, create a separate client and select its org explicitly.
Adapt the starter API version and schema for that org. The starter does not include
a record page, user assignments or validated Salesforce metadata. This private
directory ignores all files in Git; keep real client material out of public source.
""")
        return {"schema": "torque.demo/1", "synthetic": True, "status": "prepared",
                "org_calls": False, "workspace": str(root), "client": CLIENT,
                "client_root": str(client), "start_here": str(start),
                "project": str(client / "project"), "evidence": str(evidence), "handoff": str(handoff),
                "change_id": change["id"]}
    except BaseException:
        shutil.rmtree(root)
        raise
