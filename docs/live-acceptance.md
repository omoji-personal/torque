# Live acceptance: metadata, data, and recovery

Use a disposable org owned by the operator and a separate private test workspace.
Select the org explicitly. Developer Edition status alone does not establish that
an org is disposable. Record the resolved org ID and use unique component names.
Keep raw responses, snapshots, IDs, and client configuration outside this repository.

## Minimal repeatable exercise

1. Initialize a generic private workspace and one test client. Create a small SFDX
   project containing a new custom object with a text Name, one text field, and a
   permission set granting only that object's CRUD and the field's read/edit access.
   Verify those exact component names are absent before deploying.
2. Validate with `torque revert ... deploy --target-org ALIAS --source-dir PATH
   --dry-run`. Capture the exact deployment ID from its snapshot. Run the MetaAPI
   QA surface with that ID and an explicit manifest. Confirm `checkOnly=true`,
   terminal success, and that validation created no object.
3. Deploy the same artifacts. Verify the separate job ID, `checkOnly=false`,
   expected component identities, and live metadata. A temporary client QA router
   may select only MetaAPI; label the resulting coverage as metadata-only.
4. Change only the new field's label. Use `--metadata CustomField:Object__c.Field__c`
   so the wrapper captures its before-state. Check captured bytes and hashes,
   deploy outcome, and the actual live label. Preview and execute this snapshot's
   recovery, then verify that the original label is restored.
5. Observe field permissions and assignments for the actual intended user.
   Assign the new narrow permission set if needed, then query the exact
   PermissionSetAssignment by user ID and permission-set name. Current Salesforce
   CLI assignment output may contain names without assignment IDs; Torque reports
   that capture gap. Do not repeat a successful assignment because IDs are missing.
6. Create a synthetic row, update its text value, then make a separate later edit
   to its Name. Recover the value update. Verify the original value is restored
   and the later Name remains. Read-only/system fields may be present in the
   snapshot's full before-image; only explicitly updated fields belong in recovery.
7. Exercise create failure diagnostics with a nonexistent field on the owned
   test object, confirm no row was created, and verify the error identifier and
   snapshot location are visible. A successful command with missing ID or
   after-row evidence must report partial capture and must never retry creation.
8. Delete only the recorded synthetic rows and newly created assignment. Validate
   and execute exact destructive manifests for the owned permission set and
   object. Verify all are absent from active metadata/normal queries. Do not
   equate a soft delete with permanent erasure or purge unrelated recycle-bin data.

Recovery command spelling retains the native JSC delegate:

```sh
torque revert --workspace PRIVATE --client TEST revert preview SNAPSHOT --org ALIAS
torque revert --workspace PRIVATE --client TEST revert exec SNAPSHOT --org ALIAS \
  --reason "Restore the acceptance test's prior value"
```

## Interpret the evidence

- Metadata retrieval can include a parent container that the deployment report
  does not count as a changed component. Bind assertions to the intended change;
  investigate a component mismatch instead of substituting a newer job or claiming
  that the entire retrieval tree was changed.
- `--source-dir` and `--manifest` deployments currently do not capture metadata
  pre-state. Use `--metadata` for this recovery exercise. Initial creation cleanup
  is an explicit owned-artifact operation, not an automatic metadata undo claim.
- Preserve underlying command success separately from evidence capture. A create
  may have succeeded even when its snapshot is partial. Missing evidence is not
  an instruction to repeat a mutation.
- Advisory field/FLS/assignment observations do not prove non-admin browser access,
  automation, or human acceptance. QA process exit alone is not proof of all
  surfaces passing. Inspect the report and its declared scope.
- Record recovery does not undo automation or protect later edits to the same
  field. Null and single-quote-containing before-values have documented recovery
  limits. Permission-assignment automatic undo is not implemented.

## Next representative scenario

Use the same conversational workflow for a synthetic consulting engagement:
resume client context, turn a small requirement into a record-triggered Flow,
validate/deploy, prove the Flow's business result, walk it as an identified
non-admin user, restore the browser session, and produce a handoff with evidence.
Measure elapsed task time, repeated explanations, avoidable interruptions, and
corrections. This supplies a practical employer pilot and public demo while keeping
all example data synthetic. It remains separate from the narrower API acceptance
results in [validation](validation.md).

## Flow fixture cleanup

The rigorous disposable-org exercise encountered Salesforce's documented
unversioned Flow destructive-deletion error after successfully deactivating the
owned Flow. A generic cross-reference access error did not establish missing
permissions. The [Salesforce known issue](https://help.salesforce.com/s/issue?id=a028c00000gAwixAAC&language=en_US&title=deletion-of-flow-metadata-through-destructive-changes-not-supported)
and [version-qualified example](https://help.salesforce.com/s/articleView?id=004630366&language=en_US&type=1)
support investigating an exact `DeveloperName-VersionNumber` manifest member.
Check the current platform guidance for the actual deployment version.

Before applying that method, enumerate every version of the owned definition,
confirm no active version, and inspect interviews without filtering out statuses.
Bind a FlowVersionView observation using DurableId; its Id can be a placeholder.
Inspect the FlowInterview field description as well: in the exercised org,
FlowVersionViewId was a string containing a 15-character version ID. Filtering only
with its 18-character form returned no rows despite a real failed interview. Query
both observed ID forms and reconcile any exact dependency reported by Salesforce.
Use the exact observed version selector, validate separately, retain the original
job ID and re-read the prerequisites before actual deletion. Verify absence of
the definition and its versions before removing dependent fixture metadata. Do
not delete unrelated versions/interviews, grant broad permissions, or replay a
failed command based only on its error text. Record the requested selector and
the returned component name separately if the server normalizes the report.

These are fixture-cleanup lessons, not an automatic Flow rollback feature. Exact
live results and remaining qualification are recorded in [validation](validation.md).

The fixture exercise also showed why cleanup must follow component dependencies:
Salesforce refused separate deletion of an object's only layout. Remove the owned
permission sets and tabs, then remove child and parent fixture objects in dependency
order, accounting for their owned layouts in the final readback. Do not create
extra layouts just to bypass cleanup. A failed deployment can report individual
component successes while rolling back the transaction; query the exact current
components before interpreting what persisted or planning a revised attempt.
