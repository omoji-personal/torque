---
description: "Plan and execute scoped data migration with reconciliation and restoration of temporary settings."
---

# /prep-migration

Plan and execute scoped data migration with reconciliation and restoration of temporary settings.

**Interface:** Guided recipe using existing tools. A slash command is an assistant instruction, not a separate shell executable.

Identify source, destination, objects, external identifiers, mapping, file formats,
record scope and the chosen import/upsert tool. Check encoding, required fields,
duplicates, relationship order and idempotence. Resolve both org identities; do not
infer a destination from whichever Salesforce alias is currently default.

Reuse the project's configured migration engine and mappings. SFDMU is a candidate
for related-object migrations; CumulusCI data tasks or existing sfdx-hardis data
workspaces may already solve the problem. Inspect the installed tool's supported
behavior before adapting its configuration. Torque's scoped data/recovery wrappers
are not a replacement for a general relationship-aware migration engine.

Record rehearsal and real execution separately. A simulation that avoids target
writes cannot establish target validation, permissions, automation or business
outcomes. Use synthetic related data for repeatable test scenarios; Snowfakery can
help when already available or worth adding for the actual task. Small examples
do not need another dependency. Never use a synthetic fixture as live client proof.

Capture exact needed pre-state privately before destructive/update work and label
what is recoverable. Inventory relevant triggers, Flows, validations and integrations.
Use actual supported bypasses only if needed by the migration design; do not disable
all validation or assume a managed-package settings object. Record original setting
values and a restoration checklist before any temporary change.

Execute the user-authorized migration with the chosen normal tool or optional
snapshot-aware wrappers, for example
`torque data bulk update --sobject Object__c --file updates.csv --target-org ALIAS --workspace W --client C`
or `torque data bulk upsert --sobject Object__c --external-id External__c --file rows.csv --target-org ALIAS --workspace W --client C`.
Inspect help for supported record and import operations. Stage a small load, read
back the affected fields and relationships, preserve exact rollback IDs and get
the owner's go for the planned wider load within the existing authorization.
Existing-row recovery uses the reviewed `replay.csv` (Id plus null markers);
insert removal uses IDs from the exact job's successful results. Keep input manifests, job IDs,
successful/failed row results, mappings and recovery files in the private task directory.
Inspect row failures even when the command exits successfully.

Rehearsal and test records: save each created record's Id when it is created, in the
private task directory. Clean up by deleting only those Ids (an Id-file delete such as
`sf data delete bulk --file ids.csv` with an Id column, or the Torque route above).
Never build a delete from a query that matches by name, title, creator or date. If the
Id list is lost or incomplete, stop and ask instead of matching.

Reconcile intended source records against destination records by stable keys; totals
and “created today” counts alone are insufficient. Check relationships, duplicates,
representative business outcomes and side effects. Restore temporary settings to
their exact prior values after completion or failure, then verify restoration.
Report attempted/successful/failed counts, rerun strategy and remaining issues.

**Conversation input:** $ARGUMENTS
Use supplied context and existing authorization. Ask only for consequential missing information. Keep work and evidence in the selected private client workspace.
