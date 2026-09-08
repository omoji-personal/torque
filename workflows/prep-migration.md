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

Execute the user-authorized migration with the chosen normal tool or the optional
snapshot-aware `torque revert` delegate. Inspect its help for supported update/upsert/
import operations. Run bounded batches where useful. Keep input manifests, job IDs,
successful/failed row results, mappings and recovery files in the private task directory.
Inspect row failures even when the command exits successfully.

Reconcile intended source records against destination records by stable keys; totals
and “created today” counts alone are insufficient. Check relationships, duplicates,
representative business outcomes and side effects. Restore temporary settings to
their exact prior values after completion or failure, then verify restoration.
Report attempted/successful/failed counts, rerun strategy and remaining issues.
