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

# Four short scenarios that pair a synthetic inbound request with a walkthrough of
# the matching workflow recipe. Every input.md is invented for this demo; no
# customer fixtures, organization names or client facts appear anywhere below.
_SCENARIOS: dict[str, dict[str, str]] = {
    "alert-triage": {
        "input.md": """SYNTHETIC EXAMPLE. No real organisation or person.

Subject: Flow Fault: Payment: Stamp Processed Date

Org: Synthetic Community Center Sandbox
Flow API name: Payment_Stamp_Processed_Date
Flow label: Payment: Stamp Processed Date
Flow version: 6 (active)
Trigger object: npe01__OppPayment__c
Trigger record: a0X-synthetic-payment-0142 (invented ID, not a real record)
Run as: Automated Process (System)
Run time: 2026-09-21 09:14 America/New_York

Fault:
  Element: Update Records - "Set Processed Date"
  Element type: Record Update
  Error code: REQUIRED_FIELD_MISSING
  Message: Required fields are missing: [npe01__Payment_Date__c]

Occurrences: 3 faults in the last 24 hours, all on payments created by the nightly
gift-batch import. No faults on manually entered payments in the same window.

This is a synthetic support notification for demonstration only. No live org, flow
or record was involved.
""",
        "walkthrough.md": """# Walkthrough: alert-triage

Applies `/triage-alert` to the synthetic fault in input.md.

## 1. Identify flow and version

Flow: Payment: Stamp Processed Date (API name Payment_Stamp_Processed_Date), version
6, currently active. The fault notification reports the version that ran; version 6
matches the active version, so this is not a stale-version report.

## 2. Read the failed element

Element "Set Processed Date" is a Record Update on the triggering
npe01__OppPayment__c record. Error REQUIRED_FIELD_MISSING names
npe01__Payment_Date__c on that same object: the update tried to save the payment
without a value in a field the flow itself is supposed to be setting, which points at
the update action running before the value is assigned rather than after.

## 3. Classify

Configuration. The fault is not about a value missing from the source record (the
flow owns the date field), and it recurs only on batch-created payments, not manual
ones, so it does not look like a blanket permission or bulk-volume issue. It looks
like element ordering: the Update Records action appears to fire before the
Assignment element that computes the date.

## 4. Likely cause

Proposed, not yet confirmed against the actual flow canvas: the Update Records
element sits upstream of the Assignment element in version 6's element order, so on
a batch-created payment the update reaches Salesforce with npe01__Payment_Date__c
still blank. Manual payments do not fault because staff enter a date at creation, so
the flow's own value never needs to fill a blank one on that path.

## 5. Proposed fix

Reorder the flow so the Assignment element that sets the date runs before the Update
Records element, or fold the date assignment into the same Update Records action
instead of a separate step. Do not add a fallback default date as a substitute for
fixing the order: that would mask a batch-import timing problem the client should
know about.

## 6. How to verify

Retest with a single batch-import-created payment and confirm the update succeeds
with a populated date. Retest with a bulk gift-batch close producing 200+ payments
in one transaction, confirming no fault and no partial saves. Confirm the flow's
interview log shows the Assignment element executing before Update Records.

## 7. What to tell the client

Cause: the automation that stamps a payment's processed date was updating the
record before it computed the date, specifically on payments created by the nightly
batch import. Scope: 3 payments in the last 24 hours; check the full fault log for
the true count before closing this out. Fix: reorder the flow. Past payments created
during the faulted window still have no processed date and need a one-time review;
the fix does not retroactively repair them.

Proposed, not verified: the element-order explanation in steps 4 and 5. Confirming
it requires opening the actual flow version 6 canvas, which this synthetic
walkthrough does not have.
""",
    },
    "gift-payments": {
        "input.md": """SYNTHETIC EXAMPLE. No real organisation or person.

From: Synthetic Community Center, Development Operations Lead
To: Salesforce consultant
Subject: Payments need a processed date when we close a gift batch

When our gift processors close a batch in NPSP Batch Gift Entry, the payments that
come out of it show as Paid but the Payment Date field is empty. Staff have been
going in and hand-entering today's date on every payment after each batch close,
which is slow and easy to forget on a large batch.

Can you set it up so a payment gets a processed date automatically as soon as the
batch marks it paid? We do not need to touch payments that were already dated by
staff. This does not need to change how the batch itself works, just the payment
record afterward.

This is a synthetic request for demonstration only.
""",
        "walkthrough.md": """# Walkthrough: gift-payments

Applies `/gift-payments` to the synthetic request in input.md.

## Design

Record-triggered flow on npe01__OppPayment__c, after save.

Entry criteria: npe01__Paid__c equals True AND npe01__Payment_Date__c is null. This
fires only on a payment the batch just marked paid that has no date yet, matching the
stakeholder's request not to touch already-dated payments.

Action: update npe01__Payment_Date__c to today's date.

Proposal, not verified behavior: this design assumes the org's Batch Gift Entry
template sets npe01__Paid__c to True directly when the batch is closed. Confirm the
actual template configuration before building; if the template instead leaves
payments unpaid pending a separate approval step, the entry criteria need to key off
that step instead.

## Bulk-safety

The flow evaluates against the whole triggering record collection, not one record at
a time: no SOQL or DML inside a loop. A single gift-batch close can produce hundreds
of payments in one transaction; the entry criteria and update need to stay inside
Salesforce's per-transaction limits at that volume. Test at batch size, not only with
one record.

## Test plan, three cases

1. A single payment marked Paid with no processed date: confirm the flow sets
   today's date, and confirm a later unrelated save of that payment does not fire
   the update again, since the entry criteria requires the date to still be null.
2. A bulk batch close producing 200+ payments in one transaction: confirm every
   qualifying payment is dated and the transaction completes without hitting limits.
3. A payment marked Paid that already carries a processed date, for example one
   entered manually before the batch closed: confirm the flow leaves the existing
   date unchanged.

## Rollback

Deactivate the flow version. Payments already dated by the flow are unaffected,
since the flow only ever sets a previously-null field and never overwrites an
existing date. If any payments were backfilled by a separate one-time update (not
requested here), document that separately: a data change does not revert when the
flow is deactivated.
""",
    },
    "grants-outbound-funds": {
        "input.md": """SYNTHETIC EXAMPLE. No real organisation or person.

From: Synthetic Community Fund, Grants Manager
To: Salesforce consultant
Subject: Auto-create award records on funding request approval

We run our Outbound Funds Module funding requests through an approval process. Once
a request is approved, our grants team currently creates the award/disbursement
record by hand: amount, scheduled date, and a link back to the request. Some
requests pay out in one lump sum; others pay out over two or three scheduled
installments.

We want the disbursement record(s) created automatically as soon as a request is
approved, matching whatever schedule the request calls for. We do not want a second
disbursement created if someone re-saves an already-approved request.

This is a synthetic request for demonstration only.
""",
        "walkthrough.md": """# Walkthrough: grants-outbound-funds

Applies `/grants-outbound-funds` to the synthetic request in input.md.

## Objects

outfunds__Funding_Request__c: the ask, linked to an outfunds__Funding_Program__c.
outfunds__Disbursement__c: the award/payment record, linked back to the Funding
Request. Confirm the exact lookup and approval-status field API names in the
client's installed package version before building.

## Fields not writable on insert

Outbound Funds Module ships rollup and system-calculated fields on the Funding
Request, for example an amount-disbursed-to-date or disbursement-status summary
computed from child Disbursement records. Treat any such summary field as read-only
and package-computed, not something the flow sets directly. Confirm in the client's
org which Funding Request fields are actually rollups before designing the flow
around them; do not assume an unverified field name exists.

## Flow outline

Record-triggered flow on outfunds__Funding_Request__c, after save. Entry criteria:
the approval-status field transitions to "Approved" (exact value to confirm against
the client's process). For each scheduled installment on the request, one for a
lump-sum grant, more for a multi-installment schedule, create one
outfunds__Disbursement__c with the lookup to the Funding Request, the award amount
for that installment, and its scheduled date.

Scope the entry criteria to the actual status transition (old value not Approved,
new value Approved), not just "status equals Approved": a plain value check would
create a duplicate disbursement set every time an already-approved request is
re-saved for an unrelated field edit.

## Test data

An approved single-installment request with a known award amount. An approved
multi-installment request with a defined schedule. An already-approved request
re-saved with an unrelated field change, to confirm no duplicate disbursement is
created. A request saved in a non-approved status, to confirm nothing is created.

## Acceptance criteria

Approving a funding request creates the expected Disbursement record(s), each with
the correct amount, schedule date and lookup to the request. Re-saving an
already-approved request creates no duplicate. A rejected or pending request creates
no disbursement. The Funding Request's own rollup/summary fields are left to the
package to compute, not set by this flow.

Object and field names above match Outbound Funds Module as documented by the
vendor; verify the installed package version and exact API names in the client's
org before deploying.
""",
    },
    "requirements-to-build": {
        "input.md": """SYNTHETIC EXAMPLE. No real organisation or person.

From: Synthetic Community Center, Volunteer Coordinator

Right now when a volunteer signs up for a shift, we track it in a spreadsheet that
lives on one coordinator's laptop. If that coordinator is out, nobody else can see
who is signed up, and we have had two shifts this year with no coverage because of
it. We would like volunteers or staff to be able to see and manage shift sign-ups in
Salesforce instead, where the rest of our contact and program data already lives.

We need to know, for each shift, who signed up, whether they showed up, and whether
they cancelled ahead of time versus just not showing. A shift can have more than one
volunteer, and the same volunteer signs up for multiple shifts across a season. We
would like a simple way for staff to see any gaps, a shift with nobody signed up, in
the next two weeks. This is a synthetic note for demonstration only.
""",
        "walkthrough.md": """# Walkthrough: requirements-to-build

Applies `/requirements-to-build` to the synthetic note in input.md.

## User stories

1. As a volunteer coordinator, I want to record which volunteer signed up for which
   shift, so that sign-ups are visible to any staff member, not just one laptop.
2. As a volunteer coordinator, I want to mark whether a signed-up volunteer showed
   up, cancelled ahead of time, or did not show, so that attendance history is
   accurate.
3. As a staff member, I want to see which shifts in the next two weeks have no
   volunteer signed up, so that gaps can be filled before the shift happens.

## Acceptance criteria

Story 1: a staff user can create a sign-up linking a volunteer (Contact) to a shift,
for shifts that already allow more than one sign-up. Negative case: a sign-up cannot
be created for a shift date that has already passed, without an explicit override.

Story 2: a staff user can set a sign-up's status to Showed, Cancelled or No-Show,
and the status is visible on both the shift and the volunteer's record. Negative
case: a sign-up with no status set is distinguishable from one marked No-Show; the
two are not the same value.

Story 3: a staff user can filter or view shifts in the next two weeks with zero
sign-ups. Negative case: a shift with one cancelled sign-up and no other volunteer
still counts as a gap, since a cancellation is not coverage.

## Metadata list (proposed, not yet confirmed against the org)

Objects: a Shift object (custom object if none exists) and a Shift_Signup__c
junction object linking Shift to Contact.

Fields on Shift_Signup__c: lookup to Shift (required), lookup to Contact (required),
Status (picklist: Signed Up, Showed, Cancelled, No-Show; required, defaults to
Signed Up).

Automation: a report or list view filtering Shifts by date range, and a roll-up or
formula counting active sign-ups, to surface shifts with zero coverage in the next
two weeks. A validation rule blocking a new sign-up on a past shift date without an
override permission.

Permissions: read/create/edit on Shift_Signup__c for the staff and coordinator roles
who manage sign-ups; read-only for a general volunteer-facing view if one is wanted
(open question below).

## Test script

| # | Steps | Expected result | Actual result | Status |
| - | ----- | ---------------- | -------------- | ------ |
| 1 | Create a sign-up for a future shift, valid volunteer | Sign-up saves, status defaults to Signed Up | | Not run |
| 2 | Attempt a sign-up for a past shift date, no override | Save is blocked with a clear message | | Not run |
| 3 | Set a sign-up's status to Showed | Status saves and shows on both Shift and Contact views | | Not run |
| 4 | Set a sign-up's status to No-Show vs. leaving it blank | The two states are visibly distinct in any list or report | | Not run |
| 5 | View shifts in the next two weeks with a cancelled-only sign-up | Shift appears in the zero-coverage view | | Not run |

## Open questions

Does "Shift" already exist as an object or field somewhere in the org, for example
on a Program or Event object, or does this need a new object? What counts as
"showed up" if a volunteer arrives late; is that still Showed? Should volunteers
get any self-service visibility into sign-ups, or is this staff-only for now? Is
there an existing gap-notification process (email, report subscription) this should
feed into, or is a list view enough for the first version?

Hand off to `/prep-changeset` and `/validate-change` once these are answered and
scope is agreed.
""",
    },
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
        examples = []
        for name, scenario_files in _SCENARIOS.items():
            folder = client / "examples" / name
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            for filename, contents in scenario_files.items():
                ws.atomic_write_new(folder / filename, contents)
            examples.append(str(folder))
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
                "change_id": change["id"], "examples": examples}
    except BaseException:
        shutil.rmtree(root)
        raise
